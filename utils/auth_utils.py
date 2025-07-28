"""
Role-Based Access Control (RBAC) for MCP Servers and Tools
Handles authentication and authorization based on user roles
"""
import os
import sys
from typing import Dict, Set, List, Optional, Any
from enum import Enum
from pathlib import Path
from fastapi import HTTPException, Request
import logging

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger(__name__)

class UserRole(str, Enum):
    """User role definitions"""
    ADMIN = "admin"
    RND = "rnd"
    DEVELOPER = "developer"
    ANALYST = "analyst"
    VIEWER = "viewer"

class AccessLevel(str, Enum):
    """Access level definitions"""
    FULL = "full"
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    NONE = "none"

# 🎯 Role-based permissions mapping
ROLE_PERMISSIONS = {
    UserRole.ADMIN: {
        "servers": {
            "mongodb": AccessLevel.FULL,
            "milvus": AccessLevel.FULL,
            "websearch": AccessLevel.FULL,
            "scirex": AccessLevel.FULL
        },
        "tools": {
            # MongoDB tools
            "get_logs_by_date": AccessLevel.FULL,
            "query_logs": AccessLevel.FULL,
            "store_logs": AccessLevel.FULL,
            "search_logs": AccessLevel.FULL,
            "aggregate_logs": AccessLevel.FULL,
            "append_chat": AccessLevel.FULL,
            "get_chat_history": AccessLevel.FULL,
            "upload_file": AccessLevel.FULL,
            "download_file": AccessLevel.FULL,
            "delete_logs": AccessLevel.FULL,
            "get_system_stats": AccessLevel.FULL,
            
            # Milvus tools
            "search_similar": AccessLevel.FULL,
            "insert_vectors": AccessLevel.FULL,
            "get_collection_stats": AccessLevel.FULL,
            
            # WebSearch tools
            "web_search": AccessLevel.FULL,
            
            # SciREX tools
            "train_neural_network": AccessLevel.FULL,
            "perform_clustering": AccessLevel.FULL,
            "predict": AccessLevel.FULL,
            "list_models": AccessLevel.FULL
        }
    },
    
    UserRole.RND: {
        "servers": {
            "mongodb": AccessLevel.READ,
            "milvus": AccessLevel.READ,
            "websearch": AccessLevel.FULL,
            "scirex": AccessLevel.FULL
        },
        "tools": {
            # MongoDB tools - read-only
            "get_logs_by_date": AccessLevel.READ,
            "query_logs": AccessLevel.READ,
            "search_logs": AccessLevel.READ,
            "aggregate_logs": AccessLevel.READ,
            "get_chat_history": AccessLevel.READ,
            "download_file": AccessLevel.READ,
            "get_system_stats": AccessLevel.READ,
            
            # Milvus tools - read-only
            "search_similar": AccessLevel.READ,
            "get_collection_stats": AccessLevel.READ,
            
            # WebSearch tools - full access
            "web_search": AccessLevel.FULL,
            
            # SciREX tools - full access
            "train_neural_network": AccessLevel.FULL,
            "perform_clustering": AccessLevel.FULL,
            "predict": AccessLevel.FULL,
            "list_models": AccessLevel.FULL
        }
    },
    
    UserRole.DEVELOPER: {
        "servers": {
            "mongodb": AccessLevel.WRITE,
            "milvus": AccessLevel.WRITE,
            "websearch": AccessLevel.FULL,
            "scirex": AccessLevel.READ
        },
        "tools": {
            # MongoDB tools - can read/write but not delete
            "get_logs_by_date": AccessLevel.READ,
            "query_logs": AccessLevel.READ,
            "store_logs": AccessLevel.WRITE,
            "search_logs": AccessLevel.READ,
            "aggregate_logs": AccessLevel.READ,
            "append_chat": AccessLevel.WRITE,
            "get_chat_history": AccessLevel.READ,
            "upload_file": AccessLevel.WRITE,
            "download_file": AccessLevel.READ,
            "get_system_stats": AccessLevel.READ,
            
            # Cannot delete logs
            "delete_logs": AccessLevel.NONE,
            
            # Milvus tools - write access
            "search_similar": AccessLevel.READ,
            "insert_vectors": AccessLevel.WRITE,
            "get_collection_stats": AccessLevel.READ,
            
            # WebSearch tools
            "web_search": AccessLevel.FULL,
            
            # SciREX tools - read-only
            "train_neural_network": AccessLevel.READ,
            "perform_clustering": AccessLevel.READ,
            "predict": AccessLevel.READ,
            "list_models": AccessLevel.READ
        }
    },
    
    UserRole.ANALYST: {
        "servers": {
            "mongodb": AccessLevel.READ,
            "milvus": AccessLevel.READ,
            "websearch": AccessLevel.FULL,
            "scirex": AccessLevel.NONE
        },
        "tools": {
            # MongoDB tools - read-only
            "get_logs_by_date": AccessLevel.READ,
            "query_logs": AccessLevel.READ,
            "search_logs": AccessLevel.READ,
            "aggregate_logs": AccessLevel.READ,
            "get_chat_history": AccessLevel.READ,
            "download_file": AccessLevel.READ,
            "get_system_stats": AccessLevel.READ,
            
            # No write access
            "store_logs": AccessLevel.NONE,
            "append_chat": AccessLevel.NONE,
            "upload_file": AccessLevel.NONE,
            "delete_logs": AccessLevel.NONE,
            
            # Milvus tools - read-only
            "search_similar": AccessLevel.READ,
            "get_collection_stats": AccessLevel.READ,
            
            # WebSearch tools - full access
            "web_search": AccessLevel.FULL,
            
            # No SciREX access
            "train_neural_network": AccessLevel.NONE,
            "perform_clustering": AccessLevel.NONE,
            "predict": AccessLevel.NONE,
            "list_models": AccessLevel.NONE
        }
    },
    
    UserRole.VIEWER: {
        "servers": {
            "mongodb": AccessLevel.READ,
            "milvus": AccessLevel.READ,
            "websearch": AccessLevel.READ,
            "scirex": AccessLevel.NONE
        },
        "tools": {
            # MongoDB tools - read-only
            "get_logs_by_date": AccessLevel.READ,
            "query_logs": AccessLevel.READ,
            "search_logs": AccessLevel.READ,
            "get_chat_history": AccessLevel.READ,
            "download_file": AccessLevel.READ,
            "get_system_stats": AccessLevel.READ,
            
            # No write access
            "store_logs": AccessLevel.NONE,
            "aggregate_logs": AccessLevel.NONE,
            "append_chat": AccessLevel.NONE,
            "upload_file": AccessLevel.NONE,
            "delete_logs": AccessLevel.NONE,
            
            # Milvus tools - read-only
            "search_similar": AccessLevel.READ,
            "get_collection_stats": AccessLevel.READ,
            
            # WebSearch tools - read-only
            "web_search": AccessLevel.READ,
            
            # No SciREX access
            "train_neural_network": AccessLevel.NONE,
            "perform_clustering": AccessLevel.NONE,
            "predict": AccessLevel.NONE,
            "list_models": AccessLevel.NONE
        }
    }
}

class AuthManager:
    """Centralized authentication and authorization manager"""
    
    def __init__(self):
        self.role_permissions = ROLE_PERMISSIONS
    
    def extract_user_role(self, request: Request) -> UserRole:
        """Extract user role from request headers"""
        role_header = request.headers.get("X-User-Role")
        
        if not role_header:
            raise HTTPException(
                status_code=401,
                detail="X-User-Role header is required"
            )
        
        try:
            return UserRole(role_header.lower())
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid role: {role_header}. Valid roles: {[r.value for r in UserRole]}"
            )
    
    def check_server_access(self, user_role: UserRole, server_name: str) -> bool:
        """Check if user has access to a specific server"""
        permissions = self.role_permissions.get(user_role)
        if not permissions:
            return False
        
        server_access = permissions["servers"].get(server_name, AccessLevel.NONE)
        return server_access != AccessLevel.NONE
    
    def check_tool_access(
        self, 
        user_role: UserRole, 
        server_name: str, 
        tool_name: str
    ) -> AccessLevel:
        """Check user access level for a specific tool"""
        permissions = self.role_permissions.get(user_role)
        if not permissions:
            return AccessLevel.NONE
        
        # First check server access
        server_access = permissions["servers"].get(server_name, AccessLevel.NONE)
        if server_access == AccessLevel.NONE:
            return AccessLevel.NONE
        
        # Then check tool access
        tool_access = permissions["tools"].get(tool_name, AccessLevel.NONE)
        return tool_access
    
    def check_tool_permission(
        self,
        user_role: UserRole,
        server_name: str,
        tool_name: str,
        required_action: str = "read"
    ) -> bool:
        """Check if user has permission to perform specific action on tool"""
        access_level = self.check_tool_access(user_role, server_name, tool_name)
        
        if access_level == AccessLevel.NONE:
            return False
        
        # Map actions to access levels
        action_map = {
            "read": [AccessLevel.READ, AccessLevel.WRITE, AccessLevel.FULL],
            "write": [AccessLevel.WRITE, AccessLevel.FULL],
            "execute": [AccessLevel.EXECUTE, AccessLevel.FULL],
            "delete": [AccessLevel.FULL]
        }
        
        allowed_levels = action_map.get(required_action, [])
        return access_level in allowed_levels
    
    def get_accessible_tools(self, user_role: UserRole) -> Dict[str, List[str]]:
        """Get all accessible tools for a user role"""
        permissions = self.role_permissions.get(user_role)
        if not permissions:
            return {}
        
        accessible_tools = {}
        for server, server_access in permissions["servers"].items():
            if server_access != AccessLevel.NONE:
                server_tools = []
                for tool, tool_access in permissions["tools"].items():
                    if tool_access != AccessLevel.NONE:
                        # Simple mapping - assuming tools belong to servers based on naming
                        if server in ["mongodb"] and any(t in tool for t in ["logs", "chat", "file"]):
                            server_tools.append(tool)
                        elif server in ["milvus"] and any(t in tool for t in ["vector", "collection"]):
                            server_tools.append(tool)
                        elif server in ["websearch"] and "search" in tool:
                            server_tools.append(tool)
                        elif server in ["scirex"] and any(t in tool for t in ["train", "predict", "cluster"]):
                            server_tools.append(tool)
                
                if server_tools:
                    accessible_tools[server] = server_tools
        
        return accessible_tools
    
    def get_user_permissions_summary(self, user_role: UserRole) -> Dict[str, Any]:
        """Get comprehensive permissions summary for a user"""
        permissions = self.role_permissions.get(user_role)
        if not permissions:
            return {"error": "Invalid role"}
        
        return {
            "role": user_role.value,
            "servers": {
                server: access.value 
                for server, access in permissions["servers"].items()
            },
            "tools": {
                tool: access.value 
                for tool, access in permissions["tools"].items()
            },
            "accessible_tools": self.get_accessible_tools(user_role)
        }

# Global auth manager instance
auth_manager = AuthManager()

# Convenience functions
def get_user_role_from_request(request: Request) -> UserRole:
    """Get user role from request (wrapper function)"""
    return auth_manager.extract_user_role(request)

def check_server_permission(
    request: Request, 
    server_name: str
) -> bool:
    """Check server permission from request"""
    user_role = get_user_role_from_request(request)
    return auth_manager.check_server_access(user_role, server_name)

def check_tool_permission(
    request: Request,
    server_name: str,
    tool_name: str,
    required_action: str = "read"
) -> bool:
    """Check tool permission from request"""
    user_role = get_user_role_from_request(request)
    return auth_manager.check_tool_permission(
        user_role, 
        server_name, 
        tool_name, 
        required_action
    )

def get_user_permissions(request: Request) -> Dict[str, Any]:
    """Get user permissions summary from request"""
    user_role = get_user_role_from_request(request)
    return auth_manager.get_user_permissions_summary(user_role)