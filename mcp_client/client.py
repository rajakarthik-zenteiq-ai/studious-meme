"""
Enhanced MCP Client for Server Communication
"""
import os
import sys
import json
import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from contextlib import AsyncExitStack

# Project imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Third-party imports
import httpx
import base64
import uuid

from pydantic import BaseModel
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client

# LangChain imports for MCPClient class only
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MCPServer(BaseModel):
    """MCP Server configuration"""
    name: str
    url: str
    description: str
    tools: List[str]

class MCPLogAnalyticsClient:
    """
    Unified MCP client for all servers with enhanced capabilities
    """
    
    def __init__(self):
        # Server configurations with actual available tools
        self.servers = {
            "mongodb": MCPServer(
                name="MongoDB Server",
                url=os.getenv("MONGODB_MCP_URL", "http://localhost:8100/mcp/"),
                description="Log storage and retrieval",
                tools=["store_logs", "get_logs_by_date", "query_logs", "search_logs", 
                      "aggregate_logs", "append_chat", "get_chat_history", "upload_file", 
                      "download_file", "delete_logs", "get_system_stats", "health_check"]
            ),
            "milvus": MCPServer(
                name="Milvus Server",
                url=os.getenv("MILVUS_MCP_URL", "http://localhost:8110/mcp/"),
                description="Vector similarity search",
                tools=["create_collection", "insert_vectors", "search_similar", "get_collection_stats"]
            ),
            "websearch": MCPServer(
                name="WebSearch Server",
                url=os.getenv("WEBSEARCH_MCP_URL", "http://localhost:8140/mcp/"),
                description="Web search using OpenAI",
                tools=["web_search", "health_check"]
            ),
            "scirex": MCPServer(
                name="SciREX Server",
                url=os.getenv("SCIREX_MCP_URL", "http://localhost:8150/mcp/"),
                description="Scientific computing and ML",
                tools=["train_neural_network", "perform_clustering", "predict", "list_models"]
            )
        }
        
        # HTTP client
        self.client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
        
        # Connection status
        self.connected_servers = {}
    
    async def initialize(self):
        """Initialize connections to all MCP servers"""
        logger.info("Initializing MCP client...")
        
        # Test connections to all servers
        for server_id, server in self.servers.items():
            try:
                # For FastMCP servers, try to connect to the base URL
                base_url = server.url.replace('/mcp/', '')  # Get base URL
                response = await self.client.get(base_url, timeout=5.0)
                
                # Any response (even 404) means server is running
                if response.status_code < 500:
                    self.connected_servers[server_id] = True
                    logger.info(f"✅ Connected to {server.name}")
                else:
                    self.connected_servers[server_id] = False
                    logger.warning(f"❌ Failed to connect to {server.name}: HTTP {response.status_code}")
                    
            except Exception as e:
                self.connected_servers[server_id] = False
                logger.error(f"❌ Error connecting to {server.name}: All connection attempts failed")
        
        connected_count = sum(self.connected_servers.values())
        logger.info(f"Connected to {connected_count}/{len(self.servers)} MCP servers")
    
    async def _call_tool(self, server_id: str, tool_name: str, **kwargs) -> Dict[str, Any]:
        """Call a tool on an MCP server via FastMCP HTTP transport"""
        if not self.connected_servers.get(server_id, False):
            raise Exception(f"Server {server_id} is not connected")
        
        server = self.servers[server_id]
        
        try:
            import uuid
            session_id = str(uuid.uuid4())
            
            # Step 1: Initialize session with the server using the /mcp/ endpoint
            init_request = {
                "jsonrpc": "2.0",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "roots": {
                            "listChanged": True
                        },
                        "sampling": {}
                    },
                    "clientInfo": {
                        "name": "MCP-LogAnalytics-Client",
                        "version": "1.0.0"
                    }
                },
                "id": f"init_{session_id}"
            }
            
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Session-ID": session_id,
                "X-Session-ID": session_id,
                "User-Agent": "MCP-Client/1.0"
            }
            
            logger.info(f"Initializing session {session_id} for {server_id}")
            
            # Initialize session on the /mcp/ endpoint
            init_response = await self.client.post(
                server.url,  # This should be http://localhost:8140/mcp
                json=init_request,
                headers=headers,
                timeout=30.0
            )
            
            logger.info(f"Init response status: {init_response.status_code}")
            if init_response.status_code != 200:
                logger.error(f"Session initialization failed: {init_response.text}")
                return {"success": False, "error": f"Session init failed: {init_response.status_code}"}
            
            # Extract the session ID from the response if provided
            response_headers = dict(init_response.headers)
            server_session_id = response_headers.get("mcp-session-id", session_id)
            
            logger.info(f"Server provided session ID: {server_session_id}")
            
            # Update headers with the server-provided session ID
            headers["Session-ID"] = server_session_id
            headers["X-Session-ID"] = server_session_id
            headers["MCP-Session-ID"] = server_session_id  # Try FastMCP specific header
            
            # Step 1.5: Send initialized notification (required by MCP protocol)
            initialized_request = {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {}
            }
            
            logger.info(f"Sending initialized notification for session {server_session_id}")
            initialized_response = await self.client.post(
                server.url,
                json=initialized_request,
                headers=headers,
                timeout=30.0
            )
            
            logger.info(f"Initialized response status: {initialized_response.status_code}")
            
            logger.info(f"Updated headers: {headers}")
            
            # Step 2: Call the tool with the established session
            tool_request = {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": kwargs
                },
                "id": f"{server_id}_{tool_name}_{datetime.utcnow().timestamp()}"
            }
            
            logger.info(f"Calling {tool_name} on {server_id} with session {server_session_id}")
            logger.info(f"Tool request: {tool_request}")
            
            response = await self.client.post(
                server.url,  # Use the same /mcp/ endpoint
                json=tool_request,
                headers=headers,
                timeout=60.0
            )
            
            logger.info(f"Tool call response status: {response.status_code}")
            logger.info(f"Response headers: {dict(response.headers)}")
            logger.info(f"Response content: {response.text[:500]}")  # First 500 chars
            
            if response.status_code == 200:
                try:
                    # Check if response is SSE format
                    content_type = response.headers.get("content-type", "")
                    response_text = response.text
                    
                    if "text/event-stream" in content_type:
                        # Parse SSE format
                        logger.info("Parsing SSE response")
                        lines = response_text.strip().split('\n')
                        json_data = None
                        
                        for line in lines:
                            if line.startswith('data: '):
                                json_str = line[6:]  # Remove 'data: ' prefix
                                try:
                                    json_data = json.loads(json_str)
                                    break
                                except json.JSONDecodeError:
                                    continue
                        
                        if json_data:
                            result = json_data
                        else:
                            return {"success": False, "error": "Could not parse SSE data"}
                    else:
                        # Parse regular JSON
                        result = response.json()
                    
                    logger.info(f"Parsed result: {result}")
                    
                    if "result" in result:
                        tool_result = result["result"]
                        if isinstance(tool_result, dict) and "content" in tool_result:
                            # Extract content from MCP tool result
                            content = tool_result["content"]
                            if isinstance(content, list) and content:
                                # Get text from the first content item
                                first_content = content[0]
                                if isinstance(first_content, dict) and "text" in first_content:
                                    text_content = first_content["text"]
                                    # Try to parse as JSON if it looks like JSON
                                    if text_content.startswith('[') or text_content.startswith('{'):
                                        try:
                                            return {"success": True, "results": json.loads(text_content)}
                                        except:
                                            return {"success": True, "results": text_content}
                                    else:
                                        return {"success": True, "results": text_content}
                                else:
                                    return {"success": True, "results": first_content}
                            else:
                                return {"success": True, "results": content}
                        else:
                            return {"success": True, "results": tool_result}
                    elif "error" in result:
                        return {"success": False, "error": result["error"]["message"]}
                    else:
                        return {"success": False, "error": "No result in response"}
                except Exception as e:
                    logger.error(f"Error parsing response: {e}")
                    return {"success": True, "results": response.text}
            else:
                logger.error(f"Response headers: {dict(response.headers)}")
                logger.error(f"Response text: {response.text}")
                return {"success": False, "error": f"HTTP {response.status_code}: {response.text}"}
                
        except Exception as e:
            logger.error(f"Error calling {tool_name} on {server_id}: {e}")
            return {"success": False, "error": f"Tool call error: {str(e)}"}

    # MongoDB operations
    async def store_logs(self, logs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Store log entries in MongoDB"""
        return await self._call_tool("mongodb", "store_logs", logs=logs)
    
    async def get_logs_by_date(self, date: str, level: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get logs by date from MongoDB"""
        result = await self._call_tool("mongodb", "get_logs_by_date", date=date, level=level)
        return result.get("logs", [])
    
    async def get_system_stats(self) -> Dict[str, Any]:
        """Get MongoDB system statistics"""
        return await self._call_tool("mongodb", "get_system_stats")
    
    async def health_check(self, server_id: str = "mongodb") -> Dict[str, Any]:
        """Get health check for specified server"""
        return await self._call_tool(server_id, "health_check")
    
    async def append_chat(self, user_id: str, conversation_id: str, message: str, role: str = "user") -> Dict[str, Any]:
        """Append chat message to conversation"""
        return await self._call_tool(
            "mongodb", 
            "append_chat",
            user_id=user_id,
            conversation_id=conversation_id,
            message=message,
            role=role
        )
    
    async def upload_file(self, user_id: str, filename: str, content_base64: str, metadata: Optional[Dict] = None) -> Dict[str, Any]:
        """Upload file to MongoDB GridFS"""
        return await self._call_tool(
            "mongodb",
            "upload_file",
            user_id=user_id,
            filename=filename,
            content_base64=content_base64,
            metadata=metadata
        )
    
    async def store_file(self, user_id: str, filename: str, content: bytes, metadata: Optional[Dict] = None) -> Dict[str, Any]:
        """Store file in MongoDB GridFS"""
        content_base64 = base64.b64encode(content).decode()
        return await self._call_tool(
            "mongodb",
            "upload_file",
            user_id=user_id,
            filename=filename,
            content_base64=content_base64,
            metadata=metadata
        )
    
    # Vector search operations
    async def search_similar_vectors(self, collection_name: str, query_vectors: List[List[float]], top_k: int = 10) -> List[Dict[str, Any]]:
        """Search similar vectors in Milvus"""
        result = await self._call_tool(
            "milvus",
            "search_similar",
            collection_name=collection_name,
            query_vectors=query_vectors,
            top_k=top_k
        )
        return result.get("results", [])
    
    async def cluster_logs(self, dataset_id: str, n_clusters: int = 5) -> Dict[str, Any]:
        """Cluster logs using ML"""
        return await self._call_tool(
            "scirex",
            "perform_clustering",
            dataset_id=dataset_id,
            n_clusters=n_clusters,
            method="kmeans"
        )
    
    # ML operations
    async def train_neural_network(
        self,
        dataset_id: str,
        task_type: str = "classification",
        hidden_layers: Optional[List[int]] = None,
        epochs: int = 100
    ) -> Dict[str, Any]:
        """Train neural network model"""
        return await self._call_tool(
            "scirex",
            "train_neural_network",
            dataset_id=dataset_id,
            task_type=task_type,
            hidden_layers=hidden_layers or [128, 64],
            epochs=epochs
        )
    
    async def perform_clustering(
        self,
        dataset_id: str,
        n_clusters: int = 5,
        method: str = "kmeans"
    ) -> Dict[str, Any]:
        """Perform clustering analysis"""
        return await self._call_tool(
            "scirex",
            "perform_clustering",
            dataset_id=dataset_id,
            n_clusters=n_clusters,
            method=method
        )
    
    # Web search operations
    async def web_search(self, query: str, max_results: int = 10) -> List[Dict[str, Any]]:
        """Search the web"""
        result = await self._call_tool(
            "websearch",
            "web_search",
            query=query,
            limit=max_results
        )
        
        # Handle various response formats
        if isinstance(result, dict):
            if result.get("success", False):
                results = result.get("results", [])
                # Handle both list and string results
                if isinstance(results, str):
                    try:
                        # Try to parse as JSON if it's a string
                        import json
                        results = json.loads(results)
                    except:
                        # If not JSON, wrap in a list
                        results = [{"title": "Web Search Result", "content": results}]
                elif isinstance(results, list):
                    return results
                else:
                    results = [{"title": "Web Search Result", "content": str(results)}]
                return results
            else:
                logger.error(f"Web search failed: {result.get('error', 'Unknown error')}")
                return []
        
        return []
    
    async def summarize_text(self, text: str, style: str = "concise", max_length: int = 150) -> str:
        """Summarize text using AI - Note: summarize_text tool has been removed from websearch server"""
        # Since summarize_text tool was removed from websearch server, 
        # we'll use the LLM directly for summarization
        try:
            # Import LLM factory
            from pathlib import Path
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from agent.llm_providers import LLMProviderFactory
            
            llm_factory = LLMProviderFactory()
            llm = llm_factory.get_llm("openai", model="gpt-4o-mini")
            
            prompt = f"Summarize the following text in a {style} style, maximum {max_length} words:\n\n{text}"
            response = await llm.ainvoke(prompt)
            
            if hasattr(response, 'content'):
                content = response.content
                if isinstance(content, list):
                    return "\n".join(str(x) for x in content)
                return str(content)
            return str(response)
            
        except Exception as e:
            logger.error(f"Summarization failed: {e}")
            return f"Error: Unable to summarize text - {str(e)}"
    
    # Utility methods
    async def get_server_status(self) -> Dict[str, Dict[str, Any]]:
        """Get status of all MCP servers"""
        status = {}
        
        for server_id, server in self.servers.items():
            status[server_id] = {
                "name": server.name,
                "connected": self.connected_servers.get(server_id, False),
                "url": server.url,
                "tools": server.tools
            }
        
        return status
    
    async def close(self):
        """Close HTTP client"""
        await self.client.aclose()

class MCPClient:
    """
    Enhanced MCP Client with agent integration support
    This class integrates with the agent module for LLM-based tool calling
    """
    
    def __init__(self, provider: str = "openai", model: str = "gpt-4o-mini"):
        """
        Initialize the MCP client with LLM provider
        
        Args:
            provider: LLM provider (openai, gemini, vllm)
            model: Model name to use
        """
        self.provider = provider
        self.model = model
        self.exit_stack = AsyncExitStack()
        
        # Initialize MCP connections
        self.mcp_client = MCPLogAnalyticsClient()
        self.agent = None
        
        # Store sessions for different connection types
        self.sessions = {}
        self.stdio_connections = {}
    
    async def initialize(self):
        """Initialize all MCP server connections and agent"""
        await self.mcp_client.initialize()
        
        # Initialize the agent from the agent module instead of duplicating it here
        try:
            from agent.agent import LogAnalyticsAgent
            self.agent = LogAnalyticsAgent(llm_provider=self.provider)
            await self.agent.initialize()
            logger.info(f"MCPClient initialized with provider: {self.provider}, model: {self.model}")
        except ImportError as e:
            logger.warning(f"Could not import agent: {e}. Agent functionality will not be available.")
    
    async def connect_to_server(self, server_script_path: str, connection_type: str = "stdio") -> str:
        """
        Connect to an MCP server using specified transport
        
        Args:
            server_script_path: Path to the server script
            connection_type: Type of connection ("stdio", "sse", "http")
            
        Returns:
            Connection ID for this server
        """
        connection_id = f"{server_script_path}_{connection_type}"
        
        try:
            if connection_type == "stdio":
                # STDIO connection
                server_params = StdioServerParameters(
                    command="python",
                    args=[server_script_path],
                )
                
                stdio_transport = await self.exit_stack.enter_async_context(
                    stdio_client(server_params)
                )
                stdio, write = stdio_transport
                session = await self.exit_stack.enter_async_context(
                    ClientSession(stdio, write)
                )
                
                await session.initialize()
                self.sessions[connection_id] = session
                self.stdio_connections[connection_id] = (stdio, write)
                
            elif connection_type == "sse":
                # SSE connection (requires server URL)
                server_url = f"http://localhost:8050/sse"  # Default URL
                
                sse_transport = await self.exit_stack.enter_async_context(
                    sse_client(server_url)
                )
                read_stream, write_stream = sse_transport
                session = await self.exit_stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )
                
                await session.initialize()
                self.sessions[connection_id] = session
                
            # List available tools for this server
            if connection_id in self.sessions:
                tools_result = await self.sessions[connection_id].list_tools()
                logger.info(f"Connected to server {server_script_path} with tools:")
                for tool in tools_result.tools:
                    logger.info(f"  - {tool.name}: {tool.description}")
            
            return connection_id
            
        except Exception as e:
            logger.error(f"Failed to connect to server {server_script_path}: {e}")
            raise
    
    async def get_mcp_tools(self) -> List[Dict[str, Any]]:
        """
        Get available tools from MCP servers in OpenAI format
        Returns:
            List of tools in OpenAI function calling format
        """
        tools = []
        # Get tools from all sessions
        for session_id, session in self.sessions.items():
            try:
                tools_result = await session.list_tools()
                for tool in tools_result.tools:
                    tools.append({
                        "type": "function", 
                        "function": {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": tool.inputSchema,
                        },
                    })
            except Exception as e:
                logger.warning(f"Failed to get tools from session {session_id}: {e}")
        return tools
    
    async def process_query(self, query: str) -> str:
        """
        Process a query using the agent with MCP tools
        
        Args:
            query: The user query
            
        Returns:
            The response from the agent
        """
        if self.agent:
            # Use the agent to process the query
            response = await self.agent.analyze_logs(query)
            if isinstance(response, list):
                return "\n".join(str(x) for x in response)
            return str(response)
        else:
            # Fallback to direct LLM call without tools
            from agent.llm_providers import LLMProviderFactory
            llm_factory = LLMProviderFactory()
            llm = llm_factory.get_llm(self.provider, model=self.model)
            
            messages = [HumanMessage(content=query)]
            response = await llm.ainvoke(messages)
            if isinstance(response, list):
                return "\n".join(str(x) for x in response)
            return str(response)
    
    async def call_tool_directly(self, connection_id: str, tool_name: str, **arguments) -> Dict[str, Any]:
        """
        Call a tool directly on a specific MCP server session
        
        Args:
            connection_id: The connection to use
            tool_name: Name of the tool to call
            **arguments: Tool arguments
            
        Returns:
            Tool execution result
        """
        if connection_id not in self.sessions:
            raise ValueError(f"Connection {connection_id} not found")
        
        session = self.sessions[connection_id]
        result = await session.call_tool(tool_name, arguments=arguments)
        
        return {
            "success": True,
            "content": result.content[0].text if result.content else "",
            "tool_name": tool_name,
            "arguments": arguments
        }
    
    async def cleanup(self):
        """Clean up all resources"""
        try:
            await self.mcp_client.close()
            await self.exit_stack.aclose()
            logger.info("MCPClient cleanup completed")
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

# Convenience functions for standalone usage
async def create_client() -> MCPLogAnalyticsClient:
    """Create and initialize MCP client"""
    client = MCPLogAnalyticsClient()
    await client.initialize()
    return client

async def create_mcp_client(provider: str = "openai", model: str = "gpt-4o-mini") -> MCPClient:
    """Create and initialize MCP client with agent support"""
    client = MCPClient(provider=provider, model=model)
    await client.initialize()
    return client

async def test_client():
    """Test MCP client functionality"""
    client = await create_client()
    
    try:
        # Test server status
        status = await client.get_server_status()
        print("Server Status:")
        for server_id, info in status.items():
            print(f"  {info['name']}: {'✅' if info['connected'] else '❌'}")
        
        # Test storing logs
        if status["mongodb"]["connected"]:
            print("\nTesting log storage...")
            result = await client.store_logs([
                {
                    "timestamp": datetime.utcnow().isoformat(),
                    "level": "INFO",
                    "message": "Test log entry",
                    "source": "mcp_client_test"
                }
            ])
            print(f"  Stored logs: {result}")
        
        # Test web search
        if status["websearch"]["connected"]:
            print("\nTesting web search...")
            results = await client.web_search("artificial intelligence news", max_results=3)
            print(f"  Found {len(results)} results")
        
    finally:
        await client.close()

if __name__ == "__main__":
    # Run the test function
    asyncio.run(test_client())
