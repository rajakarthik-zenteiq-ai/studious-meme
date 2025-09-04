"""
FastAPI Integration Layer for Agent
Provides SSE chat endpoint and re-exports core agent interaction helpers.
"""
import logging
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

# Import canonical implementations from fastapi_integration
from agent.fastapi_integration import (
    initialize_services,
    handle_chat_request,
    handle_conversation_request,
    process_chat_message,
    health_check,
    cleanup,
    get_user_context_from_headers,
    generate_sse_stream,
)

logger = logging.getLogger(__name__)

# APIRouter for chat endpoints
router = APIRouter(prefix="/chat", tags=["chat"])

class ChatRequest(BaseModel):
    message: str
    attachments: Optional[List[Dict[str, Any]]] = None
    provider: Optional[str] = "openai"
    model: Optional[str] = "gpt-4o-mini"

@router.get("/stream")
async def ChatCompletion(
    request: Request,
    message: str,
    provider: str = "openai",
    model: str = "gpt-4o-mini",
    attachments: Optional[List[Dict[str, Any]]] = None,
) -> EventSourceResponse:
    """Server-Sent Events streaming endpoint.
    Requires headers: X-User-ID, X-Conversation-ID (and optionally X-User-Role).
    """
    # Validate and extract user context (enforces required headers)
    context = await get_user_context_from_headers(request)
    user_id = context["user_id"]
    conversation_id = context["conversation_id"]
    user_role = context["user_role"]

    async def event_generator():
        # Stream already formatted SSE chunks
        async for chunk in generate_sse_stream(
            query=message,
            user_id=user_id,
            conversation_id=conversation_id,
            attachments=attachments,
            provider=provider,
            model=model,
            user_role=user_role
        ):
            yield chunk

    logger.debug(f"Starting SSE stream user={user_id} conv={conversation_id} role={user_role}")
    return EventSourceResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )

@router.post("")
async def chat(request: Request, body: ChatRequest) -> Dict[str, Any]:
    """Non-streaming chat endpoint returning a JSON response."""
    return await handle_chat_request(
        request,
        message=body.message,
        attachments=body.attachments,
        provider=body.provider or "openai",
        model=body.model or "gpt-4o-mini",
    )

@router.get("/health")
async def chat_health() -> Dict[str, Any]:
    return await health_check()

@router.get("/history/{conversation_id}")
async def get_history(
    request: Request,
    conversation_id: str,
    limit: int = 50,
    offset: int = 0,
    order: str = "desc",
) -> Dict[str, Any]:
    return await handle_conversation_request(
        request,
        conversation_id=conversation_id,
        limit=limit,
        offset=offset,
        order=order,
    )
