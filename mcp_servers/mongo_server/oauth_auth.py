"""
Fallback OAuth authenticator for mongo_server Docker container
"""
import os
from typing import Dict, Any

class FallbackAuthenticator:
    """Fallback authenticator when utils module is not available"""
    
    def __init__(self):
        self.oauth_enabled = os.getenv("OAUTH_ENABLED", "false").lower() == "true"
        print(f"FallbackAuthenticator initialized. OAuth enabled: {self.oauth_enabled}")
    
    def validate_request(self, request: Any = None) -> Dict[str, Any]:
        """
        Validate request and extract user info
        Returns minimal user info for Docker environment
        """
        return {
            "user_id": "anonymous",
            "authenticated": False,
            "oauth_enabled": self.oauth_enabled
        }
    
    def extract_user_from_context(self, context: Any = None) -> Dict[str, Any]:
        """
        Extract user info from context
        Fallback for Docker environment
        """
        # Try to extract from headers if available
        user_id = "anonymous"
        if context and hasattr(context, 'headers'):
            # Look for user info in headers
            user_id = context.headers.get('x-user-id', 'anonymous')
        
        return {
            "user_id": user_id,
            "authenticated": user_id != "anonymous",
            "oauth_enabled": self.oauth_enabled
        }

# Create global authenticator instance
authenticator = FallbackAuthenticator()
