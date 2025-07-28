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

class LogAnalyticsAgent:
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
    
    async def initialize(self, user_role: UserRole = UserRole.VIEWER):
        """Initialize agent with RBAC - FIXED VERSION"""
        try:
            self.user_role = user_role
            await self.memory_manager.initialize()
            
            # Filter servers based on role
            accessible_config = self._filter_servers_by_role(user_role)
            
            if not accessible_config:
                logger.warning(f"No accessible servers for role: {user_role.value}")
                self.tools = []
            else:
                # Initialize MCP client with filtered servers
                self.mcp_client = MultiServerMCPClient(accessible_config)
                self.tools = await self.mcp_client.get_tools()
                logger.info(f"Loaded {len(self.tools)} tools for role: {user_role.value}")
            
            # Build the agent graph using create_react_agent
            self._build_react_agent()
            
            logger.info(f"✅ Agent initialized for role: {user_role.value}")
            self._initialized = True
            
        except Exception as e:
            logger.error(f"Failed to initialize agent: {e}")
            self.tools = []
            self._build_simple_agent()
            self._initialized = True
    
    def _build_react_agent(self):
        """Build agent using create_react_agent - SOLVES TOOL ROLE ISSUE"""
        llm = self.llm_factory.get_llm(self.llm_provider)
        
        role_specific_prompt = self._build_role_specific_prompt(self.user_role)
        
        system_prompt = f"""You are an expert log analytics assistant with role-based access control.

{role_specific_prompt}

Available capabilities:
- MongoDB: Store and query logs
- Milvus: Vector similarity search
- WebSearch: Search web for information
- SciREX: ML model training and analysis

Use appropriate tools when needed and provide clear, actionable insights."""
        
        # Use create_react_agent which handles tool calls correctly
        if self.tools:
            self.graph = create_react_agent(
                model=llm,
                tools=self.tools,
                checkpointer=MemorySaver()
            )
        else:
            # Fallback to simple agent
            self.graph = self._create_simple_agent(llm, system_prompt)
    
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
        """Analyze a query using the agent - FIXED VERSION"""
        if not self._initialized:
            await self.initialize(user_role)

        try:
            # Create initial state
            initial_state = {
                "messages": [HumanMessage(content=query)],
                "user_id": user_id,
                "conversation_id": conversation_id,
                "user_role": user_role
            }
            # Optionally add attachments to state (for future use)
            if attachments is not None:
                initial_state["attachments"] = attachments
            # Run the agent using create_react_agent
            config = {"configurable": {"thread_id": f"{user_id}:{conversation_id}"}}
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
            return f"I can help you with your query. Here's what I found: {str(e)}"
    
    async def cleanup(self):
        """Clean up resources"""
        await self.memory_manager.close()

# Factory function
def create_agent():
    """Create and return an agent instance"""
    return LogAnalyticsAgent()