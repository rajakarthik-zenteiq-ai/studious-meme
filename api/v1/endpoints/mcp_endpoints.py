"""
MCP raw JSON-RPC endpoints bridged to agent.fastapi_integration raw client.
"""
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, Request, HTTPException
from agent.fastapi_integration import list_mcp_tools_raw, call_mcp_tool_raw, get_user_context_from_headers
from utils.auth_utils import auth_manager, AccessLevel

router = APIRouter(prefix="/mcp", tags=["mcp"])

# Reuse simple heuristics from tools adapter handler
_DEF_READ = ("get_", "list_", "download", "search", "query", "inspect", "health", "stats")
_DEF_WRITE = ("upload", "store", "append", "insert", "create", "update", "start")
_DEF_EXEC = ("train", "predict", "perform", "analy", "cluster", "run", "execute")
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
        perms = auth_manager.role_permissions.get(user_role, {})
        candidates = [s for s, lvl in perms.get("servers", {}).items() if lvl != AccessLevel.NONE]
    return candidates

@router.get("/tools")
async def list_tools(request: Request) -> Dict[str, Any]:
    context = await get_user_context_from_headers(request)
    user_role = context["user_role"]
    raw = await list_mcp_tools_raw(user_role=user_role)
    # Filter visibility per server and tool (read-level)
    servers_out: Dict[str, Any] = {}
    for name, payload in (raw.get("servers") or {}).items():
        if not auth_manager.check_server_access(user_role, name):
            continue
        # payload may be {result: {...}} already flattened by helper; accept list under 'tools' or pass-through
        tools = payload.get("tools") if isinstance(payload, dict) else None
        if isinstance(tools, list):
            filtered = []
            for t in tools:
                tname = (t.get("name") or "").lower()
                if tname and any(auth_manager.check_tool_permission(user_role, srv, tname, "read") for srv in _guess_servers_for_tool(tname, user_role)):
                    filtered.append(t)
            servers_out[name] = {**payload, "tools": filtered}
        else:
            servers_out[name] = payload
    return {"role": user_role.value, "servers": servers_out}

@router.post("/tools/{server}/{tool}")
async def call_tool(request: Request, server: str, tool: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    context = await get_user_context_from_headers(request)
    user_role = context["user_role"]
    # Enforce per-tool permission
    required_action = _infer_required_action(tool)
    if not auth_manager.check_tool_permission(user_role, server, tool, required_action):
        raise HTTPException(status_code=403, detail=f"Forbidden: {user_role.value} lacks '{required_action}' permission for tool '{tool}' on server '{server}'")
    return await call_mcp_tool_raw(server, tool, arguments or {}, user_role=user_role)
