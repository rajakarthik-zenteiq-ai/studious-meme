"""
MongoDB FastMCP server - S3-focused with essential tools only
Compatible with FastMCP v2.x and MCP Inspector v0.15.0
"""
import os
import sys
import asyncio
import hashlib
import re
from datetime import datetime, timedelta
import logging
from typing import Any, Dict, List, Optional, Union, Literal
import json
import base64
from enum import Enum
import io

from motor.motor_asyncio import AsyncIOMotorClient
from motor.motor_asyncio import AsyncIOMotorGridFSBucket
from pydantic import BaseModel, Field, field_validator, model_validator
from pymongo import ASCENDING, DESCENDING, TEXT
from pymongo.errors import DuplicateKeyError, OperationFailure
from botocore.exceptions import ClientError

# Correct FastMCP v2.x import
from fastmcp import FastMCP, Context

# Add project root to path for imports
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", ".."))
from object_storage_s3 import LinodeObjectStorage

# Import authenticator with fallback
try:
    # Try to import from parent utils directory
    sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))
    from utils.oauth_auth import authenticator
    print("Using main utils.oauth_auth authenticator")
except ImportError:
    try:
        # Fallback to local oauth_auth module for Docker
        from oauth_auth import authenticator
        print("Using local oauth_auth authenticator")
    except ImportError:
        # Final fallback - create minimal authenticator
        class MinimalAuthenticator:
            def __init__(self):
                self.oauth_enabled = os.getenv("OAUTH_ENABLED", "false").lower() == "true"
            
            def validate_request(self, request=None):
                return {
                    "user_id": "anonymous",
                    "authenticated": False,
                    "oauth_enabled": self.oauth_enabled
                }
            
            def extract_user_from_context(self, context=None):
                return self.validate_request()
        
        authenticator = MinimalAuthenticator()
        print("Using minimal fallback authenticator")

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
# Also import SciREX MCP URL for cross-server analysis calls
from config.settings import SCIREX_MCP_URL, SCIREX_DATA_DIR, SCIREX_MODEL_DIR

# ── Auth utilities ──────────────────────────────────────────────────
def get_user_id_from_context(ctx: Context, tool_args: dict = None) -> str:
    """
    Extract user_id from MCP context or tool arguments.
    This will be used for all database operations to ensure data isolation.
    """
    # First priority: check tool arguments for user_id (top-level or nested)
    if tool_args and isinstance(tool_args, dict):
        # Direct top-level user_id
        user_id = tool_args.get('user_id')
        if user_id and user_id != "anonymous":
            logger.debug(f"Found user_id in tool args (top-level): {user_id}")
            return user_id
        # Nested under 'request'
        req = tool_args.get('request') if isinstance(tool_args.get('request'), dict) else None
        if req:
            user_id = req.get('user_id')
            if user_id and user_id != "anonymous":
                logger.debug(f"Found user_id in tool args (request): {user_id}")
                return user_id
            # Within metadata
            meta = req.get('metadata') if isinstance(req.get('metadata'), dict) else None
            if meta:
                user_id = meta.get('user_id')
                if user_id and user_id != "anonymous":
                    logger.debug(f"Found user_id in tool args (request.metadata): {user_id}")
                    return user_id
        # Or in top-level metadata if request model was unpacked
        meta = tool_args.get('metadata') if isinstance(tool_args.get('metadata'), dict) else None
        if meta:
            user_id = meta.get('user_id')
            if user_id and user_id != "anonymous":
                logger.debug(f"Found user_id in tool args (metadata): {user_id}")
                return user_id
    
    # Second priority: check headers
    if hasattr(ctx, 'request_context') and ctx.request_context:
        headers = getattr(ctx.request_context, 'headers', {})
        if isinstance(headers, dict):
            user_id = headers.get('x-user-id') or headers.get('X-User-ID')
            if user_id and user_id != "anonymous":
                logger.debug(f"Found user_id in headers: {user_id}")
                return user_id
    
    # Fallback to a default for development (remove in production)
    logger.warning("No user_id found in headers or tool args, using 'anonymous'")
    return "anonymous"

def validate_user_access(ctx: Context, resource_user_id: str, tool_args: dict = None) -> bool:
    """
    Validate that the requesting user has access to the resource.
    Returns True if access is allowed, False otherwise.
    """
    current_user_id = get_user_id_from_context(ctx, tool_args)
    
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
SCIREX_DATA_DIR = os.getenv("SCIREX_DATA_DIR", "/data/user_datasets")
SCIREX_MODEL_DIR = os.getenv("SCIREX_MODEL_DIR", "/data/user_models")

# ── Enums ────────────────────────────────────────────────────────────
class SortOrder(str, Enum):
    ASC = "asc"
    DESC = "desc"

# ── Database Manager with Linode Object Storage ─────────────────────────────────────────────────
class DatabaseManager:
    def __init__(self):
        self.client: Optional[AsyncIOMotorClient] = None
        self.db = None
        self.gridfs: Optional[AsyncIOMotorGridFSBucket] = None
        self.storage: Optional[LinodeObjectStorage] = None
        self._initialized = False

    async def setup_storage(self):
        """Setup Linode Object Storage connection."""
        try:
            self.storage = LinodeObjectStorage()
            logger.info("✅ Linode Object Storage initialized successfully")
        except Exception as e:
            logger.error(f"❌ Failed to initialize Linode Object Storage: {e}")
            self.storage = None

    async def connect(self):
        """Establish database connection."""
        try:
            self.client = AsyncIOMotorClient(
                MONGO_URI,
                maxPoolSize=MONGO_MAX_POOL_SIZE,
                minPoolSize=MONGO_MIN_POOL_SIZE,
                serverSelectionTimeoutMS=5000
            )
            
            # Test connection
            await self.client.admin.command('ping')
            self.db = self.client[MONGO_DB]
            
            # Setup GridFS
            self.gridfs = AsyncIOMotorGridFSBucket(self.db)
            
            logger.info(f"✅ MongoDB connected: {MONGO_DB}")
            return True
            
        except Exception as e:
            logger.error(f"❌ MongoDB connection failed: {e}")
            return False

    async def ensure_indexes(self):
        """Create necessary indexes for optimal performance."""
        if self.db is None:
            return

        try:
            # File metadata indexes
            await self.db["file_metadata"].create_index([("user_id", ASCENDING), ("uploaded_at", DESCENDING)])
            await self.db["file_metadata"].create_index([("file_id", ASCENDING)])
            await self.db["file_metadata"].create_index([("user_id", ASCENDING), ("is_dataset", ASCENDING)])
            await self.db["file_metadata"].create_index([("storage_path", ASCENDING)])
            
            # Chat history indexes
            await self.db["chat_history"].create_index([("user_id", ASCENDING), ("timestamp", DESCENDING)])
            await self.db["chat_history"].create_index([("user_id", ASCENDING), ("deleted", ASCENDING)])
            
            logger.info("✅ Database indexes created")
            
        except Exception as e:
            logger.warning(f"⚠️ Index creation warning: {e}")

    async def initialize(self):
        """Initialize database and storage connections."""
        if self._initialized:
            return True
            
        success = await self.connect()
        if success:
            await self.ensure_indexes()
            await self.setup_storage()
            self._initialized = True
            logger.info("✅ Database manager initialized")
            return True
        return False

    async def close(self):
        """Close database connections."""
        if self.client:
            self.client.close()
            logger.info("MongoDB connection closed")

    async def check_connection(self) -> bool:
        """Check if database connection is alive."""
        if self.client is None or self.db is None:
            return False
        try:
            await self.client.admin.command('ping')
            return True
        except Exception:
            return False

# Create database manager instance
db_manager = DatabaseManager()

# Create MCP instance - initialization will happen when tools are called
mcp = FastMCP(MONGO_MCP_NAME)

# ── Pydantic models with comprehensive validation ────────────────────
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
    auto_analyze: Optional[bool] = Field(
        default=True,
        description=(
            "If true and the file is detected as a dataset, start an asynchronous "
            "analysis job (clustering with auto-k) and store results in MongoDB."
        ),
    )
    
    @field_validator("filename")
    @classmethod
    def validate_filename(cls, v):
        # Check for dangerous characters
        if any(char in v for char in ['..', '/', '\\', ':', '*', '?', '"', '<', '>', '|']):
            raise ValueError("Filename contains invalid characters")
        return v
    
    @field_validator("content")
    @classmethod
    def validate_content(cls, v):
        # Basic validation - ensure it's not empty
        if not v.strip():
            raise ValueError("File content cannot be empty")
        return v

class AsyncAnalysisRequest(BaseModel):
    """Start background analysis on a dataset stored in S3 and tracked in MongoDB."""
    file_id: str = Field(..., description="File identifier returned by upload_file")
    analysis_type: Literal["cluster", "classify"] = Field(
        default="cluster",
        description="Type of analysis to run. 'cluster' uses KMeans with auto-k by default."
    )
    # Common options
    features: Optional[List[str]] = Field(default=None, description="Optional feature columns to use")
    normalize: bool = Field(default=True, description="Standardize numeric features")
    # Clustering options
    algorithm: Literal["kmeans", "dbscan", "agglomerative"] = Field(default="kmeans", description="Clustering algorithm")
    n_clusters: int = Field(default=3, ge=2, description="Cluster count when not using auto_k")
    auto_k: Optional[Literal["silhouette", "elbow"]] = Field(default="silhouette", description="Auto-select k method for KMeans")
    max_k: int = Field(default=10, ge=2, description="Upper bound for auto-k search")
    # Classification options
    target: Optional[str] = Field(default=None, description="Target column for classification")
    model_type: Optional[Literal["logistic_regression", "svm", "random_forest", "mlp", "knn"]] = Field(default="random_forest", description="Classifier to train")
    model_params: Dict[str, Any] = Field(default_factory=dict, description="Optional model hyperparameters")

class AnalysisStatusRequest(BaseModel):
    job_id: str = Field(..., description="Job id returned when starting analysis")

# ── Helper functions ─────────────────────────────────────────────────
async def ensure_db_initialized():
    """Ensure database is initialized before tool execution"""
    if not db_manager._initialized:
        await db_manager.initialize()
    # Additional check to ensure connection is alive
    if not await db_manager.check_connection():
        logger.warning("Database connection lost, reinitializing...")
        await db_manager.initialize()

# Cross-server MCP tool call helper (HTTP transport)
async def _mcp_call_tool(server_url: str, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Call a tool on another MCP server over HTTP transport (initialize -> tools/call)."""
    import httpx
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream, application/json",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        init_req = {
            "jsonrpc": "2.0",
            "id": f"init_{tool_name}",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "mongo_server", "version": "1.0.0"},
            },
        }
        init_res = await client.post(server_url, json=init_req, headers=headers)
        init_res.raise_for_status()
        session_id = init_res.headers.get("mcp-session-id")
        if not session_id:
            raise RuntimeError("No MCP session id returned by server")
        # Notify initialized (best effort)
        try:
            await client.post(server_url, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers={**headers, "mcp-session-id": session_id})
        except Exception:
            pass
        call_req = {
            "jsonrpc": "2.0",
            "id": f"call_{tool_name}",
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        res = await client.post(server_url, json=call_req, headers={**headers, "mcp-session-id": session_id})
        res.raise_for_status()
        # Try SSE-like or JSON
        try:
            data = res.json()
            return data.get("result", data)
        except Exception:
            text = res.text
            # Minimal SSE parsing
            if text.startswith("event:"):
                for line in text.splitlines():
                    if line.startswith("data: "):
                        return json.loads(line[6:])
            raise

async def _analyze_dataset_background(file_id: str, user_id: str, options: Dict[str, Any]) -> None:
    """Background task: download dataset, stage to local, call SciREX clustering/classification, store results."""
    await ensure_db_initialized()
    try:
        meta = await db_manager.db["file_metadata"].find_one({"file_id": file_id})
        if not meta:
            logger.warning(f"Analysis: metadata not found for file_id={file_id}")
            return
        storage_path = meta.get("storage_path")
        if not storage_path or not db_manager.storage:
            logger.warning("Analysis: storage not available")
            return
        # Download bytes from S3
        content = db_manager.storage.download_bytes(storage_path)
        # Stage to local shared volume for downstream tools (optional)
        os.makedirs(SCIREX_DATA_DIR, exist_ok=True)
        local_path = os.path.join(SCIREX_DATA_DIR, os.path.basename(storage_path))
        try:
            with open(local_path, "wb") as fh:
                fh.write(content or b"")
        except Exception as e:
            logger.warning(f"Could not write staged file: {e}")
        # Prepare CSV text (limit very large files to first N lines to avoid payload bloat)
        csv_text: Optional[str] = None
        if meta.get("filename", "").lower().endswith((".csv", ".txt", ".data")):
            # Limit to ~2MB to be safe
            limit_bytes = 2 * 1024 * 1024
            data_bytes = content[:limit_bytes] if content and len(content) > limit_bytes else (content or b"")
            try:
                csv_text = data_bytes.decode("utf-8", errors="ignore")
            except Exception:
                csv_text = None
        # Build arguments for SciREX tools
        analysis_type = options.get("analysis_type", "cluster")
        features = options.get("features")
        normalize = bool(options.get("normalize", True))
        result_doc: Dict[str, Any] = {}
        started = datetime.utcnow()
        if analysis_type == "cluster":
            arguments = {
                "request": {
                    "input": {"csv_text": csv_text, "features": features, "normalize": normalize},
                    "algorithm": options.get("algorithm", "kmeans"),
                    "n_clusters": int(options.get("n_clusters", 3)),
                    "auto_k": options.get("auto_k", "silhouette"),
                    "max_k": int(options.get("max_k", 10)),
                }
            }
            tool_name = "cluster_data"
            result = await _mcp_call_tool(SCIREX_MCP_URL, tool_name, arguments)
            result_doc = {"tool": tool_name, "request": arguments.get("request"), "result": result}
        elif analysis_type == "classify":
            target = options.get("target")
            if not target:
                logger.info("Classification requested but no target provided; skipping.")
                return
            arguments = {
                "request": {
                    "input": {"csv_text": csv_text, "features": features, "target": target, "normalize": normalize},
                    "model_type": options.get("model_type", "random_forest"),
                    "test_size": float(options.get("test_size", 0.2)),
                    "random_state": 42,
                    "model_params": options.get("model_params", {}),
                }
            }
            tool_name = "classify_data"
            result = await _mcp_call_tool(SCIREX_MCP_URL, tool_name, arguments)
            result_doc = {"tool": tool_name, "request": arguments.get("request"), "result": result}
        else:
            logger.warning(f"Unknown analysis_type={analysis_type}")
            return
        # Persist analysis results
        job_id = f"{file_id}:analysis:{int(started.timestamp())}"
        doc = {
            "job_id": job_id,
            "file_id": file_id,
            "user_id": user_id,
            "analysis_type": analysis_type,
            "started_at": started,
            "completed_at": datetime.utcnow(),
            "status": "completed",
            "local_path": local_path,
            "scirex_url": SCIREX_MCP_URL,
            **result_doc,
        }
        await db_manager.db["analysis_results"].insert_one(doc)
        # Update file metadata with last analysis summary
        await db_manager.db["file_metadata"].update_one(
            {"file_id": file_id},
            {"$set": {"last_analysis": {"job_id": job_id, "analysis_type": analysis_type, "completed_at": doc["completed_at"], "summary": result_doc.get("result")}}}
        )
        logger.info(f"Analysis completed and stored for file_id={file_id}")
    except Exception as e:
        logger.error(f"Background analysis error: {e}")
        try:
            await db_manager.db["analysis_results"].insert_one({
                "job_id": f"{file_id}:analysis:error:{int(datetime.utcnow().timestamp())}",
                "file_id": file_id,
                "user_id": user_id,
                "status": "failed",
                "error": str(e),
                "when": datetime.utcnow(),
            })
        except Exception:
            pass

async def _quick_analyze_dataset(file_id: str, user_id: str) -> None:
    """Lightweight async analysis: parse dataset bytes and store quick_analysis summary.
    Does NOT perform clustering. Runs fast (<2s typical) and updates file_metadata.quick_analysis.
    """
    await ensure_db_initialized()
    try:
        meta = await db_manager.db["file_metadata"].find_one({"file_id": file_id})
        if not meta:
            logger.warning(f"Quick analysis: metadata not found for file_id={file_id}")
            return
        if not meta.get("is_dataset"):
            logger.info(f"Quick analysis skipped (not dataset) file_id={file_id}")
            return
        storage_path = meta.get("storage_path")
        if not storage_path or not db_manager.storage:
            logger.warning("Quick analysis: storage not available")
            return
        # Download bytes
        content = db_manager.storage.download_bytes(storage_path)
        if content is None:
            logger.warning(f"Quick analysis: could not download bytes for {storage_path}")
            return
        # Run analysis
        result = analyze_bytes(content, meta.get("filename", "dataset"))
        if not result.get("success"):
            logger.warning(f"Quick analysis failed for {file_id}: {result.get('error')}")
            return
        quick_doc = {
            "summary": result.get("summary", {}),
            "analyzed_at": datetime.utcnow()
        }
        await db_manager.db["file_metadata"].update_one(
            {"file_id": file_id},
            {"$set": {"quick_analysis": quick_doc}}
        )
        logger.info(f"Quick analysis stored for file_id={file_id}")
    except Exception as e:
        logger.error(f"Quick analysis error: {e}")

# ── MCP Tools ────────────────────────────────────────────────────────

@mcp.tool()
async def purge_legacy_metadata() -> Dict[str, Any]:
    """
    Purge legacy S3/GridFS metadata and logs from MongoDB.
    
    This tool cleans up obsolete data to ensure only S3-backed files are available:
    - Removes file_metadata entries with GridFS storage paths
    - Drops the logs collection entirely (no longer used)
    - Removes GridFS files if accessible
    - Provides detailed cleanup report
    
    Returns:
        Detailed report of cleanup operations performed
    """
    await ensure_db_initialized()
    
    if db_manager.db is None:
        return {"success": False, "error": "Database not initialized"}
    
    if not await db_manager.check_connection():
        return {"success": False, "error": "Database not connected"}
    
    try:
        cleanup_results = {
            "success": True,
            "operations": [],
            "errors": []
        }
        
        # Remove legacy GridFS metadata entries
        gridfs_query = {"storage_path": {"$regex": "^gridfs://"}}
        gridfs_count = await db_manager.db["file_metadata"].count_documents(gridfs_query)
        
        if gridfs_count > 0:
            delete_result = await db_manager.db["file_metadata"].delete_many(gridfs_query)
            cleanup_results["operations"].append({
                "operation": "delete_gridfs_metadata",
                "count": delete_result.deleted_count,
                "description": f"Deleted {delete_result.deleted_count} GridFS metadata entries"
            })
        
        # Remove entries with bucket="gridfs"
        bucket_query = {"bucket": "gridfs"}
        bucket_count = await db_manager.db["file_metadata"].count_documents(bucket_query)
        
        if bucket_count > 0:
            delete_result = await db_manager.db["file_metadata"].delete_many(bucket_query)
            cleanup_results["operations"].append({
                "operation": "delete_gridfs_bucket_entries",
                "count": delete_result.deleted_count,
                "description": f"Deleted {delete_result.deleted_count} GridFS bucket entries"
            })
        
        # Drop logs collection entirely
        try:
            collections = await db_manager.db.list_collection_names()
            if "logs" in collections:
                await db_manager.db["logs"].drop()
                cleanup_results["operations"].append({
                    "operation": "drop_logs_collection",
                    "count": 1,
                    "description": "Dropped logs collection (no longer used)"
                })
        except Exception as e:
            cleanup_results["errors"].append(f"Failed to drop logs collection: {str(e)}")
        
        # Clean up GridFS files if accessible
        if db_manager.gridfs:
            try:
                gridfs_files = await db_manager.gridfs.find({}).to_list(length=None)
                if gridfs_files:
                    deleted_gridfs = 0
                    for gridfs_file in gridfs_files:
                        try:
                            await db_manager.gridfs.delete(gridfs_file._id)
                            deleted_gridfs += 1
                        except Exception as e:
                            cleanup_results["errors"].append(f"Failed to delete GridFS file {gridfs_file._id}: {str(e)}")
                    
                    if deleted_gridfs > 0:
                        cleanup_results["operations"].append({
                            "operation": "delete_gridfs_files",
                            "count": deleted_gridfs,
                            "description": f"Deleted {deleted_gridfs} GridFS files"
                        })
            except Exception as e:
                cleanup_results["errors"].append(f"Failed to access GridFS: {str(e)}")
        
        # Summary
        total_operations = len(cleanup_results["operations"])
        total_deleted = sum(op["count"] for op in cleanup_results["operations"])
        
        cleanup_results["summary"] = {
            "total_operations": total_operations,
            "total_items_deleted": total_deleted,
            "status": "completed_with_errors" if cleanup_results["errors"] else "completed_successfully"
        }
        
        logger.info(f"Legacy metadata purge completed: {total_deleted} items deleted in {total_operations} operations")
        
        return cleanup_results
        
    except Exception as e:
        logger.error(f"Error purging legacy metadata: {e}")
        return {
            "success": False,
            "error": f"Purge error: {str(e)}"
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
        # Extract user_id from context or tool arguments
        user_id = get_user_id_from_context(ctx, chat.dict() if hasattr(chat, 'dict') else vars(chat))
        
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
        # Extract user_id from context or function arguments
        tool_args = {"limit": limit, "before_timestamp": before_timestamp}
        user_id = get_user_id_from_context(ctx, tool_args)
        
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
    Upload a file to Linode Object Storage with metadata stored in MongoDB.
    
    This tool handles file uploads with automatic storage tier selection:
    - CSV, JSON, TXT files → "datasets" directory (for data analysis)
    - Model files → "models" directory (for ML models)
    - Other files → "files" directory (for general storage)
    - File validation and size limits (50MB max)
    - Metadata indexing for fast retrieval
    - User isolation: Files are automatically tagged with authenticated user_id
    - Optional: If auto_analyze=true and file is a dataset, a background analysis job is started
      on the SciREX ML server (KMeans clustering with auto-k by default), and results are
      stored in the analysis_results collection and linked from file_metadata.last_analysis
    
    Args:
        request: FileUploadRequest with filename, base64 content, and metadata
        ctx: FastMCP context with user authentication headers
        
    Returns:
        Structured response with file_id, storage location, and upload details
        
    Example:
        upload_file({
            "filename": "data.csv",
            "content": "base64_encoded_data...",
            "content_type": "text/csv",
            "auto_analyze": true
        })
        # user_id automatically extracted from X-User-ID header
    """
    await ensure_db_initialized()
    
    if db_manager.db is None:
        return {"success": False, "error": "Database not initialized"}
    
    if not await db_manager.check_connection():
        return {"success": False, "error": "Database not connected"}
    
    try:
        # Extract user_id from context or tool arguments
        # Pydantic v2 uses model_dump; fall back to __dict__
        try:
            req_dict = request.model_dump()  # type: ignore[attr-defined]
        except Exception:
            try:
                req_dict = request.dict()  # type: ignore[attr-defined]
            except Exception:
                req_dict = vars(request)
        user_id = get_user_id_from_context(ctx, req_dict)
        
        logger.info(f"Processing upload for user: {user_id}")
        
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
        
        # Enhanced bucket determination with dataset detection
        file_ext = os.path.splitext(request.filename)[1].lower()
        filename_lower = request.filename.lower()
        
        # Enhanced dataset detection with profile extraction
        dataset_extensions = ['.csv', '.json', '.txt', '.tsv', '.xlsx', '.xls']
        model_extensions = ['.pkl', '.joblib', '.h5', '.pt', '.pth', '.onnx', '.pb']
        
        # Determine directory based on file type and content
        if file_ext in dataset_extensions:
            directory = "datasets"
            is_dataset = True
        elif file_ext in model_extensions or 'model' in filename_lower or 'weight' in filename_lower:
            directory = "models"
            is_dataset = False
        else:
            directory = "files"  # Default directory for other file types
            is_dataset = False
        
        # Extract dataset profile for CSV files
        dataset_profile = None
        if file_ext == '.csv' and len(file_content) > 0:
            try:
                import pandas as pd
                df = pd.read_csv(io.BytesIO(file_content), nrows=500)  # Sample first 500 rows
                is_dataset = True
                dataset_profile = {
                    "columns": df.columns.tolist(),
                    "row_count_sample": len(df),
                    "dtypes": {col: str(df[col].dtype) for col in df.columns[:20]},  # Limit to first 20 columns
                    "sample_values": {col: df[col].head(3).tolist() for col in df.columns[:5]}  # Sample values for first 5 columns
                }
                logger.info(f"📊 Dataset profile extracted: {len(df.columns)} columns, {len(df)} rows")
            except Exception as e:
                # Do not fail the upload due to pandas missing; just record the reason
                logger.warning(f"Failed to extract dataset profile (non-fatal): {e}")
                dataset_profile = {"warning": str(e)}
        elif file_ext == '.json' and len(file_content) > 0:
            try:
                # Try to parse as JSON
                import json
                data = json.loads(file_content.decode('utf-8'))
                if isinstance(data, list) and len(data) > 0:
                    is_dataset = True
                    dataset_profile = {
                        "type": "json_array",
                        "length": len(data),
                        "sample_keys": list(data[0].keys()) if isinstance(data[0], dict) else []
                    }
            except Exception as e:
                logger.warning(f"Failed to parse JSON: {e}")
        
        # Upload to Linode Object Storage (single bucket with directory prefix)
        storage_path = None
        storage_success = False
        
        if db_manager.storage:
            try:
                # Use S3 key with directory prefix for organization
                s3_key = f"{directory}/{file_id}"
                
                # Upload to "mcp" bucket with directory prefix
                storage_url = db_manager.storage.upload_bytes(
                    key=s3_key,
                    data=file_content,
                    bucket_name=None  # Use default "mcp" bucket
                )
                
                if storage_url:
                    storage_path = s3_key
                    storage_success = True
                    logger.info(f"✅ File uploaded to S3: {storage_path}")
                
            except ClientError as e:
                logger.error(f"Linode Object Storage upload failed: {e}")
                return {"success": False, "error": f"S3 upload failed: {str(e)}"}
            except Exception as e:
                # Include endpoint and bucket for easier debugging of network issues
                endpoint = getattr(db_manager.storage, 'endpoint_url', None) if db_manager.storage else None
                bucket = getattr(db_manager.storage, 'default_bucket', None) if db_manager.storage else None
                logger.error(f"Unexpected storage error: {e} (endpoint={endpoint}, bucket={bucket})")
                return {"success": False, "error": f"Storage error: {str(e)}", "endpoint": endpoint, "bucket": bucket}
        
        if not storage_success:
            return {"success": False, "error": "S3 storage not available or upload failed"}
        
        # Store metadata in MongoDB for easy querying
        metadata_doc = {
            "file_id": file_id,
            "filename": request.filename,
            "content_type": request.content_type,
            "user_id": user_id,
            "file_size": len(file_content),
            "uploaded_at": datetime.utcnow(),
            "storage_path": storage_path,
            "bucket": "mcp",  # Always use the single bucket
            "directory": directory,  # Directory within bucket
            "metadata": request.metadata,
            "is_dataset": is_dataset,  # Enhanced dataset detection
            "dataset_profile": dataset_profile,  # Dataset structure information
            "file_extension": file_ext,
            "auto_detected_type": "dataset" if is_dataset else ("model" if "models" in directory else "document")
        }
        
        await db_manager.db["file_metadata"].insert_one(metadata_doc)

        response_obj = {
            "success": True,
            "file_id": str(file_id),
            "filename": request.filename,
            "file_size": len(file_content),
            "content_type": request.content_type,
            "storage_path": storage_path,
            "bucket": "mcp",
            "directory": directory
        }

        # Perform quick analysis synchronously and include results in response
        quick_analysis_result = None
        if request.auto_analyze and is_dataset:
            try:
                await _quick_analyze_dataset(file_id=str(file_id), user_id=user_id)
                # Fetch updated metadata with quick_analysis
                updated_meta = await db_manager.db["file_metadata"].find_one({"file_id": file_id})
                quick_analysis_result = updated_meta.get("quick_analysis") if updated_meta else None
                response_obj["quick_analysis"] = quick_analysis_result
                response_obj["analysis_job"] = {
                    "status": "completed",
                    "analysis_type": "quick",
                    "note": "Quick analysis completed and included in response."
                }
            except Exception as e:
                logger.warning(f"Quick analysis failed: {e}")
                response_obj["analysis_job"] = {
                    "status": "failed",
                    "analysis_type": "quick",
                    "note": f"Quick analysis failed: {str(e)}"
                }
        
        return response_obj
        
    except Exception as e:
        logger.error(f"Error uploading file: {e}")
        return {"success": False, "error": f"Upload error: {str(e)}"}

@mcp.tool()
async def download_file(request: Dict[str, Any]) -> Dict[str, Any]:
    """
    Download a file from Linode Object Storage.
    
    Default behavior:
    - Fetch from S3 and return base64-encoded content and metadata.
    
    Enhanced behavior (preferred for SciREX workflows):
    - If stage_to_local=True, write bytes directly into the shared SciREX volume
      (default paths: SCIREX_DATA_DIR=/data/user_datasets or SCIREX_MODEL_DIR=/data/user_models)
    - Then return only the absolute local path inside the container, not bytes.
    
    Args (request):
    - file_id: str (required)
    - user_id: str (optional, used for access checks)
    - stage_to_local: bool (optional) If true, write to shared volume and return path
    - return_local_path_only: bool (optional) If true, only return the local path (default True when staging)
    - local_base_dir: str (optional) Override base dir; defaults to SCIREX_DATA_DIR or SCIREX_MODEL_DIR
    - kind: str (optional) One of ['dataset','model','file']; auto-detected if omitted
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
        # Check file metadata in MongoDB
        metadata_doc = await db_manager.db["file_metadata"].find_one({"file_id": file_id})
        
        if not metadata_doc:
            return {
                "success": False, 
                "error": f"File not found in database",
                "details": {
                    "file_id": file_id,
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
        
        if not storage_path or not db_manager.storage:
            return {
                "success": False, 
                "error": "S3 storage not available",
                "details": {
                    "storage_path": storage_path,
                    "file_id": file_id
                }
            }
        
        # Download from Linode Object Storage
        try:
            file_content = db_manager.storage.download_bytes(
                object_name=storage_path,
                bucket_name=None  # Use default "mcp" bucket
            )
            if file_content is None:
                return {
                    "success": False, 
                    "error": "File not found in S3 storage",
                    "details": {
                        "storage_path": storage_path,
                        "file_id": file_id
                    }
                }
        except ClientError as e:
            logger.error(f"S3 download failed: {e}")
            return {
                "success": False, 
                "error": f"S3 download failed: {str(e)}",
                "details": {
                    "storage_path": storage_path,
                    "file_id": file_id
                }
            }
        
        # Determine staging options
        stage_to_local = bool(request.get("stage_to_local")) or bool(request.get("stage", False))
        return_local_path_only = request.get("return_local_path_only")
        local_base_dir = request.get("local_base_dir", "/data")
        
        if stage_to_local:
            # Simple user-based filtering for file ownership
            file_owner = metadata_doc.get("user_id", "anonymous")
            
            # Create a simple user subdirectory structure
            user_data_dir = os.path.join(local_base_dir, file_owner)
            os.makedirs(user_data_dir, exist_ok=True)

            # Build local filename preserving original extension
            orig_name = metadata_doc.get("filename", "")
            ext = os.path.splitext(orig_name)[1] or ""
            local_path = os.path.join(user_data_dir, f"{file_id}{ext}")

            # Write bytes to local path
            try:
                with open(local_path, 'wb') as f:
                    f.write(file_content)
            except Exception as e:
                logger.error(f"Failed writing to local path {local_path}: {e}")
                return {
                    "success": False,
                    "error": f"Failed to write local file: {str(e)}"
                }

            # Default to only returning the local path when staging
            if return_local_path_only is None:
                return_local_path_only = True

            if return_local_path_only:
                return {
                    "success": True,
                    "file_id": file_id,
                    "local_path": local_path,
                    "user_id": file_owner
                }
            else:
                encoded_content = base64.b64encode(file_content).decode('utf-8')
                return {
                    "success": True,
                    "file_id": file_id,
                    "filename": metadata_doc.get("filename", "unknown"),
                    "content": encoded_content,
                    "content_type": metadata_doc.get("content_type", "application/octet-stream"),
                    "file_size": len(file_content),
                    "metadata": metadata_doc.get("metadata", {}),
                    "storage_type": "s3",
                    "storage_path": storage_path,
                    "local_path": local_path,
                    "user_id": file_owner
                }
        
        # Default: return base64 bytes
        encoded_content = base64.b64encode(file_content).decode('utf-8')
        return {
            "success": True,
            "file_id": file_id,
            "filename": metadata_doc.get("filename", "unknown"),
            "content": encoded_content,
            "content_type": metadata_doc.get("content_type", "application/octet-stream"),
            "file_size": len(file_content),
            "metadata": metadata_doc.get("metadata", {}),
            "storage_type": "s3",
            "storage_path": storage_path
        }
         
    except Exception as e:
        logger.error(f"Error downloading file: {e}")
        return {
            "success": False,
            "error": f"Download error: {str(e)}",
             "details": {
                 "file_id": file_id,
                 "user_id": user_id
             }
         }

@mcp.tool()
async def inspect_dataset(request: Dict[str, Any]) -> Dict[str, Any]:
    """Inspect a dataset stored in S3, compute schema/profile, and persist it to MongoDB.

    Args (request):
    - file_id: str (required) The dataset file_id
    - user_id: str (optional) For access validation
    - sample_rows: int (optional, default 10) Number of rows to preview

    Returns:
    - success: bool
    - file_id: str
    - dataset_profile: dict with keys: columns, dtypes, row_count_sample, preview, null_counts
    - updated: bool whether metadata was updated
    """
    await ensure_db_initialized()

    file_id = request.get("file_id")
    user_id = request.get("user_id")
    sample_rows = int(request.get("sample_rows", 10))

    if not file_id:
        return {"success": False, "error": "file_id is required"}
    if db_manager.db is None:
        return {"success": False, "error": "Database not initialized"}
    if not await db_manager.check_connection():
        return {"success": False, "error": "Database not connected"}

    try:
        # Fetch metadata and validate ownership
        metadata_doc = await db_manager.db["file_metadata"].find_one({"file_id": file_id})
        if not metadata_doc:
            return {"success": False, "error": "File metadata not found", "file_id": file_id}
        if user_id and metadata_doc.get("user_id") != user_id:
            return {"success": False, "error": "Access denied - dataset does not belong to user"}
        storage_path = metadata_doc.get("storage_path")
        if not storage_path or not db_manager.storage:
            return {"success": False, "error": "S3 storage not available"}

        # Download bytes from S3
        try:
            file_content = db_manager.storage.download_bytes(object_name=storage_path, bucket_name=None)
        except Exception as e:
            return {"success": False, "error": f"S3 download failed: {str(e)}"}

        # Basic CSV profile (fallback if pandas not available)
        profile = {
            "columns": [],
            "dtypes": {},
            "row_count_sample": 0,
            "preview": [],
            "null_counts": {}
        }
        filename = metadata_doc.get("filename", "")
        is_csv = filename.lower().endswith(".csv") or "csv" in metadata_doc.get("content_type", "")
        if is_csv:
            try:
                import pandas as pd
                import io as _io
                df = pd.read_csv(_io.BytesIO(file_content), nrows=max(1000, sample_rows))
                profile["columns"] = df.columns.tolist()
                profile["dtypes"] = {c: str(t) for c, t in df.dtypes.items()}
                profile["row_count_sample"] = int(df.shape[0])
                profile["preview"] = df.head(sample_rows).to_dict(orient="records")
                nulls = df.isna().sum().to_dict()
                profile["null_counts"] = {k: int(v) for k, v in nulls.items()}
            except Exception as e:
                profile["error"] = f"pandas profiling failed: {str(e)}"
        else:
            # Non-CSV: record size only
            profile["note"] = "Non-CSV file; limited inspection performed"
            profile["size_bytes"] = len(file_content)

        # Persist profile back to Mongo
        updated = False
        try:
            await db_manager.db["file_metadata"].update_one(
                {"file_id": file_id},
                {"$set": {"dataset_profile": profile, "is_dataset": is_csv or bool(metadata_doc.get("is_dataset"))}}
            )
            updated = True
        except Exception:
            pass

        return {
            "success": True,
            "file_id": file_id,
            "dataset_profile": profile,
            "updated": updated
        }
    except Exception as e:
        logger.error(f"inspect_dataset error: {e}")
        return {"success": False, "error": str(e)}

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
            for col in ["chat_history", "file_metadata"]:
                collections_status[col] = "exists" if col in collections else "missing"
                if col not in collections:
                    status = "degraded"
                    issues.append(f"Collection '{col}' missing")
        except Exception as e:
            status = "degraded"
            issues.append(f"Failed to list collections: {str(e)}")
    
    # Check S3 storage
    s3_status = "unknown"
    if db_manager.storage is not None:
        try:
            # Try to list objects to test S3 connectivity
            db_manager.storage.list_objects()
            s3_status = "operational"
        except Exception as e:
            s3_status = "error"
            issues.append(f"S3 storage error: {str(e)}")
    else:
        s3_status = "not_configured"
        issues.append("S3 storage not configured")
    
    return {
        "success": status != "unhealthy",
        "status": status,
        "timestamp": datetime.utcnow().isoformat(),
        "database": {
            "connected": db_connected,
            "name": MONGO_DB if db_connected else None,
            "collections": collections_status
        },
        "storage": {
            "s3_status": s3_status,
            "type": "linode_s3"
        },
        "mcp": {
            "name": MONGO_MCP_NAME,
            "note": "Use MCP client.list_tools() for dynamic tool discovery"
        },
        "issues": issues if issues else None
    }

@mcp.tool()
async def start_dataset_analysis(request: AsyncAnalysisRequest, ctx: Context) -> Dict[str, Any]:
    """
    Start background analysis on a dataset stored in S3 and tracked in MongoDB.
    
    This tool initiates asynchronous machine learning analysis (clustering or classification)
    on datasets previously uploaded via upload_file. The analysis runs in the background
    while immediately returning a job_id for status tracking.
    
    Workflow:
    1. Validates file_id exists and belongs to authenticated user
    2. Downloads dataset from S3 storage
    3. Stages file to local shared volume for SciREX processing
    4. Calls appropriate SciREX ML tool (cluster_data or classify_data) via HTTP
    5. Stores complete results in MongoDB analysis_results collection
    6. Updates file metadata with analysis summary
    
    Args:
        request: AsyncAnalysisRequest with file_id, analysis type, and ML parameters
        ctx: FastMCP context with user authentication headers
        
    Returns:
        Structured response with job_id for tracking, analysis options, and status
        
    Example:
        start_dataset_analysis({
            "file_id": "user123_1640995200_data.csv",
            "analysis_type": "cluster",
            "algorithm": "kmeans",
            "auto_k": "silhouette",
            "max_k": 10
        })
    """
    await ensure_db_initialized()
    
    if db_manager.db is None:
        return {"success": False, "error": "Database not initialized"}
    
    if not await db_manager.check_connection():
        return {"success": False, "error": "Database not connected"}
    
    try:
        # Extract user_id from context
        req_dict = request.model_dump() if hasattr(request, 'model_dump') else vars(request)
        user_id = get_user_id_from_context(ctx, req_dict)
        
        file_id = request.file_id
        
        # Validate file exists and belongs to user
        metadata_doc = await db_manager.db["file_metadata"].find_one({"file_id": file_id})
        if not metadata_doc:
            return {"success": False, "error": "File not found", "file_id": file_id}
        
        if not validate_user_access(ctx, metadata_doc.get("user_id", ""), req_dict):
            return {"success": False, "error": "Access denied - file belongs to different user"}
        
        if not metadata_doc.get("is_dataset", False):
            return {"success": False, "error": "File is not marked as a dataset"}
        
        # Generate job_id
        timestamp = int(datetime.utcnow().timestamp())
        job_id = f"{file_id}:analysis:{timestamp}"
        
        # Convert request to options dict for background task
        options = {
            "analysis_type": request.analysis_type,
            "features": request.features,
            "normalize": request.normalize,
            "algorithm": request.algorithm,
            "n_clusters": request.n_clusters,
            "auto_k": request.auto_k,
            "max_k": request.max_k,
            "target": request.target,
            "model_type": request.model_type,
            "model_params": request.model_params
        }
        
        # Schedule background analysis
        asyncio.create_task(_analyze_dataset_background(file_id=file_id, user_id=user_id, options=options))
        
        # Record job initiation
        job_doc = {
            "job_id": job_id,
            "file_id": file_id,
            "user_id": user_id,
            "analysis_type": request.analysis_type,
            "started_at": datetime.utcnow(),
            "status": "running",
            "options": options
        }
        await db_manager.db["analysis_results"].insert_one(job_doc)
        
        return {
            "success": True,
            "job_id": job_id,
            "file_id": file_id,
            "analysis_type": request.analysis_type,
            "status": "running",
            "message": "Background analysis started. Use get_analysis_status to check progress."
        }
        
    except Exception as e:
        logger.error(f"Error starting dataset analysis: {e}")
        return {"success": False, "error": f"Analysis error: {str(e)}"}

@mcp.tool()
async def get_analysis_status(request: AnalysisStatusRequest) -> Dict[str, Any]:
    """
    Get status and results of a background analysis job.
    
    This tool retrieves the current status of a machine learning analysis job
    initiated by start_dataset_analysis. Returns detailed results when complete,
    or progress information if still running.
    
    Job Statuses:
    - "running": Analysis is in progress
    - "completed": Analysis finished successfully with results
    - "failed": Analysis encountered an error
    
    Args:
        request: AnalysisStatusRequest with job_id
        
    Returns:
        Structured response with job status, timing info, and results (if complete)
        
    Example:
        get_analysis_status({"job_id": "user123_1640995200_data.csv:analysis:1640995300"})
    """
    await ensure_db_initialized()
    
    if db_manager.db is None:
        return {"success": False, "error": "Database not initialized"}
    
    if not await db_manager.check_connection():
        return {"success": False, "error": "Database not connected"}
    
    try:
        job_id = request.job_id
        
        # Find job in analysis_results collection
        job_doc = await db_manager.db["analysis_results"].find_one({"job_id": job_id})
        
        if not job_doc:
            return {"success": False, "error": "Job not found", "job_id": job_id}
        
        # Convert ObjectId to string and datetime to ISO format
        if "_id" in job_doc:
            job_doc["_id"] = str(job_doc["_id"])
        for dt_field in ["started_at", "completed_at", "when"]:
            if dt_field in job_doc and isinstance(job_doc[dt_field], datetime):
                job_doc[dt_field] = job_doc[dt_field].isoformat()
        
        return {
            "success": True,
            "job_id": job_id,
            **job_doc
        }
        
    except Exception as e:
        logger.error(f"Error getting analysis status: {e}")
        return {"success": False, "error": f"Status retrieval error: {str(e)}"}

@mcp.tool()
async def list_user_files(
    ctx: Context,
    user_id: Optional[str] = None,
    limit: int = DEFAULT_QUERY_LIMIT,
    include_datasets_only: bool = False
) -> Dict[str, Any]:
    """
    List files uploaded by a user with optional filtering.
    
    This tool provides a comprehensive view of all files stored in S3 with metadata
    in MongoDB for the authenticated user. Supports filtering and pagination.
    
    Features:
    - User isolation: Only shows files belonging to authenticated user
    - Dataset filtering: Option to show only detected datasets
    - Rich metadata: File size, upload date, analysis status, storage paths
    - Pagination support for large file collections
    
    Args:
        ctx: FastMCP context with user authentication headers
        user_id: Optional explicit user_id (defaults to authenticated user)
        limit: Maximum number of files to return (1-500, default: 50)
        include_datasets_only: If true, only return files marked as datasets
        
    Returns:
        Structured response with files list, count, and metadata summary
        
    Example:
        list_user_files(limit=20, include_datasets_only=true)
    """
    await ensure_db_initialized()
    
    if db_manager.db is None:
        return {"success": False, "error": "Database not initialized"}
    
    if not await db_manager.check_connection():
        return {"success": False, "error": "Database not connected"}
    
    try:
        # Extract user_id from context if not provided
        tool_args = {"user_id": user_id, "limit": limit, "include_datasets_only": include_datasets_only}
        current_user_id = get_user_id_from_context(ctx, tool_args)
        
        # Use provided user_id or fall back to authenticated user
        target_user_id = user_id if user_id else current_user_id
        
        # Validate access (users can only list their own files)
        if not validate_user_access(ctx, target_user_id, tool_args):
            return {"success": False, "error": "Access denied - cannot list files for different user"}
        
        # Sanitize limit
        limit = max(1, min(limit, MAX_QUERY_LIMIT))
        
        # Build query
        query = {"user_id": target_user_id}
        if include_datasets_only:
            query["is_dataset"] = True
        
        # Get files with metadata
        cursor = (
            db_manager.db["file_metadata"]
            .find(query)
            .sort("uploaded_at", DESCENDING)
            .limit(limit)
        )
        
        files = await cursor.to_list(length=limit)
        
        # Process files for response
        for f in files:
            f["_id"] = str(f["_id"])
            if isinstance(f.get("uploaded_at"), datetime):
                f["uploaded_at"] = f["uploaded_at"].isoformat()
            
            # Add analysis summary if available
            if "last_analysis" in f and isinstance(f["last_analysis"].get("completed_at"), datetime):
                f["last_analysis"]["completed_at"] = f["last_analysis"]["completed_at"].isoformat()
        
        # Get total count
        total_count = await db_manager.db["file_metadata"].count_documents(query)
        
        return {
            "success": True,
            "user_id": target_user_id,
            "files": files,
            "count": len(files),
            "total_count": total_count,
            "include_datasets_only": include_datasets_only,
            "has_more": len(files) < total_count
        }
        
    except Exception as e:
        logger.error(f"Error listing user files: {e}")
        return {"success": False, "error": f"File listing error: {str(e)}"}

# ── LLM-Friendly Aliases ────────────────────────────────────────────

@mcp.tool()
async def upload_dataset(request: FileUploadRequest, ctx: Context) -> Dict[str, Any]:
    """LLM-friendly alias for uploading a dataset/file to S3 and MongoDB metadata."""
    return await upload_file(request, ctx)

@mcp.tool()
async def get_file(request: Dict[str, Any]) -> Dict[str, Any]:
    """LLM-friendly alias for downloading a file by file_id (optionally stage to local volume)."""
    return await download_file(request)

@mcp.tool()
async def dataset_profile(request: Dict[str, Any]) -> Dict[str, Any]:
    """LLM-friendly alias to compute and store dataset schema/profile in MongoDB."""
    return await inspect_dataset(request)

@mcp.tool()
async def start_analysis(request: AsyncAnalysisRequest, ctx: Context) -> Dict[str, Any]:
    """LLM-friendly alias to start background analysis on a dataset (cluster/classify)."""
    return await start_dataset_analysis(request, ctx)

@mcp.tool()
async def analysis_status(request: AnalysisStatusRequest) -> Dict[str, Any]:
    """LLM-friendly alias to fetch background analysis job status/results by job_id."""
    return await get_analysis_status(request)

@mcp.tool()
async def list_files(ctx: Context, user_id: Optional[str] = None) -> Dict[str, Any]:
    """LLM-friendly alias to list files uploaded by a user."""
    return await list_user_files(ctx, user_id)

@mcp.tool()
async def quick_analyze_file(request: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Immediately analyze a previously uploaded dataset (no clustering) and persist summary.
    Args:
        request: { file_id: str, user_id?: str }
    Returns: { success, file_id, quick_analysis }
    """
    await ensure_db_initialized()
    file_id = request.get("file_id")
    if not file_id:
        return {"success": False, "error": "file_id is required"}
    try:
        meta = await db_manager.db["file_metadata"].find_one({"file_id": file_id})
        if not meta:
            return {"success": False, "error": "File not found", "file_id": file_id}
        if not validate_user_access(ctx, meta.get("user_id", ""), request):
            return {"success": False, "error": "Access denied"}
        if not meta.get("is_dataset"):
            return {"success": False, "error": "File is not marked as dataset"}
        storage_path = meta.get("storage_path")
        if not storage_path or not db_manager.storage:
            return {"success": False, "error": "Storage not available"}
        content = db_manager.storage.download_bytes(storage_path)
        if content is None:
            return {"success": False, "error": "Failed to download file bytes"}
        result = analyze_bytes(content, meta.get("filename", "dataset"))
        if not result.get("success"):
            return result
        quick_doc = {
            "summary": result.get("summary", {}),
            "analyzed_at": datetime.utcnow()
        }
        await db_manager.db["file_metadata"].update_one(
            {"file_id": file_id}, {"$set": {"quick_analysis": quick_doc}}
        )
        return {"success": True, "file_id": file_id, "quick_analysis": quick_doc}
    except Exception as e:
        logger.error(f"quick_analyze_file error: {e}")
        return {"success": False, "error": str(e)}

@mcp.tool()
async def get_quick_analysis(request: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """Fetch stored quick analysis summary for a dataset.
    Args: { file_id: str }
    """
    await ensure_db_initialized()
    file_id = request.get("file_id")
    if not file_id:
        return {"success": False, "error": "file_id is required"}
    try:
        meta = await db_manager.db["file_metadata"].find_one({"file_id": file_id})
        if not meta:
            return {"success": False, "error": "File not found"}
        if not validate_user_access(ctx, meta.get("user_id", ""), request):
            return {"success": False, "error": "Access denied"}
        qa = meta.get("quick_analysis")
        if not qa:
            return {"success": False, "error": "No quick analysis stored yet"}
        # Convert datetime
        if isinstance(qa.get("analyzed_at"), datetime):
            qa["analyzed_at"] = qa["analyzed_at"].isoformat()
        return {"success": True, "file_id": file_id, "quick_analysis": qa}
    except Exception as e:
        logger.error(f"get_quick_analysis error: {e}")
        return {"success": False, "error": str(e)}

# ── Server startup and cleanup ──────────────────────────────────────
async def cleanup():
    """Cleanup resources on server shutdown."""
    await db_manager.close()

# Main entry point  
if __name__ == "__main__":
    # Remove synchronous initialization to avoid event loop conflicts
    # Database will be initialized on first tool call
    
    # Run with FastMCP streamable HTTP transport
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=MONGO_MCP_PORT,
        log_level="WARNING"
    )
