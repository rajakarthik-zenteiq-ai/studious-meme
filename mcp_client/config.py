"""
MCP Client configuration using langchain-mcp-adapters format
"""
import os
from config.settings import (
    MONGODB_MCP_URL, MILVUS_MCP_URL, WEBSEARCH_MCP_URL, SCIREX_MCP_URL
)

# MCP server configuration for MultiServerMCPClient
MCP_CONFIG = {
    "mongodb": {
        "url": MONGODB_MCP_URL,
        "transport": "streamable_http"
    },
    "milvus": {
        "url": MILVUS_MCP_URL,
        "transport": "streamable_http"
    },
    "websearch": {
        "url": WEBSEARCH_MCP_URL,
        "transport": "streamable_http"
    },
    "scirex": {
        "url": SCIREX_MCP_URL,
        "transport": "streamable_http"
    }
}