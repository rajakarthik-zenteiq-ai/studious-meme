"""
Basic OAuth 2.1 Authentication for MCP
Based on the Auth0 blog article recommendations
"""
import os
import logging
from typing import Optional, Dict, Any
from fastapi import Request, HTTPException

logger = logging.getLogger(__name__)

class MCPAuthenticator:
    """Simple OAuth authentication handler for MCP servers"""
    
    def __init__(self):
        self.oauth_enabled = os.getenv("OAUTH_ENABLED", "false").lower() == "true"
        self.oauth_issuer = os.getenv("OAUTH_ISSUER", "")
        self.oauth_client_id = os.getenv("OAUTH_CLIENT_ID", "")
        self.oauth_audience = os.getenv("OAUTH_AUDIENCE", "mcp-api")
        
        if self.oauth_enabled:
            logger.info(f"✅ OAuth enabled with issuer: {self.oauth_issuer}")
        else:
            logger.info("🔓 OAuth disabled - using anonymous authentication")
    
    def extract_user_id(self, request: Request) -> str:
        """Extract user ID from request headers or token"""
        
        if not self.oauth_enabled:
            return "anonymous"
        
        # Try to get user ID from various header formats
        headers = getattr(request, 'headers', {})
        
        # Standard OAuth headers
        authorization = headers.get('authorization') or headers.get('Authorization')
        if authorization:
            # In a real implementation, you would validate the JWT token here
            # For now, we'll just extract a simple user ID
            if authorization.startswith('Bearer '):
                token = authorization[7:]
                # This is a simplified approach - in production you'd decode/validate the JWT
                return f"user_{hash(token) % 10000}"
        
        # Fallback to custom headers
        user_id = (
            headers.get('X-User-ID') or 
            headers.get('x-user-id') or 
            headers.get('User-ID') or 
            headers.get('user-id')
        )
        
        if user_id:
            return str(user_id)
        
        # If OAuth is enabled but no auth found, use anonymous with warning
        if self.oauth_enabled:
            logger.warning("OAuth enabled but no user authentication found, using anonymous")
        
        return "anonymous"
    
    def validate_request(self, request: Request) -> Dict[str, Any]:
        """Validate OAuth request and return user context"""
        
        user_id = self.extract_user_id(request)
        
        context = {
            "user_id": user_id,
            "authenticated": user_id != "anonymous",
            "oauth_enabled": self.oauth_enabled
        }
        
        if self.oauth_enabled and user_id == "anonymous":
            logger.warning("Unauthenticated request in OAuth-enabled mode")
        
        return context

# Global authenticator instance
authenticator = MCPAuthenticator()
