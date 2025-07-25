"""
Enhanced LangGraph Agent with Direct Response Support
"""
import os
import asyncio
import logging
import base64
from typing import Any, Dict, List, Optional, TypedDict, Annotated, Sequence, Literal, AsyncGenerator
import operator
from enum import Enum
import json

# LangChain imports
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langchain_core.tools import BaseTool, ToolException
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import StrOutputParser
from langchain_core.callbacks import AsyncCallbackHandler

# LangGraph imports
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.sqlite import SqliteSaver

# Local imports
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp_client.client import MCPLogAnalyticsClient
from agent.llm_providers import LLMProviderFactory, LLMProvider
from agent.memory_manager import MemoryManager

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# State definition
class AgentState(TypedDict):
    """Enhanced agent state with direct response support"""
    messages: Annotated[Sequence[BaseMessage], add_messages]
    user_id: str
    conversation_id: str
    attachments: List[Dict[str, Any]]
    dataset_id: Optional[str]
    file_id: Optional[str]
    current_task: Optional[str]
    errors: Annotated[List[str], operator.add]
    context: Dict[str, Any]
    llm_provider: Optional[str]
    stream_callback: Optional[Any]
    memory_context: Optional[Dict[str, Any]]
    use_tools: bool  # Flag to determine if tools should be used
    response_mode: str  # "direct" or "tools"

class ResponseMode(str, Enum):
    """Response modes for the agent"""
    DIRECT = "direct"
    TOOLS = "tools"
    HYBRID = "hybrid"

class AttachmentType(str, Enum):
    """Supported attachment types"""
    CSV = "csv"
    JSON = "json"
    LOG = "log"
    TEXT = "text"
    IMAGE = "image"
    DOCUMENT = "document"
    UNKNOWN = "unknown"

class StreamingCallback(AsyncCallbackHandler):
    """Callback handler for streaming responses"""
    
    def __init__(self, queue: asyncio.Queue):
        self.queue = queue
    
    async def on_llm_new_token(self, token: str, **kwargs) -> None:
        """Handle new token from LLM"""
        await self.queue.put(token)
    
    async def on_llm_end(self, response, **kwargs) -> None:
        """Handle LLM completion"""
        await self.queue.put(None)  # Signal end of stream

class LogAnalyticsAgent:
    """Enhanced agent with direct response capability"""
    
    def __init__(self, llm_provider: Optional[str] = None):
        # LLM setup
        self.llm_provider = llm_provider or os.getenv("DEFAULT_LLM_PROVIDER", "openai")
        self.llm_factory = LLMProviderFactory()
        self.llm = None
        
        # MCP client
        self.mcp_client = None
        
        # Memory manager
        self.memory_manager = MemoryManager()
        
        # Available tools from MCP servers
        self.available_tools = {}
        
        # Memory for state persistence - using None for now to avoid context manager issues
        self.memory = None
        
        # Graph
        self.graph = None
        
        # Add asyncio lock to prevent concurrent execution issues
        self._analysis_lock = asyncio.Lock()
        
        # Categories for direct response
        self.direct_response_categories = [
            "greeting", "farewell", "thanks", "acknowledgment",
            "clarification", "simple_question", "chat"
        ]
        
        logger.info(f"Agent initialized with provider: {self.llm_provider}")
    
    async def initialize(self):
        """Initialize agent components"""
        try:
            # Initialize LLM
            self.llm = self.llm_factory.get_llm(self.llm_provider)
            
            # Initialize MCP client
            self.mcp_client = MCPLogAnalyticsClient()
            await self.mcp_client.initialize()
            
            # Initialize memory manager
            await self.memory_manager.initialize()
            
            # Discover available tools from MCP servers
            await self._discover_mcp_tools()
            
            # Build graph
            self._build_graph()
            
            logger.info("✅ Agent initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize agent: {e}")
            raise
    
    async def _discover_mcp_tools(self):
        """Discover available tools from MCP servers dynamically"""
        self.available_tools = {}
        
        if not self.mcp_client:
            logger.warning("MCP client not initialized, skipping tool discovery")
            return
        
        # Get server status and available tools
        try:
            server_status = await self.mcp_client.get_server_status()
            for server_id, server_info in server_status.items():
                if server_info.get("connected", False):
                    tools = server_info.get("tools", [])
                    for tool_name in tools:
                        self.available_tools[tool_name] = {
                            "server": server_id,
                            "description": f"{tool_name} on {server_info['name']}"
                        }
            
            logger.info(f"Discovered {len(self.available_tools)} tools from {len(server_status)} MCP servers")
            
        except Exception as e:
            logger.error(f"Failed to discover MCP tools: {e}")
            self.available_tools = {}
    
    async def _execute_mcp_tool(self, tool_name: str, **kwargs) -> Dict[str, Any]:
        """Execute an MCP tool via the client"""
        if tool_name not in self.available_tools:
            return {"error": f"Tool {tool_name} not available"}
        
        server_id = self.available_tools[tool_name]["server"]
        
        try:
            result = await self.mcp_client._call_tool(server_id, tool_name, **kwargs)
            return result
        except Exception as e:
            logger.error(f"Error executing tool {tool_name}: {e}")
            return {"error": f"Tool execution failed: {str(e)}"}
    
    def _build_graph(self):
        """Build the enhanced LangGraph workflow with direct response"""
        workflow = StateGraph(AgentState)
        
        # Add nodes
        workflow.add_node("context_enrichment", self._context_enrichment_node)
        workflow.add_node("attachment_router", self._attachment_router_node)
        workflow.add_node("response_classifier", self._response_classifier_node)
        workflow.add_node("direct_response", self._direct_response_node)
        workflow.add_node("agent_with_tools", self._agent_with_tools_node)
        workflow.add_node("tools", self._tools_node)
        
        # Set entry point
        workflow.set_entry_point("context_enrichment")
        
        # Add edges
        workflow.add_edge("context_enrichment", "attachment_router")
        workflow.add_edge("attachment_router", "response_classifier")
        
        # Conditional routing based on response mode
        workflow.add_conditional_edges(
            "response_classifier",
            self._route_response_mode,
            {
                ResponseMode.DIRECT: "direct_response",
                ResponseMode.TOOLS: "agent_with_tools"
            }
        )
        
        # Direct response goes straight to END
        workflow.add_edge("direct_response", END)
        
        # Tool-based response flow
        workflow.add_conditional_edges(
            "agent_with_tools",
            self._should_continue,
            {
                "tools": "tools",
                "end": END
            }
        )
        
        workflow.add_edge("tools", "agent_with_tools")
        
        # Compile the graph with configuration
        self.graph = workflow.compile(
            checkpointer=None,  # Disable checkpointer for now to avoid context manager issues
        )
    
    async def _context_enrichment_node(self, state: AgentState) -> Dict[str, Any]:
        """Enrich state with memory context"""
        # Get memory context from memory manager
        memory_context = await self.memory_manager.get_context(
            user_id=state["user_id"],
            conversation_id=state["conversation_id"]
        )
        
        if memory_context and (memory_context.get("summary") or memory_context.get("long_term_memory")):
            # Add context to messages
            context_parts = []
            
            if memory_context.get("summary"):
                context_parts.append(f"Previous conversation summary: {memory_context['summary']}")
            
            if memory_context.get("long_term_memory"):
                context_parts.append(f"Important context: {json.dumps(memory_context['long_term_memory'], indent=2)}")
            
            context_message = SystemMessage(content="\n".join(context_parts))
            
            return {
                "messages": [context_message],
                "memory_context": memory_context
            }
        
        return {"memory_context": memory_context}
    
    async def _attachment_router_node(self, state: AgentState) -> Dict[str, Any]:
        """Handle attachments before processing"""
        attachments = state.get("attachments", [])
        if not attachments:
            return {}
        
        updates = {}
        
        for attachment in attachments:
            try:
                result = await self._handle_attachment(attachment, state["user_id"])
                
                if "dataset_id" in result:
                    updates["dataset_id"] = result["dataset_id"]
                    msg = f"Dataset '{attachment['name']}' uploaded successfully with ID: {result['dataset_id']}"
                    updates["messages"] = [SystemMessage(content=msg)]
                    # Force tool usage for dataset analysis
                    updates["response_mode"] = ResponseMode.TOOLS
                    
                elif "file_id" in result:
                    updates["file_id"] = result["file_id"]
                    msg = f"File '{attachment['name']}' uploaded successfully with ID: {result['file_id']}"
                    updates["messages"] = [SystemMessage(content=msg)]
                    
            except Exception as e:
                logger.error(f"Error handling attachment: {e}")
                updates["errors"] = [f"Failed to upload {attachment['name']}: {str(e)}"]
        
        return updates
    
    async def _response_classifier_node(self, state: AgentState) -> Dict[str, Any]:
        """Classify whether to use direct response or tools"""
        messages = state["messages"]
        last_message = messages[-1] if messages else None
        
        if not last_message or not hasattr(last_message, 'content'):
            return {"response_mode": ResponseMode.TOOLS}
        
        last_message_content = last_message.content
        if isinstance(last_message_content, list):
            # Handle case where content is a list
            last_message_content = " ".join([str(item) for item in last_message_content])
        elif not isinstance(last_message_content, str):
            last_message_content = str(last_message_content)
        
        # If forced mode is set (e.g., from attachments), use it
        if state.get("response_mode"):
            return {}
        
        # Check for direct response indicators
        direct_indicators = [
            "hello", "hi", "hey", "good morning", "good evening",
            "thank you", "thanks", "bye", "goodbye",
            "how are you", "what's up", "help", "ok", "okay",
            "yes", "no", "sure", "understood", "got it"
        ]
        
        # Check for tool-requiring indicators
        tool_indicators = [
            "analyze", "search", "find", "train", "cluster",
            "logs", "dataset", "graph", "web", "summarize",
            "similar", "pattern", "data", "file", "upload"
        ]
        
        lower_message = last_message_content.lower()
        
        # Check if it's a simple/direct response
        is_direct = any(indicator in lower_message for indicator in direct_indicators)
        requires_tools = any(indicator in lower_message for indicator in tool_indicators)
        
        # Has attachments or datasets
        has_data = state.get("dataset_id") or state.get("file_id")
        
        if has_data or requires_tools:
            response_mode = ResponseMode.TOOLS
        elif is_direct and not requires_tools:
            response_mode = ResponseMode.DIRECT
        else:
            # Let LLM decide by checking if it would use tools
            response_mode = ResponseMode.TOOLS  # Default to tools for complex queries
        
        return {"response_mode": response_mode}
    
    def _route_response_mode(self, state: AgentState) -> str:
        """Route based on response mode"""
        return state.get("response_mode", ResponseMode.TOOLS)
    
    async def _direct_response_node(self, state: AgentState) -> Dict[str, Any]:
        """Generate direct response without tools"""
        messages = state["messages"]
        
        if not self.llm:
            logger.error("LLM not initialized")
            return {"messages": [AIMessage(content="I'm sorry, but I'm not properly initialized. Please try again.")]}
        
        # Create a simple prompt for direct responses
        prompt = ChatPromptTemplate.from_messages([
            SystemMessage(content="""You are a helpful AI assistant. 
Provide a direct, conversational response to the user.
Be friendly and natural in your response."""),
            MessagesPlaceholder(variable_name="messages")
        ])
        
        # Use streaming if callback is provided
        if state.get("stream_callback"):
            llm_with_callback = self.llm.bind(callbacks=[state["stream_callback"]])
            response = await llm_with_callback.ainvoke(messages)
        else:
            response = await self.llm.ainvoke(messages)
        
        # Update conversation in memory
        response_content = response.content
        if isinstance(response_content, list):
            response_content = " ".join([str(item) for item in response_content])
        elif not isinstance(response_content, str):
            response_content = str(response_content)
        
        await self._update_memory(state, response_content)
        
        return {"messages": [response]}
    
    async def _agent_with_tools_node(self, state: AgentState) -> Dict[str, Any]:
        """Agent node that can use tools"""
        messages = state["messages"]
        
        if not self.llm:
            logger.error("LLM not initialized")
            return {"messages": [AIMessage(content="I'm sorry, but I'm not properly initialized. Please try again.")]}
        
        # Create context-aware prompt
        context_parts = []
        if state.get("dataset_id"):
            context_parts.append(f"Available dataset ID: {state['dataset_id']}")
        if state.get("file_id"):
            context_parts.append(f"Available file ID: {state['file_id']}")
        
        # Add memory context
        memory_context = state.get("memory_context", {})
        if memory_context and memory_context.get("summary"):
            context_parts.append(f"Previous context: {memory_context['summary']}")
        
        context = "\n".join(context_parts) if context_parts else "No additional context."
        
        prompt = ChatPromptTemplate.from_messages([
            SystemMessage(content=f"""You are an advanced log analytics and data science assistant.
            
Current context:
{context}

Your capabilities include:
- Analyzing logs and searching for patterns
- Processing datasets with machine learning
- Performing web searches for information
- Training scientific models
- Managing knowledge graphs
- Vector similarity search

Always think step by step and use the appropriate tools when needed.
If a dataset or file was uploaded, acknowledge it and offer relevant analysis options."""),
            MessagesPlaceholder(variable_name="messages")
        ])
        
        # Create tool schemas for LLM binding
        tool_schemas = []
        for tool_name, tool_info in self.available_tools.items():
            tool_schemas.append({
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": tool_info["description"],
                    "parameters": {
                        "type": "object",
                        "properties": {},  # Basic schema - could be enhanced
                        "additionalProperties": True
                    }
                }
            })
        
        # Bind tools to LLM if available
        if tool_schemas:
            llm_with_tools = self.llm.bind_tools(tool_schemas)
        else:
            llm_with_tools = self.llm
        
        # Use streaming if callback is provided
        if state.get("stream_callback"):
            llm_with_tools = llm_with_tools.bind(callbacks=[state["stream_callback"]])
        
        # Get response
        response = await llm_with_tools.ainvoke(messages)
        
        return {"messages": [response]}
    
    async def _tools_node(self, state: AgentState) -> Dict[str, Any]:
        """Execute tools called by the agent"""
        messages = state["messages"]
        last_message = messages[-1]
        
        tool_results = []
        
        # Check if the message has tool calls
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            for tool_call in last_message.tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call.get("args", {})
                
                # Execute the tool via MCP client
                result = await self._execute_mcp_tool(tool_name, **tool_args)
                
                # Create tool message
                from langchain_core.messages import ToolMessage
                tool_message = ToolMessage(
                    content=json.dumps(result, indent=2),
                    tool_call_id=tool_call["id"]
                )
                tool_results.append(tool_message)
        
        return {"messages": tool_results}
    
    def _should_continue(self, state: AgentState) -> str:
        """Determine if we should continue to tools or end"""
        messages = state["messages"]
        last_message = messages[-1]
        
        # Check if the message has tool calls (for newer LangChain versions)
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            return "tools"
        # Check for older LangChain versions
        elif hasattr(last_message, "additional_kwargs") and last_message.additional_kwargs.get("tool_calls"):
            return "tools"
        # Check for function_call in additional_kwargs (another format)
        elif hasattr(last_message, "additional_kwargs") and last_message.additional_kwargs.get("function_call"):
            return "tools"
        else:
            # No tools to call, end the conversation
            return "end"
    
    async def _handle_attachment(self, attachment: Dict[str, Any], user_id: str) -> Dict[str, Any]:
        """Process attachment and return metadata"""
        if not self.mcp_client:
            raise Exception("MCP client not initialized")
        
        attachment_type = self._get_attachment_type(
            attachment['name'],
            attachment.get('type', '')
        )
        
        if attachment_type in [AttachmentType.CSV, AttachmentType.JSON]:
            # Upload as dataset
            if hasattr(self.mcp_client, 'upload_dataset'):
                result = await self.mcp_client.upload_dataset(
                    user_id=user_id,
                    filename=attachment['name'],
                    content_base64=base64.b64encode(attachment['content']).decode()
                )
                return {"dataset_id": result["dataset_id"]}
            else:
                raise Exception("upload_dataset method not available")
        else:
            # Store as file
            if hasattr(self.mcp_client, 'store_file'):
                result = await self.mcp_client.store_file(
                    user_id=user_id,
                    filename=attachment['name'],
                    content=attachment['content'],
                    metadata={"type": attachment_type.value}
                )
                return {"file_id": result["file_id"]}
            else:
                raise Exception("store_file method not available")
    
    def _get_attachment_type(self, filename: str, content_type: str) -> AttachmentType:
        """Determine attachment type from filename and content type"""
        ext = filename.lower().split('.')[-1] if '.' in filename else ''
        
        if ext in ['csv'] or 'csv' in content_type:
            return AttachmentType.CSV
        elif ext in ['json'] or 'json' in content_type:
            return AttachmentType.JSON
        elif ext in ['log', 'txt'] or 'text' in content_type:
            return AttachmentType.LOG
        elif ext in ['jpg', 'jpeg', 'png', 'gif'] or 'image' in content_type:
            return AttachmentType.IMAGE
        elif ext in ['pdf', 'doc', 'docx'] or 'document' in content_type:
            return AttachmentType.DOCUMENT
        else:
            return AttachmentType.UNKNOWN
    
    async def _update_memory(self, state: AgentState, response_content: str):
        """Update memory with conversation"""
        try:
            messages = state["messages"]
            if len(messages) > 1:
                user_message = messages[-2]
                user_content = user_message.content
                if isinstance(user_content, list):
                    user_content = " ".join([str(item) for item in user_content])
                elif not isinstance(user_content, str):
                    user_content = str(user_content)
            else:
                user_content = ""
            
            await self.memory_manager.update_conversation(
                user_id=state["user_id"],
                conversation_id=state["conversation_id"],
                message={
                    "role": "user",
                    "content": user_content
                },
                response={
                    "role": "assistant",
                    "content": response_content
                },
                metadata={
                    "provider": state.get("llm_provider", self.llm_provider),
                    "response_mode": state.get("response_mode", ResponseMode.DIRECT)
                }
            )
        except Exception as e:
            logger.error(f"Failed to update memory: {e}")
    
    async def switch_llm_provider(self, provider: str):
        """Switch to a different LLM provider"""
        self.llm_provider = provider
        self.llm = self.llm_factory.get_llm(provider)
        logger.info(f"Switched to LLM provider: {provider}")
        
        # Rebuild graph with new LLM
        self._build_graph()
    
    async def analyze_stream(
        self,
        query: str,
        user_id: str,
        conversation_id: str,
        attachments: Optional[List[Dict]] = None
    ) -> AsyncGenerator[str, None]:
        """Analyze with streaming response"""
        if not self.graph:
            await self.initialize()
        
        # Create queue for streaming
        queue = asyncio.Queue()
        callback = StreamingCallback(queue)
        
        try:
            # Create initial state
            initial_state = AgentState(
                messages=[HumanMessage(content=query)],
                user_id=user_id,
                conversation_id=conversation_id,
                attachments=attachments or [],
                dataset_id=None,
                file_id=None,
                current_task=None,
                errors=[],
                context={},
                llm_provider=self.llm_provider,
                stream_callback=callback,
                memory_context=None,
                use_tools=True,
                response_mode=""
            )
            
            # Run the graph and stream results with recursion limit
            config = {
                "configurable": {"thread_id": f"{user_id}:{conversation_id}"},
                "recursion_limit": 50  # Increase recursion limit
            }
            result = await self.graph.ainvoke(initial_state, config=config)
            
            # Stream tokens
            while True:
                token = await queue.get()
                if token is None:
                    break
                yield token
            
        except Exception as e:
            logger.error(f"Error in stream analysis: {e}")
            yield f"\n\nError: {str(e)}"
    
    async def analyze(
        self,
        query: str,
        user_id: str,
        conversation_id: str,
        attachments: Optional[List[Dict]] = None
    ) -> str:
        """Analyze a query with optional attachments (non-streaming)"""
        async with self._analysis_lock:  # Prevent concurrent execution
            if not self.graph:
                await self.initialize()

            try:
                # Create initial state
                initial_state = AgentState(
                    messages=[HumanMessage(content=query)],
                    user_id=user_id,
                    conversation_id=conversation_id,
                    attachments=attachments or [],
                    dataset_id=None,
                    file_id=None,
                    current_task=None,
                    errors=[],
                    context={},
                    llm_provider=self.llm_provider,
                    stream_callback=None,
                    memory_context=None,
                    use_tools=True,
                    response_mode=""
                )
                
                # Run the graph with recursion limit
                config = {
                    "configurable": {"thread_id": f"{user_id}:{conversation_id}"},
                    "recursion_limit": 50  # Increase recursion limit
                }
                result = await self.graph.ainvoke(initial_state, config=config)
                
                # Extract the final response
                final_message = result["messages"][-1]
                
                # Update memory with final response
                try:
                    if isinstance(final_message.content, list):
                        content = " ".join([str(item) for item in final_message.content])
                    elif isinstance(final_message.content, str):
                        content = final_message.content
                    else:
                        content = str(final_message.content)
                    await self._update_memory(result, content)
                except Exception as e:
                    logger.warning(f"Failed to update memory: {e}")
                
                # Handle errors
                if result.get("errors"):
                    logger.warning(f"Errors during execution: {result['errors']}")
                    error_msg = "\n\nNote: " + "\n".join(result['errors'])
                    return final_message.content + error_msg
                    
                return final_message.content
                
            except Exception as e:
                logger.error(f"Error in analysis: {e}")
                return f"I encountered an error while processing your request: {str(e)}"

# Factory function
def create_agent():
    """Create and return an agent instance"""
    return LogAnalyticsAgent()