"""
Production MCP Agent
Clean implementation following MCP protocol with RBAC integration
"""
import asyncio
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver

# MCP Client
from langchain_mcp_adapters.client import MultiServerMCPClient

# Local imports
from config.settings import config
from .rbac_system import UserContext, UserRole, get_rbac_manager
from .llm_providers import LLM
from .memory_manager import MemoryManager

logger = logging.getLogger(__name__)

class MCPAgent:
    """
    Production MCP Agent with proper protocol compliance
    Features:
    - Dynamic tool discovery via MCP
    - RBAC-integrated access control  
    - Clean architecture with separation of concerns
    """
    
    def __init__(self):
        self.llm_factory = LLM()
        self.memory_manager = MemoryManager()
        self.rbac = get_rbac_manager()
        
        # MCP components
        self.mcp_client: Optional[MultiServerMCPClient] = None
        self.available_tools: List[BaseTool] = []
        
        # Agent state
        self.agent = None
        self.checkpointer = MemorySaver()
        self._current_user: Optional[UserContext] = None
        self._initialized = False
    
    async def initialize(self, user_context: UserContext) -> None:
        """Initialize agent for user context"""
        try:
            logger.info(f"Initializing MCP Agent for user {user_context.user_id} (role: {user_context.role})")
            
            self._current_user = user_context
            
            # Initialize memory manager
            await self.memory_manager.initialize()
            
            # Get accessible servers for user
            accessible_servers = self._get_user_servers(user_context)
            
            if not accessible_servers:
                logger.warning(f"No accessible MCP servers for user {user_context.user_id}")
                self.available_tools = []
            else:
                try:
                    # Initialize MCP client with user's accessible servers
                    self.mcp_client = MultiServerMCPClient(accessible_servers)
                    
                    # Discover tools dynamically
                    all_tools = await self.mcp_client.get_tools()
                    
                    # Filter tools based on user permissions
                    self.available_tools = self._filter_user_tools(all_tools, user_context)
                    
                    logger.info(f"Loaded {len(self.available_tools)} tools for {user_context.user_id}")
                    
                except Exception as mcp_error:
                    logger.warning(f"Failed to initialize MCP client for {user_context.user_id}: {mcp_error}")
                    logger.info("Proceeding with no tools available")
                    self.available_tools = []
                    self.mcp_client = None
            
            # Create agent with filtered tools
            await self._create_agent()
            
            self._initialized = True
            
        except Exception as e:
            logger.error(f"Failed to initialize MCP agent: {e}")
            raise
    
    def _get_user_servers(self, user_context: UserContext) -> Dict[str, Dict[str, str]]:
        """Get MCP servers accessible to user"""
        accessible_servers = self.rbac.get_accessible_servers(user_context)
        
        user_server_config = {}
        for server_name in accessible_servers:
            if server_name in config.servers:
                server_config = config.servers[server_name]
                user_server_config[server_name] = {
                    "url": server_config.url,
                    "transport": server_config.transport
                }
        
        return user_server_config
    
    def _filter_user_tools(self, tools: List[BaseTool], user_context: UserContext) -> List[BaseTool]:
        """Filter tools based on user permissions"""
        accessible_tools = []
        
        for tool in tools:
            tool_name = getattr(tool, 'name', str(tool))
            
            if self.rbac.check_tool_access(user_context, tool_name):
                accessible_tools.append(tool)
                logger.debug(f"Tool '{tool_name}' accessible to {user_context.user_id}")
            else:
                logger.debug(f"Tool '{tool_name}' filtered for {user_context.user_id}")
        
        return accessible_tools
    
    async def _create_agent(self) -> None:
        """Create LangGraph agent with filtered tools"""
        llm = self.llm_factory.get_llm(
            provider=config.llm.provider,
            model=config.llm.model,
            temperature=config.llm.temperature,
            max_tokens=config.llm.max_tokens,
        )
        
        # Simple, dynamic system prompt
        system_prompt = self._create_system_prompt()
        
        # Create agent
        try:
            self.agent = create_react_agent(
                model=llm,
                tools=self.available_tools,
                checkpointer=self.checkpointer,
                state_modifier=system_prompt
            )
        except TypeError:
            # Fallback for older LangGraph versions
            self.agent = create_react_agent(
                model=llm,
                tools=self.available_tools,
                checkpointer=self.checkpointer
            )
        
        logger.info(f"Created agent with {len(self.available_tools)} tools")
    
    def _create_system_prompt(self) -> str:
        """Create minimal, role-aware system prompt"""
        if not self._current_user:
            return "You are an AI assistant with access to various tools."
        
        role_context = {
            "admin": "You have administrative access to all available tools.",
            "developer": "You have development access for data processing and analysis.",
            "analyst": "Focus on data analysis and insights using available tools.",
            "viewer": "You have read-only access to available tools.",
            "user": "You have basic access to available tools."
        }
        
        context = role_context.get(self._current_user.role.value, "You are an AI assistant.")
        
        return f"""You are an AI assistant with access to {len(self.available_tools)} specialized tools.

Role: {self._current_user.role.value}
Context: {context}

Use the available tools to help users effectively. Each tool will provide its own documentation and capabilities when called.

Be helpful, accurate, and explain your actions when using tools."""
    
    async def process_message(
        self,
        message: str,
        conversation_id: str,
        user_context: UserContext,
        **kwargs
    ) -> str:
        """Process user message using available MCP tools.
        Enhancement: If user references a known dataset/file_id, fetch quick_analysis and inject into system prompt for this turn.
        """
        # Ensure agent is initialized for this user
        if (not self._initialized or 
            not self._current_user or 
            self._current_user.user_id != user_context.user_id):
            await self.initialize(user_context)
        # Attempt prompt enrichment with quick analysis
        enriched_system_prefix = ""
        try:
            dataset_hint = None
            # Simple heuristic: look for token that looks like file_id pattern user_ts_filename
            tokens = message.split()
            for t in tokens:
                if t.count('_') >= 2 and '.' in t:
                    dataset_hint = t.strip(',.;')
                    break
            if dataset_hint and self.mcp_client:
                # Attempt tool call to mongodb quick analysis retrieval
                for tool in self.available_tools:
                    if getattr(tool, 'name', '') in ('mongodb:get_quick_analysis','get_quick_analysis'):
                        try:
                            qa_res = await tool.ainvoke({"file_id": dataset_hint, "user_id": user_context.user_id})
                            if isinstance(qa_res, dict) and qa_res.get('success') and qa_res.get('quick_analysis'):
                                summary = qa_res['quick_analysis'].get('summary', {})
                                cols = summary.get('columns', [])
                                col_names = [c.get('name') for c in cols[:15]]
                                enriched_system_prefix = (
                                    "Dataset context (auto-injected):\n" \
                                    f"File ID: {dataset_hint}\nRows: {summary.get('rows')} Columns: {summary.get('columns')}\n" \
                                    f"Numeric Columns: {summary.get('numeric_columns')} Categorical Columns: {summary.get('categorical_columns')}\n" \
                                    f"Column Names (sample): {col_names}\n" \
                                    "Use this schema awareness for better answers.\n"
                                )
                        except Exception:
                            pass
        except Exception:
            pass
        try:
            state = {
                "messages": [HumanMessage(content=(enriched_system_prefix + message))]
            }
            config_dict = {
                "configurable": {"thread_id": f"{user_context.user_id}:{conversation_id}"},
                "recursion_limit": 50
            }
            result = await self.agent.ainvoke(state, config=config_dict)
            if result and "messages" in result and result["messages"]:
                last_message = result["messages"][-1]
                response = getattr(last_message, 'content', str(last_message))
            else:
                response = "I couldn't process your request. Please try again."
            await self.memory_manager.update_conversation(
                user_id=user_context.user_id,
                conversation_id=conversation_id,
                message={"role": "user", "content": message},
                response={"role": "assistant", "content": response}
            )
            return response
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            return f"I encountered an error processing your request: {str(e)}"
    
    async def chat(
        self,
        query: str,
        user_id: str,
        conversation_id: str,
        user_role: UserRole,
        **kwargs
    ) -> str:
        """Chat interface for compatibility with UI and client code"""
        # Create UserContext for the user
        user_context = self.rbac.create_user_context(user_id, user_role)
        
        # Delegate to process_message
        return await self.process_message(
            message=query,
            conversation_id=conversation_id,
            user_context=user_context,
            **kwargs
        )
    
    async def list_available_tools(self, user_context: UserContext) -> List[Dict[str, Any]]:
        """List tools available to user with schemas"""
        if (not self._initialized or 
            not self._current_user or 
            self._current_user.user_id != user_context.user_id):
            await self.initialize(user_context)
        
        tools_info = []
        for tool in self.available_tools:
            tool_info = {
                "name": getattr(tool, 'name', 'unknown'),
                "description": getattr(tool, 'description', 'No description available')
            }
            
            # Add schema if available
            try:
                if hasattr(tool, 'args_schema') and tool.args_schema:
                    if hasattr(tool.args_schema, 'model_json_schema'):
                        tool_info['schema'] = tool.args_schema.model_json_schema()
                    elif hasattr(tool.args_schema, 'schema'):
                        tool_info['schema'] = tool.args_schema.schema()
                else:
                    tool_info['schema'] = {"type": "object"}
            except Exception:
                tool_info['schema'] = {"type": "object"}
            
            tools_info.append(tool_info)
        
        return tools_info
    
    async def execute_tool_direct(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        user_context: UserContext
    ) -> Any:
        """Direct tool execution with permission check"""
        # Check permissions
        if not self.rbac.check_tool_access(user_context, tool_name):
            raise PermissionError(f"User {user_context.user_id} cannot execute tool '{tool_name}'")
        
        # Find tool
        tool = None
        for t in self.available_tools:
            if getattr(t, 'name', '') == tool_name:
                tool = t
                break
        
        if not tool:
            raise ValueError(f"Tool '{tool_name}' not found or not accessible")
        
        # Execute tool
        try:
            result = await tool.ainvoke(arguments)
            logger.info(f"Tool '{tool_name}' executed by user {user_context.user_id}")
            return result
        except Exception as e:
            logger.error(f"Tool execution failed: {e}")
            raise
    
    async def invoke_tool(self, tool_name: str, arguments: Dict[str, Any], user_role: UserRole) -> Any:
        """Invoke a tool by name with RBAC using a lightweight user context.
        Stabilized to prevent unintended re-initialization with placeholder user ids.
        """
        user_id = None
        try:
            if isinstance(arguments, dict):
                req = arguments.get('request') if isinstance(arguments.get('request'), dict) else None
                if req and req.get('user_id'):
                    user_id = req.get('user_id')
                elif arguments.get('user_id'):
                    user_id = arguments.get('user_id')
        except Exception:
            pass
        # Preserve existing agent user if none supplied
        if not user_id and self._current_user:
            user_id = self._current_user.user_id
        # Final fallback – stable name instead of changing roles mid-session
        if not user_id:
            user_id = 'session_user'
        # Build / reuse context
        if self._current_user and self._current_user.user_id == user_id and self._current_user.role == user_role:
            user_context = self._current_user
        else:
            user_context = self.rbac.create_user_context(user_id, user_role)
            # Only re-init if user actually changed
            if (not self._initialized or not self._current_user or self._current_user.user_id != user_context.user_id):
                await self.initialize(user_context)
        return await self.execute_tool_direct(tool_name, arguments, user_context)
    
    async def refresh_tools(self, user_context: UserContext) -> int:
        """Refresh available tools for user"""
        if self.mcp_client:
            try:
                # Re-discover tools from MCP servers
                all_tools = await self.mcp_client.get_tools()
                
                # Re-filter for user permissions
                self.available_tools = self._filter_user_tools(all_tools, user_context)
                
                # Rebuild agent
                await self._create_agent()
                
                logger.info(f"Refreshed {len(self.available_tools)} tools for {user_context.user_id}")
                return len(self.available_tools)
                
            except Exception as e:
                logger.error(f"Failed to refresh tools: {e}")
                # Try to re-initialize if refresh fails
                try:
                    await self.initialize(user_context)
                    return len(self.available_tools)
                except Exception as init_error:
                    logger.error(f"Failed to re-initialize after refresh failure: {init_error}")
                    return len(self.available_tools)
        else:
            # Try to initialize if no client exists
            try:
                await self.initialize(user_context)
                return len(self.available_tools)
            except Exception as e:
                logger.error(f"Failed to initialize MCP client during refresh: {e}")
                return 0
    
    async def get_health_status(self) -> Dict[str, Any]:
        """Get agent health status"""
        return {
            "initialized": self._initialized,
            "mcp_client_active": self.mcp_client is not None,
            "tools_count": len(self.available_tools),
            "current_user": self._current_user.user_id if self._current_user else None,
            "timestamp": datetime.utcnow().isoformat()
        }
    
    async def get_agent_status(self) -> Dict[str, Any]:
        """Get comprehensive agent status including tools"""
        tool_names = [getattr(tool, 'name', str(tool)) for tool in self.available_tools]
        
        return {
            "status": "ready" if self._initialized else "not_initialized",
            "tool_count": len(self.available_tools),
            "tool_names": tool_names,
            "user_id": self._current_user.user_id if self._current_user else None,
            "user_role": self._current_user.role.value if self._current_user else None,
            "mcp_client_active": self.mcp_client is not None,
            "memory_manager_active": self.memory_manager is not None,
            "timestamp": datetime.utcnow().isoformat()
        }
    
    async def cleanup(self) -> None:
        """Cleanup resources"""
        if self.memory_manager:
            await self.memory_manager.close()
        
        self._initialized = False
        self._current_user = None
        logger.info("MCP Agent cleaned up")

# Global agent instance management
_agent_instance: Optional[MCPAgent] = None
_agent_lock = asyncio.Lock()

async def get_agent() -> MCPAgent:
    """Get or create global agent instance"""
    global _agent_instance
    if _agent_instance is None:
        async with _agent_lock:
            if _agent_instance is None:
                _agent_instance = MCPAgent()
    return _agent_instance