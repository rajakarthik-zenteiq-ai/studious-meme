"""
API v1 Router - Main router for version 1 endpoints
"""
from fastapi import APIRouter
from .endpoints.chat_endpoints import router as chat_router
from .endpoints.mcp_endpoints import router as mcp_router
from .endpoints.tools_endpoints import router as tools_router

# Create main v1 router
router = APIRouter(prefix="/v1")

# Include chat and MCP routers
router.include_router(chat_router)
router.include_router(mcp_router)
router.include_router(tools_router)

# Add other routers here as needed
# router.include_router(other_router)
