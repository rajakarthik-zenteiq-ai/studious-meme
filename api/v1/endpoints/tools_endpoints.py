"""
Adapter-based MCP tool endpoints using RBAC from headers.
"""
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from agent.fastapi_integration import (
    list_mcp_tools,
    call_mcp_tool,
    get_user_context_from_headers,
)
from utils.auth_utils import auth_manager, AccessLevel

router = APIRouter(prefix="/tools", tags=["tools"]) 

class ToolCallRequest(BaseModel):
    arguments: Optional[Dict[str, Any]] = None

# Heuristics to infer action type for a tool name
_DEF_READ = ("get_", "list_", "download", "search", "query", "inspect", "health", "stats")
_DEF_WRITE = ("upload", "store", "append", "insert", "create", "update", "start")
_DEF_EXEC = ("train", "predict", "perform", "analy", "cluster", "run", "execute")

# Heuristics to map tool to likely servers
_DEF_SERVER_MAP = [
    ("mongodb", ("log", "chat", "file", "system")),
    ("milvus", ("vector", "collection", "milvus")),
    ("websearch", ("search",)),
    ("scirex", ("train", "predict", "cluster")),
]

def _infer_required_action(tool_name: str) -> str:
    name = tool_name.lower()
    if "delete" in name:
        return "delete"
    if any(k in name for k in _DEF_EXEC):
        return "execute"
    if any(k in name for k in _DEF_WRITE):
        return "write"
    return "read"

def _guess_servers_for_tool(tool_name: str, user_role) -> List[str]:
    name = tool_name.lower()
    candidates: List[str] = []
    for server, keys in _DEF_SERVER_MAP:
        if any(k in name for k in keys):
            candidates.append(server)
    if not candidates:
        # Fallback to all servers user can access
        perms = auth_manager.role_permissions.get(user_role, {})
        candidates = [s for s, lvl in perms.get("servers", {}).items() if lvl != AccessLevel.NONE]
    return candidates

@router.get("")
async def list_tools(request: Request) -> List[Dict[str, Any]]:
    context = await get_user_context_from_headers(request)
    user_role = context["user_role"]
    tools = await list_mcp_tools(user_role=user_role)
    # Filter by per-tool permissions (read-level for visibility)
    allowed: List[Dict[str, Any]] = []
    for t in tools:
        name = (t.get("name") or "").lower()
        if not name:
            continue
        servers = _guess_servers_for_tool(name, user_role)
        if any(auth_manager.check_tool_permission(user_role, srv, name, "read") for srv in servers):
            allowed.append(t)
    return allowed

@router.post("/{tool}")
async def call_tool_endpoint(request: Request, tool: str, body: ToolCallRequest) -> Dict[str, Any] | Any:
    context = await get_user_context_from_headers(request)
    user_role = context["user_role"]
    tool_name = tool.lower()
    required_action = _infer_required_action(tool_name)
    servers = _guess_servers_for_tool(tool_name, user_role)
    if not any(auth_manager.check_tool_permission(user_role, srv, tool_name, required_action) for srv in servers):
        raise HTTPException(status_code=403, detail=f"Forbidden: {user_role.value} lacks '{required_action}' permission for tool '{tool}'")
    return await call_mcp_tool(tool, (body.arguments or {}), user_role=user_role)
