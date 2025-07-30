"""
Enhanced MCP Agent with automatic resource discovery and smart context handling
Fixes issues with manual file ID requests and user context extraction
"""
import os
import sys
import asyncio
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

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
        """Automatically discover available datasets for the user"""
        datasets = []
        
        try:
            if self.tools:
                # Look for dataset listing tools
                for tool in self.tools:
                    tool_name = getattr(tool, 'name', str(tool))
                    logger.info(f"Checking tool: {tool_name}")
                    
                    if 'list_available_datasets' in tool_name:
                        try:
                            # Call the tool to get datasets
                            result = await tool.ainvoke({"user_id": user_id})
                            logger.info(f"Dataset tool result: {result}")
                            if isinstance(result, dict) and result.get("success"):
                                datasets.extend(result.get("datasets", []))
                                logger.info(f"Found {len(result.get('datasets', []))} datasets via {tool_name}")
                        except Exception as e:
                            logger.warning(f"Error calling {tool_name}: {e}")
                    
                    elif 'list_uploaded_files' in tool_name:
                        try:
                            # Call the tool to get files that might be datasets
                            # Don't use request wrapper for list tools
                            params = {
                                "user_id": user_id, 
                                "file_type": "csv",
                                "limit": 50
                            }
                            logger.info(f"Calling {tool_name} with params: {params}")
                            result = await tool.ainvoke(params)
                            logger.info(f"List files tool result: {result}")
                            
                            # Fix: Properly handle successful responses with or without files
                            if isinstance(result, dict) and result.get("success") == True:
                                files = result.get("files", [])
                                returned_count = result.get("returned_count", len(files))
                                total_files = result.get("total_files", 0)
                                
                                logger.info(f"Successfully retrieved file list: {len(files)} files (returned: {returned_count}, total: {total_files})")
                                
                                if len(files) == 0:
                                    logger.info(f"No CSV files found for user {user_id} - this is normal if no files have been uploaded")
                                else:
                                    for file_info in files:
                                        logger.debug(f"Processing file: {file_info}")
                                        dataset_info = {
                                            "id": file_info.get("file_id", file_info.get("_id")),
                                            "name": file_info.get("filename", "unnamed"),
                                            "size": file_info.get("file_size", 0),
                                            "source": "uploaded_files",
                                            "type": "file",
                                            "uploaded_at": file_info.get("uploaded_at"),
                                            "content_type": file_info.get("content_type")
                                        }
                                        datasets.append(dataset_info)
                                    logger.info(f"Found {len(files)} uploaded files via {tool_name}")
                            else:
                                # Only warn on actual API errors, not on empty results
                                if isinstance(result, dict) and result.get("success") == False:
                                    logger.warning(f"Tool {tool_name} returned error: {result.get('error', 'Unknown error')}")
                                else:
                                    logger.warning(f"Tool {tool_name} returned unexpected result format: {result}")
                        except Exception as e:
                            logger.warning(f"Error calling {tool_name}: {e}")
                            import traceback
                            logger.warning(f"Traceback: {traceback.format_exc()}")
        
        except Exception as e:
            logger.error(f"Error discovering datasets: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
        
        logger.info(f"Discovered {len(datasets)} total datasets for user {user_id}")
        return datasets

    async def refresh_datasets(self, user_id: str) -> int:
        """Manually refresh the available datasets"""
        self.available_datasets = await self._discover_user_datasets(user_id)
        logger.info(f"Refreshed datasets: {len(self.available_datasets)}")
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
1. Automatically detecting uploaded datasets
2. Performing clustering and classification analysis  
3. Providing actionable business insights
4. Using appropriate tools to complete analysis tasks

When users upload datasets, automatically analyze them without asking for file IDs.
For clustering requests, use available datasets intelligently.
Provide specific insights based on the actual data uploaded.

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
        """Analyze a query using the agent with smart context handling"""
        if not self._initialized or self.user_context.get("user_id") != user_id:
            await self.initialize(user_role, user_id)

        try:
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
            result = await self.graph.ainvoke(initial_state, config=config)
            
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

# Factory function
def create_agent():
    """Create and return an agent instance"""
    return MCPAgent()
