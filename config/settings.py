import os
from dotenv import load_dotenv

load_dotenv()

# ── Environment Detection ─────────────────────────────────────────
# Detect if running in Docker container
IS_DOCKER = os.getenv("IS_DOCKER", "false").lower() == "true"

# ── MongoDB Server Settings ───────────────────────────────────────
MONGO_MCP_NAME = "mongo_tools"
MONGO_MCP_PORT = int(os.getenv("MONGO_MCP_PORT", 8100))
MONGO_URI = os.getenv("MONGO_URI", "mongodb://admin:password@localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "logsdb")

# ── Redis Server Settings ─────────────────────────────────────────
REDIS_MCP_NAME = "redis_tools"
REDIS_MCP_PORT = int(os.getenv("REDIS_MCP_PORT", 8130))
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0" if IS_DOCKER else "redis://localhost:6379/0")
REDIS_CACHE_TTL = int(os.getenv("REDIS_CACHE_TTL", 3600))

# ── Milvus Server Settings ────────────────────────────────────────
MILVUS_MCP_NAME = "milvus_tools"
MILVUS_MCP_PORT = int(os.getenv("MILVUS_MCP_PORT", 8110))
MILVUS_HOST = os.getenv("MILVUS_HOST", "milvus" if IS_DOCKER else "localhost")
MILVUS_PORT = int(os.getenv("MILVUS_PORT", 19530))
MILVUS_COLLECTION = os.getenv("MILVUS_COLLECTION", "logs_embeddings")

# ── WebSearch Server Settings ─────────────────────────────────────
WEBSEARCH_MCP_NAME = "websearch_tools"
WEBSEARCH_MCP_PORT = int(os.getenv("WEBSEARCH_MCP_PORT", 8140))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
SEARCH_MODEL = os.getenv("SEARCH_MODEL", "gpt-4o-mini")

# ── SciREX Server Settings ────────────────────────────────────────
SCIREX_MCP_NAME = "scirex_tools"
SCIREX_MCP_PORT = int(os.getenv("SCIREX_MCP_PORT", 8150))

# Set data directories based on environment
if IS_DOCKER:
    # Docker environment - use container paths
    SCIREX_DATA_DIR = os.getenv("SCIREX_DATA_DIR", "/data/user_datasets")
    SCIREX_MODEL_DIR = os.getenv("SCIREX_MODEL_DIR", "/data/user_models")
else:
    # Local development - use /tmp paths
    SCIREX_DATA_DIR = os.getenv("SCIREX_DATA_DIR", "/tmp/user_datasets")
    SCIREX_MODEL_DIR = os.getenv("SCIREX_MODEL_DIR", "/tmp/user_models")
    
    # Create directories if they don't exist (only for local development)
    try:
        os.makedirs(SCIREX_DATA_DIR, exist_ok=True)
        os.makedirs(SCIREX_MODEL_DIR, exist_ok=True)
    except PermissionError:
        # Fallback to current directory if /tmp is not writable
        SCIREX_DATA_DIR = os.path.join(os.getcwd(), "user_datasets")
        SCIREX_MODEL_DIR = os.path.join(os.getcwd(), "user_models")
        os.makedirs(SCIREX_DATA_DIR, exist_ok=True)
        os.makedirs(SCIREX_MODEL_DIR, exist_ok=True)

# ── MCP Client URLs (Updated for HTTP transport) ─────────────────
# IMPORTANT: Updated from SSE to HTTP transport
if IS_DOCKER:
    # In Docker, use service names
    MONGODB_MCP_URL = os.getenv("MONGODB_MCP_URL", "http://mongo_server:8100/mcp/")
    MILVUS_MCP_URL = os.getenv("MILVUS_MCP_URL", "http://milvus_server:8110/mcp/")
    WEBSEARCH_MCP_URL = os.getenv("WEBSEARCH_MCP_URL", "http://websearch_server:8140/mcp/")
    SCIREX_MCP_URL = os.getenv("SCIREX_MCP_URL", "http://scirex_server:8150/mcp/")
else:
    # In local development, use localhost
    MONGODB_MCP_URL = os.getenv("MONGODB_MCP_URL", "http://localhost:8100/mcp/")
    MILVUS_MCP_URL = os.getenv("MILVUS_MCP_URL", "http://localhost:8110/mcp/")
    WEBSEARCH_MCP_URL = os.getenv("WEBSEARCH_MCP_URL", "http://localhost:8140/mcp/")
    SCIREX_MCP_URL = os.getenv("SCIREX_MCP_URL", "http://localhost:8150/mcp/")

# ── General Settings ───────────────────────────────────────────────
DEBUG = os.getenv("DEBUG", "true").lower() == "true"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ── Security Settings (NEW) ────────────────────────────────────────
# OAuth 2.1 settings for MCP authentication
OAUTH_ENABLED = os.getenv("OAUTH_ENABLED", "false").lower() == "true"
OAUTH_ISSUER = os.getenv("OAUTH_ISSUER", "")  # e.g., https://auth0.com/
OAUTH_CLIENT_ID = os.getenv("OAUTH_CLIENT_ID", "")
OAUTH_CLIENT_SECRET = os.getenv("OAUTH_CLIENT_SECRET", "")
OAUTH_AUDIENCE = os.getenv("OAUTH_AUDIENCE", "mcp-api")

# Connection pool settings
MONGO_MAX_POOL_SIZE = int(os.getenv("MONGO_MAX_POOL_SIZE", 10))
MONGO_MIN_POOL_SIZE = int(os.getenv("MONGO_MIN_POOL_SIZE", 1))
NEO4J_MAX_CONNECTION_LIFETIME = int(os.getenv("NEO4J_MAX_CONNECTION_LIFETIME", 3600))
NEO4J_MAX_CONNECTION_POOL_SIZE = int(os.getenv("NEO4J_MAX_CONNECTION_POOL_SIZE", 50))
REDIS_MAX_CONNECTIONS = int(os.getenv("REDIS_MAX_CONNECTIONS", 50))