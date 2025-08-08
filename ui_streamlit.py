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
import time

# Configure reduced logging before other imports
from utils.reduce_logging import configure_logging
configure_logging()

# Imports
from agent.mcp_agent import MCPAgent
from mcp_client.client import MCPClient
from utils.auth_utils import UserRole

# Configure logging
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
    """Initialize session state variables with robust error handling"""
    # Core state variables - initialize ALL before any use
    defaults = {
        "messages": [],
        "conversation_id": str(uuid.uuid4()),
        "tool_calls": [],
        "uploaded_files": [],
        "agent_initialized": False,
        "tool_names": [],
        "user_id": "ui_user",
        "datasets": [],
        "real_time_tool_calls": [],
        "selected_role": UserRole.ADMIN,
        "action_requested": None,
        "file_upload_status": {},
        "last_dataset_check": None
    }
    
    # Initialize all defaults
    for key, default_value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = default_value
    
    # Ensure conversation_id is always a valid string
    if not st.session_state.conversation_id or not isinstance(st.session_state.conversation_id, str):
        st.session_state.conversation_id = str(uuid.uuid4())
        logger.info(f"Reset conversation_id to: {st.session_state.conversation_id}")

# Call initialization at module level
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
    
    # Store selected role in session state
    st.session_state.selected_role = selected_role
    
    # File management section
    st.header("📁 File Management")
    
    # Quick actions
    if st.button("🔍 List Uploaded Files"):
        st.session_state.action_requested = "list_files"
    
    if st.button("📊 Get Datasets"):
        st.session_state.action_requested = "get_datasets"
    
    # Tool Discovery section
    st.header("🔧 Available Tools")
    
    if st.button("🔄 Refresh Tool List"):
        if st.session_state.agent_initialized:
            try:
                with st.spinner("Refreshing tools..."):
                    # Get fresh agent status to see current tools using run_async
                    async def refresh_tools():
                        agent = MCPAgent(llm_provider=llm_provider)
                        await agent.initialize(selected_role)
                        status = await agent.get_agent_status()
                        return status
                    
                    status = run_async(refresh_tools())
                    
                    if status and status.get("tool_names"):
                        st.session_state.tool_names = status["tool_names"]
                        st.success(f"✅ Found {len(status['tool_names'])} tools")
                    else:
                        st.warning("No tools found")
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
        st.session_state.real_time_tool_calls = []
        st.session_state.conversation_id = str(uuid.uuid4())
        st.rerun()
    
    # Demo queries section
    st.header("🚀 Quick Demo Queries")
    
    demo_queries = [
        "Use list_uploaded_files and list_available_datasets tools to show all my uploaded files and available datasets with their details",
        "First call list_uploaded_files to find my CSV files, then perform clustering analysis on the first CSV dataset found", 
        "Show me the health status of all MCP servers using health check tools",
        "Upload and analyze any CSV file I provide - automatically detect the dataset and perform clustering"
    ]
    
    for i, query in enumerate(demo_queries):
        if st.button(f"📝 Try: {query[:30]}...", key=f"sidebar_demo_{i}"):
            st.session_state.demo_query = query

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

# Agent initialization with better error handling
@st.cache_resource
def get_agent_and_client(_role: UserRole, _provider: str):
    """Initialize agent and client with caching and better error handling"""
    async def init_services():
        try:
            # Initialize agent
            agent = MCPAgent(llm_provider=_provider)
            await agent.initialize(_role)
            
            # Initialize client
            client = MCPClient(provider=_provider)
            await client.initialize(_role)
            
            return client, agent
        except Exception as e:
            logger.error(f"Failed to initialize services: {e}")
            raise
    
    return run_async(init_services())

# Function to get datasets
async def get_available_datasets(agent: MCPAgent, user_id: str) -> List[Dict[str, Any]]:
    """Get available datasets for the user"""
    try:
        # First try to get uploaded files
        files_query = f"""
        Please list all uploaded files and datasets available for user {user_id}. 
        Use the list_uploaded_files and list_available_datasets tools if available.
        Show details like filenames, sizes, types, and when they were uploaded.
        """
        
        result = await agent.analyze(
            query=files_query,
            user_id=user_id,
            conversation_id=st.session_state.conversation_id,
            user_role=st.session_state.get('selected_role', UserRole.VIEWER)
        )
        
        # Track this tool call
        track_tool_calls(agent)
        
        # Also try to refresh datasets from the agent directly
        if hasattr(agent, 'refresh_datasets'):
            dataset_count = await agent.refresh_datasets(user_id)
            available_datasets = agent.available_datasets if hasattr(agent, 'available_datasets') else []
        else:
            available_datasets = []
            dataset_count = 0
        
        # Return combined information
        return [{
            "name": f"Dataset Discovery Results ({dataset_count} found)",
            "result": result,
            "timestamp": datetime.now().isoformat(),
            "agent_datasets": available_datasets,
            "count": dataset_count
        }]
        
    except Exception as e:
        logger.error(f"Failed to get datasets: {e}")
        return [{
            "name": "Dataset Discovery Error", 
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }]

# Function to upload files to database
async def upload_files_to_db(files: List, agent: MCPAgent, user_id: str) -> List[Dict[str, Any]]:
    """Upload files to database using MCP agent and return file info - IMPROVED"""
    upload_results = []
    
    # Ensure session state is available with safe defaults
    try:
        conversation_id = getattr(st.session_state, 'conversation_id', str(uuid.uuid4()))
        selected_role = getattr(st.session_state, 'selected_role', UserRole.VIEWER)
        file_upload_status = getattr(st.session_state, 'file_upload_status', {})
    except Exception as e:
        # Fallback for thread issues
        conversation_id = str(uuid.uuid4())
        selected_role = UserRole.VIEWER
        file_upload_status = {}
        logger.warning(f"Session state access issue: {e}, using fallbacks")
    
    # Make sure conversation_id is valid
    if not conversation_id or not isinstance(conversation_id, str):
        conversation_id = str(uuid.uuid4())
    
    for file in files:
        try:
            # Read file content
            file_content = file.getvalue()
            
            # Convert to base64 for proper encoding - CRITICAL FIX
            if isinstance(file_content, bytes):
                encoded_content = base64.b64encode(file_content).decode('utf-8')
            else:
                encoded_content = base64.b64encode(file_content.encode('utf-8')).decode('utf-8')
            
            # CRITICAL: Ensure clean base64 - remove any whitespace that might cause validation errors
            encoded_content = encoded_content.replace('\n', '').replace('\r', '').replace(' ', '')
            
            # Validate base64 locally before sending to tool
            try:
                # Test decode to ensure it's valid base64
                test_decode = base64.b64decode(encoded_content, validate=True)
                logger.info(f"✅ Base64 validation passed for {file.name}: {len(test_decode)} bytes decoded")
            except Exception as b64_error:
                logger.error(f"❌ Base64 validation failed for {file.name}: {b64_error}")
                raise Exception(f"Base64 validation failed: {b64_error}")
            
            # Create a more structured upload query with explicit tool calling instructions
            upload_query = f"""
            CRITICAL TASK: Upload file "{file.name}" for user {user_id} using the upload_file tool.

            File details:
            - Filename: {file.name}
            - Size: {len(file_content)} bytes  
            - Content Type: {file.type}
            - Base64 content length: {len(encoded_content)} characters
            - First 50 chars of base64: {encoded_content[:50]}...

            INSTRUCTIONS:
            1. Call the upload_file tool with these EXACT parameters:
               - filename: "{file.name}"
               - content: "{encoded_content}"
               - content_type: "{file.type}"
            
            2. The user_id ({user_id}) should be automatically extracted from headers
            
            3. Return the file_id and confirm successful upload
            
            4. If there are ANY errors with base64 validation, report them immediately
            
            DO NOT modify the base64 content - use it exactly as provided.
            """
            
            # Track upload start (safely)
            try:
                file_upload_status[file.name] = "uploading"
                if hasattr(st.session_state, 'file_upload_status'):
                    st.session_state.file_upload_status[file.name] = "uploading"
            except:
                pass  # Ignore session state errors in threads
            
            # Use agent to upload with timeout
            result = await agent.analyze(
                query=upload_query,
                user_id=user_id,
                conversation_id=conversation_id,
                user_role=selected_role
            )
            
            # Mark as completed (safely)
            try:
                file_upload_status[file.name] = "completed"
                if hasattr(st.session_state, 'file_upload_status'):
                    st.session_state.file_upload_status[file.name] = "completed"
            except:
                pass  # Ignore session state errors in threads
            
            upload_results.append({
                "filename": file.name,
                "size": len(file_content),
                "content_type": file.type,
                "upload_result": result,
                "status": "success",
                "timestamp": datetime.now().isoformat()
            })
            
            logger.info(f"✅ Successfully processed upload for {file.name}")
            
            # CRITICAL: Invalidate dataset cache to ensure new files are discovered
            try:
                if hasattr(agent, 'invalidate_dataset_cache'):
                    agent.invalidate_dataset_cache(user_id)
                    logger.info(f"Invalidated dataset cache after upload: {file.name}")
            except Exception as cache_error:
                logger.warning(f"Failed to invalidate cache: {cache_error}")
            
        except Exception as e:
            error_msg = f"Failed to upload {file.name}: {str(e)}"
            logger.error(error_msg)
            
            # Mark as failed (safely)
            try:
                file_upload_status[file.name] = "failed"
                if hasattr(st.session_state, 'file_upload_status'):
                    st.session_state.file_upload_status[file.name] = "failed"
            except:
                pass  # Ignore session state errors in threads
            
            upload_results.append({
                "filename": file.name,
                "status": "error",
                "error": error_msg,
                "timestamp": datetime.now().isoformat()
            })
    
    return upload_results

# Real-time tool call tracking
def track_tool_calls(agent: MCPAgent):
    """Track tool calls from agent in real-time"""
    try:
        if hasattr(agent, 'current_tool_calls') and agent.current_tool_calls:
            # Get new calls since last check
            existing_count = len(st.session_state.real_time_tool_calls)
            new_calls = agent.current_tool_calls[existing_count:]
            
            for call in new_calls:
                st.session_state.real_time_tool_calls.append({
                    "tool_name": call.get("name", "Unknown"),
                    "status": call.get("status", "success"),
                    "timestamp": call.get("timestamp", datetime.now().isoformat()),
                    "input": call.get("args", {}),
                    "output": call.get("result", "Tool executed successfully")
                })
                
            # Also check for traditional tool_calls
        if hasattr(agent, 'tool_calls') and agent.tool_calls:
            for call in agent.tool_calls[-3:]:  # Get last 3 calls
                st.session_state.real_time_tool_calls.append({
                    "tool_name": call.get("name", "Unknown"),
                    "status": call.get("status", "success"),
                    "timestamp": call.get("timestamp", datetime.now().isoformat()),
                    "input": call.get("args", {}),
                    "output": call.get("result", "Tool executed successfully")
                })
    except Exception as e:
        logger.error(f"Error tracking tool calls: {e}")

# Display tool calls with real-time updates
def display_tool_calls():
    """Display tool calls in a collapsible, organized format with real-time updates"""
    total_calls = len(st.session_state.tool_calls) + len(st.session_state.real_time_tool_calls)
    
    if total_calls > 0:
        # Summary header with count
        st.markdown(f"""
        <div style="background-color: #f0f2f6; padding: 10px; border-radius: 5px; margin: 10px 0;">
            <h4>🔧 Tool Execution Summary</h4>
            <p><strong>Total Calls:</strong> {total_calls} | <strong>Live Calls:</strong> {len(st.session_state.real_time_tool_calls)}</p>
        </div>
        """, unsafe_allow_html=True)
        
        # Group tool calls by operation type for better organization
        operation_groups = {
            "📊 Dataset Operations": [],
            "🤖 ML/AI Operations": [],
            "🔍 Search Operations": [],
            "⚙️ Other Operations": []
        }
        
        # Categorize tool calls
        all_calls = st.session_state.tool_calls + st.session_state.real_time_tool_calls
        for call in all_calls:
            tool_name = call.get('tool_name', 'Unknown').lower()
            if any(keyword in tool_name for keyword in ['dataset', 'upload', 'file', 'list']):
                operation_groups["📊 Dataset Operations"].append(call)
            elif any(keyword in tool_name for keyword in ['cluster', 'train', 'predict', 'model']):
                operation_groups["🤖 ML/AI Operations"].append(call)
            elif any(keyword in tool_name for keyword in ['search', 'web', 'datetime']):
                operation_groups["🔍 Search Operations"].append(call)
            else:
                operation_groups["⚙️ Other Operations"].append(call)
        
        # Display each group in collapsible sections
        for group_name, group_calls in operation_groups.items():
            if group_calls:
                with st.expander(f"{group_name} ({len(group_calls)} calls)", expanded=len(group_calls) <= 3):
                    for i, call in enumerate(group_calls):
                        status_emoji = "✅" if call.get("status") == "success" else "❌" if call.get("status") == "error" else "🔄"
                        is_realtime = call in st.session_state.real_time_tool_calls
                        realtime_label = " (Live)" if is_realtime else ""
                        
                        st.markdown(f"""
                        <div style="border-left: 3px solid {'#28a745' if call.get('status') == 'success' else '#dc3545' if call.get('status') == 'error' else '#ffc107'}; 
                                    padding: 8px; margin: 5px 0; background-color: #f8f9fa;">
                            <strong>{status_emoji} {call.get('tool_name', 'Unknown')}{realtime_label}</strong><br>
                            <small>⏱️ {call.get('timestamp', 'Unknown')}</small>
                        </div>
                        """, unsafe_allow_html=True)
                        
                        # Input/Output in nested expanders
                        col1, col2 = st.columns(2)
                        with col1:
                            if call.get("input"):
                                with st.expander("📥 Input", expanded=False):
                                    st.json(call["input"])
                        with col2:
                            if call.get("output"):
                                with st.expander("📤 Output", expanded=False):
                                    output_str = str(call["output"])
                                    st.text(output_str[:1000] + "..." if len(output_str) > 1000 else output_str)
    else:
        st.info("No tool calls yet. Send a query to see tool usage.")


def display_live_operation_status():
    """Display live operation status with progress indicators"""
    if st.session_state.real_time_tool_calls:
        latest_operations = st.session_state.real_time_tool_calls[-3:]  # Show last 3
        
        st.markdown("### 🔄 Current Operations")
        for op in latest_operations:
            status_color = {
                "running": "orange",
                "success": "green", 
                "error": "red"
            }.get(op.get("status", "unknown"), "gray")
            
            st.markdown(f"""
            <div style="display: flex; align-items: center; padding: 5px; margin: 2px 0; 
                        border-left: 4px solid {status_color}; background-color: #f8f9fa;">
                <span style="margin-right: 10px; font-size: 14px;">
                    {op.get('tool_name', 'Unknown Operation')}
                </span>
                <span style="color: {status_color}; font-size: 12px;">
                    {op.get('details', op.get('status', 'Unknown'))}
                </span>
            </div>
            """, unsafe_allow_html=True)

# Main content area
col1, col2 = st.columns([3, 1])

with col1:
    # Initialize services
    try:
        client, agent = get_agent_and_client(selected_role, llm_provider)
        if client and agent:
            st.success(f"✅ MCP services initialized for role: **{selected_role.value}**")
            st.session_state.agent_initialized = True
            st.session_state.agent = agent
            st.session_state.client = client
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
                result = run_async(agent.analyze(
                    "List all uploaded files in the database with their details and metadata",
                    user_id=st.session_state.user_id,
                    conversation_id=st.session_state.conversation_id,
                    user_role=selected_role
                ))
                track_tool_calls(agent)
                st.info(result)
            
            elif action == "get_datasets":
                datasets = run_async(get_available_datasets(agent, st.session_state.user_id))
                st.session_state.datasets = datasets
                if datasets:
                    st.success(f"Found {len(datasets)} datasets")
                    for dataset in datasets:
                        st.json(dataset)
                else:
                    st.warning("No datasets found")
            
            elif action == "sync_metadata":
                result = run_async(agent.analyze(
                    "Sync file metadata and clean up orphaned entries in the database",
                    user_id=st.session_state.user_id, 
                    conversation_id=st.session_state.conversation_id,
                    user_role=selected_role
                ))
                track_tool_calls(agent)
                st.info(result)
            
            elif action == "health_check":
                health_result = run_async(agent.get_agent_status())
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
                        status_emoji = "✅" if file_info.get("status") == "success" else "❌"
                        st.markdown(f"- {status_emoji} {file_info['filename']} ({file_info.get('status', 'unknown')})")
        else:
            with st.chat_message("assistant"):
                st.markdown(msg["content"])
                if msg.get("tool_calls_count", 0) > 0:
                    st.caption(f"🔧 Used {msg['tool_calls_count']} tools")
    
    # Chat input
    user_input = st.chat_input("Type your message here...")
    
    # Handle demo queries
    if hasattr(st.session_state, 'demo_query'):
        user_input = st.session_state.demo_query
        delattr(st.session_state, 'demo_query')
    
    # Process user input
    if user_input:
        # Add user message to history
        user_msg = {"role": "user", "content": user_input}
        
        # Handle file uploads if any
        file_upload_results = []
        if uploaded_files:
            with st.spinner("📤 Uploading files..."):
                file_upload_results = run_async(upload_files_to_db(
                    uploaded_files, agent, st.session_state.user_id
                ))
                
                # Ensure file_upload_results is not None
                if file_upload_results is None:
                    file_upload_results = []
                    st.error("❌ File upload failed - no results returned")
                else:
                    user_msg["files"] = file_upload_results
                
                # Display upload status
                for result in file_upload_results:
                    if result["status"] == "success":
                        st.success(f"✅ Uploaded: {result['filename']}")
                    else:
                        st.error(f"❌ Failed to upload: {result['filename']} - {result.get('error', 'Unknown error')}")
        
        st.session_state.messages.append(user_msg)
        
        # Process the query with real-time tool tracking
        with st.chat_message("assistant"):
            # Create placeholders for real-time updates
            status_placeholder = st.empty()
            tool_calls_placeholder = st.empty()
            response_placeholder = st.empty()
            
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
                    
                    # Process with real-time updates
                    start_time = datetime.now()
                    
                    # Clear previous real-time tool calls for this query
                    st.session_state.real_time_tool_calls = []
                    
                    status_placeholder.info("🔍 Starting analysis...")
                    
                    response = run_async(agent.analyze(
                        query=enhanced_query,
                        user_id=st.session_state.user_id,
                        conversation_id=st.session_state.conversation_id,
                        user_role=selected_role
                    ))
                    
                    # Track tool calls from agent after execution
                    track_tool_calls(agent)
                    
                    # Update real-time display
                    status_placeholder.success(f"✅ Analysis completed in {(datetime.now() - start_time).total_seconds():.2f}s")
                    
                    if st.session_state.real_time_tool_calls:
                        with tool_calls_placeholder.container():
                            st.markdown(f"**🔧 Tools Used: {len(st.session_state.real_time_tool_calls)}**")
                            for i, call in enumerate(st.session_state.real_time_tool_calls[-5:]):  # Show last 5
                                timestamp_short = call['timestamp'][-8:] if len(call['timestamp']) > 8 else call['timestamp']
                                status_emoji = "✅" if call.get('status') == 'success' else "❌"
                                st.markdown(f"• {status_emoji} {call['tool_name']} ({timestamp_short})")
                    else:
                        tool_calls_placeholder.info("No tools were called for this query")
                    
                    response_placeholder.markdown(response)
                    
                    # Add to permanent tool calls
                    st.session_state.tool_calls.extend(st.session_state.real_time_tool_calls)
                    
                    # Add assistant response
                    st.session_state.messages.append({
                        "role": "assistant", 
                        "content": response,
                        "tool_calls_count": len(st.session_state.real_time_tool_calls)
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
    
    # Quick demo queries
    st.header("🚀 Quick Demo")
    
    demo_queries = [
        "Use list_uploaded_files and list_available_datasets tools to show all my uploaded files and available datasets with their details",
        "First call list_uploaded_files to find my CSV files, then perform clustering analysis on the first CSV dataset found", 
        "Show me the health status of all MCP servers using health check tools",
        "Upload and analyze any CSV file I provide - automatically detect the dataset and perform clustering"
    ]
    
    for i, query in enumerate(demo_queries):
        if st.button(f"📝 {query}", key=f"demo_{i}"):
            st.session_state.demo_query = query
            user_input = query  # Simulate user input
            # Add user message to history
            user_msg = {"role": "user", "content": user_input}
            
            # Process the demo query
            with st.chat_message("assistant"):
                with st.spinner("🤖 Agent is processing demo query..."):
                    try:
                        response = run_async(agent.analyze(
                            query=user_input,
                            user_id=st.session_state.user_id,
                            conversation_id=st.session_state.conversation_id,
                            user_role=selected_role
                        ))
                        
                        # Track tool calls from agent
                        track_tool_calls(agent)
                        
                        # Display demo response
                        st.markdown(response)
                        
                        # Add to permanent tool calls
                        st.session_state.tool_calls.extend(st.session_state.real_time_tool_calls)
                        
                        # Add assistant response
                        st.session_state.messages.append({
                            "role": "assistant", 
                            "content": response,
                            "tool_calls_count": len(st.session_state.real_time_tool_calls)
                        })
                        
                        st.rerun()
                        
                    except Exception as e:
                        error_msg = f"❌ Error processing demo query: {str(e)}"
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
    st.text(f"User ID: {st.session_state.user_id}")
    st.text(f"Messages: {len(st.session_state.messages)}")
    total_tool_calls = len(st.session_state.tool_calls) + len(st.session_state.real_time_tool_calls)
    st.text(f"Tool Calls: {total_tool_calls}")
    st.text(f"Conv ID: {st.session_state.conversation_id[:8]}...")
    
    # Dataset info
    if st.session_state.datasets:
        st.markdown("**📊 Datasets:**")
        st.text(f"Available: {len(st.session_state.datasets)}")
    
    # Display tool calls
    display_tool_calls()

# Footer
st.markdown("---")
st.markdown("*Enhanced MCP Agent UI - Built with error handling and file management*")

def update_tool_call_progress(tool_name: str, status: str, details: str = "", input_data: dict = None, output_data: dict = None):
    """Update real-time tool call progress with enhanced feedback"""
    if tool_name and status:
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        # Create progress entry
        progress_entry = {
            "tool_name": tool_name,
            "status": status,
            "timestamp": timestamp,
            "details": details,
            "input": input_data or {},
            "output": output_data or {}
        }
        
        # Add to real-time tracking
        if "real_time_tool_calls" not in st.session_state:
            st.session_state.real_time_tool_calls = []
        
        st.session_state.real_time_tool_calls.append(progress_entry)
        
        # Show progress indicator in sidebar
        if status == "running":
            st.sidebar.info(f"🔄 {tool_name}: {details}")
        elif status == "success":
            st.sidebar.success(f"✅ {tool_name}: Completed")
        elif status == "error":
            st.sidebar.error(f"❌ {tool_name}: Failed - {details}")