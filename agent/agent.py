"""
Enhanced LangGraph Agent with MCP Integration
Uses langchain-mcp-adapters for proper MCP tool integration
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

# LangChain imports
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI

# LangGraph imports
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver  
from langgraph.prebuilt import ToolNode, tools_condition

# MCP Adapters
from langchain_mcp_adapters.client import MultiServerMCPClient

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
    metadata: Dict[str, Any]

class LogAnalyticsAgent:
    """MCP-integrated LangGraph agent for log analytics"""
    
    def __init__(self, llm_provider: Optional[str] = None):
        self.llm_provider = llm_provider or os.getenv("DEFAULT_LLM_PROVIDER", "openai")
        self.llm_factory = LLMProviderFactory()
        self.memory_manager = MemoryManager()
        self.mcp_client = None
        self.graph = None
        self.tools = []
        
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
    
    async def initialize(self):
        """Initialize agent with MCP client and tools"""
        try:
            # Initialize memory manager
            await self.memory_manager.initialize()
            
            # Initialize MCP client WITHOUT context manager
            config = self._load_config()
            self.mcp_client = MultiServerMCPClient(config)
            
            # Load tools from all servers
            self.tools = await self.mcp_client.get_tools()
            logger.info(f"Loaded {len(self.tools)} tools from MCP servers")
            
            # Build the agent graph
            self._build_graph()
            
            logger.info("✅ Agent initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize agent: {e}")
            # Don't raise - allow agent to work without tools
            self.tools = []
            self._build_graph()
    
    def _build_graph(self):
        """Build the LangGraph agent with MCP tools"""
        workflow = StateGraph(AgentState)
        
        # Add nodes
        workflow.add_node("agent", self._agent_node)
        if self.tools:
            workflow.add_node("tools", ToolNode(self.tools))
        
        # Add edges
        workflow.add_edge(START, "agent")
        
        if self.tools:
            workflow.add_conditional_edges(
                "agent",
                tools_condition,
                {"tools": "tools", "__end__": END}
            )
            workflow.add_edge("tools", "agent")
        else:
            workflow.add_edge("agent", END)
        
        # Use MemorySaver instead of SqliteSaver to avoid database issues
        memory = MemorySaver()
        self.graph = workflow.compile(checkpointer=memory)
    
    def _agent_node(self, state: AgentState) -> Dict[str, Any]:
        """Agent node that processes messages"""
        llm = self.llm_factory.get_llm(self.llm_provider)
        
        system_prompt = """You are an expert log analytics assistant with access to powerful tools.

Available capabilities:
- Store and query logs from MongoDB
- Perform vector similarity search with Milvus
- Search the web for information
- Train ML models with SciREX
- Manage file uploads and downloads

Always use the appropriate tools when they can help answer the user's question.
Provide clear, actionable insights based on the data available."""
        
        if self.tools:
            llm = llm.bind_tools(self.tools)
        
        messages = [SystemMessage(content=system_prompt)] + state["messages"]
        response = llm.invoke(messages)
        
        return {"messages": [response]}
    
    async def analyze(self, query: str, user_id: str, conversation_id: str) -> str:
        """Analyze a query using the agent"""
        if not self.graph:
            await self.initialize()
        
        try:
            # Create initial state
            initial_state = {
                "messages": [HumanMessage(content=query)],
                "user_id": user_id,
                "conversation_id": conversation_id,
                "metadata": {
                    "timestamp": datetime.utcnow().isoformat(),
                    "provider": self.llm_provider
                }
            }
            
            # Run the graph
            config = {"configurable": {"thread_id": f"{user_id}:{conversation_id}"}}
            result = await self.graph.ainvoke(initial_state, config=config)
            
            # Get the last message
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
            logger.error(f"Error during analysis: {e}")
            return f"I encountered an error: {str(e)}"
    
    async def cleanup(self):
        """Clean up resources"""
        await self.memory_manager.close()

# Factory function
def create_agent():
    """Create and return an agent instance"""
    return LogAnalyticsAgent()

# Graph visualization (simplified)
def visualize_agent_graph():
    """Visualize the LangGraph structure"""
    import asyncio
    
    async def _visualize():
        agent = create_agent()
        try:
            await agent.initialize()
            print("✅ Agent graph structure:")
            print("- START -> agent")
            if agent.tools:
                print("- agent -> tools")
                print("- tools -> agent")
                print("- agent -> END")
            else:
                print("- agent -> END")
            print(f"Tools available: {len(agent.tools)}")
        except Exception as e:
            print(f"Error: {e}")
        finally:
            await agent.cleanup()
    
    asyncio.run(_visualize())

if __name__ == "__main__":
    visualize_agent_graph()