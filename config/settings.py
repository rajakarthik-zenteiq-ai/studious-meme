"""
Production Configuration System
Environment-aware configuration with validation
"""
import os
from typing import Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum
from dotenv import load_dotenv

load_dotenv()

class Environment(str, Enum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"

@dataclass
class ServerConfig:
    """MCP Server configuration"""
    url: str
    transport: str = "streamable_http"
    timeout: int = 30
    max_retries: int = 3
    enabled: bool = True

@dataclass
class DatabaseConfig:
    """Database configuration"""
    mongo_uri: str
    redis_url: str
    mongo_db: str = "mcp_agent"
    redis_db: int = 0
    connection_pool_size: int = 20

@dataclass
class AuthConfig:
    """Authentication configuration"""
    jwt_secret: str
    session_timeout: int = 3600
    token_algorithm: str = "HS256"
    refresh_token_days: int = 30

@dataclass
class LLMConfig:
    """LLM configuration"""
    provider: str = "openai"
    model: str = "gpt-4o-mini"
    api_key: str = ""
    base_url: Optional[str] = None
    temperature: float = 0.1
    max_tokens: Optional[int] = None
    timeout: int = 30

class Config:
    """Centralized configuration manager"""
    
    def __init__(self):
        self.environment = Environment(os.getenv("ENVIRONMENT", "development"))
        self.debug = self.environment == Environment.DEVELOPMENT
        
        # Validate critical environment variables
        self._validate_required_env()
        
        # Initialize configurations
        self.servers = self._init_servers()
        self.database = self._init_database()
        self.auth = self._init_auth()
        self.llm = self._init_llm()
        
        # Application settings
        self.host = os.getenv("HOST", "0.0.0.0")
        self.port = int(os.getenv("PORT", "8000"))
        self.workers = int(os.getenv("WORKERS", "4"))
        self.log_level = os.getenv("LOG_LEVEL", "DEBUG" if self.debug else "INFO")
        
    def _validate_required_env(self) -> None:
        """Validate required environment variables"""
        required = {
            "JWT_SECRET": "JWT secret for authentication",
            "MONGO_URI": "MongoDB connection string",
            "REDIS_URL": "Redis connection URL"
        }
        
        # LLM-specific validation
        llm_provider = os.getenv("LLM_PROVIDER", "openai")
        if llm_provider == "openai":
            required["OPENAI_API_KEY"] = "OpenAI API key"
        elif llm_provider == "vllm":
            required["VLLM_ENDPOINT"] = "vLLM endpoint URL"
            
        missing = [f"{var} ({desc})" for var, desc in required.items() if not os.getenv(var)]
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
    
    def _init_servers(self) -> Dict[str, ServerConfig]:
        """Initialize MCP server configurations"""
        is_docker = os.getenv("IS_DOCKER", "false").lower() == "true"
        base_host = "localhost" if not is_docker else "host.docker.internal"
        
        servers = {}
        
        # MongoDB MCP Server
        mongodb_url = os.getenv("MONGODB_MCP_URL")
        if not mongodb_url:
            mongodb_port = os.getenv("MONGODB_MCP_PORT", "8100")
            mongodb_url = f"http://{base_host}:{mongodb_port}/mcp/"
        
        if mongodb_url:
            servers["mongodb"] = ServerConfig(
                url=mongodb_url,
                timeout=int(os.getenv("MONGODB_TIMEOUT", "30"))
            )
        
        # Milvus MCP Server
        milvus_url = os.getenv("MILVUS_MCP_URL")
        if not milvus_url:
            milvus_port = os.getenv("MILVUS_MCP_PORT", "8110")
            milvus_url = f"http://{base_host}:{milvus_port}/mcp/"
            
        if milvus_url:
            servers["milvus"] = ServerConfig(
                url=milvus_url,
                timeout=int(os.getenv("MILVUS_TIMEOUT", "30"))
            )
        
        # WebSearch MCP Server
        websearch_url = os.getenv("WEBSEARCH_MCP_URL")
        if not websearch_url:
            websearch_port = os.getenv("WEBSEARCH_MCP_PORT", "8140")
            websearch_url = f"http://{base_host}:{websearch_port}/mcp/"
            
        if websearch_url:
            servers["websearch"] = ServerConfig(
                url=websearch_url,
                timeout=int(os.getenv("WEBSEARCH_TIMEOUT", "30"))
            )
        
        # SciREX MCP Server
        scirex_url = os.getenv("SCIREX_MCP_URL")
        if not scirex_url:
            scirex_port = os.getenv("SCIREX_MCP_PORT", "8150")
            scirex_url = f"http://{base_host}:{scirex_port}/mcp/"
            
        if scirex_url:
            servers["scirex"] = ServerConfig(
                url=scirex_url,
                timeout=int(os.getenv("SCIREX_TIMEOUT", "60"))  # Longer timeout for ML operations
            )
        
        return servers
    
    def _init_database(self) -> DatabaseConfig:
        """Initialize database configuration"""
        return DatabaseConfig(
            mongo_uri=os.getenv("MONGO_URI"),
            redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            mongo_db=os.getenv("MONGO_DB", "mcp_agent"),
            redis_db=int(os.getenv("REDIS_DB", "0")),
            connection_pool_size=int(os.getenv("DB_POOL_SIZE", "20"))
        )
    
    def _init_auth(self) -> AuthConfig:
        """Initialize authentication configuration"""
        return AuthConfig(
            jwt_secret=os.getenv("JWT_SECRET"),
            session_timeout=int(os.getenv("SESSION_TIMEOUT", "3600")),
            token_algorithm=os.getenv("TOKEN_ALGORITHM", "HS256"),
            refresh_token_days=int(os.getenv("REFRESH_TOKEN_DAYS", "30"))
        )
    
    def _init_llm(self) -> LLMConfig:
        """Initialize LLM configuration"""
        provider = os.getenv("LLM_PROVIDER", "openai")
        
        config = LLMConfig(
            provider=provider,
            model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
            temperature=float(os.getenv("LLM_TEMPERATURE", "0.1")),
            timeout=int(os.getenv("LLM_TIMEOUT", "30"))
        )
        
        max_tokens = os.getenv("LLM_MAX_TOKENS")
        if max_tokens:
            config.max_tokens = int(max_tokens)
        
        if provider == "openai":
            config.api_key = os.getenv("OPENAI_API_KEY")
        elif provider == "vllm":
            config.base_url = os.getenv("VLLM_ENDPOINT")
            config.api_key = os.getenv("VLLM_API_KEY", "dummy-key")
        
        return config
    
    def get_server_config_dict(self) -> Dict[str, Dict[str, str]]:
        """Get server configuration in MultiServerMCPClient format"""
        return {
            name: {
                "url": server.url,
                "transport": server.transport
            }
            for name, server in self.servers.items()
            if server.enabled
        }
    
    def is_production(self) -> bool:
        """Check if running in production"""
        return self.environment == Environment.PRODUCTION

# Global configuration instance
config = Config()

# -------------------------------------------------------------------------------------------------
# Backward-compatibility exports for MCP server scripts
# These provide simple module-level constants expected by the individual MCP servers.
# -------------------------------------------------------------------------------------------------
IS_DOCKER = os.getenv("IS_DOCKER", "false").lower() == "true"
_BASE_HOST = "host.docker.internal" if IS_DOCKER else "localhost"

# Database
MONGO_URI: str = config.database.mongo_uri
MONGO_DB: str = os.getenv("MONGO_DB", config.database.mongo_db)
REDIS_URL: str = config.database.redis_url
MONGO_MAX_POOL_SIZE: int = int(os.getenv("MONGO_MAX_POOL_SIZE", "10"))
MONGO_MIN_POOL_SIZE: int = int(os.getenv("MONGO_MIN_POOL_SIZE", "1"))

# Auth
JWT_SECRET: str = config.auth.jwt_secret

# LLM / OpenAI
OPENAI_API_KEY: str = config.llm.api_key
SEARCH_MODEL: str = os.getenv("SEARCH_MODEL", config.llm.model)

# Mongo MCP server
MONGO_MCP_PORT: int = int(os.getenv("MONGO_MCP_PORT", "8100"))
MONGO_MCP_NAME: str = os.getenv("MONGO_MCP_NAME", "mongo_tools")

# Milvus MCP server
MILVUS_HOST: str = os.getenv("MILVUS_HOST", "localhost")
MILVUS_PORT: int = int(os.getenv("MILVUS_PORT", "19530"))
MILVUS_COLLECTION: str = os.getenv("MILVUS_COLLECTION", "logs_embeddings")
MILVUS_MCP_PORT: int = int(os.getenv("MILVUS_MCP_PORT", "8110"))

# Websearch MCP server
WEBSEARCH_MCP_PORT: int = int(os.getenv("WEBSEARCH_MCP_PORT", "8140"))
WEBSEARCH_MCP_NAME: str = os.getenv("WEBSEARCH_MCP_NAME", "websearch_tools")

# SciREX MCP server
SCIREX_MCP_PORT: int = int(os.getenv("SCIREX_MCP_PORT", "8150"))
SCIREX_MCP_URL: str = os.getenv(
    "SCIREX_MCP_URL",
    f"http://{_BASE_HOST}:{SCIREX_MCP_PORT}/mcp/"
)
SCIREX_DATA_DIR: str = os.getenv("SCIREX_DATA_DIR", "/data/user_datasets")
SCIREX_MODEL_DIR: str = os.getenv("SCIREX_MODEL_DIR", "/data/user_models")