"""
Updated MCP Client with RBAC integration
"""
import os
import sys
import asyncio
import logging
from typing import Dict, Any, List, Optional
import json

# Import auth utilities
from utils.auth_utils import AuthManager, UserRole

# LangChain MCP Adapters
from langchain_mcp_adapters.client import MultiServerMCPClient

# Local imports
from agent.agent import LogAnalyticsAgent

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MCPClient:
    """
    Unified MCP client with agent integration and RBAC
    """
    
    def __init__(self, provider: str = "openai", model: str = "gpt-4o-mini"):
        self.provider = provider
        self.model = model
        self.agent = LogAnalyticsAgent(llm_provider=provider)
        self.auth_manager = AuthManager()
        self.current_role: Optional[UserRole] = None
    
    async def initialize(self, user_role: UserRole = UserRole.VIEWER):
        """Initialize agent with user role"""
        self.current_role = user_role
        await self.agent.initialize()
        
        # Filter accessible servers based on role
        accessible_servers = self._get_accessible_servers(user_role)
        logger.info(f"✅ MCPClient initialized for role: {user_role.value}")
        logger.info(f"Accessible servers: {list(accessible_servers.keys())}")
    
    def _get_accessible_servers(self, user_role: UserRole) -> Dict[str, str]:
        """Get accessible servers for the user role"""
        permissions = self.auth_manager.role_permissions.get(user_role)
        if not permissions:
            return {}
        
        config = {
            "mongodb": {"url": "http://localhost:8100/mcp/"},
            "milvus": {"url": "http://localhost:8110/mcp/"},
            "websearch": {"url": "http://localhost:8140/mcp/"},
            "scirex": {"url": "http://localhost:8150/mcp/"}
        }
        
        # Filter based on server access
        accessible = {}
        for server, access in permissions["servers"].items():
            if access != "none":
                accessible[server] = config[server]
        
        return accessible
    
    async def process_query(
        self, 
        query: str, 
        user_id: str = "default", 
        conversation_id: str = None,
        user_role: UserRole = UserRole.VIEWER
    ) -> str:
        """Process query with role-based access control"""
        if not conversation_id:
            import uuid
            conversation_id = str(uuid.uuid4())
        
        # Initialize with user role
        await self.initialize(user_role)
        
        # Check if query involves restricted tools
        try:
            # The agent will handle tool access through RBAC
            return await self.agent.analyze(
                query=query,
                user_id=user_id,
                conversation_id=conversation_id
            )
        except Exception as e:
            if "permission denied" in str(e).lower():
                return f"❌ Access denied: {e}"
            raise
    
    async def check_tool_access(
        self, 
        user_role: UserRole, 
        server_name: str, 
        tool_name: str
    ) -> bool:
        """Check if user has access to specific tool"""
        return self.auth_manager.check_tool_access(user_role, server_name, tool_name) != "none"
    
    async def get_user_permissions(self, user_role: UserRole) -> Dict[str, Any]:
        """Get permissions for user role"""
        return self.auth_manager.get_user_permissions_summary(user_role)
    
    async def health_check(self, user_role: UserRole = UserRole.VIEWER) -> Dict[str, Any]:
        """Check health of accessible servers based on role"""
        results = {}
        
        accessible_servers = self._get_accessible_servers(user_role)
        
        for server_name, server_config in accessible_servers.items():
            try:
                import httpx
                async with httpx.AsyncClient(timeout=5.0) as client:
                    response = await client.post(
                        server_config['url'] + 'tools/health_check',
                        json={}
                    )
                    results[server_name] = {
                        "status": "healthy" if response.status_code == 200 else "error",
                        "url": server_config['url'],
                        "response_time": response.elapsed.total_seconds() if response.status_code == 200 else None,
                        "accessible": True
                    }
            except Exception as e:
                results[server_name] = {
                    "status": "error",
                    "url": server_config['url'],
                    "error": str(e),
                    "accessible": True
                }
        
        return {
            "role": user_role.value,
            "accessible_servers": results,
            "permissions": await self.get_user_permissions(user_role)
        }
    
    async def cleanup(self):
        """Clean up all resources"""
        await self.agent.cleanup()

# Utility functions
async def create_mcp_client(
    provider: str = "openai", 
    model: str = "gpt-4o-mini",
    user_role: UserRole = UserRole.VIEWER
) -> MCPClient:
    """Create and initialize MCP client with RBAC"""
    client = MCPClient(provider=provider, model=model)
    await client.initialize(user_role=user_role)
    return client