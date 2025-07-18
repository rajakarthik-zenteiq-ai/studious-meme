"""
Enhanced MCP Client with LangGraph Agent Integration
"""
import os
import sys
import json
import base64
import asyncio
import uuid
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
import logging
from contextlib import AsyncExitStack

import httpx
import nest_asyncio
from pydantic import BaseModel
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client

# LangChain/LangGraph imports
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from langgraph.graph.message import add_messages

# Apply nest_asyncio to allow nested event loops
nest_asyncio.apply()

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
        # Server configurations
        self.servers = {
            "mongodb": MCPServer(
                name="MongoDB Server",
                url=os.getenv("MONGODB_MCP_URL", "http://localhost:8100/mcp"),
                description="Log storage and retrieval",
                tools=["store_logs", "get_logs_by_date", "append_chat", "upload_dataset", "store_file"]
            ),
            "milvus": MCPServer(
                name="Milvus Server",
                url=os.getenv("MILVUS_MCP_URL", "http://localhost:8110/mcp"),
                description="Vector similarity search",
                tools=["create_collection", "insert_vectors", "search_similar", "get_collection_stats"]
            ),
            "websearch": MCPServer(
                name="WebSearch Server",
                url=os.getenv("WEBSEARCH_MCP_URL", "http://localhost:8140/mcp"),
                description="Web search using OpenAI",
                tools=["web_search", "health_check"]
            ),
            "scirex": MCPServer(
                name="SciREX Server",
                url=os.getenv("SCIREX_MCP_URL", "http://localhost:8150/mcp"),
                description="Scientific computing and ML",
                tools=["train_neural_network", "perform_clustering", "predict", "list_models"]
            )
        }
        
        # HTTP client
        self.client = httpx.AsyncClient(timeout=30.0)
        
        # Connection status
        self.connected_servers = {}
    
    async def initialize(self):
        """Initialize connections to all MCP servers"""
        logger.info("Initializing MCP client...")
        
        # Test connections to all servers
        for server_id, server in self.servers.items():
            try:
                # For FastMCP servers, try to connect to the base URL
                base_url = server.url.replace('/mcp', '')  # Get base URL
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
        """Call a tool on an MCP server via streamable HTTP"""
        if not self.connected_servers.get(server_id, False):
            raise Exception(f"Server {server_id} is not connected")
        
        server = self.servers[server_id]
        
        # Create JSON-RPC request for MCP
        request = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": kwargs
            },
            "id": f"{server_id}_{tool_name}_{datetime.utcnow().timestamp()}"
        }
        
        try:
            # For FastMCP, we need to establish a session and handle streamable responses
            import uuid
            session_id = str(uuid.uuid4())
            
            # Send request to MCP server using POST with proper headers for FastMCP
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "X-Session-ID": session_id,
                "Session-ID": session_id,  # Add both header formats
                "User-Agent": "MCP-Client/1.0"
            }
            
            logger.info(f"Calling {tool_name} on {server_id} with session {session_id}")
            
            response = await self.client.post(
                f"{server.url}/tools/call",
                json=request,
                headers=headers,
                timeout=60.0
            )
            
            logger.info(f"Response status: {response.status_code}")
            if response.status_code != 200:
                logger.error(f"Response headers: {dict(response.headers)}")
                logger.error(f"Response text: {response.text}")
            
            if response.status_code == 200:
                # Handle both JSON and streaming responses
                content_type = response.headers.get("content-type", "")
                
                if "text/event-stream" in content_type:
                    # Handle Server-Sent Events
                    lines = response.text.strip().split('\n')
                    for line in lines:
                        if line.startswith('data: '):
                            try:
                                data = json.loads(line[6:])  # Remove 'data: ' prefix
                                if data.get("type") == "result":
                                    return data.get("content", {"success": False, "error": "No content"})
                            except json.JSONDecodeError:
                                continue
                    return {"success": False, "error": "No valid data in stream"}
                else:
                    # Handle regular JSON response
                    result = response.json()
                    return result.get("result", {"success": False, "error": "No result received"})
            else:
                logger.error(f"HTTP {response.status_code}: {response.text}")
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
    
    async def upload_dataset(self, user_id: str, filename: str, content_base64: str, metadata: Optional[Dict] = None) -> Dict[str, Any]:
        """Upload dataset to MongoDB"""
        return await self._call_tool(
            "mongodb",
            "upload_dataset",
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
            "store_file",
            user_id=user_id,
            filename=filename,
            content_base64=content_base64,
            metadata=metadata
        )
    
    # Vector search operations
    async def find_similar_logs(self, query: str, top_k: int = 10) -> List[Dict[str, Any]]:
        """Find similar logs using vector search"""
        # First, get embedding from websearch server
        embedding_result = await self._call_tool("websearch", "get_embedding", text=query)
        embedding = embedding_result.get("embedding", [])
        
        # Search in Milvus
        result = await self._call_tool(
            "milvus",
            "search_similar",
            collection_name="logs",
            query_vectors=[embedding],
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
    
    # Remove Neo4j operations: add_to_knowledge_graph, query_knowledge_graph, etc.
    
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
        
        # Handle both successful results and errors
        if isinstance(result, dict):
            if result.get("success", False):
                return result.get("results", [])
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

# Agent State for LangGraph
from typing import TypedDict, Annotated, Sequence
import operator

class AgentState(TypedDict):
    """State for the MCP LangGraph agent"""
    messages: Annotated[Sequence[BaseMessage], add_messages]
    tools_available: List[Dict[str, Any]]
    current_tool_results: Dict[str, Any]

class MCPTool(BaseTool):
    """LangChain tool wrapper for MCP tools"""
    
    def __init__(self, name: str, description: str, mcp_client, server: str, tool_name: str, parameters: Dict[str, Any]):
        super().__init__(name=name, description=description)
        self.mcp_client = mcp_client
        self.server = server
        self.tool_name = tool_name
        self.parameters = parameters
    
    def _run(self, **kwargs) -> str:
        """Synchronous run - not used"""
        raise NotImplementedError("Use async version")
    
    async def _arun(self, **kwargs) -> str:
        """Execute the MCP tool"""
        try:
            result = await self.mcp_client._call_tool(self.server, self.tool_name, **kwargs)
            return json.dumps(result, indent=2)
        except Exception as e:
            return f"Error executing tool {self.tool_name}: {str(e)}"

class MCPLLMAgent:
    """LangGraph-based agent wrapper for LLM routing and MCP tool integration"""
    
    def __init__(self, mcp_client, default_provider: str = "openai", default_model: str = "gpt-4o-mini"):
        self.mcp_client = mcp_client
        self.default_provider = default_provider
        self.default_model = default_model
        
        # Import LLM factory here to avoid circular imports
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent))
        
        from agent.llm_providers import LLMProviderFactory
        self.llm_factory = LLMProviderFactory()
        
        # Initialize LangGraph
        self.graph = None
        self._build_graph()
    
    def _build_graph(self):
        """Build the LangGraph workflow"""
        workflow = StateGraph(AgentState)
        
        # Add nodes
        workflow.add_node("agent", self._agent_node)
        workflow.add_node("tools", self._tools_node)
        
        # Add edges
        workflow.set_entry_point("agent")
        workflow.add_conditional_edges(
            "agent",
            self._should_continue,
            {
                "continue": "tools",
                "end": END
            }
        )
        workflow.add_edge("tools", "agent")
        
        # Set recursion limit to 50 to avoid hitting the default limit
        self.graph = workflow.compile()
    
    async def _agent_node(self, state: AgentState) -> AgentState:
        """Agent decision node with dynamic LLM routing"""
        messages = state["messages"]
        tools = state.get("tools_available", [])
        
        # Get LLM instance with current provider/model settings
        llm = self.llm_factory.get_llm(self.default_provider, model=self.default_model)
        
        # Add system message for tool awareness if tools are available
        if tools and len(messages) == 1:  # Only add system message for first interaction
            system_message = SystemMessage(content=f"""You are an intelligent assistant with access to {len(tools)} tools.
            
Available tools include:
{', '.join([tool.get('name', 'unknown') for tool in tools[:10]])}

Use these tools when appropriate to help answer the user's question. Always prefer using tools over making assumptions.""")
            messages = [system_message] + list(messages)
        
        # Bind tools to LLM if available
        if tools:
            tool_schemas = [self._format_tool_for_openai(tool) for tool in tools]
            llm = llm.bind_tools(tool_schemas)
        
        # Generate response
        response = await llm.ainvoke(messages)

        return {
            "messages": [response],
            "tools_available": state.get("tools_available", []),
            "current_tool_results": state.get("current_tool_results", {}),
        }

    async def _tools_node(self, state: AgentState) -> AgentState:
        """Execute tools and return results"""
        last_message = state["messages"][-1]
        
        # Support both dict and object attribute access for tool_calls
        tool_calls = None
        if isinstance(last_message, dict):
            tool_calls = last_message.get("tool_calls", [])
        else:
            tool_calls = getattr(last_message, "tool_calls", [])

        if tool_calls:
            tool_results = []
            for tool_call in tool_calls:
                # Ensure tool_name is a string
                tool_name = tool_call.get("name") if isinstance(tool_call, dict) else getattr(tool_call, "name", None)
                if not isinstance(tool_name, str) or not tool_name:
                    continue  # skip invalid tool calls
                tool_args = tool_call.get("args", {}) if isinstance(tool_call, dict) else getattr(tool_call, "args", {})
                try:
                    result = await self._execute_mcp_tool(tool_name, tool_args)
                    tool_results.append({
                        "tool_call_id": tool_call.get("id") if isinstance(tool_call, dict) else getattr(tool_call, "id", None),
                        "content": result
                    })
                except Exception as e:
                    tool_results.append({
                        "tool_call_id": tool_call.get("id") if isinstance(tool_call, dict) else getattr(tool_call, "id", None),
                        "content": f"Error: {str(e)}"
                    })
            # Create tool messages
            tool_messages = []
            for result in tool_results:
                tool_messages.append({
                    "role": "tool",
                    "tool_call_id": result["tool_call_id"],
                    "content": result["content"]
                })
            return {
                "messages": tool_messages,
                "current_tool_results": {"results": tool_results},
                "tools_available": state.get("tools_available", []),
            }
        return {
            "messages": [],
            "current_tool_results": {},
            "tools_available": state.get("tools_available", []),
        }

    def _should_continue(self, state: AgentState) -> str:
        """Decide whether to continue with tools or end"""
        last_message = state["messages"][-1]
        # Check if last_message is a dict and has 'tool_calls' key
        tool_calls = None
        if isinstance(last_message, dict):
            tool_calls = last_message.get("tool_calls")
        else:
            # Try attribute access for compatibility with message objects
            tool_calls = getattr(last_message, "tool_calls", None)
        if tool_calls:
            return "continue"
        return "end"

    async def _execute_mcp_tool(self, tool_name: str, args: Dict[str, Any]) -> str:
        """Execute an MCP tool by name"""
        tool_mapping = {
            "store_logs": ("mongodb", "store_logs"),
            "get_logs_by_date": ("mongodb", "get_logs_by_date"),
            "web_search": ("websearch", "web_search"),
            "search_similar": ("milvus", "search_similar"),
            "train_neural_network": ("scirex", "train_neural_network"),
            "perform_clustering": ("scirex", "perform_clustering"),
            "summarize_text": ("websearch", "summarize_text"),
        }
        if tool_name in tool_mapping:
            server, method = tool_mapping[tool_name]
            result = await self.mcp_client._call_tool(server, method, **args)
            return json.dumps(result, indent=2)
        else:
            return f"Unknown tool: {tool_name}"

    def _format_tool_for_openai(self, tool: Dict[str, Any]) -> Dict[str, Any]:
        """Format MCP tool for OpenAI function calling"""
        return {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool.get("parameters", {"type": "object", "properties": {}})
            }
        }

    async def process_query(self, query: str, provider: Optional[str] = None, model: Optional[str] = None, 
                          tools: Optional[List[Dict[str, Any]]] = None, tool_choice: str = "auto") -> str:
        """Process a query using the LangGraph agent with MCP tools"""
        # Use provided or default provider/model
        if provider:
            self.default_provider = provider
        if model:
            self.default_model = model
        # Get available MCP tools if not provided
        if tools is None:
            tools = []
        # Initialize state
        initial_state: AgentState = {
            "messages": [HumanMessage(content=query)],
            "tools_available": tools,
            "current_tool_results": {}
        }
        # Ensure the graph is initialized
        if self.graph is None:
            raise RuntimeError("LangGraph workflow is not initialized.")
        # Run the graph with increased recursion limit
        final_state = await self.graph.ainvoke(initial_state, config={"recursion_limit": 50})
        # Extract the final response
        messages = final_state["messages"]
        if messages:
            last_message = messages[-1]
            if hasattr(last_message, 'content'):
                if isinstance(last_message.content, list):
                    # Join list of strings/dicts as a string
                    return "\n".join(str(x) for x in last_message.content)
                return str(last_message.content)
            elif isinstance(last_message, dict) and 'content' in last_message:
                content = last_message['content']
                if isinstance(content, list):
                    return "\n".join(str(x) for x in content)
                return str(content)
            elif isinstance(last_message, list):
                return "\n".join(str(x) for x in last_message)
            else:
                return str(last_message)
        # Final fallback: always return a string
        return "No response generated"
    
    def switch_provider(self, provider: str, model: Optional[str] = None):
        """
        Switch LLM provider and model dynamically
        
        Args:
            provider: New provider (openai, gemini, vllm)
            model: New model (optional, uses default if not provided)
        """
        self.default_provider = provider
        if model:
            self.default_model = model
        
        logger.info(f"Switched to provider: {provider}, model: {self.default_model}")
    
    def get_available_providers(self) -> dict:
        """Get available LLM providers and their status"""
        return self.llm_factory.get_available_providers()

# Convenience functions for standalone usage
async def create_client() -> MCPLogAnalyticsClient:
    """Create and initialize MCP client"""
    client = MCPLogAnalyticsClient()
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

async def test_mcp_client_with_agent():
    """Test the new MCPClient with LangGraph agent"""
    print("Testing MCPClient with LangGraph Agent...")
    
    # Create client with OpenAI provider
    client = await create_mcp_client(provider="openai", model="gpt-4o-mini")
    
    try:
        # Test queries that should use different tools
        test_queries = [
            "Search for the latest news about artificial intelligence",
            "Store a test log entry with current timestamp", 
            "What information do you have about machine learning?",
            "Can you summarize the text 'Artificial intelligence is transforming industries across the globe'?",
        ]
        
        for query in test_queries:
            print(f"\nQuery: {query}")
            try:
                response = await client.process_query(query)
                print(f"Response: {response}")
            except Exception as e:
                print(f"Error: {e}")
        
        # Test connecting to a local MCP server
        try:
            print("\nTesting connection to local MCP server...")
            # This would connect to a server.py file if it exists
            # connection_id = await client.connect_to_server("server.py", "stdio")
            # print(f"Connected with ID: {connection_id}")
        except Exception as e:
            print(f"Could not connect to local server: {e}")
        
        # Test getting available tools
        print("\nAvailable tools:")
        tools = await client.get_mcp_tools()
        for tool in tools[:5]:  # Show first 5 tools
            func = tool.get("function", {})
            print(f"  - {func.get('name', 'unknown')}: {func.get('description', 'no description')}")
        
    finally:
        await client.cleanup()

if __name__ == "__main__":
    # Run both test functions
    asyncio.run(test_client())
    print("\n" + "="*50 + "\n")
    asyncio.run(test_mcp_client_with_agent())

class MCPClient:
    """
    Enhanced MCP Client with LangGraph-based agent integration
    Similar to the reference MCPOpenAIClient but with multi-provider support
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
        self.mcp_llm_agent = None
        
        # Store sessions for different connection types
        self.sessions = {}
        self.stdio_connections = {}
    
    async def initialize(self):
        """Initialize all MCP server connections"""
        await self.mcp_client.initialize()
        
        # Initialize the LangGraph agent
        self.mcp_llm_agent = MCPLLMAgent(
            mcp_client=self.mcp_client,
            default_provider=self.provider,
            default_model=self.model
        )
        
        logger.info(f"MCPClient initialized with provider: {self.provider}, model: {self.model}")
    
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
        Process a query using the LangGraph agent with MCP tools
        
        Args:
            query: The user query
            
        Returns:
            The response from the agent
        """
        # Get available tools
        tools = await self.get_mcp_tools()
        
        # Use the MCP LLM agent to process the query with automatic tool calling
        if self.mcp_llm_agent:
            response = await self.mcp_llm_agent.process_query(
                query=query,
                provider=self.provider,
                model=self.model,
                tools=tools,
                tool_choice="auto"
            )
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

# Convenience functions
async def create_mcp_client(provider: str = "openai", model: str = "gpt-4o-mini") -> MCPClient:
    """Create and initialize MCP client"""
    client = MCPClient(provider=provider, model=model)
    await client.initialize()
    return client