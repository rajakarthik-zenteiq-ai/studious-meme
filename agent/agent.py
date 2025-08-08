"""
Enhanced LangGraph Agent with MCP Integration and RBAC
Fixed: OpenAI tool role error and excessive API calls
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

# Import auth utilities
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

# MCP Adapters - FIXED: Use create_react_agent instead of manual tool handling
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

# Local imports
from .memory_manager import MemoryManager
from .llm_providers import LLMProviderFactory

# Logging setup - Control HTTP request logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Reduce HTTP logging verbosity to minimize log noise
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("mcp.client.streamable_http").setLevel(logging.WARNING)

class AgentState(dict):
    """Agent state with messages and metadata"""
    messages: List[BaseMessage]
    user_id: str
    conversation_id: str
    user_role: UserRole
    metadata: Dict[str, Any]

class MCPAgent:
    """MCP-integrated LangGraph agent with RBAC - FIXED VERSION"""
    
    def __init__(self, llm_provider: Optional[str] = None):
        self.llm_provider = llm_provider or os.getenv("DEFAULT_LLM_PROVIDER", "openai")
        self.llm_factory = LLMProviderFactory()
        self.memory_manager = MemoryManager()
        self.mcp_client = None
        self.graph = None
        self.tools = []
        self.auth_manager = AuthManager()
        self._initialized = False
        self.tool_calls = []  # Track tool calls for UI display
        
    def _load_config(self) -> Dict[str, Any]:
        """Load MCP server configuration with proper URL formatting"""
        from config.settings import (
            MONGODB_MCP_URL, MILVUS_MCP_URL, 
            WEBSEARCH_MCP_URL, SCIREX_MCP_URL
        )
        
        # Fix: Ensure URLs end with / to avoid 307 redirects
        def ensure_trailing_slash(url):
            return url.rstrip('/') + '/'
        
        return {
            "mongodb": {
                "url": ensure_trailing_slash(MONGODB_MCP_URL),
                "transport": "streamable_http"
            },
            "milvus": {
                "url": ensure_trailing_slash(MILVUS_MCP_URL),
                "transport": "streamable_http"
            },
            "websearch": {
                "url": ensure_trailing_slash(WEBSEARCH_MCP_URL),
                "transport": "streamable_http"
            },
            "scirex": {
                "url": ensure_trailing_slash(SCIREX_MCP_URL),
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
    
    async def initialize(self, user_role: UserRole = UserRole.VIEWER):
        """Initialize agent with RBAC following LangGraph MCP best practices"""
        try:
            self.user_role = user_role
            await self.memory_manager.initialize()
            
            # Filter servers based on role
            accessible_config = self._filter_servers_by_role(user_role)
            
            if not accessible_config:
                logger.warning(f"No accessible servers for role: {user_role.value}")
                self.tools = []
                self.mcp_client = None
            else:
                # Initialize MCP client following documented patterns
                await self._initialize_mcp_client(accessible_config)
            
            # Build the agent graph using create_react_agent (works with or without tools)
            self._build_react_agent()
            
            # Validate tool count consistency if MCP client is available
            if self.mcp_client:
                await self._validate_tool_consistency()
            
            logger.info(f"✅ Agent initialized for role: {user_role.value}")
            logger.info(f"   - MCP Client: {'✓' if self.mcp_client else '✗'}")
            logger.info(f"   - Tools loaded: {len(self.tools)}")
            logger.info(f"   - Agent graph: {'✓' if self.graph else '✗'}")
            self._initialized = True
            
        except Exception as e:
            logger.error(f"Failed to initialize agent: {e}")
            await self._handle_initialization_failure(user_role, e)
            self._initialized = True
    
    def _build_react_agent(self):
        """Build agent using create_react_agent following LangGraph MCP patterns"""
        try:
            llm = self.llm_factory.get_llm(self.llm_provider)
            
            role_specific_prompt = self._build_role_specific_prompt(self.user_role)
            
            system_prompt = f"""You are an expert log analytics assistant with role-based access control.

{role_specific_prompt}

Available capabilities based on your role:
- MongoDB: Store and query logs
- Milvus: Vector similarity search  
- WebSearch: Search web for information
- SciREX: ML model training and analysis

IMPORTANT: Always use the appropriate tools when available. Be precise with tool calls and handle errors gracefully.
Provide clear, actionable insights based on the data and analysis."""
            
            # Use create_react_agent exactly as documented
            if self.tools and len(self.tools) > 0:
                logger.info(f"Building ReAct agent with {len(self.tools)} MCP tools")
                
                # Following the exact documented pattern
                self.graph = create_react_agent(
                    model=llm,
                    tools=self.tools,
                    checkpointer=MemorySaver(),
                    # Add system message to modify default prompt
                    state_modifier=system_prompt
                )
                
                logger.info("✓ ReAct agent created successfully with MCP tools")
                
            else:
                # Fallback to simple agent when no tools available
                logger.warning("No tools available, creating simple agent without MCP tools")
                self.graph = self._build_simple_agent(llm, system_prompt)
                
        except Exception as e:
            logger.error(f"Error building ReAct agent: {e}")
            # Fallback to simple agent on error
            try:
                llm = self.llm_factory.get_llm(self.llm_provider)
                fallback_prompt = f"""You are an expert log analytics assistant with role-based access control.

{self._build_role_specific_prompt(self.user_role)}

Available capabilities:
- Basic text analysis and responses
- General assistance with log-related questions

Note: Advanced MCP tools are currently unavailable due to: {str(e)[:100]}"""
                
                self.graph = self._build_simple_agent(llm, fallback_prompt)
                logger.info("✓ Fallback simple agent created")
                
            except Exception as fallback_error:
                logger.error(f"Failed to create fallback agent: {fallback_error}")
                raise
    
    def _build_simple_agent(self, llm, system_prompt):
        """Create simple agent without tools"""
        def simple_agent(state):
            messages = [SystemMessage(content=system_prompt)] + state["messages"]
            response = llm.invoke(messages)
            return {"messages": [response]}
        
        workflow = StateGraph(AgentState)
        workflow.add_node("agent", simple_agent)
        workflow.add_edge(START, "agent")
        workflow.add_edge("agent", END)
        return workflow.compile(checkpointer=MemorySaver())
    
    def _build_role_specific_prompt(self, user_role: UserRole) -> str:
        """Build role-specific instructions"""
        role_prompts = {
            UserRole.ADMIN: "You have full access to all tools and servers.",
            UserRole.RND: "You can use SciREX for ML models and web search for research.",
            UserRole.DEVELOPER: "You can read/write to databases and upload files.",
            UserRole.ANALYST: "You can read data and search web for analysis.",
            UserRole.VIEWER: "You have read-only access to view and analyze data."
        }
        
        return role_prompts.get(user_role, "You have basic viewing access.")
    
    async def analyze(
        self,
        query: str,
        user_id: str,
        conversation_id: str,
        user_role: UserRole = UserRole.VIEWER,
        attachments: Optional[List[Any]] = None
    ) -> str:
        """Analyze a query using the agent following LangGraph MCP patterns"""
        if not self._initialized:
            await self.initialize(user_role)

        try:
            logger.info(f"Processing query for user {user_id} (role: {user_role.value})")
            logger.debug(f"Query: {query[:100]}{'...' if len(query) > 100 else ''}")
            
            # Log available tools before execution
            if self.tools:
                tool_names = [getattr(tool, 'name', 'unnamed') for tool in self.tools]
                logger.info(f"Available tools for this query: {', '.join(tool_names[:3])}{'...' if len(tool_names) > 3 else ''}")
            else:
                logger.info("No MCP tools available - using basic agent capabilities")
            
            # Create initial state following LangGraph patterns
            initial_state = {
                "messages": [HumanMessage(content=query)],
                "user_id": user_id,
                "conversation_id": conversation_id,
                "user_role": user_role
            }
            
            # Add attachments to state if provided
            if attachments is not None:
                initial_state["attachments"] = attachments
                logger.debug(f"Added {len(attachments)} attachments to state")
            
            # Configure thread for conversation persistence with recursion limit
            config = {
                "configurable": {"thread_id": f"{user_id}:{conversation_id}"},
                "recursion_limit": 10  # Prevent infinite loops
            }
            
            # Execute agent graph with monitoring
            logger.debug("Invoking agent graph...")
            start_time = datetime.now()
            
            result = await self._execute_with_monitoring(initial_state, config)
            
            execution_time = (datetime.now() - start_time).total_seconds()
            logger.info(f"✓ Agent execution completed in {execution_time:.2f}s")
            
            # Extract response from result
            if "messages" in result and result["messages"]:
                last_message = result["messages"][-1]
                response = last_message.content
                logger.info("✓ Agent analysis completed successfully")
            else:
                response = "I apologize, but I couldn't generate a proper response. Please try again."
                logger.warning("No messages in agent result")
            
            # Update conversation memory
            try:
                await self.memory_manager.update_conversation(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    message={"role": "user", "content": query},
                    response={"role": "assistant", "content": response}
                )
                logger.debug("✓ Conversation memory updated")
            except Exception as memory_error:
                logger.warning(f"Failed to update memory: {memory_error}")
            
            return response
            
        except Exception as e:
            logger.error(f"Error during analysis: {str(e)}")
            
            # Provide helpful fallback response
            fallback_response = f"""I encountered an issue while processing your request: {str(e)[:100]}

I can still help you with basic analysis and questions. Please try rephrasing your query or contact support if the issue persists.

Available capabilities in fallback mode:
- General log analysis guidance
- Basic troubleshooting assistance  
- Conceptual explanations"""
            
            return fallback_response
    
    async def cleanup(self):
        """Clean up resources"""
        await self.memory_manager.close()
    
    async def _initialize_mcp_client(self, accessible_config: Dict[str, Any]):
        """Initialize MCP client with optimized connection handling and reduced HTTP noise"""
        try:
            logger.info(f"Initializing MCP client with {len(accessible_config)} servers")
            
            # Log server configurations (debug level to reduce noise)
            for server_name, config in accessible_config.items():
                logger.debug(f"  - {server_name}: {config.get('url', config.get('command', 'Unknown'))}")
            
            # Temporarily reduce HTTP logging during initialization
            old_httpx_level = logging.getLogger("httpx").level
            old_mcp_level = logging.getLogger("mcp.client.streamable_http").level
            
            logging.getLogger("httpx").setLevel(logging.ERROR)
            logging.getLogger("mcp.client.streamable_http").setLevel(logging.ERROR)
            
            try:
                # Initialize MultiServerMCPClient
                logger.info("Creating MCP client connection...")
                self.mcp_client = MultiServerMCPClient(accessible_config)
                
                # Get tools with minimal retries to reduce HTTP noise
                logger.info("Loading tools from MCP servers...")
                self.tools = await self.mcp_client.get_tools()
                
                if self.tools:
                    logger.info(f"✓ Successfully loaded {len(self.tools)} tools")
                    # Log first few tool names for visibility
                    tool_names = [getattr(tool, 'name', str(tool)) for tool in self.tools[:3]]
                    if len(self.tools) > 3:
                        tool_names.append(f"... and {len(self.tools) - 3} more")
                    logger.info(f"Available tools: {', '.join(tool_names)}")
                else:
                    logger.warning("No tools returned from MCP servers")
                    self.tools = []
                
            finally:
                # Restore original logging levels
                logging.getLogger("httpx").setLevel(old_httpx_level)
                logging.getLogger("mcp.client.streamable_http").setLevel(old_mcp_level)
                
        except Exception as e:
            logger.error(f"Failed to initialize MCP client: {str(e)[:200]}")
            logger.debug(f"Full error details: {e}")
            self.tools = []
            self.mcp_client = None
            
            # Don't raise - allow agent to continue with fallback mode
            logger.warning("Continuing without MCP tools - using fallback mode")
    
    async def _validate_tool_consistency(self):
        """Validate tool count consistency and availability"""
        if self.mcp_client is None:
            logger.info("No MCP client initialized - skipping tool validation")
            return
        
        try:
            # Re-fetch tools to ensure consistency
            fresh_tools = await self.mcp_client.get_tools()
            
            if len(fresh_tools) != len(self.tools):
                logger.warning(f"Tool count mismatch! Initial: {len(self.tools)}, Fresh: {len(fresh_tools)}")
                self.tools = fresh_tools
            
            # Validate each tool is callable
            valid_tools = []
            for tool in self.tools:
                try:
                    # Basic validation - check if tool has required attributes
                    if hasattr(tool, 'name') and hasattr(tool, '_run'):
                        valid_tools.append(tool)
                    else:
                        logger.warning(f"Tool validation failed for: {getattr(tool, 'name', str(tool))}")
                except Exception as tool_error:
                    logger.warning(f"Tool validation error: {tool_error}")
            
            if len(valid_tools) != len(self.tools):
                logger.warning(f"Tool validation: {len(valid_tools)}/{len(self.tools)} tools are valid")
                self.tools = valid_tools
            
            logger.info(f"Tool validation complete: {len(self.tools)} valid tools")
            
        except Exception as e:
            logger.error(f"Tool validation failed: {e}")
    
    async def _execute_with_monitoring(self, initial_state, config):
        """Execute agent graph with tool call monitoring and debugging"""
        try:
            # Execute with enhanced monitoring
            result = await self.graph.ainvoke(initial_state, config=config)
            
            # Log tool calls and agent behavior
            if "messages" in result:
                tool_calls_found = 0
                agent_responses = 0
                
                for i, msg in enumerate(result["messages"]):
                    # Log tool calls
                    if hasattr(msg, 'tool_calls') and msg.tool_calls:
                        tool_calls_found += len(msg.tool_calls)
                        for tool_call in msg.tool_calls:
                            tool_name = tool_call.get('name', 'unknown')
                            tool_args = tool_call.get('args', {})
                            logger.info(f"🔧 Tool called: {tool_name} with args: {str(tool_args)[:100]}")
                            # Track tool calls for UI display
                            self.tool_calls.append({
                                "name": tool_name,
                                "args": tool_args,
                                "timestamp": datetime.now().isoformat()
                            })
                    
                    # Count agent responses
                    if hasattr(msg, 'content') and msg.content:
                        agent_responses += 1
                        if i == len(result["messages"]) - 1:  # Last message
                            logger.debug(f"Final response length: {len(msg.content)} chars")
                
                # Summary
                if tool_calls_found > 0:
                    logger.info(f"✓ Execution summary: {tool_calls_found} tool calls, {agent_responses} responses")
                else:
                    logger.info(f"✓ Execution summary: No tool calls (handled by LLM directly), {agent_responses} responses")
            
            return result
            
        except Exception as e:
            logger.error(f"Agent execution failed: {str(e)}")
            logger.error(f"Error details: {str(e.__class__.__name__)}: {str(e)[:200]}")
            raise

    async def _handle_initialization_failure(self, user_role: UserRole, error: Exception):
        """Handle initialization failure with graceful fallback"""
        logger.error(f"Initialization failed for role {user_role.value}: {error}")
        
        try:
            # Create fallback agent
            llm = self.llm_factory.get_llm(self.llm_provider)
            fallback_prompt = f"""You are an expert log analytics assistant with role-based access control.

Your role: {user_role.value}
{self._build_role_specific_prompt(user_role)}

Available capabilities:
- Basic text analysis and responses
- General assistance with log-related questions
- Conceptual explanations

Note: Advanced MCP tools are currently unavailable due to initialization issues."""
            
            self.graph = self._build_simple_agent(llm, fallback_prompt)
            self.tools = []
            self.mcp_client = None
            
            logger.info("Fallback agent created successfully")
            
        except Exception as fallback_error:
            logger.error(f"Failed to create fallback agent: {fallback_error}")
            raise fallback_error

    async def get_agent_status(self) -> Dict[str, Any]:
        """Get comprehensive agent status for monitoring"""
        status = {
            "initialized": self._initialized,
            "mcp_client_active": self.mcp_client is not None,
            "tools_count": len(self.tools),
            "user_role": self.user_role.value if hasattr(self, 'user_role') else None,
            "graph_created": self.graph is not None,
            "llm_provider": self.llm_provider,
            "memory_manager_active": self.memory_manager is not None
        }
        
        # Add tool details if available
        if self.tools:
            status["tool_names"] = [getattr(tool, 'name', str(tool)) for tool in self.tools]
            status["tool_details"] = []
            
            for tool in self.tools:
                tool_info = {
                    "name": getattr(tool, 'name', 'unnamed'),
                    "description": getattr(tool, 'description', 'No description available')
                }
                
                # Try to get input schema if available
                if hasattr(tool, 'args_schema') and tool.args_schema:
                    try:
                        tool_info["input_schema"] = tool.args_schema.schema()
                    except:
                        tool_info["input_schema"] = "Schema not available"
                elif hasattr(tool, 'args') and tool.args:
                    tool_info["input_schema"] = str(tool.args)
                else:
                    tool_info["input_schema"] = "No schema information"
                    
                status["tool_details"].append(tool_info)
        
        # Test MCP client connectivity if available
        if self.mcp_client:
            try:
                # Quick connectivity test
                test_tools = await self.mcp_client.get_tools()
                status["mcp_connectivity"] = "healthy"
                status["mcp_tools_accessible"] = len(test_tools)
            except Exception as e:
                status["mcp_connectivity"] = f"error: {str(e)[:50]}"
                status["mcp_tools_accessible"] = 0
        
        return status
    
    async def refresh_tools(self) -> bool:
        """Refresh MCP tools and rebuild agent if needed"""
        if not self.mcp_client:
            logger.warning("No MCP client available for tool refresh")
            return False
        
        try:
            logger.info("Refreshing MCP tools...")
            old_tool_count = len(self.tools)
            
            # Re-fetch tools
            self.tools = await self.mcp_client.get_tools()
            new_tool_count = len(self.tools)
            
            logger.info(f"Tool refresh: {old_tool_count} → {new_tool_count} tools")
            
            # Rebuild agent if tool count changed
            if old_tool_count != new_tool_count:
                logger.info("Tool count changed, rebuilding agent...")
                self._build_react_agent()
                logger.info("✓ Agent rebuilt with updated tools")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to refresh tools: {e}")
            return False
    
    def get_recent_tool_calls(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent tool calls for UI display"""
        return self.tool_calls[-limit:] if self.tool_calls else []
    
    def clear_tool_calls(self):
        """Clear tool call history"""
        self.tool_calls = []
    
    async def get_available_tools_with_descriptions(self) -> Dict[str, Any]:
        """Get detailed information about all available tools with descriptions and schemas"""
        if not self.mcp_client:
            return {
                "success": False,
                "error": "MCP client not initialized",
                "tools": []
            }
        
        try:
            # Get tools from MCP client - this is the correct dynamic approach
            tools = await self.mcp_client.get_tools()
            
            tools_info = []
            for tool in tools:
                tool_info = {
                    "name": getattr(tool, 'name', 'unnamed'),
                    "description": getattr(tool, 'description', 'No description available'),
                    "available": True
                }
                
                # Try to get input schema
                if hasattr(tool, 'args_schema') and tool.args_schema:
                    try:
                        schema = tool.args_schema.schema()
                        tool_info["input_schema"] = {
                            "type": schema.get("type", "object"),
                            "properties": schema.get("properties", {}),
                            "required": schema.get("required", [])
                        }
                    except Exception as e:
                        tool_info["input_schema"] = f"Schema error: {str(e)}"
                elif hasattr(tool, 'args') and tool.args:
                    tool_info["input_schema"] = str(tool.args)
                else:
                    tool_info["input_schema"] = "No schema available"
                
                # Add server information if available
                if hasattr(tool, '_server_name'):
                    tool_info["server"] = tool._server_name
                
                tools_info.append(tool_info)
            
            return {
                "success": True,
                "total_tools": len(tools_info),
                "tools": tools_info,
                "last_updated": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error getting tool descriptions: {e}")
            return {
                "success": False,
                "error": f"Failed to get tool descriptions: {str(e)}",
                "tools": []
            }
# Factory function
def create_agent():
    """Create and return an agent instance"""
    return MCPAgent()