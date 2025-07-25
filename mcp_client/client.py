"""
Unified MCP Client using langchain-mcp-adapters
Fixed health check endpoints
"""
import os
import sys
import asyncio
import logging
from typing import Dict, Any, List, Optional
import json

# LangChain MCP Adapters
from langchain_mcp_adapters.client import MultiServerMCPClient

# Local imports
from agent.agent import LogAnalyticsAgent

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MCPClient:
    """
    Unified MCP client with agent integration
    """
    
    def __init__(self, provider: str = "openai", model: str = "gpt-4o-mini"):
        self.provider = provider
        self.model = model
        self.agent = LogAnalyticsAgent(llm_provider=provider)
        
    async def initialize(self):
        """Initialize agent (which includes MCP client)"""
        await self.agent.initialize()
        logger.info("✅ MCPClient initialized successfully")
    
    async def process_query(self, query: str, user_id: str = "default", conversation_id: str = None) -> str:
        """Process a query using the agent"""
        if not conversation_id:
            import uuid
            conversation_id = str(uuid.uuid4())
        
        return await self.agent.analyze(
            query=query,
            user_id=user_id,
            conversation_id=conversation_id
        )
    
    async def health_check(self) -> Dict[str, Any]:
        """Check health of all MCP servers using POST /health"""
        results = {}
        
        config = {
            "mongodb": {"url": "http://localhost:8100/mcp/"},
            "milvus": {"url": "http://localhost:8110/mcp/"},
            "websearch": {"url": "http://localhost:8140/mcp/"},
            "scirex": {"url": "http://localhost:8150/mcp/"}
        }
        
        for server_name, server_config in config.items():
            try:
                # Use POST /health instead of GET
                import httpx
                async with httpx.AsyncClient(timeout=5.0) as client:
                    response = await client.post(
                        server_config['url'] + 'tools/health_check',
                        json={}
                    )
                    results[server_name] = {
                        "status": "healthy" if response.status_code == 200 else "error",
                        "url": server_config['url'],
                        "response_time": response.elapsed.total_seconds() if response.status_code == 200 else None
                    }
            except Exception as e:
                results[server_name] = {
                    "status": "error",
                    "url": server_config['url'],
                    "error": str(e)
                }
        
        return results
    
    async def cleanup(self):
        """Clean up all resources"""
        await self.agent.cleanup()

# Utility functions
async def create_mcp_client(provider: str = "openai", model: str = "gpt-4o-mini") -> MCPClient:
    """Create and initialize MCP client"""
    client = MCPClient(provider=provider, model=model)
    await client.initialize()
    return client