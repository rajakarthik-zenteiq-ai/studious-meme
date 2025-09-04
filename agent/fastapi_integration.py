"""
FastAPI Integration for MCP Agent
Handles streaming and context management
Optimized: reduced per-token overhead and removed artificial delays.
"""
import asyncio
import json
import uuid
import time
from typing import Optional, Dict, Any, List, AsyncGenerator
from datetime import datetime
import logging
import httpx

from fastapi import Request, HTTPException
from utils.auth_utils import UserRole
from .mcp_agent import MCPAgent
from langchain_core.messages import HumanMessage

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
    """Generate Server-Sent Events stream.
    Optimization:
      - Remove fixed sleep delay
      - Send in sentence / chunk groups for fewer network flushes
      - Avoid redundant agent.initialize if already initialized with same role
    """
    try:
        agent = await initialize_services()
        if getattr(agent, 'user_role', None) != user_role:
            await agent.initialize(user_role)
        # Build initial state and run graph directly (avoids chat wrapper)
        initial_state = {
            "messages": [HumanMessage(content=query)],
            "user_id": user_id,
            "conversation_id": conversation_id,
            "user_role": user_role,
        }
        if attachments is not None:
            initial_state["attachments"] = attachments
        config = {
            "configurable": {"thread_id": f"{user_id}:{conversation_id}"},
            "recursion_limit": 50,
        }
        result = await agent.graph.ainvoke(initial_state, config=config)
        # Extract final message content
        last_message = result["messages"][-1]
        response = last_message.content if hasattr(last_message, 'content') else getattr(last_message, 'content', '')
        # Chunk by ~40 words to balance latency vs overhead
        words = response.split()
        chunk_size = 40
        for i in range(0, len(words), chunk_size):
            group = words[i:i+chunk_size]
            is_last = i + chunk_size >= len(words)
            chunk_text = " ".join(group) + (" " if not is_last else "")
            chunk_data = {
                "id": str(uuid.uuid4()),
                "event": "message",
                "data": json.dumps({
                    "content": chunk_text,
                    "role": "assistant",
                    "finish_reason": None if not is_last else "stop",
                }),
            }
            yield f"data: {json.dumps(chunk_data)}\n\n"
        # Final sentinel
        yield "data: {\"content\": \"\", \"role\": \"assistant\", \"finish_reason\": \"stop\"}\n\n"
        yield "data: [DONE]\n\n"
    except Exception as e:
        logger.error(f"Error in SSE stream: {e}")
        error_data = {"content": f"Error processing query: {str(e)}", "role": "assistant", "finish_reason": "error"}
        yield f"data: {json.dumps(error_data)}\n\n"
        yield "data: [DONE]\n\n"

async def handle_chat_request(
    request: Request,
    message: str,
    attachments: Optional[List[Dict]] = None,
    provider: str = "openai",
    model: str = "gpt-4o-mini"
) -> Dict[str, Any]:
    """Handle chat request and return response using LangGraph agent directly."""
    context = await get_user_context_from_headers(request)
    user_id = context["user_id"]
    conversation_id = context["conversation_id"]
    user_role = context["user_role"]
    try:
        agent = await initialize_services()
        if agent.user_role != user_role:
            await agent.initialize(user_role)
        initial_state = {
            "messages": [HumanMessage(content=message)],
            "user_id": user_id,
            "conversation_id": conversation_id,
            "user_role": user_role,
        }
        if attachments is not None:
            initial_state["attachments"] = attachments
        config = {
            "configurable": {"thread_id": f"{user_id}:{conversation_id}"},
            "recursion_limit": 50,
        }
        result = await agent.graph.ainvoke(initial_state, config=config)
        last_message = result["messages"][-1]
        response = last_message.content if hasattr(last_message, 'content') else getattr(last_message, 'content', '')
        return {
            "success": True,
            "response": response,
            "user_id": user_id,
            "conversation_id": conversation_id,
            "user_role": user_role.value,
        }
    except Exception as e:
        logger.error(f"Error handling chat request: {e}")
        return {
            "success": False,
            "error": str(e),
            "user_id": user_id,
            "conversation_id": conversation_id,
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
    """Process chat message using the LangGraph agent directly."""
    try:
        agent = await initialize_services()
        if agent.user_role != user_role:
            await agent.initialize(user_role)
        initial_state = {
            "messages": [HumanMessage(content=query)],
            "user_id": user_id,
            "conversation_id": conversation_id,
            "user_role": user_role,
        }
        if attachments is not None:
            initial_state["attachments"] = attachments
        config = {
            "configurable": {"thread_id": f"{user_id}:{conversation_id}"},
            "recursion_limit": 50,
        }
        result = await agent.graph.ainvoke(initial_state, config=config)
        last_message = result["messages"][-1]
        return last_message.content if hasattr(last_message, 'content') else getattr(last_message, 'content', '')
    except Exception as e:
        logger.error(f"Error processing chat message: {e}")
        return f"Error processing your request: {str(e)}"

# --- MCP Tool utilities for API layer ---
async def list_mcp_tools(user_role: UserRole = UserRole.VIEWER) -> List[Dict[str, Any]]:
    """List available MCP tools with schemas (dynamic discovery)."""
    agent = await initialize_services()
    if agent.user_role != user_role:
        await agent.initialize(user_role)
    # Refresh to pick up dynamic updates
    try:
        await agent.refresh_tools()
    except Exception:
        pass
    return await agent.list_available_tools()

async def call_mcp_tool(tool_name: str, arguments: Dict[str, Any], user_role: UserRole = UserRole.VIEWER) -> Any:
    """Call a specific MCP tool via adapters (JSON-RPC handled by adapter)."""
    agent = await initialize_services()
    if agent.user_role != user_role:
        await agent.initialize(user_role)
    return await agent.invoke_tool(tool_name, arguments or {}, user_role=user_role)

# Raw JSON-RPC MCP sessions per server
_raw_mcp_sessions: Dict[str, Dict[str, str]] = {}

async def _get_accessible_server_configs(user_role: UserRole) -> Dict[str, Dict[str, str]]:
    """Use MCPAgent's role-aware server filtering to obtain server URLs."""
    agent = await initialize_services()
    if agent.user_role != user_role:
        await agent.initialize(user_role)
    return agent._filter_servers_by_role(user_role)  # type: ignore[attr-defined]

async def _mcp_http_initialize(server_url: str, client: httpx.AsyncClient) -> str:
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream, application/json"}
    initialize_request = {
        "jsonrpc": "2.0",
        "id": f"init_{server_url.rstrip('/').split('/')[-1]}",
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {"listChanged": True}},
            "clientInfo": {"name": "mcp-fastapi", "version": "1.0.0"},
        },
    }
    init_response = await client.post(server_url, json=initialize_request, headers=headers)
    init_response.raise_for_status()
    session_id = init_response.headers.get("mcp-session-id")
    if not session_id:
        raise RuntimeError("MCP server did not return mcp-session-id")
    initialized_request = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    session_headers = {**headers, "mcp-session-id": session_id}
    await client.post(server_url, json=initialized_request, headers=session_headers)
    return session_id

async def _ensure_sessions(user_role: UserRole) -> Dict[str, Dict[str, str]]:
    servers = await _get_accessible_server_configs(user_role)
    async with httpx.AsyncClient(timeout=10.0) as client:
        for name, cfg in servers.items():
            if name in _raw_mcp_sessions and _raw_mcp_sessions[name].get("session_id"):
                continue
            try:
                sid = await _mcp_http_initialize(cfg["url"], client)
                _raw_mcp_sessions[name] = {"url": cfg["url"], "session_id": sid}
            except Exception as e:
                logger.warning(f"Raw MCP initialize failed for {name}: {e}")
    return _raw_mcp_sessions

async def _parse_mcp_response(resp: httpx.Response) -> Dict[str, Any]:
    if resp.headers.get("Content-Type", "").startswith("text/event-stream") or resp.text.startswith("event:"):
        # Basic SSE parse: take first data: line
        try:
            lines = [ln for ln in resp.text.splitlines() if ln.startswith("data: ")]
            if lines:
                return json.loads(lines[0][6:])
        except Exception:
            pass
        return {"error": "Unable to parse SSE response", "raw": resp.text}
    try:
        return resp.json()
    except Exception:
        return {"error": "Invalid JSON response", "raw": resp.text}

async def list_mcp_tools_raw(user_role: UserRole = UserRole.VIEWER) -> Dict[str, Any]:
    """List tools from all accessible servers via raw JSON-RPC (tools/list)."""
    await _ensure_sessions(user_role)
    results: Dict[str, Any] = {"role": user_role.value, "servers": {}}
    headers_base = {"Content-Type": "application/json", "Accept": "text/event-stream, application/json"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        for name, sess in _raw_mcp_sessions.items():
            url = sess["url"]
            sid = sess.get("session_id")
            if not sid:
                results["servers"][name] = {"error": "No session"}
                continue
            headers = {**headers_base, "mcp-session-id": sid}
            req = {"jsonrpc": "2.0", "id": f"tools_list_{name}", "method": "tools/list", "params": {}}
            try:
                resp = await client.post(url, json=req, headers=headers)
                data = await _parse_mcp_response(resp)
                results["servers"][name] = data.get("result", data)
            except Exception as e:
                results["servers"][name] = {"error": str(e)}
    return results

async def call_mcp_tool_raw(server: str, tool_name: str, arguments: Dict[str, Any], user_role: UserRole = UserRole.VIEWER) -> Dict[str, Any]:
    """Call a tool on a specific server via raw JSON-RPC (tools/call)."""
    await _ensure_sessions(user_role)
    if server not in _raw_mcp_sessions:
        raise ValueError(f"Unknown or inaccessible server: {server}")
    url = _raw_mcp_sessions[server]["url"]
    sid = _raw_mcp_sessions[server].get("session_id")
    if not sid:
        raise RuntimeError(f"No active MCP session for server: {server}")
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream, application/json", "mcp-session-id": sid}
    req = {
        "jsonrpc": "2.0",
        "id": f"tools_call_{server}_{tool_name}",
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments or {}},
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, json=req, headers=headers)
        return await _parse_mcp_response(resp)

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
