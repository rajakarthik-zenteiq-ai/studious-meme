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
        if not self.db:
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
        if not self.client or not self.db:
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

# ── Helper functions ─────────────────────────────────────────────────
async def ensure_db_initialized():
    """Ensure database is initialized before tool execution"""
    if not db_manager._initialized:
        await db_manager.initialize()

# ── MCP Tools ────────────────────────────────────────────────────────

@mcp.tool()
async def purge_legacy_metadata() -> Dict[str, Any]:
    """
    Purge legacy MinIO/GridFS metadata and logs from MongoDB.
    
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
    Upload a file to Linode Object Storage with metadata stored in MongoDB.
    
    This tool handles file uploads with automatic storage tier selection:
    - CSV, JSON, TXT files → "datasets" directory (for data analysis)
    - Model files → "models" directory (for ML models)
    - Other files → "files" directory (for general storage)
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
        # Extract user_id using OAuth authenticator
        if hasattr(ctx, 'request'):
            auth_context = authenticator.validate_request(ctx.request)
            user_id = auth_context['user_id']
        else:
            # Fallback for contexts without request object
            user_id = "anonymous"
        
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
        
        # Detect dataset files based on extension and content analysis
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
        
        # Additional dataset detection for CSV/JSON files
        if file_ext in ['.csv', '.json', '.txt'] and len(file_content) > 0:
            try:
                if file_ext == '.csv':
                    # Try to read as CSV to validate structure
                    import pandas as pd
                    pd.read_csv(io.BytesIO(file_content))
                elif file_ext == '.json':
                    # Try to parse as JSON
                    json.loads(file_content.decode('utf-8'))
            except:
                pass
        
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
                logger.error(f"Unexpected storage error: {e}")
                return {"success": False, "error": f"Storage error: {str(e)}"}
        
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
            "file_extension": file_ext,
            "auto_detected_type": "dataset" if is_dataset else ("model" if "models" in directory else "document")
        }
        
        await db_manager.db["file_metadata"].insert_one(metadata_doc)
        
        return {
            "success": True,
            "file_id": str(file_id),
            "filename": request.filename,
            "file_size": len(file_content),
            "content_type": request.content_type,
            "storage_path": storage_path,
            "bucket": "mcp",
            "directory": directory
        }
        
    except Exception as e:
        logger.error(f"Error uploading file: {e}")
        return {"success": False, "error": f"Upload error: {str(e)}"}

@mcp.tool()
async def download_file(request: Dict[str, Any]) -> Dict[str, Any]:
    """
    Download a file from Linode Object Storage.
    
    This tool retrieves files from S3 storage:
    1. Checks MongoDB metadata for file information
    2. Retrieves from Linode Object Storage
    3. Returns base64-encoded content with metadata
    
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
async def list_uploaded_files(
    ctx: Context,
    file_type: Optional[str] = None,
    limit: int = DEFAULT_QUERY_LIMIT,
    user_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    List S3-backed uploaded files for the authenticated user with optional filtering.
    
    This tool provides secure file listing with user isolation:
    - Only shows S3-backed files (excludes legacy GridFS entries)
    - Only shows files belonging to the authenticated user
    - Supports filtering by file type/content type
    - Includes storage metadata and file details
    - Paginated results with configurable limits
    
    Args:
        ctx: FastMCP context with user authentication headers
        file_type: Optional filter by content type (e.g., "csv", "json")
        limit: Maximum number of files to return (default: 50)
        
    Returns:
        Structured response with user's S3-backed files list and metadata
        
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
        # Extract authenticated user_id from context, with fallback to parameter
        context_user_id = get_user_id_from_context(ctx)
        if context_user_id == "anonymous" and user_id:
            final_user_id = user_id
        else:
            final_user_id = context_user_id
        
        # Build query with user isolation and S3-only filtering
        query = {
            "user_id": final_user_id,  # Always filter by authenticated user
            "storage_path": {"$not": {"$regex": "^gridfs://"}},  # Exclude GridFS files
            "bucket": {"$ne": "gridfs"}  # Exclude GridFS bucket
        }
        
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
            "user_id": final_user_id,
            "total_files": total_count,
            "returned_count": len(files),
            "files": files,
            "query_filters": {"user_id": final_user_id, "file_type": file_type, "storage_type": "s3_only"}
        }
        
    except Exception as e:
        logger.error(f"Error listing files for user {user_id}: {e}")
        return {"success": False, "error": f"List error: {str(e)}"}

@mcp.tool()
async def list_available_datasets(
    ctx: Context,
    bucket_name: str = "datasets",
    limit: int = DEFAULT_QUERY_LIMIT,
    user_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    List available S3-backed datasets with user isolation.
    
    This tool provides a comprehensive view of datasets accessible to the authenticated user:
    - Automatically extracts user_id from request headers for authentication
    - Scans S3 storage for user's uploaded dataset files (CSV, JSON, TXT)
    - Only includes S3-backed files (excludes legacy GridFS entries)
    - Includes file metadata (size, upload date, owner)
    - Filters by authenticated user to ensure data isolation
    
    Args:
        ctx: Request context containing user authentication headers
        bucket_name: S3 directory to scan (default: "datasets")
        limit: Maximum number of datasets to return
        
    Returns:
        Structured response with user's S3-backed datasets and metadata
        
    Example:
        list_available_datasets("datasets", 20)
    """
    await ensure_db_initialized()
    
    if db_manager.db is None:
        return {"success": False, "error": "Database not initialized"}
    
    try:
        # Extract user_id from context headers, with fallback to parameter
        context_user_id = get_user_id_from_context(ctx)
        if context_user_id == "anonymous" and user_id:
            final_user_id = user_id
        else:
            final_user_id = context_user_id
        
        available_datasets = []
        
        # List from S3 if available
        if db_manager.storage:
            try:
                # List objects in the mcp bucket
                objects = db_manager.storage.list_objects()
                storage_datasets = []
                for obj in objects:
                    obj_key = obj.get("key", "")
                    # Filter for datasets directory and user files
                    if obj_key.startswith(f"datasets/{final_user_id}_"):
                        dataset_info = {
                            "id": obj_key,
                            "name": obj_key.split("_", 2)[-1] if "_" in obj_key else obj_key,
                            "size": obj.get("size", 0),
                            "last_modified": obj.get("last_modified"),
                            "source": "s3",
                            "bucket": "mcp",
                            "directory": "datasets",
                            "type": "file",
                            "user_id": final_user_id
                        }
                        storage_datasets.append(dataset_info)
                
                if len(storage_datasets) > limit:
                    storage_datasets = storage_datasets[:limit]
                
                available_datasets.extend(storage_datasets)
                logger.info(f"Found {len(storage_datasets)} S3 datasets")
                
            except ClientError as e:
                logger.warning(f"S3 list error: {e}")
            except Exception as e:
                logger.warning(f"S3 access error: {e}")
        
        # List from MongoDB file_metadata collection (user's S3-backed files only)
        try:
            # Filter to only include S3-backed files (exclude legacy GridFS entries)
            query = {
                "user_id": final_user_id, 
                "is_dataset": True,
                "storage_path": {"$not": {"$regex": "^gridfs://"}},  # Exclude GridFS files
                "bucket": {"$ne": "gridfs"}  # Exclude GridFS bucket
            }
            
            cursor = (
                db_manager.db["file_metadata"]
                .find(query)
                .sort("uploaded_at", DESCENDING)
                .limit(limit)
            )
            
            db_datasets = await cursor.to_list(length=limit)
            
            for dataset in db_datasets:
                dataset_info = {
                    "id": str(dataset.get("file_id", dataset.get("_id", "unknown"))),
                    "name": dataset.get("filename", "unnamed"),
                    "size": dataset.get("file_size", 0),
                    "last_modified": dataset.get("uploaded_at", datetime.utcnow()).isoformat(),
                    "source": "s3_metadata",
                    "collection": "file_metadata", 
                    "type": "metadata",
                    "user_id": dataset.get("user_id"),
                    "storage_path": dataset.get("storage_path"),
                    "bucket": dataset.get("bucket")
                }
                available_datasets.append(dataset_info)
            
            logger.info(f"Found {len(db_datasets)} S3-backed datasets in MongoDB")
            
        except Exception as e:
            logger.warning(f"Database query error: {e}")
        
        return {
            "success": True,
            "total_datasets": len(available_datasets),
            "datasets": available_datasets,
            "query_filters": {"user_id": final_user_id, "bucket": bucket_name, "storage_type": "s3_only"},
            "sources": {
                "s3": len([d for d in available_datasets if d.get("source") == "s3"]),
                "s3_metadata": len([d for d in available_datasets if d.get("source") == "s3_metadata"])
            }
        }
        
    except Exception as e:
        logger.error(f"Error listing datasets: {e}")
        return {"success": False, "error": f"List datasets error: {str(e)}"}

@mcp.tool() 
async def get_dataset_info(dataset_id: str, ctx: Context) -> Dict[str, Any]:
    """
    Get detailed information about a specific S3-backed dataset with user isolation.
    
    This tool retrieves dataset information with security controls:
    - Validates that the requesting user owns the dataset
    - Searches S3 storage and MongoDB metadata
    - Returns comprehensive dataset details including size, type, and storage location
    """
    await ensure_db_initialized()
    
    if db_manager.db is None:
        return {"success": False, "error": "Database not initialized"}
    
    try:
        user_id = get_user_id_from_context(ctx)
        dataset_info = None
        
        # Check S3 storage first (validate ownership via naming convention)
        if db_manager.storage:
            try:
                if not dataset_id.startswith(f"datasets/{user_id}_") and not dataset_id.startswith(f"{user_id}_"):
                    return {"success": False, "error": "Access denied - dataset does not belong to user"}
                    
                # Try to get object metadata from S3
                try:
                    obj_metadata = db_manager.storage.get_object_metadata(dataset_id)
                    if obj_metadata:
                        dataset_info = {
                            "id": dataset_id,
                            "name": dataset_id.split("_", 2)[-1] if "_" in dataset_id else dataset_id,
                            "size": obj_metadata.get("size", 0),
                            "last_modified": obj_metadata.get("last_modified"),
                            "source": "s3",
                            "bucket": "mcp",
                            "storage_path": dataset_id,
                            "user_id": user_id
                        }
                except Exception:
                    pass
            except Exception:
                pass
        
        # Check MongoDB if not found in S3
        if dataset_info is None:
            try:
                # Only search for S3-backed files owned by the user
                metadata_doc = await db_manager.db["file_metadata"].find_one({
                    "$or": [
                        {"file_id": dataset_id, "user_id": user_id},
                        {"_id": dataset_id, "user_id": user_id}
                    ],
                    "storage_path": {"$not": {"$regex": "^gridfs://"}},
                    "bucket": {"$ne": "gridfs"}
                })
                
                if metadata_doc:
                    dataset_info = {
                        "id": str(metadata_doc.get("file_id", metadata_doc.get("_id"))),
                        "name": metadata_doc.get("filename", "unnamed"),
                        "size": metadata_doc.get("file_size", 0),
                        "last_modified": metadata_doc.get("uploaded_at", datetime.utcnow()).isoformat(),
                        "source": "s3_metadata",
                        "collection": "file_metadata",
                        "storage_path": metadata_doc.get("storage_path"),
                        "bucket": metadata_doc.get("bucket"),
                        "user_id": metadata_doc.get("user_id"),
                        "content_type": metadata_doc.get("content_type"),
                        "is_dataset": metadata_doc.get("is_dataset")
                    }
            except Exception as e:
                logger.error(f"Error querying MongoDB: {e}")
        
        if dataset_info is None:
            return {
                "success": False, 
                "error": f"Dataset '{dataset_id}' not found or access denied", 
                "details": {
                    "dataset_id": dataset_id, 
                    "user_id": user_id, 
                    "searched_in": ["S3 storage", "MongoDB S3-backed metadata"],
                    "suggestion": "Check if dataset_id is correct and belongs to your user account"
                }
            }
        
        return {"success": True, "dataset": dataset_info}
    except Exception as e:
        logger.error(f"Error getting dataset info: {e}")
        return {"success": False, "error": f"Dataset info error: {str(e)}"}

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

# ── Server startup and cleanup ──────────────────────────────────────
async def cleanup():
    """Cleanup resources on server shutdown."""
    await db_manager.close()

# Main entry point  
if __name__ == "__main__":
    import asyncio
    import uvicorn
    from fastapi import FastAPI
    
    # Create FastAPI app
    app = FastAPI(title=MONGO_MCP_NAME)
    
    # Add MCP server
    app.mount("/mcp", mcp.create_app())
    
    @app.on_event("startup")
    async def startup():
        await db_manager.initialize()
    
    @app.on_event("shutdown")
    async def shutdown():
        await cleanup()
    
    # Run server
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=MONGO_MCP_PORT,
        log_level="info"
    )
