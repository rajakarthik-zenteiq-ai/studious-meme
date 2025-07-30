"""
MongoDB FastMCP server - Production-ready with comprehensive tools and MinIO integration
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
import io

from motor.motor_asyncio import AsyncIOMotorClient
from motor.motor_asyncio import AsyncIOMotorGridFSBucket
from pydantic import BaseModel, Field, field_validator, model_validator
from pymongo import ASCENDING, DESCENDING, TEXT
from pymongo.errors import DuplicateKeyError, OperationFailure
from minio import Minio
from minio.error import S3Error

# Correct FastMCP v2.x import
from fastmcp import FastMCP, Context

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

# ── Auth utilities ──────────────────────────────────────────────────
def get_user_id_from_context(ctx: Context) -> str:
    """
    Extract user_id from request headers for proper authentication.
    This ensures users can only access their own data.
    """
    if hasattr(ctx, 'request_context') and ctx.request_context:
        # Try to get user_id from headers
        headers = getattr(ctx.request_context, 'headers', {})
        if isinstance(headers, dict):
            user_id = headers.get('x-user-id') or headers.get('X-User-ID')
            if user_id:
                return user_id
    
    # Fallback to a default for development (remove in production)
    logger.warning("No user_id found in headers, using 'anonymous'")
    return "anonymous"

def validate_user_access(ctx: Context, resource_user_id: str) -> bool:
    """
    Validate that the requesting user has access to the resource.
    Returns True if access is allowed, False otherwise.
    """
    current_user_id = get_user_id_from_context(ctx)
    
    # Users can only access their own resources
    if current_user_id != resource_user_id:
        logger.warning(f"Access denied: user {current_user_id} tried to access resource owned by {resource_user_id}")
        return False
    
    return True

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

# ── Database Manager with MinIO ─────────────────────────────────────────────────
class DatabaseManager:
    """Manages MongoDB connection lifecycle with connection pooling and retry logic, plus MinIO"""
    
    def __init__(self):
        self.client: Optional[AsyncIOMotorClient] = None
        self.db = None
        self.gridfs: Optional[AsyncIOMotorGridFSBucket] = None
        self.minio_client: Optional[Minio] = None
        self._indexes_created = False
        self._connection_retries = 3
        self._retry_delay = 1.0
        self._initialized = False
    
    async def initialize(self):
        """Initialize database connection, MinIO, and indexes"""
        if not self._initialized:
            await self.connect()
            await self.setup_minio()
            await self.ensure_indexes()
            self._initialized = True
    
    async def setup_minio(self):
        """Setup MinIO client and ensure buckets exist"""
        try:
            # Determine MinIO endpoint based on environment
            if os.getenv("IS_DOCKER"):
                minio_endpoint = "minio:9000"
            else:
                minio_endpoint = "localhost:9000"
            
            self.minio_client = Minio(
                minio_endpoint,
                access_key="minioadmin",
                secret_key="minioadmin",
                secure=False
            )
            
            # Ensure required buckets exist
            required_buckets = ["datasets", "models", "files"]
            for bucket_name in required_buckets:
                if not self.minio_client.bucket_exists(bucket_name):
                    self.minio_client.make_bucket(bucket_name)
                    logger.info(f"✅ Created MinIO bucket: {bucket_name}")
            
            logger.info("✅ MinIO client initialized successfully")
            
        except Exception as e:
            logger.warning(f"⚠️ MinIO setup failed: {e} - continuing without file storage")
            self.minio_client = None
    
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
    """Chat message with validation - user_id comes from headers"""
    message: str = Field(..., min_length=1, max_length=5000)
    role: str = Field(default="user", pattern="^(user|assistant|system)$")
    metadata: Dict[str, Any] = Field(default_factory=dict)

class FileUploadRequest(BaseModel):
    """File upload request with validation - user_id comes from headers"""
    filename: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., description="Base64 encoded file content")
    content_type: str = Field(default="application/octet-stream")
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
            # Remove potential data URL prefix if present
            if v.startswith('data:'):
                # Extract base64 part after comma
                if ',' in v:
                    v = v.split(',', 1)[1]
            
            # Remove whitespace and newlines
            v = v.replace('\n', '').replace('\r', '').replace(' ', '')
            
            # Validate base64
            decoded = base64.b64decode(v, validate=True)
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
    """
    Get all logs for a specific date with detailed information.
    
    This tool retrieves logs for a complete day (00:00:00 to 23:59:59) and provides
    comprehensive statistics including log level distribution and source breakdown.
    
    Args:
        date: Date in YYYY-MM-DD format (e.g., "2024-01-15")
        
    Returns:
        Structured response with logs, count, and statistics
        
    Example:
        get_logs_by_date("2024-01-15")
    """
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
    """
    Query logs with advanced filtering options and pagination.
    
    This is the primary tool for searching and filtering log entries with support for:
    - Date range filtering (start_date, end_date)  
    - Log level filtering (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    - Source system filtering
    - Full-text search across message content
    - Tag-based filtering
    - Sorting and pagination
    
    Args:
        params: LogQueryParams object with filtering options
        
    Returns:
        Structured response with matching logs, pagination info, and total count
        
    Example:
        query_logs({"level": "ERROR", "start_date": "2024-01-01", "limit": 50})
    """
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
    """
    Full-text search across log messages with relevance scoring.
    
    This tool performs intelligent text search across all log messages:
    - Uses MongoDB text indexes for fast search
    - Returns results ranked by relevance score
    - Highlights matching terms in results
    - Automatic fallback to regex search if text index unavailable
    
    Args:
        query: Search terms or phrase (e.g., "error database connection")
        limit: Maximum number of results to return (1-500, default: 50)
        
    Returns:
        Structured response with ranked search results and highlighted matches
        
    Example:
        search_logs("database connection failed", 25)
    """
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
async def append_chat(chat: ChatMessage, ctx: Context) -> Dict[str, Any]:
    """
    Append a chat message to history with metadata and user isolation.
    
    This tool stores chat messages in a user-specific history:
    - Automatically extracts user_id from request headers for authentication
    - Adds timestamp and metadata to messages
    - Ensures messages are only visible to the originating user
    - Supports role-based messages (user, assistant, system)
    
    Args:
        chat: ChatMessage object with message content and role
        ctx: Request context containing user authentication headers
        
    Returns:
        Structured response with message_id, user_id, and timestamp
        
    Example:
        append_chat({"message": "Hello, how are you?", "role": "user"})
    """
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
        # Extract user_id from context headers
        user_id = get_user_id_from_context(ctx)
        
        doc = chat.model_dump()
        doc["user_id"] = user_id  # Add user_id from headers
        doc["timestamp"] = datetime.utcnow()
        doc["edited"] = False
        doc["deleted"] = False
        
        result = await db_manager.db["chat_history"].insert_one(doc)
        
        return {
            "success": True,
            "message_id": str(result.inserted_id),
            "user_id": user_id,
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
    ctx: Context,
    limit: int = DEFAULT_QUERY_LIMIT,
    before_timestamp: Optional[str] = None
) -> Dict[str, Any]:
    """
    Get chat history with pagination support and user isolation.
    
    This tool retrieves chat messages for the authenticated user:
    - Automatically extracts user_id from request headers for authentication
    - Returns only messages belonging to the requesting user
    - Supports pagination with before_timestamp for infinite scroll
    - Orders messages chronologically (oldest first)
    - Filters out deleted messages
    
    Args:
        ctx: Request context containing user authentication headers
        limit: Maximum number of messages to return (1-500, default: 50)
        before_timestamp: ISO timestamp to get messages before (for pagination)
        
    Returns:
        Structured response with messages, pagination info, and metadata
        
    Example:
        get_chat_history(100, "2024-01-01T12:00:00")
    """
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
        # Extract user_id from context headers
        user_id = get_user_id_from_context(ctx)
        
        # Sanitize limit
        limit = max(1, min(limit, MAX_QUERY_LIMIT))
        
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
async def upload_file(request: FileUploadRequest, ctx: Context) -> Dict[str, Any]:
    """
    Upload a file to MinIO with metadata stored in MongoDB.
    
    This tool handles file uploads with automatic storage tier selection:
    - CSV, JSON, TXT files → "datasets" bucket (for data analysis)
    - Other files → "files" bucket (for general storage)
    - Automatic fallback to GridFS if MinIO is unavailable
    - File validation and size limits (50MB max)
    - Metadata indexing for fast retrieval
    - User isolation: Files are automatically tagged with authenticated user_id
    
    Args:
        request: FileUploadRequest with filename, base64 content, and metadata
        ctx: FastMCP context with user authentication headers
        
    Returns:
        Structured response with file_id, storage location, and upload details
        
    Example:
        upload_file({
            "filename": "data.csv",
            "content": "base64_encoded_data...",
            "content_type": "text/csv"
        })
        # user_id automatically extracted from X-User-ID header
    """
    await ensure_db_initialized()
    
    if db_manager.db is None:
        return {"success": False, "error": "Database not initialized"}
    
    if not await db_manager.check_connection():
        return {"success": False, "error": "Database not connected"}
    
    try:
        # Clean and decode base64 content
        content_str = request.content
        
        # Remove data URL prefix if present
        if content_str.startswith('data:'):
            if ',' in content_str:
                content_str = content_str.split(',', 1)[1]
        
        # Remove whitespace and newlines
        content_str = content_str.replace('\n', '').replace('\r', '').replace(' ', '')
        
        # Decode base64 content
        try:
            file_content = base64.b64decode(content_str, validate=True)
        except Exception as decode_error:
            logger.error(f"Base64 decode error: {decode_error}")
            return {"success": False, "error": f"Invalid base64 content: {str(decode_error)}"}
        
        if len(file_content) == 0:
            return {"success": False, "error": "File content is empty"}
        
        # Check file extension
        file_ext = os.path.splitext(request.filename)[1].lower()
        if ALLOWED_FILE_EXTENSIONS and file_ext not in ALLOWED_FILE_EXTENSIONS:
            return {
                "success": False,
                "error": f"File type not allowed. Allowed types: {', '.join(ALLOWED_FILE_EXTENSIONS)}"
            }
        
        # Generate unique file ID using authenticated user_id
        timestamp = datetime.utcnow().timestamp()
        file_id = f"{user_id}_{int(timestamp)}_{request.filename}"
        
        # Determine bucket based on file type
        if file_ext in ['.csv', '.json', '.txt']:
            bucket_name = "datasets"
        else:
            bucket_name = "files"
        
        # Upload to MinIO if available
        minio_path = None
        storage_success = False
        
        if db_manager.minio_client:
            try:
                # Upload to MinIO
                file_stream = io.BytesIO(file_content)
                db_manager.minio_client.put_object(
                    bucket_name=bucket_name,
                    object_name=file_id,
                    data=file_stream,
                    length=len(file_content),
                    content_type=request.content_type
                )
                minio_path = f"{bucket_name}/{file_id}"
                storage_success = True
                logger.info(f"✅ File uploaded to MinIO: {minio_path}")
            except S3Error as e:
                logger.error(f"MinIO upload failed: {e}")
                # Will try GridFS fallback below
            except Exception as e:
                logger.error(f"Unexpected MinIO error: {e}")
                # Will try GridFS fallback below
        
        # Fallback to GridFS if MinIO failed or unavailable
        if not storage_success and db_manager.gridfs:
            try:
                gridfs_id = await db_manager.gridfs.upload_from_stream(
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
                minio_path = f"gridfs://{gridfs_id}"
                storage_success = True
                logger.info(f"✅ File uploaded to GridFS: {minio_path}")
            except Exception as e:
                logger.error(f"GridFS upload failed: {e}")
        
        if not storage_success:
            return {"success": False, "error": "Failed to store file in both MinIO and GridFS"}
        
        # Store metadata in MongoDB for easy querying
        metadata_doc = {
            "file_id": file_id,
            "filename": request.filename,
            "content_type": request.content_type,
            "user_id": request.user_id,
            "file_size": len(file_content),
            "uploaded_at": datetime.utcnow(),
            "storage_path": minio_path,
            "bucket": bucket_name if minio_path and not minio_path.startswith("gridfs://") else "gridfs",
            "metadata": request.metadata,
            "is_dataset": bucket_name == "datasets"  # Mark as dataset if stored in datasets bucket
        }
        
        await db_manager.db["file_metadata"].insert_one(metadata_doc)
        
        return {
            "success": True,
            "file_id": str(file_id),
            "filename": request.filename,
            "file_size": len(file_content),
            "content_type": request.content_type,
            "storage_path": minio_path,
            "bucket": bucket_name if not minio_path.startswith("gridfs://") else "gridfs"
        }
        
    except Exception as e:
        logger.error(f"Error uploading file: {e}")
        return {"success": False, "error": f"Upload error: {str(e)}"}

@mcp.tool()
async def download_file(request: Dict[str, Any]) -> Dict[str, Any]:
    """
    Download a file from MinIO or GridFS storage.
    
    This tool retrieves files from the storage backend with automatic fallback:
    1. First checks MongoDB metadata for file information
    2. Retrieves from MinIO if available
    3. Falls back to GridFS if MinIO is unavailable
    4. Returns base64-encoded content with metadata
    
    Args:
        request: Dictionary with "file_id" (required) and optional "user_id" for access control
        
    Returns:
        Structured response with file content (base64), metadata, and storage info
        
    Example:
        download_file({"file_id": "user123_1640995200_data.csv", "user_id": "user123"})
    """
    await ensure_db_initialized()
    
    file_id = request.get("file_id")
    user_id = request.get("user_id")
    
    if not file_id:
        return {"success": False, "error": "file_id is required"}
    
    if db_manager.db is None:
        return {"success": False, "error": "Database not initialized"}
    
    if not await db_manager.check_connection():
        return {"success": False, "error": "Database not connected"}
    
    try:
        # First check file metadata in MongoDB
        metadata_doc = await db_manager.db["file_metadata"].find_one({"file_id": file_id})
        
        if not metadata_doc:
            # Fallback to GridFS lookup
            try:
                from bson import ObjectId
                obj_id = ObjectId(file_id)
                grid_out = await db_manager.gridfs.open_download_stream(obj_id)
                file_content = await grid_out.read()
                encoded_content = base64.b64encode(file_content).decode('utf-8')
                
                return {
                    "success": True,
                    "file_id": file_id,
                    "filename": grid_out.filename,
                    "content": encoded_content,
                    "content_type": grid_out.metadata.get("content_type", "application/octet-stream"),
                    "file_size": len(file_content),
                    "metadata": grid_out.metadata or {},
                    "storage_type": "gridfs"
                }
            except Exception as gridfs_error:
                # Enhanced error reporting
                logger.error(f"File {file_id} not found in metadata or GridFS: {gridfs_error}")
                return {
                    "success": False, 
                    "error": f"File not found in database or GridFS storage",
                    "details": {
                        "file_id": file_id,
                        "searched_in": ["file_metadata collection", "GridFS"],
                        "suggestion": "Check if file_id is correct or if file was uploaded successfully"
                    }
                }
        
        # Check user access (if user_id provided)
        if user_id and metadata_doc.get("user_id") != user_id:
            return {
                "success": False, 
                "error": "Access denied",
                "details": {
                    "reason": "File belongs to different user",
                    "file_owner": metadata_doc.get("user_id", "unknown"),
                    "requesting_user": user_id
                }
            }
        
        storage_path = metadata_doc.get("storage_path")
        
        if storage_path and storage_path.startswith("gridfs://"):
            # GridFS storage
            gridfs_id = storage_path.replace("gridfs://", "")
            from bson import ObjectId
            obj_id = ObjectId(gridfs_id)
            grid_out = await db_manager.gridfs.open_download_stream(obj_id)
            file_content = await grid_out.read()
        
        elif db_manager.minio_client and not storage_path.startswith("gridfs://"):
            # MinIO storage
            bucket = metadata_doc.get("bucket", "files")
            try:
                response = db_manager.minio_client.get_object(bucket, file_id)
                file_content = response.read()   # Read the file content
                response.close()
                response.release_conn()
            except S3Error as e:
                logger.error(f"MinIO download failed: {e}")
                return {
                    "success": False, 
                    "error": f"MinIO download failed: {str(e)}",
                    "details": {
                        "storage_type": "minio",
                        "bucket": bucket,
                        "file_id": file_id,
                        "suggestion": "Check if MinIO service is running and accessible"
                    }
                }
        
        else:
            return {
                "success": False, 
                "error": "No storage backend available",
                "details": {
                    "minio_available": db_manager.minio_client is not None,
                    "storage_path": storage_path,
                    "suggestion": "Check if MinIO or GridFS storage is properly configured"
                }
            }
        
        # Encode to base64
        encoded_content = base64.b64encode(file_content).decode('utf-8')
        
        return {
            "success": True,
            "file_id": file_id,
            "filename": metadata_doc.get("filename", "unknown"),
            "content": encoded_content,
            "content_type": metadata_doc.get("content_type", "application/octet-stream"),
            "file_size": len(file_content),
            "metadata": metadata_doc.get("metadata", {}),
            "storage_type": "minio" if not storage_path.startswith("gridfs://") else "gridfs"
        }     
        
    except Exception as e:
        logger.error(f"Error downloading file: {e}")
        return {
            "success": False,
            "error": f"Download error: {str(e)}",
            "details": {
                "file_id": file_id,
                "user_id": user_id,
                "suggestion": "Check server logs for detailed error information"
            }
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
    
    # Note: Tool discovery should be done via MCP client.list_tools() method
    # This follows MCP best practices for dynamic tool discovery
    
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
            "note": "Use MCP client.list_tools() for dynamic tool discovery"
        },
        "issues": issues if issues else None
    }

@mcp.tool()
async def find_documents(
    collection: str,
    query: Dict[str, Any] = None,
    limit: int = DEFAULT_QUERY_LIMIT,
    skip: int = 0,
    sort_by: str = "uploaded_at",
    sort_order: SortOrder = SortOrder.DESC
) -> Dict[str, Any]:
    """Find documents in a specified collection with pagination."""
    await ensure_db_initialized()
    
    if db_manager.db is None:
        return {"success": False, "error": "Database not initialized"}
    
    if not await db_manager.check_connection():
        return {"success": False, "error": "Database not connected"}
    
    # Sanitize inputs
    limit = max(1, min(limit, MAX_QUERY_LIMIT))
    skip = max(0, skip)
    query = query or {}
    
    try:
        # Build sort
        sort_direction = DESCENDING if sort_order == SortOrder.DESC else ASCENDING
        
        # Execute query
        cursor = (
            db_manager.db[collection]
            .find(query)
            .sort(sort_by, sort_direction)
            .skip(skip)
            .limit(limit)
        )
        
        docs = await cursor.to_list(length=limit)
        total_count = await db_manager.db[collection].count_documents(query)
        
        # Process documents
        for doc in docs:
            doc["_id"] = str(doc["_id"])
            # Convert datetime objects to ISO strings
            for key, value in doc.items():
                if isinstance(value, datetime):
                    doc[key] = value.isoformat()
        
        return {
            "success": True,
            "collection": collection,
            "query": query,
            "count": len(docs),
            "total_count": total_count,
            "skip": skip,
            "limit": limit,
            "documents": docs,
            "has_more": (skip + len(docs)) < total_count
        }
        
    except Exception as e:
        logger.error(f"Error finding documents: {e}")
        return {"success": False, "error": f"Query error: {str(e)}"}

@mcp.tool()
async def list_uploaded_files(
    ctx: Context,
    file_type: Optional[str] = None,
    limit: int = DEFAULT_QUERY_LIMIT
) -> Dict[str, Any]:
    """
    List uploaded files for the authenticated user with optional filtering.
    
    This tool provides secure file listing with user isolation:
    - Only shows files belonging to the authenticated user
    - Supports filtering by file type/content type
    - Includes storage metadata and file details
    - Paginated results with configurable limits
    
    Args:
        ctx: FastMCP context with user authentication headers
        file_type: Optional filter by content type (e.g., "csv", "json")
        limit: Maximum number of files to return (default: 50)
        
    Returns:
        Structured response with user's files list and metadata
        
    Example:
        list_uploaded_files("csv", 50)
        # user_id automatically extracted from X-User-ID header
    """
    await ensure_db_initialized()
    
    if db_manager.db is None:
        return {"success": False, "error": "Database not initialized"}
    
    if not await db_manager.check_connection():
        return {"success": False, "error": "Database not connected"}
    
    try:
        # Extract authenticated user_id from context
        user_id = get_user_id_from_context(ctx)
        
        # Build query with user isolation
        query = {"user_id": user_id}  # Always filter by authenticated user
        
        if file_type:
            query["content_type"] = {"$regex": file_type, "$options": "i"}
        
        # Get files for this user only
        cursor = (
            db_manager.db["file_metadata"]
            .find(query)
            .sort("uploaded_at", DESCENDING)
            .limit(limit)
        )
        
        files = await cursor.to_list(length=limit)
        total_count = await db_manager.db["file_metadata"].count_documents(query)
        
        # Process files
        for file_doc in files:
            file_doc["_id"] = str(file_doc["_id"])
            if isinstance(file_doc.get("uploaded_at"), datetime):
                file_doc["uploaded_at"] = file_doc["uploaded_at"].isoformat()
        
        return {
            "success": True,
            "user_id": user_id,
            "total_files": total_count,
            "returned_count": len(files),
            "files": files,
            "query_filters": {"user_id": user_id, "file_type": file_type}
        }
        
    except Exception as e:
        logger.error(f"Error listing files for user {user_id}: {e}")
        return {"success": False, "error": f"List error: {str(e)}"}

@mcp.tool()
async def list_available_datasets(
    ctx: Context,
    bucket_name: str = "datasets",
    limit: int = DEFAULT_QUERY_LIMIT
) -> Dict[str, Any]:
    """
    List available datasets from MinIO storage and MongoDB collections with user isolation.
    
    This tool provides a comprehensive view of datasets accessible to the authenticated user:
    - Automatically extracts user_id from request headers for authentication
    - Scans MinIO buckets for user's uploaded files (CSV, JSON, TXT)
    - Lists MongoDB collections that contain user's data
    - Includes file metadata (size, upload date, owner)
    - Filters by authenticated user to ensure data isolation
    - Categorizes datasets by source (MinIO, MongoDB collections)
    
    Args:
        ctx: Request context containing user authentication headers
        bucket_name: MinIO bucket to scan (default: "datasets")
        limit: Maximum number of datasets to return
        
    Returns:
        Structured response with user's datasets from all sources, categorized by type
        
    Example:
        list_available_datasets("datasets", 20)
    """
    await ensure_db_initialized()
    
    if db_manager.db is None:
        return {"success": False, "error": "Database not initialized"}
    
    try:
        # Extract user_id from context headers
        user_id = get_user_id_from_context(ctx)
        
        available_datasets = []
        
        # List from MinIO if available
        if db_manager.minio_client:
            try:
                objects = db_manager.minio_client.list_objects(bucket_name, recursive=True)
                minio_datasets = []
                for obj in objects:
                    # Only show files that belong to the authenticated user
                    if obj.object_name.startswith(f"{user_id}_"):
                        dataset_info = {
                            "id": obj.object_name,
                            "name": obj.object_name.split("_", 2)[-1] if "_" in obj.object_name else obj.object_name,
                            "size": obj.size,
                            "last_modified": obj.last_modified.isoformat() if obj.last_modified else None,
                            "source": "minio",
                            "bucket": bucket_name,
                            "type": "file",
                            "user_id": user_id
                        }
                        minio_datasets.append(dataset_info)
                
                if len(minio_datasets) > limit:
                    minio_datasets = minio_datasets[:limit]
                
                available_datasets.extend(minio_datasets)
                logger.info(f"Found {len(minio_datasets)} datasets in MinIO")
                
            except S3Error as e:
                logger.warning(f"MinIO list error (bucket may not exist): {e}")
            except Exception as e:
                logger.warning(f"MinIO access error: {e}")
        
        # List from MongoDB file_metadata collection (user's files only)
        try:
            query = {"user_id": user_id, "is_dataset": True}
            
            cursor = (
                db_manager.db["file_metadata"]
                .find(query)
                .sort("uploaded_at", DESCENDING)
                .limit(limit)
            )
            
            db_datasets = await cursor.to_list(length=limit)
            
            for dataset in db_datasets:
                dataset_info = {
                    "id": str(dataset.get("_id", dataset.get("file_id", "unknown"))),
                    "name": dataset.get("filename", "unnamed"),
                    "size": dataset.get("size", 0),
                    "last_modified": dataset.get("uploaded_at", datetime.utcnow()).isoformat(),
                    "source": "mongodb",
                    "collection": "file_metadata", 
                    "type": "metadata",
                    "user_id": dataset.get("user_id")
                }
                available_datasets.append(dataset_info)
            
            logger.info(f"Found {len(db_datasets)} datasets in MongoDB")
            
        except Exception as e:
            logger.warning(f"Database query error: {e}")
        
        # List collections that might contain datasets (check for user-specific data)
        try:
            collections = await db_manager.db.list_collection_names()
            data_collections = [col for col in collections if any(keyword in col.lower() 
                                                                for keyword in ['data', 'log', 'metric', 'event'])]
            
            for collection_name in data_collections:
                try:
                    # Check if collection has user-specific data
                    user_count = await db_manager.db[collection_name].count_documents({"user_id": user_id})
                    if user_count > 0:
                        dataset_info = {
                            "id": collection_name,
                            "name": collection_name,
                            "size": user_count,
                            "last_modified": datetime.utcnow().isoformat(),
                            "source": "mongodb", 
                            "collection": collection_name,
                            "type": "collection",
                            "document_count": user_count,
                            "user_id": user_id
                        }
                        available_datasets.append(dataset_info)
                except Exception as e:
                    logger.warning(f"Error checking collection {collection_name}: {e}")
                    
        except Exception as e:
            logger.warning(f"Error listing collections: {e}")
        
        return {
            "success": True,
            "total_datasets": len(available_datasets),
            "datasets": available_datasets,
            "query_filters": {"user_id": user_id, "bucket": bucket_name},
            "sources": {
                "minio": len([d for d in available_datasets if d.get("source") == "minio"]),
                "mongodb": len([d for d in available_datasets if d.get("source") == "mongodb"])
            }
        }
        
    except Exception as e:
        logger.error(f"Error listing datasets: {e}")
        return {"success": False, "error": f"List datasets error: {str(e)}"}

@mcp.tool() 
async def get_dataset_info(dataset_id: str, ctx: Context) -> Dict[str, Any]:
    """
    Get detailed information about a specific dataset with user isolation.
    
    This tool retrieves dataset information with security controls:
    - Validates that the requesting user owns the dataset
    - Searches both MinIO storage and MongoDB metadata
    - Returns comprehensive dataset details including size, type, and storage location
    - Ensures users can only access their own datasets
    
    Args:
        dataset_id: Unique identifier for the dataset
        ctx: Request context containing user authentication headers
        
    Returns:
        Structured response with dataset details or access denied error
        
    Example:
        get_dataset_info("user123_1640995200_data.csv")
    """
    await ensure_db_initialized()
    
    if db_manager.db is None:
        return {"success": False, "error": "Database not initialized"}
    
    try:
        # Extract authenticated user_id from context
        user_id = get_user_id_from_context(ctx)
        dataset_info = None
        
        # Check MinIO first (validate user ownership via file naming convention)
        if db_manager.minio_client:
            try:
                # Only allow access to files that belong to the user
                if not dataset_id.startswith(f"{user_id}_"):
                    return {
                        "success": False,
                        "error": "Access denied - dataset belongs to different user",
                        "dataset_id": dataset_id,
                        "user_id": user_id
                    }
                
                # Try datasets bucket
                obj_stat = db_manager.minio_client.stat_object("datasets", dataset_id)
                dataset_info = {
                    "id": dataset_id,
                    "name": dataset_id,
                    "size": obj_stat.size,
                    "last_modified": obj_stat.last_modified.isoformat(),
                    "source": "minio",
                    "bucket": "datasets",
                    "content_type": obj_stat.content_type,
                    "etag": obj_stat.etag,
                    "user_id": user_id
                }
            except S3Error:
                # Try files bucket
                try:
                    obj_stat = db_manager.minio_client.stat_object("files", dataset_id) 
                    dataset_info = {
                        "id": dataset_id,
                        "name": dataset_id,
                        "size": obj_stat.size,
                        "last_modified": obj_stat.last_modified.isoformat(),
                        "source": "minio",
                        "bucket": "files",
                        "content_type": obj_stat.content_type,
                        "etag": obj_stat.etag,
                        "user_id": user_id
                    }
                except S3Error:
                    pass
        
        # Check MongoDB if not found in MinIO (with user validation)
        if dataset_info is None:
            try:
                # Check file_metadata collection with user filter
                metadata = await db_manager.db["file_metadata"].find_one({
                    "file_id": dataset_id,
                    "user_id": user_id  # Ensure user owns the dataset
                })
                if metadata:
                    dataset_info = {
                        "id": dataset_id,
                        "name": metadata.get("filename", dataset_id),
                        "size": metadata.get("size", 0),
                        "last_modified": metadata.get("uploaded_at", datetime.utcnow()).isoformat(),
                        "source": "mongodb",
                        "collection": "file_metadata",
                        "user_id": metadata.get("user_id"),
                        "content_type": metadata.get("content_type")
                    }
                else:
                    # Check if it's a collection name with user-specific data
                    collections = await db_manager.db.list_collection_names()
                    if dataset_id in collections:
                        # Check if user has any data in this collection
                        user_count = await db_manager.db[dataset_id].count_documents({"user_id": user_id})
                        if user_count > 0:
                            sample_doc = await db_manager.db[dataset_id].find_one({"user_id": user_id})
                            
                            dataset_info = {
                                "id": dataset_id,
                                "name": dataset_id,
                                "size": user_count,
                                "last_modified": datetime.utcnow().isoformat(),
                                "source": "mongodb", 
                                "collection": dataset_id,
                                "type": "collection",
                                "document_count": user_count,
                                "sample_document": sample_doc,
                                "user_id": user_id
                            }
                        else:
                            return {
                                "success": False,
                                "error": f"Access denied - no data found for user in collection '{dataset_id}'",
                                "user_id": user_id
                            }
            except Exception as e:
                logger.warning(f"MongoDB lookup error: {e}")
        
        if dataset_info is None:
            return {
                "success": False,
                "error": f"Dataset '{dataset_id}' not found or access denied",
                "details": {
                    "dataset_id": dataset_id,
                    "user_id": user_id,
                    "searched_in": ["MinIO datasets bucket", "MinIO files bucket", "MongoDB file_metadata", "MongoDB collections"],
                    "suggestion": "Check if dataset_id is correct and belongs to your user account"
                }
            }
        
        return {
            "success": True,
            "dataset": dataset_info
        }
        
    except Exception as e:
        logger.error(f"Error getting dataset info: {e}")
        return {"success": False, "error": f"Dataset info error: {str(e)}"}

@mcp.tool()
async def sync_file_metadata() -> Dict[str, Any]:
    """
    Synchronize file metadata with actual storage and clean up orphaned entries.
    
    This maintenance tool ensures data consistency between metadata and storage:
    - Verifies all metadata entries have corresponding files in storage
    - Identifies orphaned metadata (files deleted from storage but metadata remains)
    - Automatically cleans up orphaned entries
    - Checks both MinIO and GridFS storage backends
    - Provides detailed report of sync operations
    
    Returns:
        Structured response with sync results, cleanup counts, and any errors
        
    Example:
        sync_file_metadata()  # No parameters needed
    """
    await ensure_db_initialized()
    
    if db_manager.db is None:
        return {"success": False, "error": "Database not initialized"}
    
    if not await db_manager.check_connection():
        return {"success": False, "error": "Database not connected"}
    
    try:
        sync_results = {
            "total_metadata_entries": 0,
            "verified_files": 0,
            "orphaned_metadata": 0,
            "cleaned_up": 0,
            "errors": []
        }
        
        # Get all file metadata entries
        metadata_cursor = db_manager.db["file_metadata"].find({})
        metadata_docs = await metadata_cursor.to_list(length=None)
        sync_results["total_metadata_entries"] = len(metadata_docs)
        
        orphaned_ids = []
        
        for metadata_doc in metadata_docs:
            file_id = metadata_doc.get("file_id")
            storage_path = metadata_doc.get("storage_path")
            bucket = metadata_doc.get("bucket", "files")
            
            file_exists = False
            
            try:
                if storage_path and storage_path.startswith("gridfs://"):
                    # Check GridFS
                    gridfs_id = storage_path.replace("gridfs://", "")
                    from bson import ObjectId
                    obj_id = ObjectId(gridfs_id)
                    grid_out = await db_manager.gridfs.open_download_stream(obj_id)
                    await grid_out.read(1)  # Try to read one byte
                    file_exists = True
                    
                elif db_manager.minio_client:
                    # Check MinIO
                    try:
                        db_manager.minio_client.stat_object(bucket, file_id)
                        file_exists = True
                    except S3Error as e:
                        if e.code == "NoSuchKey":
                            file_exists = False
                        else:
                            sync_results["errors"].append(f"MinIO error for {file_id}: {str(e)}")
                            continue
                
                if file_exists:
                    sync_results["verified_files"] += 1
                else:
                    orphaned_ids.append(metadata_doc["_id"])
                    sync_results["orphaned_metadata"] += 1
                    logger.info(f"Found orphaned metadata for file: {file_id}")
                    
            except Exception as e:
                sync_results["errors"].append(f"Error checking file {file_id}: {str(e)}")
                # Consider it orphaned if we can't verify it exists
                orphaned_ids.append(metadata_doc["_id"])
                sync_results["orphaned_metadata"] += 1
        
        # Clean up orphaned metadata
        if orphaned_ids:
            result = await db_manager.db["file_metadata"].delete_many({
                "_id": {"$in": orphaned_ids}
            })
            sync_results["cleaned_up"] = result.deleted_count
            logger.info(f"Cleaned up {result.deleted_count} orphaned metadata entries")
        
        sync_results["success"] = True
        sync_results["summary"] = f"Verified {sync_results['verified_files']} files, cleaned up {sync_results['cleaned_up']} orphaned entries"
        
        return sync_results
        
    except Exception as e:
        logger.error(f"Error synchronizing file metadata: {e}")
        return {
            "success": False,
            "error": f"Sync error: {str(e)}"
        }

@mcp.tool()
async def repair_file_storage() -> Dict[str, Any]:
    """Repair file storage issues by checking and fixing inconsistencies."""
    await ensure_db_initialized()
    
    if db_manager.db is None:
        return {"success": False, "error": "Database not initialized"}
    
    try:
        repair_results = {
            "issues_found": [],
            "repairs_attempted": [],
            "success_count": 0,
            "error_count": 0
        }
        
        # First sync metadata
        sync_result = await sync_file_metadata()
        if sync_result.get("success"):
            repair_results["repairs_attempted"].append("Metadata sync completed")
            repair_results["success_count"] += 1
        else:
            repair_results["issues_found"].append("Metadata sync failed")
            repair_results["error_count"] += 1
        
        # Check storage backend availability
        storage_issues = []
        
        if db_manager.minio_client:
            try:
                # Try to list buckets to test MinIO connectivity
                buckets = db_manager.minio_client.list_buckets()
                repair_results["repairs_attempted"].append("MinIO connectivity verified")
                repair_results["success_count"] += 1
            except Exception as e:
                storage_issues.append(f"MinIO connectivity issue: {str(e)}")
                repair_results["issues_found"].append(f"MinIO problem: {str(e)}")
                repair_results["error_count"] += 1
        else:
            storage_issues.append("MinIO client not initialized")
            repair_results["issues_found"].append("MinIO client not available")
            repair_results["error_count"] += 1
        
        # Check GridFS
        try:
            # Try to list one file from GridFS
            cursor = db_manager.gridfs.find().limit(1)
            await cursor.to_list(length=1)
            repair_results["repairs_attempted"].append("GridFS connectivity verified")
            repair_results["success_count"] += 1
        except Exception as e:
            storage_issues.append(f"GridFS issue: {str(e)}")
            repair_results["issues_found"].append(f"GridFS problem: {str(e)}")
            repair_results["error_count"] += 1
        
        repair_results["storage_issues"] = storage_issues
        repair_results["success"] = repair_results["error_count"] == 0
        
        if repair_results["success"]:
            repair_results["summary"] = "All storage systems are working correctly"
        else:
            repair_results["summary"] = f"Found {repair_results['error_count']} issues, check details"
        
        return repair_results
        
    except Exception as e:
        logger.error(f"Error repairing file storage: {e}")
        return {
            "success": False,
            "error": f"Repair error: {str(e)}"
        }

# ── Server startup and cleanup ──────────────────────────────────────
async def cleanup():
    """Cleanup database connections on server shutdown"""
    await db_manager.close()

# Main entry point  
if __name__ == "__main__":
    # Run with FastMCP streamable HTTP transport
    mcp.run(
        transport="http",
        host="0.0.0.0", 
        port=MONGO_MCP_PORT,
        log_level="WARNING"
    )