"""
Pydantic models for agent
"""
from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional

class AgentState(BaseModel):
    """Agent state structure"""
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    user_id: str = Field(..., description="User ID")
    conversation_id: str = Field(..., description="Conversation ID")
    metadata: Dict[str, Any] = Field(default_factory=dict)

class ChatRequest(BaseModel):
    """Chat request model"""
    query: str = Field(..., description="User query")
    user_id: str = Field(..., description="User ID")
    conversation_id: Optional[str] = Field(None, description="Conversation ID")
    provider: Optional[str] = Field("openai", description="LLM provider")
    attachments: Optional[List[Dict[str, Any]]] = Field(None, description="File attachments")

class FileUploadRequest(BaseModel):
    """File upload request"""
    filename: str = Field(..., description="Filename")
    content: str = Field(..., description="Base64 encoded content")
    content_type: str = Field(default="application/octet-stream")
    user_id: str = Field(..., description="User ID")
    metadata: Dict[str, Any] = Field(default_factory=dict)