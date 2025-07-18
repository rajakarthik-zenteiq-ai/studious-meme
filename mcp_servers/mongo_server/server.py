"""
MongoDB FastMCP server - Production-ready with comprehensive tools
Compatible with FastMCP v2.x and MCP Inspector v0.15.0
"""
import os
import sys
import asyncio
import hashlib
import re
from datetime import datetime, timedelta
import logging
from typing import Any, Dict, List, Optional, Union
import json
import base64
from enum import Enum

from motor.motor_asyncio import AsyncIOMotorClient
from motor.motor_asyncio import AsyncIOMotorGridFSBucket
from pydantic import BaseModel, Field, field_validator, model_validator
from pymongo import ASCENDING, DESCENDING, TEXT
from pymongo.errors import DuplicateKeyError, OperationFailure

# Correct FastMCP v2.x import
from fastmcp import FastMCP

# ── Config ──────────────────────────────────────────────────────────
HERE = os.path.dirname(__file__)
PROJECT_ROOT = os.path.join(HERE, "..", "..")
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "config"))

from config.settings import (
    MONGO_URI, MONGO_DB, MONGO_MCP_NAME,
    MONGO_MAX_POOL_SIZE, MONGO_MIN_POOL_SIZE,
    MONGO_MCP_PORT
)

# ── Logging setup ───────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────
MAX_BATCH_SIZE = 1000
MAX_QUERY_LIMIT = 500
DEFAULT_QUERY_LIMIT = 50
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
ALLOWED_FILE_EXTENSIONS = ['.txt', '.log', '.json', '.csv', '.xml']

# ── Enums ────────────────────────────────────────────────────────────
class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

class SortOrder(str, Enum):
    ASC = "asc"
    DESC = "desc"

class AggregationType(str, Enum):
    COUNT = "count"
    AVG = "avg"
    SUM = "sum"
    MIN = "min"
    MAX = "max"

# ── Database Manager ─────────────────────────────────────────────────
class DatabaseManager:
    """Manages MongoDB connection lifecycle with connection pooling and retry logic"""
    
    def __init__(self):
        self.client: Optional[AsyncIOMotorClient] = None
        self.db = None
        self.gridfs: Optional[AsyncIOMotorGridFSBucket] = None
        self._indexes_created = False
        self._connection_retries = 3
        self._retry_delay = 1.0
        self._initialized = False
    
    async def initialize(self):
        """Initialize database connection and indexes"""
        if not self._initialized:
            await self.connect()
            await self.ensure_indexes()
            self._initialized = True
    
    async def connect(self):
        """Establish database connection with retry logic"""
        for attempt in range(self._connection_retries):
            try:
                if self.client is None:
                    self.client = AsyncIOMotorClient(
                        MONGO_URI,
                        maxPoolSize=MONGO_MAX_POOL_SIZE,
                        minPoolSize=MONGO_MIN_POOL_SIZE,
                        serverSelectionTimeoutMS=5000,
                        connectTimeoutMS=10000,
                        socketTimeoutMS=10000,
                        retryWrites=True,
                        retryReads=True
                    )
                    self.db = self.client[MONGO_DB]
                    self.gridfs = AsyncIOMotorGridFSBucket(self.db)
                    
                    # Verify connection
                    await self.client.admin.command("ping")
                    logger.info(f"✅ MongoDB connected → {MONGO_URI}/{MONGO_DB}")
                    return
                    
            except Exception as e:
                logger.error(f"Connection attempt {attempt + 1} failed: {e}")
                if attempt < self._connection_retries - 1:
                    await asyncio.sleep(self._retry_delay * (attempt + 1))
                else:
                    raise
    
    async def ensure_indexes(self):
        """Create indexes with error handling"""
        if not self._indexes_created and self.db is not None:
            try:
                # Logs collection indexes
                await self.db.logs.create_index([("timestamp", DESCENDING)])
                await self.db.logs.create_index([("level", ASCENDING)])
                await self.db.logs.create_index([("source", ASCENDING)])
                await self.db.logs.create_index([("message", TEXT)])
                await self.db.logs.create_index(
                    [("timestamp", DESCENDING), ("level", ASCENDING)],
                    name="timestamp_level_compound"
                )
                
                # Chat history indexes
                await self.db.chat_history.create_index([("user_id", ASCENDING)])
                await self.db.chat_history.create_index([("timestamp", DESCENDING)])
                await self.db.chat_history.create_index(
                    [("user_id", ASCENDING), ("timestamp", DESCENDING)],
                    name="user_timestamp_compound"
                )
                
                # File metadata indexes
                await self.db.file_metadata.create_index([("filename", ASCENDING)])
                await self.db.file_metadata.create_index([("uploaded_at", DESCENDING)])
                await self.db.file_metadata.create_index([("user_id", ASCENDING)])
                
                self._indexes_created = True
                logger.info("✅ MongoDB indexes created successfully")
                
            except OperationFailure as e:
                if "not authorized" in str(e):
                    logger.warning("⚠️ Not authorized to create indexes - continuing without them")
                else:
                    logger.error(f"Failed to create indexes: {e}")
            except Exception as e:
                logger.warning(f"⚠️ Could not create indexes: {e}")
    
    async def close(self):
        """Close database connection gracefully"""
        if self.client:
            self.client.close()
            logger.info("🔌 MongoDB connection closed")
    
    async def check_connection(self) -> bool:
        """Check if database is connected and responsive"""
        try:
            if self.client is not None and self.db is not None:
                await self.client.admin.command("ping")
                return True
        except Exception:
            return False
        return False

# Create database manager instance
db_manager = DatabaseManager()

# Create MCP instance - initialization will happen when tools are called
mcp = FastMCP(MONGO_MCP_NAME)

# ── Pydantic models with comprehensive validation ────────────────────
class LogEntry(BaseModel):
    """Log entry with full validation"""
    timestamp: str = Field(..., description="ISO timestamp")
    level: LogLevel = Field(..., description="Log level")
    message: str = Field(..., min_length=1, max_length=10000)
    source: str = Field(default="unknown", max_length=255)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list, max_length=50)
    
    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, v: str) -> str:
        try:
            # Try to parse ISO format
            datetime.fromisoformat(v.replace('Z', '+00:00'))
            return v
        except ValueError:
            # If not valid, use current time
            return datetime.utcnow().isoformat()
    
    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: List[str]) -> List[str]:
        # Remove duplicates and empty tags
        return list(set(tag.strip() for tag in v if tag.strip()))

class LogQueryParams(BaseModel):
    """Parameters for querying logs"""
    start_date: Optional[str] = Field(None, description="Start date (YYYY-MM-DD)")
    end_date: Optional[str] = Field(None, description="End date (YYYY-MM-DD)")
    level: Optional[LogLevel] = Field(None, description="Filter by log level")
    source: Optional[str] = Field(None, description="Filter by source")
    search_text: Optional[str] = Field(None, description="Full-text search query")
    tags: Optional[List[str]] = Field(None, description="Filter by tags")
    limit: int = Field(DEFAULT_QUERY_LIMIT, ge=1, le=MAX_QUERY_LIMIT)
    skip: int = Field(0, ge=0)
    sort_by: str = Field("timestamp", description="Field to sort by")
    sort_order: SortOrder = Field(SortOrder.DESC)
    
    @model_validator(mode='after')
    def validate_date_range(self):
        if self.start_date and self.end_date:
            start = datetime.strptime(self.start_date, "%Y-%m-%d")
            end = datetime.strptime(self.end_date, "%Y-%m-%d")
            if start > end:
                raise ValueError("start_date must be before or equal to end_date")
        return self

class ChatMessage(BaseModel):
    """Chat message with validation"""
    user_id: str = Field(..., min_length=1, max_length=100)
    message: str = Field(..., min_length=1, max_length=5000)
    role: str = Field(default="user", pattern="^(user|assistant|system)$")
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    @field_validator("user_id")
    @classmethod
    def validate_user_id(cls, v: str) -> str:
        # Remove potentially dangerous characters
        return v.strip().replace("$", "").replace(".", "")

class FileUploadRequest(BaseModel):
    """File upload request with validation"""
    filename: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., description="Base64 encoded file content")
    content_type: str = Field(default="application/octet-stream")
    user_id: str = Field(..., min_length=1, max_length=100)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    @field_validator("filename")
    @classmethod
    def validate_filename(cls, v: str) -> str:
        # Sanitize filename
        import re
        # Remove path components and dangerous characters
        filename = os.path.basename(v)
        filename = re.sub(r'[^\w\s.-]', '', filename)
        return filename or "unnamed_file"
    
    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str) -> str:
        try:
            # Validate base64
            decoded = base64.b64decode(v)
            if len(decoded) > MAX_FILE_SIZE:
                raise ValueError(f"File size exceeds maximum of {MAX_FILE_SIZE} bytes")
            return v
        except Exception as e:
            raise ValueError(f"Invalid base64 content: {str(e)}")

class LogAggregationRequest(BaseModel):
    """Request for log aggregation operations"""
    group_by: str = Field(..., description="Field to group by")
    aggregation: AggregationType = Field(..., description="Type of aggregation")
    field: Optional[str] = Field(None, description="Field to aggregate (for sum/avg)")
    filters: Optional[LogQueryParams] = Field(None, description="Query filters")
    limit: int = Field(20, ge=1, le=100)

# ── Helper functions ─────────────────────────────────────────────────
async def ensure_db_initialized():
    """Ensure database is initialized before tool execution"""
    if not db_manager._initialized:
        await db_manager.initialize()

def build_query_filter(params: LogQueryParams) -> Dict[str, Any]:
    """Build MongoDB query filter from parameters"""
    query = {}
    
    # Date range filter
    if params.start_date or params.end_date:
        timestamp_filter = {}
        if params.start_date:
            timestamp_filter["$gte"] = f"{params.start_date}T00:00:00"
        if params.end_date:
            timestamp_filter["$lte"] = f"{params.end_date}T23:59:59"
        query["timestamp"] = timestamp_filter
    
    # Level filter
    if params.level:
        query["level"] = params.level.value
    
    # Source filter
    if params.source:
        query["source"] = {"$regex": params.source, "$options": "i"}
    
    # Tags filter
    if params.tags:
        query["tags"] = {"$in": params.tags}
    
    # Text search
    if params.search_text:
        query["$text"] = {"$search": params.search_text}
    
    return query

# ── MCP Tools ────────────────────────────────────────────────────────

@mcp.tool()
async def get_logs_by_date(date: str) -> Dict[str, Any]:
    """Get all logs for a specific date with detailed information."""
    await ensure_db_initialized()
    
    # Validate date format
    try:
        parsed_date = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return {
            "success": False,
            "error": "Invalid date format. Use YYYY-MM-DD"
        }
    
    if db_manager.db is None:
        return {
            "success": False,
            "error": "Database not initialized"
        }
    if not await db_manager.check_connection():
        return {
            "success": False,
            "error": "Database not connected"
        }
    
    try:
        # Build date range for the entire day
        start_time = parsed_date.isoformat()
        end_time = (parsed_date + timedelta(days=1)).isoformat()
        
        cursor = db_manager.db["logs"].find({
            "timestamp": {
                "$gte": start_time,
                "$lt": end_time
            }
        }).sort("timestamp", DESCENDING)
        
        docs = await cursor.to_list(length=MAX_BATCH_SIZE)
        
        # Process documents
        for doc in docs:
            doc["_id"] = str(doc["_id"])
        
        # Get summary statistics
        stats = {
            "total": len(docs),
            "by_level": {},
            "by_source": {}
        }
        
        for doc in docs:
            level = doc.get("level", "UNKNOWN")
            source = doc.get("source", "unknown")
            
            stats["by_level"][level] = stats["by_level"].get(level, 0) + 1
            stats["by_source"][source] = stats["by_source"].get(source, 0) + 1
        
        return {
            "success": True,
            "date": date,
            "count": len(docs),
            "logs": docs,
            "statistics": stats
        }
        
    except Exception as e:
        logger.error(f"Error fetching logs by date: {e}")
        return {
            "success": False,
            "error": f"Database error: {str(e)}"
        }

@mcp.tool()
async def query_logs(params: LogQueryParams) -> Dict[str, Any]:
    """Query logs with advanced filtering options."""
    await ensure_db_initialized()
    
    if db_manager.db is None:
        return {
            "success": False,
            "error": "Database not initialized"
        }
    if not await db_manager.check_connection():
        return {
            "success": False,
            "error": "Database not connected"
        }
    
    try:
        # Build query
        query = build_query_filter(params)
        
        # Build sort
        sort_direction = DESCENDING if params.sort_order == SortOrder.DESC else ASCENDING
        
        # Execute query
        cursor = (
            db_manager.db["logs"]
            .find(query)
            .sort(params.sort_by, sort_direction)
            .skip(params.skip)
            .limit(params.limit)
        )
        
        docs = await cursor.to_list(length=params.limit)
        
        # Get total count for pagination
        total_count = await db_manager.db["logs"].count_documents(query)
        
        # Process documents
        for doc in docs:
            doc["_id"] = str(doc["_id"])
        
        return {
            "success": True,
            "query": query,
            "count": len(docs),
            "total_count": total_count,
            "skip": params.skip,
            "limit": params.limit,
            "logs": docs,
            "has_more": (params.skip + len(docs)) < total_count
        }
        
    except Exception as e:
        logger.error(f"Error querying logs: {e}")
        return {
            "success": False,
            "error": f"Query error: {str(e)}"
        }

@mcp.tool()
async def store_logs(logs: List[LogEntry]) -> Dict[str, Any]:
    """Store multiple log entries with validation and deduplication."""
    await ensure_db_initialized()
    
    if db_manager.db is None:
        return {
            "success": False,
            "error": "Database not initialized"
        }
    if not await db_manager.check_connection():
        return {
            "success": False,
            "error": "Database not connected"
        }
    
    if not logs:
        return {
            "success": False,
            "error": "No logs provided"
        }
    
    if len(logs) > MAX_BATCH_SIZE:
        return {
            "success": False,
            "error": f"Batch size exceeds maximum of {MAX_BATCH_SIZE}"
        }
    
    try:
        to_insert = []
        duplicates = 0
        
        for log in logs:
            doc = log.model_dump()
            doc["server_timestamp"] = datetime.utcnow()
            
            # Add hash for deduplication
            import hashlib
            content = f"{doc['timestamp']}:{doc['level']}:{doc['message']}:{doc['source']}"
            doc["content_hash"] = hashlib.sha256(content.encode()).hexdigest()
            
            to_insert.append(doc)
        
        # Insert with duplicate handling
        inserted_count = 0
        if to_insert:
            try:
                result = await db_manager.db["logs"].insert_many(
                    to_insert,
                    ordered=False
                )
                inserted_count = len(result.inserted_ids)
            except Exception as e:
                # Handle partial inserts due to duplicates
                if "duplicate key error" in str(e).lower():
                    # Count actual inserts
                    for doc in to_insert:
                        try:
                            await db_manager.db["logs"].insert_one(doc)
                            inserted_count += 1
                        except DuplicateKeyError:
                            duplicates += 1
                else:
                    raise
        
        return {
            "success": True,
            "inserted_count": inserted_count,
            "duplicate_count": duplicates,
            "total_provided": len(logs)
        }
        
    except Exception as e:
        logger.error(f"Error storing logs: {e}")
        return {
            "success": False,
            "error": f"Storage error: {str(e)}"
        }

@mcp.tool()
async def search_logs(query: str, limit: int = DEFAULT_QUERY_LIMIT) -> Dict[str, Any]:
    """Full-text search across log messages."""
    await ensure_db_initialized()
    
    if db_manager.db is None:
        return {
            "success": False,
            "error": "Database not initialized"
        }
    if not await db_manager.check_connection():
        return {
            "success": False,
            "error": "Database not connected"
        }
    
    if not query or not query.strip():
        return {
            "success": False,
            "error": "Search query cannot be empty"
        }
    
    # Sanitize limit
    limit = max(1, min(limit, MAX_QUERY_LIMIT))
    
    try:
        # Use text search with relevance score
        cursor = (
            db_manager.db["logs"].find(
                {"$text": {"$search": query}},
                {"score": {"$meta": "textScore"}}
            )
            .sort([("score", {"$meta": "textScore"})])
            .limit(limit)
        )
        
        docs = await cursor.to_list(length=limit)
        
        # Process and enhance results
        results = []
        for doc in docs:
            doc["_id"] = str(doc["_id"])
            relevance_score = doc.pop("score", 0)
            
            # Highlight matching text (simple version)
            message = doc.get("message", "")
            query_terms = query.lower().split()
            for term in query_terms:
                if term in message.lower():
                    # Simple highlighting
                    message = message.replace(term, f"**{term}**")
            
            results.append({
                **doc,
                "relevance_score": relevance_score,
                "highlighted_message": message
            })
        
        return {
            "success": True,
            "query": query,
            "count": len(results),
            "results": results
        }
        
    except OperationFailure as e:
        if "text index required" in str(e):
            # Fallback to regex search if text index not available
            try:
                cursor = (
                    db_manager.db["logs"]
                    .find({"message": {"$regex": query, "$options": "i"}})
                    .sort("timestamp", DESCENDING)
                    .limit(limit)
                )
                docs = await cursor.to_list(length=limit)
                for doc in docs:
                    doc["_id"] = str(doc["_id"])
                
                return {
                    "success": True,
                    "query": query,
                    "count": len(docs),
                    "results": docs,
                    "search_type": "regex_fallback"
                }
            except Exception as fallback_error:
                logger.error(f"Fallback search error: {fallback_error}")
                return {
                    "success": False,
                    "error": "Search failed: Text index not available and regex fallback failed"
                }
        else:
            logger.error(f"Search error: {e}")
            return {
                "success": False,
                "error": f"Search error: {str(e)}"
            }
    except Exception as e:
        logger.error(f"Error searching logs: {e}")
        return {
            "success": False,
            "error": f"Search error: {str(e)}"
        }

@mcp.tool()
async def aggregate_logs(request: LogAggregationRequest) -> Dict[str, Any]:
    """Perform aggregation operations on logs."""
    await ensure_db_initialized()
    
    if db_manager.db is None:
        return {
            "success": False,
            "error": "Database not initialized"
        }
    if not await db_manager.check_connection():
        return {
            "success": False,
            "error": "Database not connected"
        }
    
    try:
        # Build match stage from filters
        match_stage = {}
        if request.filters:
            match_stage = build_query_filter(request.filters)
        
        # Build aggregation pipeline
        pipeline = []
        
        if match_stage:
            pipeline.append({"$match": match_stage})
        
        # Group stage
        group_dict = {"_id": f"${request.group_by}"}
        if request.aggregation == AggregationType.COUNT:
            group_dict["count"] = {"$sum": 1}  # type: ignore
        elif request.aggregation == AggregationType.SUM and request.field:
            group_dict["sum"] = {"$sum": f"${request.field}"}  # type: ignore
        elif request.aggregation == AggregationType.AVG and request.field:
            group_dict["average"] = {"$avg": f"${request.field}"}  # type: ignore
        elif request.aggregation == AggregationType.MIN and request.field:
            group_dict["min"] = {"$min": f"${request.field}"}  # type: ignore
        elif request.aggregation == AggregationType.MAX and request.field:
            group_dict["max"] = {"$max": f"${request.field}"}  # type: ignore
        else:
            group_dict["count"] = {"$sum": 1}  # type: ignore
        group_stage = {"$group": group_dict}  # type: ignore
        pipeline.append(group_stage)
        
        # Sort by aggregated value
        sort_field = next((k for k in group_dict if k != "_id"), "count")
        pipeline.append({"$sort": {sort_field: -1}})
        
        # Limit results
        pipeline.append({"$limit": request.limit})
        
        # Execute aggregation
        cursor = db_manager.db["logs"].aggregate(pipeline)
        results = await cursor.to_list(length=request.limit)
        
        # Format results
        formatted_results = []
        for result in results:
            formatted_results.append({
                request.group_by: result["_id"],
                **{k: v for k, v in result.items() if k != "_id"}
            })
        
        return {
            "success": True,
            "aggregation": request.aggregation.value,
            "group_by": request.group_by,
            "count": len(formatted_results),
            "results": formatted_results
        }
        
    except Exception as e:
        logger.error(f"Error aggregating logs: {e}")
        return {
            "success": False,
            "error": f"Aggregation error: {str(e)}"
        }

@mcp.tool()
async def append_chat(chat: ChatMessage) -> Dict[str, Any]:
    """Append a chat message to history with metadata."""
    await ensure_db_initialized()
    
    if db_manager.db is None:
        return {
            "success": False,
            "error": "Database not initialized"
        }
    if not await db_manager.check_connection():
        return {
            "success": False,
            "error": "Database not connected"
        }
    
    try:
        doc = chat.model_dump()
        doc["timestamp"] = datetime.utcnow()
        doc["edited"] = False
        doc["deleted"] = False
        
        result = await db_manager.db["chat_history"].insert_one(doc)
        
        return {
            "success": True,
            "message_id": str(result.inserted_id),
            "user_id": chat.user_id,
            "timestamp": doc["timestamp"].isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error appending chat: {e}")
        return {
            "success": False,
            "error": f"Chat storage error: {str(e)}"
        }

@mcp.tool()
async def get_chat_history(
    user_id: str,
    limit: int = DEFAULT_QUERY_LIMIT,
    before_timestamp: Optional[str] = None
) -> Dict[str, Any]:
    """Get chat history with pagination support."""
    await ensure_db_initialized()
    
    if db_manager.db is None:
        return {
            "success": False,
            "error": "Database not initialized"
        }
    if not await db_manager.check_connection():
        return {
            "success": False,
            "error": "Database not connected"
        }
    
    if not user_id or not user_id.strip():
        return {
            "success": False,
            "error": "User ID cannot be empty"
        }
    
    # Sanitize limit
    limit = max(1, min(limit, MAX_QUERY_LIMIT))
    
    try:
        # Build query
        query = {"user_id": user_id, "deleted": {"$ne": True}}
        
        if before_timestamp:
            query["timestamp"] = {"$lt": datetime.fromisoformat(before_timestamp)}
        
        # Get messages
        cursor = (
            db_manager.db["chat_history"]
            .find(query)
            .sort("timestamp", DESCENDING)
            .limit(limit)
        )
        
        msgs = await cursor.to_list(length=limit)
        
        # Process messages
        for m in msgs:
            m["_id"] = str(m["_id"])
            if isinstance(m.get("timestamp"), datetime):
                m["timestamp"] = m["timestamp"].isoformat()
        
        # Reverse to get chronological order
        msgs.reverse()
        
        # Check if there are more messages
        oldest_timestamp = msgs[0]["timestamp"] if msgs else None
        has_more = False
        if oldest_timestamp:
            older_count = await db_manager.db["chat_history"].count_documents({
                "user_id": user_id,
                "timestamp": {"$lt": datetime.fromisoformat(oldest_timestamp)},
                "deleted": {"$ne": True}
            })
            has_more = older_count > 0
        
        return {
            "success": True,
            "user_id": user_id,
            "count": len(msgs),
            "messages": msgs,
            "has_more": has_more,
            "oldest_timestamp": oldest_timestamp
        }
        
    except Exception as e:
        logger.error(f"Error fetching chat history: {e}")
        return {
            "success": False,
            "error": f"Database error: {str(e)}"
        }

@mcp.tool()
async def upload_file(request: FileUploadRequest) -> Dict[str, Any]:
    """Upload a file to GridFS with metadata."""
    await ensure_db_initialized()
    
    if db_manager.db is None or db_manager.gridfs is None:
        return {
            "success": False,
            "error": "Database or GridFS not initialized"
        }
    if not await db_manager.check_connection():
        return {
            "success": False,
            "error": "Database not connected"
        }
    
    try:
        # Decode base64 content
        file_content = base64.b64decode(request.content)
        
        # Check file extension
        file_ext = os.path.splitext(request.filename)[1].lower()
        if ALLOWED_FILE_EXTENSIONS and file_ext not in ALLOWED_FILE_EXTENSIONS:
            return {
                "success": False,
                "error": f"File type not allowed. Allowed types: {', '.join(ALLOWED_FILE_EXTENSIONS)}"
            }
        
        # Store file in GridFS
        file_id = await db_manager.gridfs.upload_from_stream(
            request.filename,
            file_content,
            metadata={
                "content_type": request.content_type,
                "user_id": request.user_id,
                "uploaded_at": datetime.utcnow(),
                "file_size": len(file_content),
                **request.metadata
            }
        )
        
        # Store metadata in separate collection for easier querying
        metadata_doc = {
            "file_id": file_id,
            "filename": request.filename,
            "content_type": request.content_type,
            "user_id": request.user_id,
            "file_size": len(file_content),
            "uploaded_at": datetime.utcnow(),
            "metadata": request.metadata
        }
        
        await db_manager.db["file_metadata"].insert_one(metadata_doc)
        
        return {
            "success": True,
            "file_id": str(file_id),
            "filename": request.filename,
            "file_size": len(file_content),
            "content_type": request.content_type
        }
        
    except Exception as e:
        logger.error(f"Error uploading file: {e}")
        return {
            "success": False,
            "error": f"Upload error: {str(e)}"
        }

@mcp.tool()
async def download_file(file_id: str) -> Dict[str, Any]:
    """Download a file from GridFS."""
    await ensure_db_initialized()
    
    if db_manager.db is None or db_manager.gridfs is None:
        return {
            "success": False,
            "error": "Database or GridFS not initialized"
        }
    if not await db_manager.check_connection():
        return {
            "success": False,
            "error": "Database not connected"
        }
    
    try:
        from bson import ObjectId
        
        # Convert string to ObjectId
        try:
            obj_id = ObjectId(file_id)
        except:
            return {
                "success": False,
                "error": "Invalid file ID format"
            }
        
        # Download file
        grid_out = await db_manager.gridfs.open_download_stream(obj_id)
        
        # Read file content
        file_content = await grid_out.read()
        
        # Get metadata
        metadata = grid_out.metadata or {}
        
        # Encode to base64
        encoded_content = base64.b64encode(file_content).decode('utf-8')
        
        return {
            "success": True,
            "file_id": file_id,
            "filename": grid_out.filename,
            "content": encoded_content,
            "content_type": metadata.get("content_type", "application/octet-stream"),
            "file_size": len(file_content),
            "metadata": metadata
        }
        
    except Exception as e:
        logger.error(f"Error downloading file: {e}")
        return {
            "success": False,
            "error": f"Download error: {str(e)}"
        }

@mcp.tool()
async def delete_logs(
    query: LogQueryParams,
    confirm: bool = False
) -> Dict[str, Any]:
    """Delete logs matching the query criteria (requires confirmation)."""
    await ensure_db_initialized()
    
    if db_manager.db is None:
        return {
            "success": False,
            "error": "Database not initialized"
        }
    if not await db_manager.check_connection():
        return {
            "success": False,
            "error": "Database not connected"
        }
    
    if not confirm:
        return {
            "success": False,
            "error": "Deletion requires confirm=true parameter",
            "warning": "This operation cannot be undone"
        }
    
    try:
        # Build query filter
        query_filter = build_query_filter(query)
        
        if not query_filter:
            return {
                "success": False,
                "error": "Cannot delete all logs. Please specify filter criteria."
            }
        
        # Count documents to be deleted
        count = await db_manager.db["logs"].count_documents(query_filter)
        
        if count == 0:
            return {
                "success": True,
                "deleted_count": 0,
                "message": "No logs matched the criteria"
            }
        
        # Perform deletion
        result = await db_manager.db["logs"].delete_many(query_filter)
        
        return {
            "success": True,
            "deleted_count": result.deleted_count,
            "query_filter": query_filter
        }
        
    except Exception as e:
        logger.error(f"Error deleting logs: {e}")
        return {
            "success": False,
            "error": f"Deletion error: {str(e)}"
        }

@mcp.tool()
async def get_system_stats() -> Dict[str, Any]:
    """Get system statistics and database information."""
    await ensure_db_initialized()
    
    if db_manager.db is None or db_manager.client is None:
        return {
            "success": False,
            "error": "Database or client not initialized"
        }
    if not await db_manager.check_connection():
        return {
            "success": False,
            "error": "Database not connected"
        }
    
    try:
        # Get database statistics
        db_stats = await db_manager.db.command("dbStats")
        
        # Get collection statistics
        collections_info = {}
        for collection_name in ["logs", "chat_history", "file_metadata"]:
            try:
                stats = await db_manager.db.command("collStats", collection_name)
                collections_info[collection_name] = {
                    "count": stats.get("count", 0),
                    "size": stats.get("size", 0),
                    "avgObjSize": stats.get("avgObjSize", 0),
                    "storageSize": stats.get("storageSize", 0),
                    "indexes": stats.get("nindexes", 0)
                }
            except:
                collections_info[collection_name] = {"error": "Collection not found"}
        
        # Get index information
        indexes_info = {}
        for collection_name in ["logs", "chat_history", "file_metadata"]:
            try:
                indexes = await db_manager.db[collection_name].list_indexes().to_list(None)
                indexes_info[collection_name] = [
                    {
                        "name": idx.get("name"),
                        "keys": idx.get("key"),
                        "unique": idx.get("unique", False)
                    }
                    for idx in indexes
                ]
            except:
                indexes_info[collection_name] = []
        
        # Get server info
        server_info = await db_manager.client.server_info()
        
        return {
            "success": True,
            "database": {
                "name": db_stats.get("db"),
                "collections": db_stats.get("collections", 0),
                "dataSize": db_stats.get("dataSize", 0),
                "storageSize": db_stats.get("storageSize", 0),
                "indexes": db_stats.get("indexes", 0)
            },
            "collections": collections_info,
            "indexes": indexes_info,
            "server": {
                "version": server_info.get("version"),
                "host": MONGO_URI.split("@")[-1] if "@" in MONGO_URI else MONGO_URI,
                "uptime": server_info.get("uptime", 0)
            },
            "connection_pool": {
                "max_size": MONGO_MAX_POOL_SIZE,
                "min_size": MONGO_MIN_POOL_SIZE
            },
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error getting system stats: {e}")
        return {
            "success": False,
            "error": f"Stats error: {str(e)}"
        }

@mcp.tool()
async def health_check() -> Dict[str, Any]:
    """Comprehensive health check of the MongoDB MCP server."""
    await ensure_db_initialized()
    
    status = "healthy"
    issues = []
    
    # Check database connection
    db_connected = await db_manager.check_connection()
    if not db_connected:
        status = "unhealthy"
        issues.append("Database connection failed")
    
    # Check collections
    collections_status = {}
    if db_connected and db_manager.db is not None:
        try:
            collections = await db_manager.db.list_collection_names()
            for col in ["logs", "chat_history", "file_metadata"]:
                collections_status[col] = "exists" if col in collections else "missing"
                if col not in collections:
                    status = "degraded"
                    issues.append(f"Collection '{col}' missing")
        except Exception as e:
            status = "degraded"
            issues.append(f"Failed to list collections: {str(e)}")
    
    # Check GridFS
    gridfs_status = "unknown"
    if db_connected and db_manager.gridfs is not None:
        try:
            # Try to list GridFS files (limit 1)
            await db_manager.gridfs.find({}).to_list(1)
            gridfs_status = "operational"
        except Exception as e:
            gridfs_status = "error"
            issues.append(f"GridFS error: {str(e)}")
    
    # Server-side tools information (static list since list_tools() is client-side only)
    # Note: For dynamic tool discovery, use client.list_tools() from the client side
    available_tools = [
        "get_logs_by_date", "query_logs", "store_logs", "search_logs",
        "aggregate_logs", "append_chat", "get_chat_history", "upload_file",
        "download_file", "delete_logs", "get_system_stats", "health_check"
    ]
    
    return {
        "success": status != "unhealthy",
        "status": status,
        "timestamp": datetime.utcnow().isoformat(),
        "database": {
            "connected": db_connected,
            "name": MONGO_DB if db_connected else None,
            "collections": collections_status
        },
        "gridfs": gridfs_status,
        "mcp": {
            "name": MONGO_MCP_NAME,
            "tools_count": len(available_tools),
            "tools": available_tools,
            "note": "For dynamic tool discovery, use client.list_tools() from client-side"
        },
        "issues": issues if issues else None
    }

# ── Main entry point ─────────────────────────────────────────────────
if __name__ == "__main__":
    # Run with HTTP transport (recommended for production)
    # The database initialization will happen when the first tool is called
    mcp.run(
        transport="http",
        host="0.0.0.0",  # Allow external connections
        port=MONGO_MCP_PORT,
        log_level="INFO"
    )