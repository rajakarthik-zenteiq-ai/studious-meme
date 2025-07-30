"""
FastAPI Integration for MCP Agent
Handles streaming and context management
"""
import asyncio
import json
import uuid
import time
from typing import Optional, Dict, Any, List, AsyncGenerator
from datetime import datetime
import logging

from fastapi import Request, HTTPException
from utils.auth_utils import UserRole
from .mcp_agent import MCPAgent

logger = logging.getLogger(__name__)

# Global agent instance
_agent_instance = None

async def initialize_services() -> MCPAgent:
    """Initialize MCP agent services"""
    global _agent_instance
    if _agent_instance is None:
        _agent_instance = MCPAgent()
        await _agent_instance.initialize(UserRole.ADMIN, "system_user")
    return _agent_instance

async def get_user_context_from_headers(request: Request) -> Dict[str, str]:
    """Extract user context from request headers with RBAC"""
    user_id = request.headers.get("X-User-ID", "demo_user")
    conversation_id = request.headers.get("X-Conversation-ID", str(uuid.uuid4()))
    user_role_str = request.headers.get("X-User-Role", "VIEWER")
    
    # Convert string to UserRole enum
    try:
        user_role = UserRole(user_role_str.upper())
    except ValueError:
        user_role = UserRole.VIEWER
    
    return {
        "user_id": user_id,
        "conversation_id": conversation_id,
        "user_role": user_role
    }

async def generate_sse_stream(
    query: str,
    user_id: str,
    conversation_id: str,
    attachments: Optional[List[Dict]] = None,
    provider: str = "openai",
    model: str = "gpt-4o-mini",
    user_role: UserRole = UserRole.VIEWER
) -> AsyncGenerator[Dict[str, Any], None]:
    """Generate Server-Sent Events stream"""
    
    try:
        agent = await initialize_services()
        
        # Re-initialize agent with user's role if different
        if agent.user_role != user_role:
            await agent.initialize(user_role)
        
        # Process the query
        response = await agent.analyze(
            query=query,
            user_id=user_id,
            conversation_id=conversation_id,
            user_role=user_role,
            attachments=attachments
        )
        
        # Stream the response in chunks
        words = response.split()
        for i, word in enumerate(words):
            chunk_data = {
                "id": str(uuid.uuid4()),
                "event": "message",
                "data": json.dumps({
                    "content": word + " ",
                    "role": "assistant",
                    "finish_reason": None if i < len(words) - 1 else "stop"
                })
            }
            yield f"data: {json.dumps(chunk_data)}\n\n"
            await asyncio.sleep(0.05)  # Small delay for streaming effect
            
        # Final chunk
        yield f"data: {json.dumps({'content': '', 'role': 'assistant', 'finish_reason': 'stop'})}\n\n"
        yield "data: [DONE]\n\n"
        
    except Exception as e:
        logger.error(f"Error in SSE stream: {e}")
        error_data = {
            "content": f"Error processing query: {str(e)}",
            "role": "assistant", 
            "finish_reason": "error"
        }
        yield f"data: {json.dumps(error_data)}\n\n"
        yield "data: [DONE]\n\n"

async def handle_chat_request(
    request: Request,
    message: str,
    attachments: Optional[List[Dict]] = None,
    provider: str = "openai",
    model: str = "gpt-4o-mini"
) -> Dict[str, Any]:
    """Handle chat request and return response"""
    
    context = await get_user_context_from_headers(request)
    user_id = context["user_id"]
    conversation_id = context["conversation_id"] 
    user_role = context["user_role"]
    
    try:
        agent = await initialize_services()
        
        # Re-initialize agent with user's role if different
        if agent.user_role != user_role:
            await agent.initialize(user_role)
            
        response = await agent.analyze(
            query=message,
            user_id=user_id,
            conversation_id=conversation_id,
            user_role=user_role,
            attachments=attachments
        )
        
        return {
            "success": True,
            "response": response,
            "user_id": user_id,
            "conversation_id": conversation_id,
            "user_role": user_role.value
        }
        
    except Exception as e:
        logger.error(f"Error handling chat request: {e}")
        return {
            "success": False,
            "error": str(e),
            "user_id": user_id,
            "conversation_id": conversation_id
        }

async def handle_conversation_request(
    request: Request,
    conversation_id: str,
    limit: int = 50,
    offset: int = 0,
    order: str = "desc"
) -> Dict[str, Any]:
    """Handle conversation history request"""
    
    context = await get_user_context_from_headers(request)
    user_id = context["user_id"]
    
    try:
        agent = await initialize_services()
        
        # Get conversation history from memory manager
        history = await agent.memory_manager.get_conversation_history(
            user_id=user_id,
            conversation_id=conversation_id,
            limit=limit,
            offset=offset
        )
        
        return {
            "success": True,
            "conversation_id": conversation_id,
            "messages": history,
            "total_messages": len(history),
            "limit": limit,
            "offset": offset
        }
        
    except Exception as e:
        logger.error(f"Error getting conversation history: {e}")
        return {
            "success": False,
            "error": str(e),
            "conversation_id": conversation_id
        }

async def process_chat_message(
    query: str,
    user_id: str,
    conversation_id: str,
    user_role: UserRole = UserRole.VIEWER,
    attachments: Optional[List[Any]] = None
) -> str:
    """Process chat message with proper context"""
    
    try:
        agent = await initialize_services()
        
        # Re-initialize agent with user's role if different
        if agent.user_role != user_role:
            await agent.initialize(user_role)
            
        response = await agent.analyze(
            query=query,
            user_id=user_id,
            conversation_id=conversation_id,
            user_role=user_role,
            attachments=attachments
        )
        
        return response
        
    except Exception as e:
        logger.error(f"Error processing chat message: {e}")
        return f"Error processing your request: {str(e)}"

async def get_conversation_history(
    user_id: str,
    conversation_id: str,
    limit: int = 50,
    offset: int = 0
) -> List[Dict[str, Any]]:
    """Get conversation history"""
    
    try:
        agent = await initialize_services()
        
        history = await agent.memory_manager.get_conversation_history(
            user_id=user_id,
            conversation_id=conversation_id,
            limit=limit,
            offset=offset
        )
        
        return history
        
    except Exception as e:
        logger.error(f"Error getting conversation history: {e}")
        return []

async def health_check() -> Dict[str, Any]:
    """Health check endpoint"""
    try:
        agent = await initialize_services()
        
        return {
            "status": "healthy",
            "agent_initialized": agent._initialized,
            "user_role": agent.user_role.value if hasattr(agent, 'user_role') else None,
            "tools_count": len(agent.tools) if agent.tools else 0,
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }

async def cleanup():
    """Cleanup resources"""
    global _agent_instance
    if _agent_instance:
        await _agent_instance.cleanup()
        _agent_instance = None
