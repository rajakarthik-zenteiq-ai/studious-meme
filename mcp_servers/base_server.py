"""
Base MCP Server with RBAC support
"""
from typing import Dict, Any, List, Optional, Callable
from functools import wraps
import logging
from mcp.server import Server
from mcp.server.models import Tool, TextContent
from mcp.types import CallToolRequest, CallToolResult

from config.rbac import rbac

logger = logging.getLogger(__name__)

def require_auth(tool_name: str):
    """Decorator to enforce RBAC on tools"""
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(user_id: str, user_role: str, *args, **kwargs):
            # Check if role has access to this tool
            server_name = kwargs.get('_server_name', 'unknown')
            
            if not rbac.is_tool_allowed(user_role, server_name, tool_name):
                raise PermissionError(
                    f"Role '{user_role}' does not have access to tool '{server_name}.{tool_name}'"
                )
            
            # Add user_id to kwargs for filtering
            kwargs['_user_id'] = user_id
            
            return await func(*args, **kwargs)
        
        wrapper._tool_name = tool_name
        wrapper._require_auth = True
        return wrapper
    
    return decorator

class BaseRBACServer:
    """Base class for MCP servers with RBAC support"""
    
    def __init__(self, name: str, version: str = "1.0.0"):
        self.name = name
        self.version = version
        self.server = Server(name, version)
        self.tools: Dict[str, Callable] = {}
        
    def register_tool(self, func: Callable, schema: Dict[str, Any]):
        """Register a tool with RBAC support"""
        tool_name = func._tool_name if hasattr(func, '_tool_name') else func.__name__
        
        # Add user_id and user_role to required fields
        if 'properties' not in schema:
            schema['properties'] = {}
            
        schema['properties']['user_id'] = {
            'type': 'string',
            'description': 'Authenticated user ID'
        }
        schema['properties']['user_role'] = {
            'type': 'string',
            'description': 'User role for access control'
        }
        
        if 'required' not in schema:
            schema['required'] = []
            
        if 'user_id' not in schema['required']:
            schema['required'].append('user_id')
        if 'user_role' not in schema['required']:
            schema['required'].append('user_role')
        
        # Create tool
        tool = Tool(
            name=tool_name,
            description=schema.get('description', ''),
            inputSchema=schema
        )
        
        # Store the function
        self.tools[tool_name] = func
        
        # Register with MCP server
        @self.server.list_tools()
        async def list_tools() -> List[Tool]:
            return [tool for tool_name, tool in self.tools.items()]
        
        @self.server.call_tool()
        async def call_tool(request: CallToolRequest) -> CallToolResult:
            if request.name not in self.tools:
                raise ValueError(f"Unknown tool: {request.name}")
            
            func = self.tools[request.name]
            arguments = request.arguments or {}
            
            # Add server name to context
            arguments['_server_name'] = self.name
            
            try:
                result = await func(**arguments)
                return CallToolResult(content=[TextContent(text=str(result))])
            except PermissionError as e:
                return CallToolResult(
                    content=[TextContent(text=f"Permission denied: {str(e)}")],
                    isError=True
                )
            except Exception as e:
                logger.error(f"Error in tool {request.name}: {e}")
                return CallToolResult(
                    content=[TextContent(text=f"Error: {str(e)}")],
                    isError=True
                )