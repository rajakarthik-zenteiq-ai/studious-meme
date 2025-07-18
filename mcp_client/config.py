"""
MCP Client configuration with HTTP transport endpoints
"""
import os
import sys
from pathlib import Path

# Add config to path
sys.path.insert(0, str(Path(__file__).parent.parent / "config"))

from config.settings import (
    MONGODB_MCP_URL,
    MILVUS_MCP_URL,
    WEBSEARCH_MCP_URL,
    SCIREX_MCP_URL,
    OAUTH_ENABLED,
    OAUTH_CLIENT_ID,
    OAUTH_CLIENT_SECRET,
    OAUTH_AUDIENCE
)

# Server configuration with HTTP transport
SERVER_CONFIG = {
    "mongodb": {
        "url": MONGODB_MCP_URL,
        "transport": "http",
        "auth_required": OAUTH_ENABLED
    },
    "milvus": {
        "url": MILVUS_MCP_URL,
        "transport": "http",
        "auth_required": OAUTH_ENABLED
    },
    "websearch": {
        "url": WEBSEARCH_MCP_URL,
        "transport": "http",
        "auth_required": OAUTH_ENABLED
    },
    "scirex": {
        "url": SCIREX_MCP_URL,
        "transport": "http",
        "auth_required": OAUTH_ENABLED
    }
}

# OAuth configuration if enabled
if OAUTH_ENABLED:
    OAUTH_CONFIG = {
        "client_id": OAUTH_CLIENT_ID,
        "client_secret": OAUTH_CLIENT_SECRET,
        "audience": OAUTH_AUDIENCE,
        "scope": "mcp:tools:execute"
    }
else:
    OAUTH_CONFIG = None

# Utility functions
def extract_date(log_timestamp: str) -> str:
    """Extract date from ISO timestamp."""
    try:
        return log_timestamp.split('T')[0]
    except:
        return ""

def validate_server_name(server: str) -> bool:
    """Check if server name is valid."""
    return server in SERVER_CONFIG

def get_server_url(server: str) -> str:
    """Get the URL for a server."""
    if server in SERVER_CONFIG:
        return SERVER_CONFIG[server]["url"]
    raise ValueError(f"Unknown server: {server}")

# Tool name mapping (server.tool -> full tool name)
TOOL_MAPPING = {
    # MongoDB tools
    "mongodb.search_logs": "search_logs",
    "mongodb.get_logs_by_date": "get_logs_by_date",
    "mongodb.store_logs": "store_logs",
    "mongodb.upload_file": "upload_file",
    "mongodb.append_chat": "append_chat",
    "mongodb.get_system_stats": "get_system_stats",
    "mongodb.get_chat_history": "get_chat_history",
    
    # Redis tools
    "redis.cache_set": "cache_set",
    "redis.cache_get": "cache_get",
    "redis.cache_delete": "cache_delete",
    "redis.memory_store": "memory_store",
    "redis.memory_search": "memory_search",
    "redis.short_term_memory_set": "short_term_memory_set",
    "redis.short_term_memory_get": "short_term_memory_get",
    
    # Milvus tools
    "milvus.store_embedding": "store_embedding",
    "milvus.search_similar": "search_similar",
    "milvus.delete_vectors": "delete_vectors",
    "milvus.get_collection_stats": "get_collection_stats",
    
    # Neo4j tools
    "neo4j.create_node": "create_node",
    "neo4j.create_relationship": "create_relationship",
    "neo4j.store_knowledge": "store_knowledge",
    "neo4j.query_knowledge_graph": "query_knowledge_graph",
    "neo4j.find_patterns": "find_patterns",
    
    # WebSearch tools
    "websearch.search_web": "search_web",
    "websearch.summarize_page": "summarize_page",
    
    # SciREX tools
    "scirex.upload_dataset": "upload_dataset",
    "scirex.train_kmeans": "train_kmeans",
    "scirex.train_neural_net": "train_neural_net",
    "scirex.train_fastvpinn": "train_fastvpinn",
    "scirex.list_datasets": "list_datasets",
}