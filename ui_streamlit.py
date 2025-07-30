"""
Enhanced MCP Agent Streamlit UI with proper file upload and tool call display
"""
import streamlit as st
import asyncio
import base64
import json
import uuid
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
import concurrent.futures
import threading
import io

# Imports
from agent.agent import MCPAgent
from mcp_client.client import MCPClient
from utils.auth_utils import UserRole

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Page config
st.set_page_config(
    page_title="MCP Agent UI", 
    page_icon="🤖", 
    layout="wide",
    initial_sidebar_state="expanded"
)

st.title("🤖 Enhanced MCP Agent UI")
st.markdown("*Advanced log analytics with file management and detailed tool tracking*")

# CSS for better styling
st.markdown("""
<style>
.tool-call {
    background-color: #f0f2f6;
    padding: 10px;
    border-radius: 5px;
    margin: 5px 0;
    border-left: 4px solid #1f77b4;
}
.tool-success {
    border-left: 4px solid #28a745;
}
.tool-error {
    border-left: 4px solid #dc3545;
}
.file-upload-status {
    background-color: #e8f5e8;
    padding: 8px;
    border-radius: 4px;
    margin: 5px 0;
}
</style>
""", unsafe_allow_html=True)

# Initialize session state
def init_session_state():
    """Initialize session state variables"""
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "conversation_id" not in st.session_state:
        st.session_state.conversation_id = str(uuid.uuid4())
    if "tool_calls" not in st.session_state:
        st.session_state.tool_calls = []
    if "uploaded_files" not in st.session_state:
        st.session_state.uploaded_files = []
    if "agent_initialized" not in st.session_state:
        st.session_state.agent_initialized = False
    if "tool_names" not in st.session_state:
        st.session_state.tool_names = []

init_session_state()

# Sidebar configuration
with st.sidebar:
    st.header("⚙️ Configuration")
    
    # User role selection
    user_role = st.selectbox(
        "Select User Role",
        options=[role.value for role in UserRole],
        index=3,  # Default to ADMIN
        help="Different roles have different access permissions"
    )
    selected_role = UserRole(user_role)
    
    # LLM provider selection
    llm_provider = st.selectbox(
        "LLM Provider",
        options=["openai", "anthropic", "local"],
        index=0,
        help="Choose the language model provider"
    )
    
    # File management section
    st.header("📁 File Management")
    
    # Tool Discovery section
    st.header("🔧 Available Tools")
    
    if st.button("🔄 Refresh Tool List"):
        if "mcp_client" in st.session_state and st.session_state.mcp_client:
            try:
                with st.spinner("Refreshing tools..."):
                    # Get fresh agent status to see current tools
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        agent = MCPAgent(llm_provider=llm_provider)
                        status = loop.run_until_complete(agent.get_agent_status())
                        
                        if status.get("tool_names"):
                            st.session_state.tool_names = status["tool_names"]
                            st.success(f"✅ Found {len(status['tool_names'])} tools")
                        else:
                            st.warning("No tools found")
                    finally:
                        loop.close()
            except Exception as e:
                st.error(f"Error refreshing tools: {e}")
    
    # Display available tools
    if "tool_names" in st.session_state and st.session_state.tool_names:
        with st.expander("📋 Tool List", expanded=False):
            for tool_name in st.session_state.tool_names:
                st.markdown(f"• **{tool_name}**")
    else:
        st.info("Initialize agent to see available tools")
    
    # File upload
    uploaded_files = st.file_uploader(
        "Upload Files",
        accept_multiple_files=True,
        type=["csv", "json", "txt", "log", "pdf"],
        help="Upload files to be processed with your queries"
    )
    
    # Display uploaded files
    if uploaded_files:
        st.subheader("📎 Files Ready for Upload")
        for file in uploaded_files:
            st.markdown(f"- **{file.name}** ({file.type}, {len(file.getvalue())} bytes)")
    
    # Database file management
    st.subheader("🗄️ Database Actions")
    
    if st.button("🔍 List Database Files"):
        st.session_state.action_requested = "list_files"
    
    if st.button("🔧 Sync File Metadata"):
        st.session_state.action_requested = "sync_metadata"
    
    if st.button("🩺 Health Check"):
        st.session_state.action_requested = "health_check"
    
    if st.button("🧹 Clear Chat History"):
        st.session_state.messages = []
        st.session_state.tool_calls = []
        st.session_state.conversation_id = str(uuid.uuid4())
        st.rerun()

# Event loop management
@st.cache_resource
def get_event_loop():
    """Create a persistent event loop for async operations"""
    def create_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop
    
    def run_loop(loop):
        try:
            loop.run_forever()
        except Exception as e:
            logger.error(f"Event loop error: {e}")
    
    loop = create_loop()
    thread = threading.Thread(target=run_loop, args=(loop,), daemon=True)
    thread.start()
    return loop

def run_async(coro, timeout=60):
    """Run async function in the event loop"""
    loop = get_event_loop()
    try:
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        st.error(f"⏰ Operation timed out after {timeout} seconds")
        return None
    except Exception as e:
        st.error(f"❌ Async operation error: {e}")
        return None

# Agent initialization
@st.cache_resource
def get_agent_and_client(_role: UserRole, _provider: str):
    """Initialize agent and client with caching"""
    async def init_services():
        try:
            client = MCPClient(provider=_provider)
            await client.initialize(_role)
            
            agent = MCPAgent(llm_provider=_provider)
            await agent.initialize(_role)
            
            return client, agent
        except Exception as e:
            logger.error(f"Failed to initialize services: {e}")
            raise
    
    return run_async(init_services())

# Function to upload files to database
async def upload_files_to_db(files: List, client: MCPClient, user_id: str) -> List[Dict[str, Any]]:
    """Upload files to database and return file info"""
    upload_results = []
    
    for file in files:
        try:
            # Read file content
            file_content = file.getvalue()
            
            # Create upload query
            upload_query = f"""Upload a file named '{file.name}' with content type '{file.type}' 
            to the database for user '{user_id}'. The file size is {len(file_content)} bytes."""
            
            # Use the agent to upload the file
            result = await client.process_query(
                query=upload_query,
                user_id=user_id,
                conversation_id=st.session_state.conversation_id,
                user_role=selected_role
            )
            
            upload_results.append({
                "filename": file.name,
                "size": len(file_content),
                "type": file.type,
                "status": "success" if "success" in result.lower() else "error",
                "result": result
            })
            
        except Exception as e:
            upload_results.append({
                "filename": file.name,
                "status": "error",
                "error": str(e)
            })
    
    return upload_results

# Display tool calls
def display_tool_calls():
    """Display tool calls in an expandable section"""
    if st.session_state.tool_calls:
        with st.expander(f"🔧 Tool Calls ({len(st.session_state.tool_calls)})", expanded=False):
            for i, call in enumerate(st.session_state.tool_calls):
                status_class = "tool-success" if call.get("status") == "success" else "tool-error"
                
                st.markdown(f"""
                <div class="tool-call {status_class}">
                    <strong>Tool {i+1}:</strong> {call.get('tool_name', 'Unknown')} <br>
                    <strong>Status:</strong> {call.get('status', 'Unknown')} <br>
                    <strong>Time:</strong> {call.get('timestamp', 'Unknown')}
                </div>
                """, unsafe_allow_html=True)
                
                if call.get("input"):
                    st.json(call["input"])
                if call.get("output"):
                    st.text(str(call["output"])[:500] + "..." if len(str(call["output"])) > 500 else str(call["output"]))

# Main content area
col1, col2 = st.columns([3, 1])

with col1:
    # Initialize services
    try:
        client, agent = get_agent_and_client(selected_role, llm_provider)
        if client and agent:
            st.success(f"✅ MCP services initialized for role: **{selected_role.value}**")
            st.session_state.agent_initialized = True
        else:
            st.error("❌ Failed to initialize MCP services")
            st.stop()
    except Exception as e:
        st.error(f"❌ Initialization error: {e}")
        st.stop()
    
    # Handle sidebar actions
    if hasattr(st.session_state, 'action_requested'):
        action = st.session_state.action_requested
        delattr(st.session_state, 'action_requested')
        
        with st.spinner(f"Executing {action}..."):
            if action == "list_files":
                result = run_async(client.process_query(
                    "List all uploaded files in the database",
                    user_id="ui_user",
                    conversation_id=st.session_state.conversation_id,
                    user_role=selected_role
                ))
                st.info(result)
            
            elif action == "sync_metadata":
                result = run_async(client.process_query(
                    "Sync file metadata and clean up orphaned entries",
                    user_id="ui_user", 
                    conversation_id=st.session_state.conversation_id,
                    user_role=selected_role
                ))
                st.info(result)
            
            elif action == "health_check":
                health_result = run_async(client.health_check(selected_role))
                st.json(health_result)
    
    # Chat interface
    st.subheader("💬 Chat Interface")
    
    # Display conversation history
    for msg in st.session_state.messages:
        if msg["role"] == "user":
            with st.chat_message("user"):
                st.markdown(msg["content"])
                if msg.get("files"):
                    st.markdown("📎 **Files attached:**")
                    for file_info in msg["files"]:
                        st.markdown(f"- {file_info['filename']} ({file_info.get('status', 'unknown')})")
        else:
            with st.chat_message("assistant"):
                st.markdown(msg["content"])
    
    # Chat input
    user_input = st.chat_input("Type your message here...")
    
    # Process user input
    if user_input:
        # Add user message to history
        user_msg = {"role": "user", "content": user_input}
        
        # Handle file uploads if any
        file_upload_results = []
        if uploaded_files:
            with st.spinner("📤 Uploading files..."):
                file_upload_results = run_async(upload_files_to_db(
                    uploaded_files, client, "ui_user"
                ))
                user_msg["files"] = file_upload_results
                
                # Display upload status
                for result in file_upload_results:
                    if result["status"] == "success":
                        st.success(f"✅ Uploaded: {result['filename']}")
                    else:
                        st.error(f"❌ Failed to upload: {result['filename']}")
        
        st.session_state.messages.append(user_msg)
        
        # Process the query
        with st.spinner("🤖 Agent is thinking..."):
            try:
                # Enhanced query with file context
                enhanced_query = user_input
                if file_upload_results:
                    file_context = "\n".join([
                        f"File '{r['filename']}' ({r['status']})" 
                        for r in file_upload_results
                    ])
                    enhanced_query = f"{user_input}\n\nUploaded files:\n{file_context}"
                
                # Track tool calls
                start_time = datetime.now()
                
                response = run_async(agent.analyze(
                    query=enhanced_query,
                    user_id="ui_user",
                    conversation_id=st.session_state.conversation_id,
                    user_role=selected_role
                ))
                
                # Simulate tool call tracking (in real implementation, this would come from agent)
                st.session_state.tool_calls.append({
                    "tool_name": "agent.analyze",
                    "status": "success",
                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                    "input": {"query": user_input[:100] + "..." if len(user_input) > 100 else user_input},
                    "output": response[:200] + "..." if len(response) > 200 else response
                })
                
                # Add assistant response
                st.session_state.messages.append({
                    "role": "assistant", 
                    "content": response
                })
                
                st.rerun()
                
            except Exception as e:
                error_msg = f"❌ Error processing query: {str(e)}"
                st.error(error_msg)
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": error_msg
                })
                st.rerun()

with col2:
    st.subheader("📊 Status Panel")
    
    # Connection status
    if st.session_state.agent_initialized:
        st.success("🟢 Connected")
    else:
        st.error("🔴 Disconnected")
    
    # Role info
    st.info(f"**Role:** {selected_role.value}")
    st.info(f"**LLM:** {llm_provider}")
    
    # Session info
    st.markdown("**Session Info:**")
    st.text(f"Messages: {len(st.session_state.messages)}")
    st.text(f"Tool Calls: {len(st.session_state.tool_calls)}")
    st.text(f"Conv ID: {st.session_state.conversation_id[:8]}...")
    
    # Display tool calls
    display_tool_calls()

# Footer
st.markdown("---")
st.markdown("*Enhanced MCP Agent UI - Built with error handling and file management*")