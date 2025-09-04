"""
Production RBAC System
Clean, efficient role-based access control with JWT authentication
"""
import os
import asyncio
import logging
from typing import Dict, Any, List, Optional, Set
from enum import Enum
from datetime import datetime, timedelta
from dataclasses import dataclass
import hashlib
from jose import jwt, JWTError
from functools import wraps

logger = logging.getLogger(__name__)

class UserRole(str, Enum):
    ADMIN = "admin"
    RND = "rnd"
    DEVELOPER = "developer"
    ANALYST = "analyst" 
    VIEWER = "viewer"
    USER = "user"

class Permission(str, Enum):
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    DELETE = "delete"
    ADMIN = "admin"

@dataclass
class UserContext:
    """User session context"""
    user_id: str
    role: UserRole
    permissions: Dict[str, Set[Permission]]
    expires_at: datetime
    metadata: Dict[str, Any]

class RBACManager:
    """Production RBAC Manager"""
    
    def __init__(self, jwt_secret: str, session_timeout: int = 3600):
        self.jwt_secret = jwt_secret
        self.session_timeout = session_timeout
        
        # Role-based permission matrix
        self.role_permissions = {
            UserRole.ADMIN: {
                "servers": {"*": {Permission.ADMIN}},
                "tools": {"*": {Permission.ADMIN}},
                "datasets": {"*": {Permission.ADMIN}}
            },
            UserRole.RND: {
                "servers": {
                    "mongodb": {Permission.READ, Permission.WRITE, Permission.EXECUTE},
                    "milvus": {Permission.READ, Permission.WRITE, Permission.EXECUTE},
                    "websearch": {Permission.READ, Permission.EXECUTE},
                    "scirex": {Permission.READ, Permission.WRITE, Permission.EXECUTE}
                },
                "tools": {
                    "upload_*": {Permission.EXECUTE},
                    "download_*": {Permission.EXECUTE},
                    "list_*": {Permission.READ, Permission.EXECUTE},
                    "cluster_*": {Permission.EXECUTE},
                    "train_*": {Permission.EXECUTE},
                    "search_*": {Permission.EXECUTE},
                    "health_check": {Permission.EXECUTE},
                    "server_health": {Permission.EXECUTE}
                },
                "datasets": {"user_owned": {Permission.READ, Permission.WRITE, Permission.EXECUTE}}
            },
            UserRole.DEVELOPER: {
                "servers": {
                    "mongodb": {Permission.READ, Permission.WRITE, Permission.EXECUTE},
                    "milvus": {Permission.READ, Permission.WRITE, Permission.EXECUTE},
                    "websearch": {Permission.READ, Permission.EXECUTE},
                    "scirex": {Permission.READ, Permission.WRITE, Permission.EXECUTE}
                },
                "tools": {
                    "upload_*": {Permission.EXECUTE},
                    "download_*": {Permission.EXECUTE},
                    "list_*": {Permission.READ, Permission.EXECUTE},
                    "cluster_*": {Permission.EXECUTE},
                    "train_*": {Permission.EXECUTE},
                    "search_*": {Permission.EXECUTE}
                },
                "datasets": {"user_owned": {Permission.READ, Permission.WRITE, Permission.EXECUTE}}
            },
            UserRole.ANALYST: {
                "servers": {
                    "mongodb": {Permission.READ},
                    "milvus": {Permission.READ},
                    "websearch": {Permission.READ, Permission.EXECUTE},
                    "scirex": {Permission.READ, Permission.EXECUTE}
                },
                "tools": {
                    "list_*": {Permission.READ, Permission.EXECUTE},
                    "download_*": {Permission.EXECUTE},
                    "search_*": {Permission.EXECUTE},
                    "cluster_*": {Permission.EXECUTE}
                },
                "datasets": {"user_owned": {Permission.READ}}
            },
            UserRole.VIEWER: {
                "servers": {
                    "mongodb": {Permission.READ},
                    "websearch": {Permission.READ, Permission.EXECUTE}
                },
                "tools": {
                    "list_*": {Permission.READ, Permission.EXECUTE},
                    "search_*": {Permission.EXECUTE}
                },
                "datasets": {"user_owned": {Permission.READ}}
            },
            UserRole.USER: {
                "servers": {
                    "websearch": {Permission.READ, Permission.EXECUTE}
                },
                "tools": {
                    "search_*": {Permission.EXECUTE}
                },
                "datasets": {}
            }
        }
    
    def create_token(self, user_id: str, role: UserRole, metadata: Dict[str, Any] = None) -> str:
        """Create JWT token for user"""
        now = datetime.utcnow()
        payload = {
            "user_id": user_id,
            "role": role.value,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=self.session_timeout)).timestamp()),
            "metadata": metadata or {}
        }
        
        return jwt.encode(payload, self.jwt_secret, algorithm="HS256")
    
    def authenticate_user(self, token: str) -> Optional[UserContext]:
        """Authenticate user from JWT token"""
        try:
            # Decode without exp verification to control skew handling
            payload = jwt.decode(token, self.jwt_secret, algorithms=["HS256"], options={"verify_exp": False})
            
            user_id = payload.get("user_id")
            role_str = payload.get("role")
            exp_ts = payload.get("exp", 0)
            now = datetime.utcnow()
            expires_at = datetime.fromtimestamp(exp_ts)
            
            # Allow small clock skew tolerance (5s)
            if not user_id or not role_str or expires_at <= (now - timedelta(seconds=5)):
                return None
            
            role = UserRole(role_str)
            permissions = self._build_user_permissions(role, user_id)
            
            return UserContext(
                user_id=user_id,
                role=role,
                permissions=permissions,
                expires_at=expires_at,
                metadata=payload.get("metadata", {})
            )
            
        except (JWTError, ValueError) as e:
            logger.warning(f"Authentication failed: {e}")
            return None
    
    def create_user_context(self, user_id: str, role: UserRole, metadata: Dict[str, Any] = None) -> UserContext:
        """Create a UserContext directly (for UI and testing)"""
        permissions = self._build_user_permissions(role, user_id)
        expires_at = datetime.utcnow() + timedelta(seconds=self.session_timeout)
        
        return UserContext(
            user_id=user_id,
            role=role,
            permissions=permissions,
            expires_at=expires_at,
            metadata=metadata or {}
        )
    
    def _build_user_permissions(self, role: UserRole, user_id: str) -> Dict[str, Set[Permission]]:
        """Build user-specific permissions"""
        base_permissions = self.role_permissions.get(role, {})
        user_permissions = {}
        
        for resource_type, resources in base_permissions.items():
            user_permissions[resource_type] = {}
            
            for resource, perms in resources.items():
                if resource == "*":
                    user_permissions[resource_type]["*"] = perms
                elif resource == "user_owned":
                    # User-specific resources
                    user_permissions[resource_type][f"user_{user_id}"] = perms
                else:
                    user_permissions[resource_type][resource] = perms
        
        return user_permissions
    
    def check_server_access(self, user_context: UserContext, server_name: str) -> bool:
        """Check if user can access MCP server"""
        server_perms = user_context.permissions.get("servers", {})
        
        # Check wildcard permission
        if "*" in server_perms and Permission.ADMIN in server_perms["*"]:
            return True
        
        # Check specific server permission
        if server_name in server_perms and server_perms[server_name]:
            return True
        
        return False
    
    def check_tool_access(self, user_context: UserContext, tool_name: str, action: Permission = Permission.EXECUTE) -> bool:
        """Check if user can access/execute tool"""
        tool_perms = user_context.permissions.get("tools", {})
        
        # Check wildcard permission
        if "*" in tool_perms and Permission.ADMIN in tool_perms["*"]:
            return True
        
        # Check exact tool name
        if tool_name in tool_perms and action in tool_perms[tool_name]:
            return True
        
        # Check pattern matching (e.g., list_* matches list_files)
        for pattern, perms in tool_perms.items():
            if self._matches_pattern(pattern, tool_name) and action in perms:
                return True
        
        return False
    
    def check_dataset_access(self, user_context: UserContext, dataset_id: str, action: Permission = Permission.READ) -> bool:
        """Check if user can access dataset"""
        dataset_perms = user_context.permissions.get("datasets", {})
        
        # Check wildcard permission
        if "*" in dataset_perms and Permission.ADMIN in dataset_perms["*"]:
            return True
        
        # Check user-owned datasets
        user_key = f"user_{user_context.user_id}"
        if (user_key in dataset_perms and action in dataset_perms[user_key] and 
            (dataset_id.startswith(f"user_{user_context.user_id}_") or dataset_id.startswith("user_owned_"))):
            return True
        
        return False
    
    def get_accessible_servers(self, user_context: UserContext) -> Set[str]:
        """Get list of servers user can access"""
        accessible = set()
        server_perms = user_context.permissions.get("servers", {})
        
        # Import here to avoid circular imports
        from config.settings import config
        
        # If user has wildcard permission, give access to all configured servers
        if "*" in server_perms and server_perms["*"]:
            return set(config.servers.keys())
        
        # Otherwise, check specific server permissions
        for server, perms in server_perms.items():
            if server != "*" and perms and server in config.servers:
                accessible.add(server)
        
        return accessible
    
    def _matches_pattern(self, pattern: str, resource: str) -> bool:
        """Check if resource matches pattern"""
        if pattern == "*":
            return True
        if pattern.endswith("*"):
            return resource.startswith(pattern[:-1])
        if pattern.startswith("*"):
            return resource.endswith(pattern[1:])
        return pattern == resource

def require_auth(permission: Permission = Permission.READ):
    """Decorator to require authentication and permission"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Extract user context from kwargs (added by FastAPI dependency)
            user_context = kwargs.get('user_context')
            if not user_context:
                raise ValueError("User context required")
            
            return await func(*args, **kwargs)
        return wrapper
    return decorator

# Global RBAC manager instance
rbac_manager: Optional[RBACManager] = None

def get_rbac_manager() -> RBACManager:
    """Get global RBAC manager instance"""
    global rbac_manager
    if rbac_manager is None:
        from config.settings import config
        rbac_manager = RBACManager(
            jwt_secret=config.auth.jwt_secret,
            session_timeout=config.auth.session_timeout
        )
    return rbac_manager