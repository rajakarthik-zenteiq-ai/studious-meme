"""
Pydantic models for agent
"""
from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional

class FileUploadRequest(BaseModel):
    """File upload request"""
    filename: str = Field(..., description="Filename")
    content: str = Field(..., description="Base64 encoded content")
    content_type: str = Field(default="application/octet-stream")
    user_id: str = Field(..., description="User ID")
    user_role: Optional[str] = Field(None, description="User role (e.g., admin, user)")
    metadata: Dict[str, Any] = Field(default_factory=dict)