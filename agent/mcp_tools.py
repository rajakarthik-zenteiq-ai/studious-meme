"""
MCP Tool wrappers for LangChain
"""
from typing import List, Dict, Any
import json
import logging

from langchain_core.tools import BaseTool
from langchain_core.pydantic_v1 import BaseModel, Field

from mcp_client.client import MCPLogAnalyticsClient

logger = logging.getLogger(__name__)

class MCPTool(BaseTool):
    """Wrapper for MCP tools"""
    
    def __init__(self, name: str, description: str, mcp_client: MCPLogAnalyticsClient, 
                 server: str, tool_name: str, args_schema=None):
        super().__init__(
            name=name,
            description=description,
            args_schema=args_schema
        )
        self.mcp_client = mcp_client
        self.server = server
        self.tool_name = tool_name
        self.return_direct = False
    
    def _run(self, *args, **kwargs) -> str:
        """Synchronous run (not used)"""
        raise NotImplementedError("Use async methods")
    
    async def _arun(self, *args, **kwargs) -> str:
        """Run the tool asynchronously"""
        try:
            # Handle both positional and keyword arguments
            if args and not kwargs:
                # If only positional args, assume it's a query string
                if len(args) == 1 and isinstance(args[0], str):
                    kwargs = {"query": args[0]}
                else:
                    kwargs = {}
            
            # Call the MCP tool
            result = await self.mcp_client.call_tool(self.server, self.tool_name, **kwargs)
            
            # Format result for agent
            if isinstance(result, dict):
                if result.get("success") is False:
                    return f"Error: {result.get('error', 'Unknown error')}"
                return json.dumps(result, indent=2)
            else:
                return str(result)
                
        except Exception as e:
            logger.error(f"Error calling {self.server}.{self.tool_name}: {e}")
            return f"Error: {str(e)}"

# Schema definitions for tools
class LogQuerySchema(BaseModel):
    query: str = Field(..., description="Search query for logs")
    limit: int = Field(50, description="Maximum number of results")

class DatasetUploadSchema(BaseModel):
    user_id: str = Field(..., description="User ID")
    filename: str = Field(..., description="Filename")
    content: str = Field(..., description="Base64 encoded content")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")

class KMeansSchema(BaseModel):
    dataset_id: str = Field(..., description="Dataset ID from upload")
    n_clusters: int = Field(3, description="Number of clusters")

class NeuralNetSchema(BaseModel):
    dataset_id: str = Field(..., description="Dataset ID from upload")
    target: str = Field(..., description="Target column name")
    hidden_layers: List[int] = Field([64, 32], description="Hidden layer sizes")
    epochs: int = Field(10, description="Training epochs")

class WebSearchSchema(BaseModel):
    query: str = Field(..., description="Search query")
    limit: int = Field(10, description="Number of results")  # Changed from top_k to limit to match server

async def create_mcp_tools(mcp_client: MCPLogAnalyticsClient) -> List[MCPTool]:
    """Create all MCP tools"""
    tools = []
    
    # MongoDB tools
    tools.extend([
        MCPTool(
            name="search_logs",
            description="Search through logs using text search",
            mcp_client=mcp_client,
            server="mongodb",
            tool_name="search_logs",
            args_schema=LogQuerySchema
        ),
        MCPTool(
            name="get_logs_by_date",
            description="Get logs for a specific date (YYYY-MM-DD)",
            mcp_client=mcp_client,
            server="mongodb",
            tool_name="get_logs_by_date"
        ),
        MCPTool(
            name="get_system_stats",
            description="Get system statistics and database information",
            mcp_client=mcp_client,
            server="mongodb",
            tool_name="get_system_stats"
        )
    ])
    
    # SciREX tools
    tools.extend([
        MCPTool(
            name="upload_dataset",
            description="Upload a dataset for machine learning analysis",
            mcp_client=mcp_client,
            server="scirex",
            tool_name="upload_dataset",
            args_schema=DatasetUploadSchema
        ),
        MCPTool(
            name="train_kmeans",
            description="Train K-means clustering on a dataset",
            mcp_client=mcp_client,
            server="scirex",
            tool_name="train_kmeans",
            args_schema=KMeansSchema
        ),
        MCPTool(
            name="train_neural_network",
            description="Train a neural network for regression or classification",
            mcp_client=mcp_client,
            server="scirex",
            tool_name="train_neural_net",
            args_schema=NeuralNetSchema
        ),
        MCPTool(
            name="list_datasets",
            description="List all uploaded datasets for a user",
            mcp_client=mcp_client,
            server="scirex",
            tool_name="list_datasets"
        )
    ])
    
    # WebSearch tools
    tools.extend([
        MCPTool(
            name="web_search",
            description="Search the web for information",
            mcp_client=mcp_client,
            server="websearch",
            tool_name="web_search",  # Fix: should match the @mcp.tool() function name
            args_schema=WebSearchSchema
        )
    ])
    
    return tools