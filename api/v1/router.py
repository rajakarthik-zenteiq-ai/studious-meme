"""
API v1 Router - Main router for version 1 endpoints
"""
from fastapi import APIRouter
from .chat_router import router as chat_router

# Create main v1 router
router = APIRouter(prefix="/v1")

# Include chat router
router.include_router(chat_router)

# Add other routers here as needed
# router.include_router(other_router)
