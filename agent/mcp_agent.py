"""
Enhanced MCP Agent with automatic resource discovery and smart context handling
Fixes issues with manual file ID requests and user context extraction
"""
import os
import sys
import asyncio
import logging
import time  # Add time import
from typing import Any, Dict, List, Optional
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Global cache for dataset discovery to prevent redundant calls across instances
_dataset_cache = {}
_cache_ttl = 60  # Increased to 60 seconds TTL to reduce HTTP calls
_tool_call_cache = {}  # Cache for tool call results
_tool_cache_ttl = 30  # 30 seconds for tool results

from utils.auth_utils import AuthManager, UserRole

# LangChain imports
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI

# LangGraph imports
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

# MCP Adapters
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

# Local imports
from .memory_manager import MemoryManager
from .llm_providers import LLMProviderFactory

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class AgentState(dict):
    """Agent state with messages and metadata"""
    messages: List[BaseMessage]
    user_id: str
    conversation_id: str
    user_role: UserRole
    metadata: Dict[str, Any]

class MCPAgent:
    """Enhanced MCP Agent with automatic resource discovery and smart context handling"""
    
    def __init__(self, llm_provider: Optional[str] = None):
        self.llm_provider = llm_provider or os.getenv("DEFAULT_LLM_PROVIDER", "openai")
        self.llm_factory = LLMProviderFactory()
        self.memory_manager = MemoryManager()
        self.mcp_client = None
        self.graph = None
        self.tools = []
        self.auth_manager = AuthManager()
        self._initialized = False
        
        # Enhanced: Tool call tracking for UI display
        self.tool_calls = []
        self.current_tool_calls = []  # Track current session tool calls
        
        # New: Smart context tracking
        self.user_context = {}
        self.available_datasets = []
        self.user_role = UserRole.VIEWER
        
    def _load_config(self) -> Dict[str, Any]:
        """Load MCP server configuration"""
        from config.settings import (
            MONGODB_MCP_URL, MILVUS_MCP_URL, 
            WEBSEARCH_MCP_URL, SCIREX_MCP_URL
        )
        
        return {
            "mongodb": {
                "url": MONGODB_MCP_URL,
                "transport": "streamable_http"
            },
            "milvus": {
                "url": MILVUS_MCP_URL,
                "transport": "streamable_http"
            },
            "websearch": {
                "url": WEBSEARCH_MCP_URL,
                "transport": "streamable_http"
            },
            "scirex": {
                "url": SCIREX_MCP_URL,
                "transport": "streamable_http"
            }
        }
    
    def _filter_servers_by_role(self, user_role: UserRole) -> Dict[str, Any]:
        """Filter servers based on user role"""
        all_servers = self._load_config()
        permissions = self.auth_manager.role_permissions.get(user_role)
        
        if not permissions:
            return {}
        
        accessible_servers = {}
        for server_name, server_config in all_servers.items():
            if self.auth_manager.check_server_access(user_role, server_name):
                accessible_servers[server_name] = server_config
        
        return accessible_servers
    
    async def _discover_user_datasets(self, user_id: str) -> List[Dict[str, Any]]:
        """Automatically discover available datasets for the user - OPTIMIZED with global caching"""
        global _dataset_cache, _cache_ttl
        
        # Check global cache first
        cache_key = f"datasets_{user_id}"
        current_time = time.time()
        
        if cache_key in _dataset_cache:
            cache_entry = _dataset_cache[cache_key]
            if current_time - cache_entry['timestamp'] < _cache_ttl:
                logger.info(f"Using cached datasets ({len(cache_entry['data'])} found) - global cache hit")
                return cache_entry['data']
        
        datasets = []
        
        try:
            if self.tools:
                # Prioritize and limit tool calls to reduce HTTP overhead
                dataset_tools = []
                file_tools = []
                
                for tool in self.tools:
                    tool_name = getattr(tool, 'name', str(tool))
                    if 'list_available_datasets' in tool_name.lower():
                        dataset_tools.append(tool)
                    elif 'list_uploaded_files' in tool_name.lower():
                        file_tools.append(tool)
                
                # Strategy 1: Try dataset-specific tools first (most efficient)
                for tool in dataset_tools[:1]:  # Only use the first dataset tool
                    try:
                        logger.info(f"Calling dataset tool: {tool.name}")
                        result = await tool.ainvoke({"limit": 10, "user_id": user_id})
                        
                        if isinstance(result, dict) and result.get("success"):
                            datasets.extend(result.get("datasets", []))
                            logger.info(f"✅ Found {len(result.get('datasets', []))} datasets via {tool.name}")
                            # Cache the results globally
                            _dataset_cache[cache_key] = {
                                'data': datasets,
                                'timestamp': current_time
                            }
                            return datasets  # Early return to avoid redundant calls
                        
                    except Exception as e:
                        logger.warning(f"Dataset tool {tool.name} failed: {e}")
                
                # Strategy 2: Only use file tools if no datasets found and limit calls
                if not datasets and file_tools:
                    tool = file_tools[0]  # Use only the first available file tool
                    try:
                        # Minimal parameters to reduce processing time
                        params = {
                            "file_type": "csv",  # Focus on data files
                            "limit": 5,  # Strict limit to reduce processing
                            "user_id": user_id
                        }
                        
                        logger.info(f"Calling file tool: {tool.name} (limited to 5 files)")
                        result = await tool.ainvoke(params)
                        
                        if isinstance(result, dict) and result.get("success"):
                            files = result.get("files", [])
                            logger.info(f"✅ Retrieved {len(files)} files via {tool.name}")
                            
                            # Convert files to dataset format
                            for file_info in files:
                                dataset_info = {
                                    "id": file_info.get("file_id", file_info.get("_id")),
                                    "name": file_info.get("filename", "unnamed"),
                                    "size": file_info.get("file_size", 0),
                                    "source": "uploaded_files",
                                    "type": "file",
                                    "uploaded_at": file_info.get("uploaded_at"),
                                    "content_type": file_info.get("content_type", "unknown")
                                }
                                datasets.append(dataset_info)
                                
                    except Exception as e:
                        logger.warning(f"File tool {tool.name} failed: {e}")
        
        except Exception as e:
            logger.error(f"Error in dataset discovery: {e}")
        
        # Cache results globally even if empty to avoid repeated failures
        _dataset_cache[cache_key] = {
            'data': datasets,
            'timestamp': current_time
        }
        
        logger.info(f"📊 Dataset discovery completed: {len(datasets)} total datasets")
        return datasets

    async def refresh_datasets(self, user_id: str) -> int:
        """Manually refresh the available datasets"""
        self.available_datasets = await self._discover_user_datasets(user_id)
        logger.info(f"Refreshed datasets: {len(self.available_datasets)}")
        return len(self.available_datasets)

    def invalidate_dataset_cache(self, user_id: str):
        """Invalidate dataset cache for a specific user to force refresh"""
        cache_key = f"datasets_{user_id}"
        if cache_key in _dataset_cache:
            del _dataset_cache[cache_key]
            logger.info(f"Invalidated dataset cache for user: {user_id}")

    async def refresh_datasets_after_upload(self, user_id: str) -> int:
        """Refresh datasets after file upload by invalidating cache"""
        self.invalidate_dataset_cache(user_id)
        self.available_datasets = await self._discover_user_datasets(user_id)
        logger.info(f"Refreshed datasets after upload: {len(self.available_datasets)}")
        return len(self.available_datasets)

    async def initialize(self, user_role: UserRole = UserRole.VIEWER, user_id: str = "system"):
        """Initialize agent with user context and automatic resource discovery"""
        try:
            self.user_role = user_role
            self.user_context = {"user_id": user_id, "role": user_role}
            
            await self.memory_manager.initialize()
            
            # Load all available servers - following LangGraph MCP pattern
            all_servers_config = self._load_config()
            
            if not all_servers_config:
                logger.warning("No MCP servers configured")
                self.tools = []
            else:
                # Initialize MCP client exactly as shown in LangGraph documentation
                try:
                    logger.info(f"Initializing MCP client with servers: {list(all_servers_config.keys())}")
                    self.mcp_client = MultiServerMCPClient(all_servers_config)
                    
                    # Get tools from MCP servers
                    self.tools = await self.mcp_client.get_tools()
                    logger.info(f"Successfully loaded {len(self.tools)} tools from {len(all_servers_config)} MCP servers")
                    
                    # Validate tools are proper tool objects
                    valid_tools = []
                    for tool in self.tools:
                        if hasattr(tool, 'name') and hasattr(tool, 'invoke'):
                            valid_tools.append(tool)
                            logger.debug(f"Validated tool: {tool.name}")
                        else:
                            logger.warning(f"Invalid tool detected: {tool}")
                    
                    self.tools = valid_tools
                    logger.info(f"Validated {len(self.tools)} tools for agent use")
                    
                    # Log tool names for debugging
                    tool_names = [getattr(tool, 'name', str(tool)) for tool in self.tools]
                    logger.info(f"Available MCP tools: {tool_names}")
                    
                except Exception as mcp_error:
                    logger.error(f"Failed to initialize MCP client: {mcp_error}")
                    import traceback
                    logger.error(f"MCP initialization traceback: {traceback.format_exc()}")
                    self.tools = []
                    self.mcp_client = None
            
            # Discover available datasets
            self.available_datasets = await self._discover_user_datasets(user_id)
            logger.info(f"Discovered {len(self.available_datasets)} datasets for user {user_id}")
            
            # Run startup validation
            validation_results = self._startup_validation()
            if validation_results["errors"]:
                logger.warning(f"Startup validation warnings: {validation_results['errors']}")
            
            # Build the agent graph
            self._build_react_agent()
            
            logger.info(f"✅ MCPAgent initialized for user: {user_id}")
            self._initialized = True
            
        except Exception as e:
            logger.error(f"Failed to initialize agent: {e}")
            import traceback
            logger.error(f"Initialization traceback: {traceback.format_exc()}")
            
            # Still initialize with empty tools rather than failing completely
            self.tools = []
            self.available_datasets = []
            
            # Always use create_react_agent, even with no tools - following LangGraph pattern
            try:
                llm = self.llm_factory.get_llm(self.llm_provider)
                system_prompt = """You are an expert data analyst.

Currently no advanced tools are available, but I can still help with:
- General data analysis questions
- Clustering and classification concepts
- Business insights and recommendations

Please upload your data or ask questions about data analysis."""
                
                # Use create_react_agent with empty tools list - this is still the recommended pattern
                self.graph = create_react_agent(
                    model=llm,
                    tools=[],  # Empty tools list
                    checkpointer=MemorySaver(),
                    state_modifier=system_prompt
                )
                
                self._initialized = True
                logger.info("Agent initialized with basic capabilities (no MCP tools available)")
                
            except Exception as fallback_error:
                logger.error(f"Failed to initialize even basic agent: {fallback_error}")
                raise fallback_error
    
    def _build_react_agent(self):
        """Build agent using create_react_agent following LangGraph MCP documentation pattern"""
        try:
            # Get LLM without any tool binding - create_react_agent handles all tool binding internally
            llm = self.llm_factory.get_llm(self.llm_provider)
            
            # Build context-aware prompt with discovered resources
            resource_context = self._build_resource_context()
            
            system_prompt = f"""You are an expert data analyst specialized in customer segmentation and clustering analysis.

{resource_context}

Your goal is to help users analyze their data by:
1. Automatically detecting uploaded datasets using list_uploaded_files and list_available_datasets tools
2. Performing clustering and classification analysis using proper file IDs  
3. Providing actionable business insights
4. Using appropriate tools to complete analysis tasks

CRITICAL WORKFLOW FOR DATA ANALYSIS:

For File Upload:
- Use upload_file tool with exact parameters: filename, content (base64), content_type
- ALWAYS extract and save the file_id from the upload response
- Confirm successful upload before proceeding

For Clustering Analysis:
- First call list_uploaded_files to get available CSV files with their file IDs
- Use the file_id (not filename) as dataset_id in cluster_analysis tool
- Example: cluster_analysis with dataset_id as the actual file_id, user_id as user, n_clusters as 3

For Dataset Discovery:
- Use list_uploaded_files to find user's uploaded CSV/data files
- Use list_available_datasets to find datasets in storage buckets
- Each file has a unique file_id that must be used for downstream analysis

NEVER use filename as dataset_id - always use the actual file_id returned from upload or listing tools.

Use the available tools effectively to provide comprehensive data analysis."""
            
            # Create react agent following LangGraph MCP pattern exactly
            logger.info(f"Creating react agent with {len(self.tools)} MCP tools")
            
            # IMPORTANT: Don't bind tools to LLM when using create_react_agent
            # create_react_agent handles tool binding internally
            self.graph = create_react_agent(
                model=llm,  # Raw LLM without any manual tool binding
                tools=self.tools,  # MCP tools - let create_react_agent handle binding
                checkpointer=MemorySaver(),
                state_modifier=system_prompt
            )
            
            logger.info(f"✅ React agent created successfully with {len(self.tools)} MCP tools")
            
        except Exception as e:
            logger.error(f"Error building react agent: {e}")
            import traceback
            logger.error(f"React agent build traceback: {traceback.format_exc()}")
            
            # For debugging: log tool details
            logger.error(f"Tool count: {len(self.tools)}")
            for i, tool in enumerate(self.tools):
                logger.error(f"Tool {i}: {getattr(tool, 'name', 'unnamed')} - {type(tool)}")
            
            raise e
    
    def _build_resource_context(self) -> str:
        """Build context string about available resources"""
        if not self.available_datasets:
            return "Currently no datasets are available for analysis."
        
        context = f"Available datasets ({len(self.available_datasets)}):\n"
        for i, dataset in enumerate(self.available_datasets[:5], 1):
            name = dataset.get('name', 'unnamed')
            size = dataset.get('size', 'unknown size')
            source = dataset.get('source', 'unknown source')
            dataset_id = dataset.get('id', 'unknown')
            context += f"  {i}. {name} (ID: {dataset_id}, Size: {size}, Source: {source})\n"
        
        if len(self.available_datasets) > 5:
            context += f"  ... and {len(self.available_datasets) - 5} more datasets\n"
        
        context += "\nWhen users ask about data analysis, clustering, or working with data, reference these available datasets automatically."
        return context
    
    async def analyze(
        self,
        query: str,
        user_id: str,
        conversation_id: str,
        user_role: UserRole = UserRole.VIEWER,
        attachments: Optional[List[Any]] = None
    ) -> str:
        """Analyze a query using the agent with smart context handling and tool tracking"""
        if not self._initialized or self.user_context.get("user_id") != user_id:
            await self.initialize(user_role, user_id)

        try:
            # Clear previous tool calls for this query
            self.clear_current_tool_calls()
            
            # Enhance query with context if needed
            enhanced_query = self._enhance_query_with_context(query, user_id)
            
            # Create initial state
            initial_state = {
                "messages": [HumanMessage(content=enhanced_query)],
                "user_id": user_id,
                "conversation_id": conversation_id,
                "user_role": user_role
            }
            
            if attachments is not None:
                initial_state["attachments"] = attachments
            
            # Run the agent with recursion limit configured
            config = {
                "configurable": {"thread_id": f"{user_id}:{conversation_id}"},
                "recursion_limit": 50  # Increase recursion limit for complex operations
            }
            
            # Execute with tool call monitoring
            result = await self._execute_with_tool_tracking(initial_state, config)
            
            # Extract response
            last_message = result["messages"][-1]
            response = last_message.content
            
            # Update memory
            await self.memory_manager.update_conversation(
                user_id=user_id,
                conversation_id=conversation_id,
                message={"role": "user", "content": query},
                response={"role": "assistant", "content": response}
            )
            
            return response
            
        except Exception as e:
            logger.error(f"Error during analysis: {str(e)}")
            return self._generate_helpful_fallback_response(query, str(e))
    
    async def _execute_with_tool_tracking(self, initial_state, config):
        """Execute agent graph with enhanced tool call tracking"""
        try:
            # Clear current tool calls for this execution
            self.clear_current_tool_calls()
            
            # Execute the graph
            result = await self.graph.ainvoke(initial_state, config=config)
            
            # Process tool calls from messages with improved tracking
            tool_call_count = 0
            if "messages" in result:
                for msg in result["messages"]:
                    # Track tool call messages
                    if hasattr(msg, 'tool_calls') and msg.tool_calls:
                        for tool_call in msg.tool_calls:
                            tool_name = tool_call.get('name', 'unknown')
                            tool_args = tool_call.get('args', {})
                            tool_id = tool_call.get('id', 'unknown')
                            
                            # Track tool call with input
                            self._track_tool_call(tool_name, tool_args)
                            tool_call_count += 1
                            
                            logger.info(f"🔧 Tool call detected: {tool_name} with args: {list(tool_args.keys())}")
                    
                    # Track tool response messages
                    if hasattr(msg, 'content') and hasattr(msg, 'tool_call_id'):
                        # This is a tool response message - find the corresponding call and update result
                        tool_call_id = msg.tool_call_id
                        content = str(msg.content)
                        
                        # Update the most recent call with this result
                        if self.current_tool_calls:
                            for call in reversed(self.current_tool_calls):
                                if call.get('id') == tool_call_id or not call.get('result'):
                                    call['result'] = content[:200]  # Limit result size
                                    call['full_result'] = content
                                    
                                    # Check for errors in the response
                                    if any(error_word in content.lower() for error_word in ['error', 'failed', 'exception']):
                                        call['status'] = 'error'
                                        call['error'] = content[:100]
                                    
                                    logger.info(f"🔧 Tool response detected: content='{content[:100]}...'")
                                    break
            
            logger.info(f"✅ Execution completed with {tool_call_count} tool calls tracked")
            return result
            
        except Exception as e:
            logger.error(f"Tool tracking execution failed: {str(e)}")
            # Track the error
            self._track_tool_call("execution_error", {"error": str(e)}, error=str(e))
            raise
    
    def _enhance_query_with_context(self, query: str, user_id: str) -> str:
        """Enhance user query with available context"""
        # Check if query is about data analysis or clustering
        data_keywords = ['cluster', 'clustering', 'data', 'dataset', 'analysis', 'analyze']
        
        if any(keyword in query.lower() for keyword in data_keywords):
            if self.available_datasets:
                dataset_info = "\n\nContext: You have access to these datasets:\n"
                for dataset in self.available_datasets[:3]:
                    dataset_info += f"- {dataset.get('name', 'unnamed')} (ID: {dataset.get('id')})\n"
                return query + dataset_info
        
        return query
    
    def _generate_helpful_fallback_response(self, query: str, error: str) -> str:
        """Generate helpful response when tools fail"""
        if 'cluster' in query.lower() or 'data' in query.lower():
            if self.available_datasets:
                response = f"I can help you with data analysis. I found {len(self.available_datasets)} datasets available:\n\n"
                for i, dataset in enumerate(self.available_datasets[:3], 1):
                    response += f"{i}. {dataset.get('name', 'unnamed')} (Size: {dataset.get('size', 'unknown')})\n"
                response += "\nWould you like me to help you analyze any of these datasets?"
                return response
            else:
                return "I can help you with data analysis, but I don't see any datasets currently available. You may need to upload some data first."
        
        return f"I can help you with your query. Here's what I found: {error}"
    
    async def cleanup(self):
        """Clean up resources"""
        await self.memory_manager.close()

    def _validate_tools(self) -> bool:
        """Validate tools are proper BaseTool instances with required attributes"""
        try:
            for i, tool in enumerate(self.tools):
                if not hasattr(tool, 'name'):
                    logger.error(f"Tool {i} missing 'name' attribute: {tool}")
                    return False
                if not hasattr(tool, 'invoke') and not hasattr(tool, 'ainvoke'):
                    logger.error(f"Tool {i} missing 'invoke'/'ainvoke' method: {tool.name}")
                    return False
                logger.debug(f"Tool {i} validated: {tool.name}")
            
            logger.info(f"All {len(self.tools)} tools validated successfully")
            return True
            
        except Exception as e:
            logger.error(f"Tool validation failed: {e}")
            return False
    
    def _startup_validation(self) -> Dict[str, Any]:
        """Run startup validation suite to catch issues early"""
        validation_results = {
            "tools_valid": False,
            "tool_count": len(self.tools),
            "mcp_client_connected": self.mcp_client is not None,
            "errors": []
        }
        
        try:
            # Validate tools
            validation_results["tools_valid"] = self._validate_tools()
            
            # Check MCP client connection
            if self.mcp_client is None:
                validation_results["errors"].append("MCP client not initialized")
            
            # Log validation summary
            logger.info(f"Startup validation: {validation_results}")
            
        except Exception as e:
            validation_results["errors"].append(f"Validation error: {e}")
            logger.error(f"Startup validation failed: {e}")
        
        return validation_results

    def _track_tool_call(self, tool_name: str, args: Dict[str, Any], result: Any = None, error: str = None):
        """Track tool calls for UI display with improved detail"""
        call_info = {
            "name": tool_name,
            "args": args,
            "timestamp": datetime.now().isoformat(),
            "status": "error" if error else "success",
            "result": str(result)[:500] if result else None,
            "full_result": str(result) if result else None,
            "error": error,
            "id": f"{tool_name}_{len(self.tool_calls)}"
        }
        
        self.tool_calls.append(call_info)
        self.current_tool_calls.append(call_info)
        
        # Log tool call details
        status_icon = "❌" if error else "✅"
        logger.info(f"🔧 Tool tracked: {status_icon} {tool_name} - {call_info['status']}")
        
        if error:
            logger.error(f"Tool error details: {error}")
        if result:
            result_preview = str(result)[:100] + "..." if len(str(result)) > 100 else str(result)
            logger.info(f"Tool response detected: content='{result_preview}'")
        
    def get_current_tool_calls(self) -> List[Dict[str, Any]]:
        """Get tool calls from current session"""
        return self.current_tool_calls.copy()
        
    def clear_current_tool_calls(self):
        """Clear current session tool calls"""
        self.current_tool_calls = []
    
    async def get_agent_status(self) -> Dict[str, Any]:
        """Get comprehensive agent status for monitoring"""
        status = {
            "initialized": self._initialized,
            "mcp_client_active": self.mcp_client is not None,
            "tools_count": len(self.tools),
            "user_role": self.user_role.value if hasattr(self, 'user_role') else None,
            "graph_created": self.graph is not None,
            "llm_provider": self.llm_provider,
            "memory_manager_active": self.memory_manager is not None,
            "datasets_count": len(self.available_datasets),
            "tool_calls_count": len(self.tool_calls)
        }
        
        # Add tool details if available
        if self.tools:
            status["tool_names"] = [getattr(tool, 'name', str(tool)) for tool in self.tools]
        else:
            status["tool_names"] = []
        
        # Add dataset info
        if self.available_datasets:
            status["datasets"] = [
                {
                    "name": dataset.get('name', 'unnamed'),
                    "id": dataset.get('id', 'unknown'),
                    "source": dataset.get('source', 'unknown')
                }
                for dataset in self.available_datasets[:5]
            ]
        
        return status

# Factory function
def create_agent():
    """Create and return an agent instance"""
    return MCPAgent()
