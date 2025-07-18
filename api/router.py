"""
Main API Router - Includes all API versions
"""
from fastapi import APIRouter
from .v1.router import router as v1_router

# Create main API router
router = APIRouter(prefix="/api")

# Include v1 router
router.include_router(v1_router)

# Add other versions here as needed
# router.include_router(v2_router)
