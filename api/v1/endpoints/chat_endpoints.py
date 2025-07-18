"""
FastAPI Integration Layer for Agent
This module handles the interface between your existing API Gateway and the Agent
"""
import asyncio
import json
import uuid
import time
from typing import Optional, Dict, Any, List, AsyncGenerator
from datetime import datetime
import logging

from fastapi import Request, HTTPException
from sse_starlette.sse import EventSourceResponse
from pydantic import BaseModel

from agent.fastapi_integration import (
    initialize_services,
    handle_chat_request,
    handle_conversation_request,
    process_chat_message,
    get_conversation_history,
    health_check,
    cleanup
)

# Re-export the functions for backward compatibility
__all__ = [
    'initialize_services',
    'handle_chat_request',
    'handle_conversation_request',
    'process_chat_message',
    'get_conversation_history',
    'health_check',
    'cleanup'
]

async def get_user(request: Request) -> Dict[str, str]:
    """Extract user_id and conversation_id from request headers"""
    user_id = request.headers.get("X-User-ID")
    conversation_id = request.headers.get("X-Conversation-ID")
    
    if not user_id:
        raise HTTPException(status_code=400, detail="X-User-ID header is required")
    
    if not conversation_id:
        raise HTTPException(status_code=400, detail="X-Conversation-ID header is required")
    
    return {
        "user_id": user_id,
        "conversation_id": conversation_id
    }

async def ChatCompletion(
    request: Request,
    message: str,
    attachments: Optional[List[Dict]] = None,
    provider: str = "openai",
    model: str = "gpt-4o-mini"
) -> EventSourceResponse:
    """
    Handle chat request from existing API Gateway
    Expects user_id and conversation_id in headers
    """
    # Get user context from headers
    context = await get_user_context_from_headers(request)
    user_id = context["user_id"]
    conversation_id = context["conversation_id"]
    
    # Create SSE generator
    async def event_generator():
        async for chunk in generate_sse_stream(
            query=message,
            user_id=user_id,
            conversation_id=conversation_id,
            attachments=attachments,
            provider=provider,
            model=model
        ):
            yield chunk
    
    # Return SSE response
    return EventSourceResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )

async def handle_conversation_request(
    request: Request,
    conversation_id: str,
    limit: int = 50,
    offset: int = 0,
    order: str = "desc"
) -> Dict[str, Any]:
    """
    Handle conversation retrieval request
    Expects user_id in headers for authorization
    """
    global memory_manager_instance
    
    if not memory_manager_instance:
        await initialize_services()
    
    # Get user_id from headers for authorization
    user_id = request.headers.get("X-User-ID")
    if not user_id:
        raise HTTPException(status_code=400, detail="X-User-ID header is required")
    
    try:
        # Get conversation from memory manager
        conversation = await memory_manager_instance.get_conversation(
            conversation_id=conversation_id,
            limit=limit,
            offset=offset,
            order=order
        )
        
        # Verify user access
        if conversation["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        return conversation
        
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error retrieving conversation: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

# Utility functions for direct usage in existing API Gateway

async def process_chat_message(
    message: str,
    conversation_id: str,
    attachments: Optional[List[Dict]] = None,
    provider: str = "openai",
    model: str = "gpt-4o-mini"
) -> AsyncGenerator[str, None]:
    """
    Direct function to process chat messages
    Can be called from your existing API Gateway
    """
    async for chunk in generate_sse_stream(
        query=message,
        user_id=get_user["user_id"],
        conversation_id=get_user["conversation_id"],
        attachments=attachments,
        provider=provider,
        model=model
    ):
        yield chunk

async def get_conversation_history(
    conversation_id: str,
    limit: int = 50,
    offset: int = 0,
    order: str = "desc"
) -> Dict[str, Any]:
    """
    Direct function to get conversation history
    Can be called from your existing API Gateway
    """
    global memory_manager_instance
    
    if not memory_manager_instance:
        await initialize_services()
    
    conversation = await memory_manager_instance.get_conversation(
        user_id=get_user["user_id"],
        conversation_id=conversation_id,
        limit=limit,
        offset=offset,
        order=order
    )
    
    # Verify user access
    if conversation["user_id"] != user_id:
        raise ValueError("Access denied")
    
    return conversation

# Health check function
async def health_check() -> Dict[str, Any]:
    """Check health of agent services"""
    global agent_instance, memory_manager_instance
    
    status = {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "services": {
            "agent": "healthy" if agent_instance else "not_initialized",
            "memory_manager": "healthy" if memory_manager_instance else "not_initialized"
        }
    }
    
    if agent_instance:
        # Check MCP server connections
        mcp_status = await agent_instance.mcp_client.get_server_status()
        status["mcp_servers"] = {
            server_id: "healthy" if info["connected"] else "disconnected"
            for server_id, info in mcp_status.items()
        }
    
    return status

# Cleanup function
async def cleanup():
    """Cleanup resources on shutdown"""
    global agent_instance, memory_manager_instance
    
    if memory_manager_instance:
        await memory_manager_instance.close()
    
    if agent_instance and agent_instance.mcp_client:
        await agent_instance.mcp_client.close()
    
    logger.info("Services cleaned up")
