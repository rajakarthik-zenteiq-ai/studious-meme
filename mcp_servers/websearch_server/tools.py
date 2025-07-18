"""
Tools for the OpenAI Web Search MCP Server
"""

from typing import List, Optional
from pydantic import BaseModel
from pydantic_extra_types.timezone_name import TimeZoneName
from typing import Literal

class UserLocation(BaseModel):
    """User location for localized search results"""
    type: Literal["approximate"] = "approximate"
    city: str
    country: Optional[str] = None
    region: Optional[str] = None
    timezone: TimeZoneName

class WebSearchTool:
    """Tool for performing web searches using OpenAI's web search functionality"""
    
    name = "web_search"
    description = "It allows AI assistants to search the web during conversations with users"
    
    def __init__(self):
        self.supported_models = ["gpt-4o", "gpt-4o-mini"]
        self.supported_types = ["web_search_preview", "web_search_preview_2025_03_11"]
        self.supported_context_sizes = ["low", "medium", "high"]
    
    def validate_input(self, input: str) -> bool:
        """Validate search input"""
        return bool(input and input.strip())
    
    def validate_model(self, model: str) -> bool:
        """Validate model selection"""
        return model in self.supported_models
    
    def validate_search_type(self, search_type: str) -> bool:
        """Validate search type"""
        return search_type in self.supported_types
    
    def validate_context_size(self, context_size: str) -> bool:
        """Validate context size"""
        return context_size in self.supported_context_sizes

class SummarizeTextTool:
    """Tool for summarizing text content using OpenAI models"""
    
    name = "summarize_text"
    description = "Summarize text content using OpenAI's models"
    
    def __init__(self):
        self.supported_models = ["gpt-4o", "gpt-4o-mini"]
        self.max_text_length = 50000  # Maximum text length for summarization
        self.max_summary_length = 1000  # Maximum summary length
    
    def validate_text(self, text: str) -> bool:
        """Validate text input"""
        return bool(text and len(text) <= self.max_text_length)
    
    def validate_model(self, model: str) -> bool:
        """Validate model selection"""
        return model in self.supported_models
    
    def validate_max_length(self, max_length: int) -> bool:
        """Validate maximum length parameter"""
        return 50 <= max_length <= self.max_summary_length
