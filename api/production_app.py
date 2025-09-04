"""
Production FastAPI Application
Clean API with proper RBAC, streaming, and error handling
"""
import asyncio
import json
import uuid
import logging
from typing import Dict, Any, List, Optional, AsyncGenerator
from datetime import datetime, timedelta, timedelta

from fastapi import FastAPI, Request, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

# Local imports - corrected based on existing codebase
from config.settings import config
from agent.mcp_agent import MCPAgent
from agent.rbac_system import RBACManager, UserContext, UserRole
from agent.memory_manager import MemoryManager

logger = logging.getLogger(__name__)

# Global instances
_agent_instance: Optional[MCPAgent] = None
_rbac_manager: Optional[RBACManager] = None
_memory_manager: Optional[MemoryManager] = None

async def get_agent() -> MCPAgent:
    """Get or create global agent instance"""
    global _agent_instance
    if _agent_instance is None:
        _agent_instance = MCPAgent()
        # Initialize with system admin context
        system_context = UserContext(
            user_id="system_user",
            role=UserRole.ADMIN,
            permissions={},
            expires_at=datetime.utcnow() + timedelta(days=365),
            metadata={"system": True}
        )
        await _agent_instance.initialize(system_context)
    return _agent_instance

def get_rbac_manager() -> RBACManager:
    """Get global RBAC manager instance"""
    global _rbac_manager
    if _rbac_manager is None:
        _rbac_manager = RBACManager(
            jwt_secret=config.auth.jwt_secret,
            session_timeout=config.auth.session_timeout
        )
    return _rbac_manager

async def get_memory_manager() -> MemoryManager:
    """Get global memory manager instance"""
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = MemoryManager()
        await _memory_manager.initialize()
    return _memory_manager

# Request/Response Models
class ChatRequest(BaseModel):
    message: str = Field(..., description="User message")
    conversation_id: Optional[str] = Field(None, description="Conversation ID") 
    stream: bool = Field(False, description="Enable streaming")

class ToolRequest(BaseModel):
    arguments: Dict[str, Any] = Field(default_factory=dict)

class TokenRequest(BaseModel):
    user_id: str
    role: str
    metadata: Optional[Dict[str, Any]] = None

# Dependencies
async def get_user_context(request: Request) -> UserContext:
    """Extract and authenticate user from request"""
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization header"
        )
    
    token = auth_header.split(" ")[1]
    rbac = get_rbac_manager()
    user_context = rbac.authenticate_user(token)
    
    if not user_context:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token"
        )
    
    return user_context

# Create FastAPI app
def create_app() -> FastAPI:
    """Create and configure FastAPI application"""
    
    app = FastAPI(
        title="Production MCP Agent API",
        description="Clean MCP Agent with RBAC and dynamic tool discovery",
        version="2.0.0",
        docs_url="/docs" if config.debug else None,
        redoc_url="/redoc" if config.debug else None
    )
    
    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Configure appropriately for production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    @app.on_event("startup")
    async def startup():
        """Initialize services on startup"""
        logger.info("Starting MCP Agent API...")
        logger.info(f"Environment: {config.environment.value}")
        logger.info(f"Available servers: {list(config.servers.keys())}")
        
        # Initialize services
        await get_agent()
        await get_memory_manager()
    
    @app.on_event("shutdown")
    async def shutdown():
        """Cleanup on shutdown"""
        logger.info("Shutting down MCP Agent API...")
        try:
            agent = await get_agent()
            await agent.cleanup()
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
    
    # Health check
    @app.get("/health")
    async def health():
        """Health check endpoint"""
        try:
            agent = await get_agent()
            # Use agent health check method
            status_info = await agent.get_health_status()
            
            return {
                "status": "healthy",
                "timestamp": datetime.utcnow().isoformat(),
                "environment": config.environment.value,
                "agent": status_info
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat()
            }
    
    # Authentication endpoint
    @app.post("/auth/token")
    async def create_token(request: TokenRequest):
        """Create authentication token"""
        try:
            role = UserRole(request.role.lower())
            
            rbac = get_rbac_manager()
            token = rbac.create_token(
                user_id=request.user_id,
                role=role,
                metadata=request.metadata
            )
            
            return {
                "access_token": token,
                "token_type": "bearer",
                "expires_in": config.auth.session_timeout,
                "user_id": request.user_id,
                "role": role.value
            }
            
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid role: {e}"
            )
        except Exception as e:
            logger.error(f"Token creation failed: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create token"
            )
    
    # Chat endpoint
    @app.post("/chat")
    async def chat(
        request: ChatRequest,
        user_context: UserContext = Depends(get_user_context)
    ):
        """Process chat message with optional streaming"""
        conversation_id = request.conversation_id or str(uuid.uuid4())
        
        try:
            agent = await get_agent()
            
            if request.stream:
                # Return streaming response
                return StreamingResponse(
                    _generate_stream(
                        agent,
                        request.message,
                        conversation_id,
                        user_context
                    ),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                    }
                )
            else:
                # Regular response - use process_message method
                response = await agent.process_message(
                    message=request.message,
                    conversation_id=conversation_id,
                    user_context=user_context
                )
                
                return {
                    "success": True,
                    "response": response,
                    "conversation_id": conversation_id,
                    "user_id": user_context.user_id,
                    "timestamp": datetime.utcnow().isoformat()
                }
                
        except Exception as e:
            logger.error(f"Chat error: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e)
            )
    
    async def _generate_stream(
        agent: MCPAgent,
        message: str,
        conversation_id: str,
        user_context: UserContext
    ) -> AsyncGenerator[str, None]:
        """Generate streaming response"""
        try:
            # Use existing streaming functionality from fastapi_integration
            from agent.fastapi_integration import generate_sse_stream
            
            async for chunk in generate_sse_stream(
                query=message,
                user_id=user_context.user_id,
                conversation_id=conversation_id,
                user_role=user_context.role
            ):
                yield chunk
            
        except Exception as e:
            error_data = {"error": str(e), "finish_reason": "error"}
            yield f"data: {json.dumps(error_data)}\n\n"
            yield "data: [DONE]\n\n"
    
    # Tools endpoints
    @app.get("/tools")
    async def list_tools(user_context: UserContext = Depends(get_user_context)):
        """List available tools"""
        try:
            agent = await get_agent()
            tools = await agent.list_available_tools(user_context)
            
            return {
                "success": True,
                "tools": tools,
                "count": len(tools),
                "user_role": user_context.role.value
            }
            
        except Exception as e:
            logger.error(f"List tools error: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e)
            )
    
    @app.post("/tools/{tool_name}")
    async def execute_tool(
        tool_name: str,
        request: ToolRequest,
        user_context: UserContext = Depends(get_user_context)
    ):
        """Execute tool directly"""
        try:
            agent = await get_agent()
            result = await agent.execute_tool_direct(
                tool_name=tool_name,
                arguments=request.arguments,
                user_context=user_context
            )
            
            return {
                "success": True,
                "tool": tool_name,
                "result": result,
                "timestamp": datetime.utcnow().isoformat()
            }
            
        except PermissionError as e:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=str(e)
            )
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e)
            )
        except Exception as e:
            logger.error(f"Tool execution error: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e)
            )
    
    # Conversation endpoints
    @app.get("/conversations")
    async def list_conversations(
        limit: int = 20,
        offset: int = 0,
        user_context: UserContext = Depends(get_user_context)
    ):
        """List user conversations"""
        try:
            memory = await get_memory_manager()
            
            conversations = await memory.list_user_conversations(
                user_id=user_context.user_id,
                limit=limit,
                offset=offset
            )
            
            return {
                "success": True,
                "conversations": conversations,
                "count": len(conversations)
            }
            
        except Exception as e:
            logger.error(f"List conversations error: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e)
            )
    
    @app.get("/conversations/{conversation_id}")
    async def get_conversation(
        conversation_id: str,
        limit: int = 50,
        user_context: UserContext = Depends(get_user_context)
    ):
        """Get conversation messages"""
        try:
            memory = await get_memory_manager()
            
            messages = await memory.get_conversation_history(
                user_id=user_context.user_id,
                conversation_id=conversation_id,
                limit=limit
            )
            
            return {
                "success": True,
                "conversation_id": conversation_id,
                "messages": messages,
                "count": len(messages)
            }
            
        except Exception as e:
            logger.error(f"Get conversation error: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e)
            )
    
    @app.delete("/conversations/{conversation_id}")
    async def delete_conversation(
        conversation_id: str,
        user_context: UserContext = Depends(get_user_context)
    ):
        """Delete conversation"""
        try:
            memory = await get_memory_manager()
            
            deleted = await memory.delete_conversation(
                user_id=user_context.user_id,
                conversation_id=conversation_id
            )
            
            if not deleted:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Conversation not found"
                )
            
            return {"success": True, "message": "Conversation deleted"}
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Delete conversation error: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e)
            )
    
    # Admin endpoints
    @app.post("/admin/tools/refresh")
    async def refresh_tools(user_context: UserContext = Depends(get_user_context)):
        """Refresh tools (admin only)"""
        if user_context.role.value != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access required"
            )
        
        try:
            agent = await get_agent()
            count = await agent.refresh_tools(user_context)
            
            return {
                "success": True,
                "message": f"Refreshed {count} tools",
                "tools_count": count
            }
            
        except Exception as e:
            logger.error(f"Refresh tools error: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e)
            )
    
    return app

# Application instance
app = create_app()
