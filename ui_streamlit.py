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

# Load environment variables first
from dotenv import load_dotenv
load_dotenv()

# Configure reduced logging before other imports
from utils.reduce_logging import configure_logging
configure_logging()

# Imports
from agent.mcp_agent import MCPAgent
from agent.rbac_system import UserContext, get_rbac_manager
from mcp_client.client import MCPClient
from utils.auth_utils import UserRole

# Configure logging
logger = logging.getLogger(__name__)

# Auto-refresh configuration for live updates - DISABLED
REFRESH_INTERVAL = 2  # seconds
LIVE_POLLING_ENABLED = False  # Disabled to prevent fragment errors

# Page config
st.set_page_config(
    page_title="MCP Agent UI", 
    page_icon="🤖", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# Disable fragments globally to prevent errors
if hasattr(st, '_fragment_runner'):
    st._fragment_runner = None

# CRITICAL: Initialize session state FIRST before any other operations
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
        "user_id": "ui_user",  # Normalized consistent user_id
        "datasets": [],
        "real_time_tool_calls": [],
        "selected_role": UserRole.ADMIN,
        "action_requested": None,
        "file_upload_status": {},
        "last_dataset_check": None,
        # Tool approval system
        "require_tool_approval": False,
        "auto_approve_safe_tools": True,
        "safe_auto_tools": [
            'health_check', 'list_uploaded_files', 'list_available_datasets',
            'get_logs_by_date', 'data_inspect', 'get_agent_status'
        ],
        "pending_tool_calls": [],  # Each: {id, tool_name, args, status, created_at, context}
        "approved_tool_calls": [],
        "rejected_tool_calls": [],
        # Live polling state - DISABLED
        "query_in_progress": False,
        "last_tool_poll": None,
        "live_polling_enabled": False  # Disabled to prevent fragment errors
    }
    
    # Initialize all defaults
    for key, default_value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = default_value
    
    # Ensure conversation_id is always a valid string
    if not st.session_state.conversation_id or not isinstance(st.session_state.conversation_id, str):
        st.session_state.conversation_id = str(uuid.uuid4())
        logger.info(f"Reset conversation_id to: {st.session_state.conversation_id}")

# Call initialization immediately
init_session_state()

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

# Sidebar configuration
with st.sidebar:
    st.header("⚙️ Configuration")
    
    # User role selection
    user_role = st.selectbox(
        "Select User Role",
        options=[role.value for role in UserRole],
        index=0,  # Default to ADMIN (first in the enum)
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
    
    # Store selected role in session state and check for changes
    if 'current_role' not in st.session_state:
        st.session_state.current_role = selected_role
    
    # Detect role change and force re-initialization
    role_changed = st.session_state.current_role != selected_role
    if role_changed:
        logger.info(f"Role changed from {st.session_state.current_role.value} to {selected_role.value} - forcing re-initialization")
        st.session_state.current_role = selected_role
        st.session_state.agent_initialized = False  # Force re-initialization
        st.session_state.pop('agent', None)  # Clear cached agent
        st.session_state.pop('client', None)  # Clear cached client
        # Clear any Streamlit resource cache
        if hasattr(st, 'cache_resource'):
            st.cache_resource.clear()
        st.info(f"🔄 Role changed to {selected_role.value} - reinitializing services...")
        st.rerun()  # Force immediate rerun to apply changes
    
    st.session_state.selected_role = selected_role
    
    # Tool Approval Configuration
    st.header("🔐 Tool Approval Settings")
    
    require_approval = st.checkbox(
        "Require Tool Approval",
        value=st.session_state.get('require_tool_approval', False),
        help="When enabled, tools will require manual approval before execution"
    )
    st.session_state.require_tool_approval = require_approval
    
    if require_approval:
        auto_approve_safe = st.checkbox(
            "Auto-approve Safe Tools",
            value=st.session_state.get('auto_approve_safe_tools', True),
            help="Automatically approve safe read-only tools like health checks and file listings"
        )
        st.session_state.auto_approve_safe_tools = auto_approve_safe
        
        # Display safe tools list
        if auto_approve_safe:
            with st.expander("🔒 Safe Auto-approved Tools", expanded=False):
                safe_tools = st.session_state.get('safe_auto_tools', [])
                for tool in safe_tools:
                    st.markdown(f"• **{tool}**")
    
    # Show pending approvals count
    pending_count = len(st.session_state.get('pending_tool_calls', []))
    if pending_count > 0:
        st.warning(f"⏳ {pending_count} tool calls awaiting approval")
        if st.button("🔍 Review Pending Tools"):
            st.session_state.action_requested = "review_pending"
    
    # Live Polling Configuration
    st.header("🔄 Live Updates")
    
    live_polling = st.checkbox(
        "Enable Live Tool Monitoring",
        value=st.session_state.get('live_polling_enabled', True),
        help="Auto-refresh tool calls during query processing for real-time feedback"
    )
    st.session_state.live_polling_enabled = live_polling
    
    if live_polling:
        st.info("� Live tool monitoring disabled to prevent UI issues")
        
        # Show current status
        if st.session_state.get('query_in_progress', False):
            st.success("🟢 Query in progress - Live monitoring active")
        else:
            st.info("⚪ Ready - Live monitoring on standby")
    else:
        st.warning("⚠️ Live monitoring disabled - tool calls will only update after completion")
    
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
        if st.session_state.agent_initialized and st.session_state.get('agent'):
            try:
                with st.spinner("Refreshing tools..."):
                    # Use existing agent from session state
                    agent = st.session_state.agent
                    
                    # Get agent status directly
                    status = run_async(agent.get_agent_status())
                    
                    if status and status.get("tool_names"):
                        st.session_state.tool_names = status["tool_names"]
                        st.success(f"✅ Found {len(status['tool_names'])} tools")
                    else:
                        st.warning("No tools found")
            except Exception as e:
                st.error(f"Error refreshing tools: {e}")
        else:
            st.warning("Agent not initialized yet")
    
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
    
    # Chat Management section
    st.header("💬 Chat Management")
    
    # Show chat statistics
    if st.session_state.messages:
        user_msgs = len([m for m in st.session_state.messages if m["role"] == "user"])
        assistant_msgs = len([m for m in st.session_state.messages if m["role"] == "assistant"])
        st.info(f"📊 {user_msgs} queries, {assistant_msgs} responses")
        
        # Quick chat actions
        col1, col2 = st.columns(2)
        
        with col1:
            if st.button("🗑️ Delete Last", key="sidebar_delete"):
                if len(st.session_state.messages) >= 2:
                    st.session_state.messages = st.session_state.messages[:-2]
                elif len(st.session_state.messages) == 1:
                    st.session_state.messages = st.session_state.messages[:-1]
                st.rerun()
        
        with col2:
            if st.button("✏️ Edit Last", key="sidebar_edit"):
                # Find the last user message
                for i in reversed(range(len(st.session_state.messages))):
                    if st.session_state.messages[i]["role"] == "user":
                        st.session_state.edit_mode = True
                        st.session_state.edit_message_idx = i
                        st.session_state.edit_content = st.session_state.messages[i]["content"]
                        st.rerun()
                        break
        
        if st.button("🧹 Clear All Chat", key="sidebar_clear"):
            st.session_state.messages = []
            st.session_state.tool_calls = []
            st.session_state.real_time_tool_calls = []
            st.session_state.conversation_id = str(uuid.uuid4())
            st.rerun()
    else:
        st.info("No chat history yet")
    
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

# Agent initialization with better error handling (no caching to ensure role changes work)
def get_agent_and_client(role: UserRole, provider: str):
    """Initialize agent and client with proper role handling"""
    async def init_services():
        try:
            # Log the initialization
            logger.info(f"Initializing MCP services for role: {role.value}")
            
            agent = MCPAgent()
            # Create proper UserContext
            uid = st.session_state.get('user_id') if hasattr(st, 'session_state') else 'ui_user'
            rbac = get_rbac_manager()
            user_context = rbac.create_user_context(uid, role)
            await agent.initialize(user_context)
            
            # Get agent status and store tool names
            status = await agent.get_agent_status()
            if hasattr(st, 'session_state') and status.get("tool_names"):
                st.session_state.tool_names = status["tool_names"]
            
            # Also return MCP client if needed
            client = MCPClient(provider=provider)
            await client.initialize(role)
            
            logger.info(f"Successfully initialized MCP services for role: {role.value} with {len(status.get('tool_names', []))} tools")
            return agent, client
        except Exception as e:
            logger.error(f"Failed to initialize services for role {role.value}: {e}")
            return None, None

    return run_async(init_services())

# Function to get datasets
async def get_available_datasets(agent: MCPAgent, user_id: str) -> List[Dict[str, Any]]:
    """Get available datasets for the user via direct tools"""
    try:
        # Prefer direct tool calls
        files = await agent.invoke_tool("list_uploaded_files", {"request": {"limit": 20, "file_type": "csv"}}, user_role=st.session_state.get('selected_role', UserRole.VIEWER))
        datasets = await agent.invoke_tool("list_available_datasets", {"request": {"limit": 20}}, user_role=st.session_state.get('selected_role', UserRole.VIEWER))
        return [{
            "name": "Dataset Discovery Results",
            "timestamp": datetime.now().isoformat(),
            "files_result": files,
            "datasets_result": datasets
        }]
    except Exception as e:
        logger.error(f"Failed to get datasets: {e}")
        return [{"name": "Dataset Discovery Error", "error": str(e), "timestamp": datetime.now().isoformat()}]

# Wrapper for invoking tools with approval workflow
async def ui_invoke_tool(agent: MCPAgent, tool_name: str, args: Dict[str, Any], user_role: UserRole, approval_context: str = '') -> Dict[str, Any]:
    """Invoke a tool with optional approval gating.
    Returns either actual tool result or a pending approval placeholder.
    """
    # Ensure user_id is injected for isolation
    try:
        session_uid = st.session_state.get('user_id') if hasattr(st, 'session_state') else None
    except Exception:
        session_uid = None
    session_uid = session_uid or 'ui_user'
    args = dict(args or {})
    # Top-level
    if not args.get('user_id'):
        args['user_id'] = session_uid
    # Nested request
    if isinstance(args.get('request'), dict):
        req = dict(args['request'])
        if not req.get('user_id'):
            req['user_id'] = session_uid
        args['request'] = req
    else:
        args['request'] = {'user_id': session_uid, **(args.get('request') or {})}

    # Determine effective role from session state, fallback to provided user_role
    effective_role = user_role
    try:
        sess_role = None
        if hasattr(st, 'session_state'):
            sess_role = st.session_state.get('current_role') or st.session_state.get('selected_role')
        if isinstance(sess_role, UserRole):
            effective_role = sess_role
        elif isinstance(sess_role, str) and sess_role:
            effective_role = UserRole(sess_role)
    except Exception:
        pass

    # Auto-approval logic
    require = st.session_state.get('require_tool_approval', False)
    auto = st.session_state.get('auto_approve_safe_tools', True)
    safe_list = set(t.lower() for t in st.session_state.get('safe_auto_tools', []))
    lname = tool_name.lower()

    # If approval not required OR auto-approve applies
    if (not require) or (auto and lname in safe_list):
        try:
            result = await agent.invoke_tool(tool_name, args, user_role=effective_role)
            # Track tool call - handle non-dict results safely
            success_status = True
            if isinstance(result, dict):
                success_status = result.get('success', True)
            elif result is None or (isinstance(result, str) and 'error' in result.lower()):
                success_status = False
            
            st.session_state.real_time_tool_calls.append({
                'tool_name': tool_name,
                'status': 'success' if success_status else 'error',
                'timestamp': datetime.utcnow().isoformat(),
                'input': {k: v for k, v in args.get('request', {}).items() if k != 'content'},
                'output': result
            })
            return result if isinstance(result, dict) else {'success': True, 'result': result}
        except Exception as e:
            err = {'success': False, 'error': str(e)}
            st.session_state.real_time_tool_calls.append({
                'tool_name': tool_name,
                'status': 'error',
                'timestamp': datetime.utcnow().isoformat(),
                'input': {k: v for k, v in args.get('request', {}).items() if k != 'content'},
                'output': err
            })
            return err

    # Queue for approval
    call_id = str(uuid.uuid4())
    # Avoid storing huge base64 in pending display; keep shortened copy
    display_args = args.copy()
    if 'request' in display_args and isinstance(display_args['request'], dict) and 'content' in display_args['request']:
        content_val = display_args['request']['content']
        display_args['request'] = display_args['request'].copy()
        display_args['request']['content_preview'] = content_val[:80] + '...' if isinstance(content_val, str) else '<binary>'
        # Full content retained separately
    pending_record = {
        'id': call_id,
        'tool_name': tool_name,
        'args': args,              # full args with content
        'display_args': display_args,  # sanitized for UI
        'status': 'pending',
        'context': approval_context or '',
        'created_at': datetime.utcnow().isoformat(),
        'user_role': (effective_role.value if isinstance(effective_role, UserRole) else str(effective_role))
    }
    st.session_state.pending_tool_calls.append(pending_record)
    return {
        'success': False,
        'pending_approval': True,
        'tool_call_id': call_id,
        'tool_name': tool_name,
        'message': f"Tool '{tool_name}' queued for approval"
    }

async def execute_approved_tool(call_record: Dict[str, Any], agent: MCPAgent) -> Dict[str, Any]:
    """Execute a previously approved tool call"""
    try:
        tool_name = call_record['tool_name']
        args = call_record['args']
        user_role = UserRole(call_record.get('user_role', 'viewer'))
        
        result = await agent.invoke_tool(tool_name, args, user_role=user_role)
        
        # Track execution
        st.session_state.real_time_tool_calls.append({
            'tool_name': tool_name,
            'status': 'success' if result and result.get('success', True) else 'error',
            'timestamp': datetime.utcnow().isoformat(),
            'input': {k: v for k, v in args.get('request', {}).items() if k != 'content'},
            'output': result,
            'approved': True
        })
        
        return result if isinstance(result, dict) else {'success': True, 'result': result}
    except Exception as e:
        err = {'success': False, 'error': str(e)}
        st.session_state.real_time_tool_calls.append({
            'tool_name': call_record['tool_name'],
            'status': 'error',
            'timestamp': datetime.utcnow().isoformat(),
            'input': {},
            'output': err,
            'approved': True
        })
        return err

def display_tool_approval_panel():
    """Display tool approval interface for pending tool calls"""
    pending_calls = st.session_state.get('pending_tool_calls', [])
    
    if not pending_calls:
        st.info("No pending tool approvals")
        return
    
    st.subheader(f"🔐 Tool Approval Queue ({len(pending_calls)} pending)")
    
    for i, call in enumerate(pending_calls):
        with st.container():
            col1, col2, col3 = st.columns([3, 1, 1])
            
            with col1:
                st.markdown(f"""
                **🔧 {call['tool_name']}**  
                *Context:* {call.get('context', 'N/A')}  
                *Created:* {call['created_at'][:19]}  
                *Role:* {call.get('user_role', 'unknown')}
                """)
                
                # Show sanitized arguments
                with st.expander("📋 Tool Arguments", expanded=False):
                    st.json(call.get('display_args', {}))
            
            with col2:
                if st.button("✅ Approve", key=f"approve_{call['id']}"):
                    # Move to approved list
                    call['status'] = 'approved'
                    call['approved_at'] = datetime.utcnow().isoformat()
                    st.session_state.approved_tool_calls.append(call)
                    st.session_state.pending_tool_calls.remove(call)
                    st.rerun()
            
            with col3:
                if st.button("❌ Reject", key=f"reject_{call['id']}"):
                    # Move to rejected list
                    call['status'] = 'rejected'
                    call['rejected_at'] = datetime.utcnow().isoformat()
                    st.session_state.rejected_tool_calls.append(call)
                    st.session_state.pending_tool_calls.remove(call)
                    st.rerun()
            
            st.divider()
    
    # Bulk actions
    if len(pending_calls) > 1:
        col1, col2, col3 = st.columns(3)
        
        with col1:
            if st.button("✅ Approve All Safe Tools"):
                safe_tools = set(t.lower() for t in st.session_state.get('safe_auto_tools', []))
                for call in pending_calls.copy():
                    if call['tool_name'].lower() in safe_tools:
                        call['status'] = 'approved'
                        call['approved_at'] = datetime.utcnow().isoformat()
                        st.session_state.approved_tool_calls.append(call)
                        st.session_state.pending_tool_calls.remove(call)
                st.rerun()
        
        with col2:
            if st.button("✅ Approve All"):
                for call in pending_calls.copy():
                    call['status'] = 'approved'
                    call['approved_at'] = datetime.utcnow().isoformat()
                    st.session_state.approved_tool_calls.append(call)
                    st.session_state.pending_tool_calls.remove(call)
                st.rerun()
        
        with col3:
            if st.button("❌ Reject All"):
                for call in pending_calls.copy():
                    call['status'] = 'rejected'
                    call['rejected_at'] = datetime.utcnow().isoformat()
                    st.session_state.rejected_tool_calls.append(call)
                    st.session_state.pending_tool_calls.remove(call)
                st.rerun()

# Function to upload files to database
async def upload_files_to_db(files: List, agent: MCPAgent, user_id: str) -> List[Dict[str, Any]]:
    """Upload files directly via MCP upload_file tool with approval workflow.
    Returns list of result dicts with upload + (optional) inspection summary.
    """
    upload_results: List[Dict[str, Any]] = []

    # Safe session state access - ensure real_time_tool_calls is initialized
    try:
        conversation_id = getattr(st.session_state, 'conversation_id', str(uuid.uuid4()))
        selected_role = getattr(st.session_state, 'selected_role', UserRole.VIEWER)
        file_upload_status = getattr(st.session_state, 'file_upload_status', {})
        
        # Ensure real_time_tool_calls is always available
        if not hasattr(st.session_state, 'real_time_tool_calls'):
            st.session_state.real_time_tool_calls = []
    except Exception:
        conversation_id = str(uuid.uuid4())
        selected_role = UserRole.VIEWER
        file_upload_status = {}
        if not hasattr(st.session_state, 'real_time_tool_calls'):
            st.session_state.real_time_tool_calls = []

    if not isinstance(conversation_id, str) or not conversation_id:
        conversation_id = str(uuid.uuid4())

    for file in files:
        start_time = time.time()
        try:
            raw_content = file.getvalue()
            if isinstance(raw_content, str):
                raw_content = raw_content.encode('utf-8')
            # Encode base64 (strip whitespace/newlines proactively)
            encoded_content = base64.b64encode(raw_content).decode('utf-8')
            encoded_content = encoded_content.replace('\n', '').replace('\r', '').replace(' ', '')

            # Local validation
            try:
                test_bytes = base64.b64decode(encoded_content, validate=True)
                if len(test_bytes) == 0:
                    raise ValueError("Decoded content empty")
            except Exception as b64err:
                raise RuntimeError(f"Base64 validation failed: {b64err}")

            # Mark uploading
            file_upload_status[file.name] = 'uploading'
            if hasattr(st.session_state, 'file_upload_status'):
                st.session_state.file_upload_status[file.name] = 'uploading'

            # Build tool args (tool param name is 'request')
            tool_args = {
                'request': {
                    'filename': file.name,
                    'content': encoded_content,
                    'content_type': file.type or 'application/octet-stream',
                    'user_id': user_id,  # Explicitly include user_id for server-side isolation
                    'metadata': {
                        'source': 'streamlit_ui',
                        'original_name': file.name,
                        'upload_time': datetime.utcnow().isoformat()
                    }
                }
            }

            # Use approval-aware tool invocation
            upload_resp = await ui_invoke_tool(
                agent, 'upload_file', tool_args, selected_role, 
                approval_context=f'File Upload: {file.name} ({len(raw_content)} bytes)'
            )

            pending = bool(upload_resp.get('pending_approval')) if isinstance(upload_resp, dict) else False
            success = bool(upload_resp and upload_resp.get('success') and not pending)
            file_id = upload_resp.get('file_id') if success else None

            # Optional: auto-inspect datasets (csv/json/txt) if upload succeeded
            inspection = None
            clustering_result = None
            dataset_exts = {'.csv', '.json', '.txt', '.tsv', '.xlsx', '.xls'}
            ext = ('.' + file.name.split('.')[-1].lower()) if '.' in file.name else ''
            if success and ext in dataset_exts and file_id:
                try:
                    # First, inspect the data structure
                    inspect_args = {'request': {'file_id': file_id, 'sample_rows': 5, 'user_id': user_id}}
                    inspection = await ui_invoke_tool(
                        agent, 'data_inspect', inspect_args, selected_role, 
                        approval_context=f'Auto Inspection: {file.name}'
                    )
                    
                    # If it's a CSV with numerical columns, try clustering
                    if (inspection and inspection.get('success') and 
                        ext == '.csv' and file.name.lower().endswith('.csv')):
                        
                        data_info = inspection.get('data_info', {})
                        columns = data_info.get('column_names', [])
                        
                        # Look for numerical columns that might be good for clustering
                        numerical_cols = []
                        if 'Age' in columns:
                            numerical_cols.append('Age')
                        if 'Annual Income (k$)' in columns:
                            numerical_cols.append('Annual Income (k$)')
                        if 'Spending Score (1-100)' in columns:
                            numerical_cols.append('Spending Score (1-100)')
                        
                        # Auto-cluster if we found good columns
                        if len(numerical_cols) >= 2:
                            try:
                                cluster_args = {
                                    'request': {
                                        'dataset_id': file_id,  # was 'file_id'; scirex expects 'dataset_id'
                                        'n_clusters': 5,
                                        'features': numerical_cols,
                                        'algorithm': 'kmeans',
                                        'user_id': user_id
                                    }
                                }
                                clustering_result = await ui_invoke_tool(
                                    agent, 'cluster_analysis', cluster_args, selected_role,
                                    approval_context=f'Auto Clustering: {file.name}'
                                )
                            except Exception as ce:
                                clustering_result = {'success': False, 'error': f'Auto-clustering failed: {ce}'}
                        
                except Exception as ie:
                    inspection = {'success': False, 'error': f'Inspection failed: {ie}'}

            duration = round(time.time() - start_time, 3)

            # Mark completion
            status_val = 'pending' if pending else ('success' if success else 'error')
            file_upload_status[file.name] = status_val
            if hasattr(st.session_state, 'file_upload_status'):
                st.session_state.file_upload_status[file.name] = status_val

            result_record: Dict[str, Any] = {
                'filename': file.name,
                'size': len(raw_content),
                'content_type': file.type,
                'status': status_val,
                'upload_result': upload_resp,
                'file_id': file_id,
                'inspection': inspection,
                'clustering_result': clustering_result,
                'duration_s': duration,
                'timestamp': datetime.utcnow().isoformat(),
                'pending_approval': pending
            }
            upload_results.append(result_record)

            # Invalidate dataset cache if present
            try:
                if success and hasattr(agent, 'invalidate_dataset_cache'):
                    agent.invalidate_dataset_cache(user_id)
            except Exception:
                pass

        except Exception as e:
            err_msg = f"Failed to upload {file.name}: {e}"
            logger.error(err_msg)
            file_upload_status[file.name] = 'failed'
            if hasattr(st.session_state, 'file_upload_status'):
                st.session_state.file_upload_status[file.name] = 'failed'
            upload_results.append({
                'filename': file.name,
                'status': 'error',
                'error': str(e),
                'timestamp': datetime.utcnow().isoformat()
            })

    return upload_results

# Real-time tool call tracking with live polling
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
        
        # Update last poll time
        st.session_state.last_tool_poll = datetime.now()
            
    except Exception as e:
        logger.error(f"Error tracking tool calls: {e}")

def poll_tool_calls_live():
    """Poll for new tool calls during active queries"""
    if (st.session_state.get('query_in_progress', False) and 
        st.session_state.get('live_polling_enabled', True) and
        st.session_state.get('agent_initialized', False)):
        
        try:
            agent = st.session_state.get('agent')
            if agent:
                track_tool_calls(agent)
                
                # Check if we should auto-refresh
                last_poll = st.session_state.get('last_tool_poll')
                if last_poll:
                    seconds_since = (datetime.now() - last_poll).total_seconds()
                    if seconds_since >= REFRESH_INTERVAL:
                        # Trigger a minimal rerun to update displays
                        st.rerun()
        except Exception as e:
            logger.debug(f"Live polling error: {e}")

# Auto-refresh mechanism for live updates - DISABLED to prevent fragment errors
# @st.fragment(run_every=REFRESH_INTERVAL)
def live_tool_monitor():
    """Auto-refresh fragment for live tool call monitoring - DISABLED"""
    # Disabled due to fragment stability issues
    pass
    # if st.session_state.get('query_in_progress', False):
    #     poll_tool_calls_live()
    #     
    #     # Display live status in a compact way
    #     if st.session_state.real_time_tool_calls:
    #         latest = st.session_state.real_time_tool_calls[-1]
    #         time_ago = datetime.now() - datetime.fromisoformat(latest['timestamp'].replace('Z', '+00:00').replace('+00:00', ''))
    #         if time_ago.total_seconds() < 30:  # Only show recent activity
    #             st.info(f"🔄 Latest: {latest['tool_name']} ({int(time_ago.total_seconds())}s ago)")

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
    # Initialize services (re-initialize if role changed or not initialized)
    need_init = (not st.session_state.get('agent_initialized', False) or 
                 not st.session_state.get('agent') or 
                 not st.session_state.get('client'))
    
    if need_init:
        try:
            with st.spinner(f"Initializing MCP services for {selected_role.value} role..."):
                # get_agent_and_client returns (agent, client)
                agent, client = get_agent_and_client(selected_role, llm_provider)
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
    else:
        # Show current status
        st.success(f"✅ MCP services ready for role: **{selected_role.value}**")
        agent = st.session_state.agent
        client = st.session_state.client
    
    # Handle sidebar actions
    if hasattr(st.session_state, 'action_requested'):
        action = st.session_state.action_requested
        delattr(st.session_state, 'action_requested')
        
        with st.spinner(f"Executing {action}..."):
            if action == "list_files":
                result = run_async(agent.invoke_tool("list_uploaded_files", {"request": {"limit": 50}}, user_role=selected_role))
                st.json(result)
            elif action == "get_datasets":
                datasets = run_async(get_available_datasets(agent, st.session_state.user_id))
                st.session_state.datasets = datasets
                if datasets:
                    st.success(f"Found {len(datasets)} result sets")
                    for dataset in datasets:
                        st.json(dataset)
                else:
                    st.warning("No datasets found")
            elif action == "sync_metadata":
                st.info("This action requires a specific tool; please add a sync tool or remove this action.")
            elif action == "health_check":
                health_result = run_async(agent.get_agent_status())
                st.json(health_result)
            elif action == "review_pending":
                # Display tool approval panel in main area
                with st.container():
                    display_tool_approval_panel()
                    
                    # Execute approved tools
                    approved_calls = st.session_state.get('approved_tool_calls', [])
                    if approved_calls and st.button("🚀 Execute Approved Tools"):
                        with st.spinner("Executing approved tools..."):
                            for call in approved_calls.copy():
                                try:
                                    result = run_async(execute_approved_tool(call, agent))
                                    st.success(f"✅ Executed {call['tool_name']}: {result.get('message', 'Success')}")
                                    st.session_state.approved_tool_calls.remove(call)
                                except Exception as e:
                                    st.error(f"❌ Failed to execute {call['tool_name']}: {e}")
                        st.rerun()
    
    # Chat interface
    st.subheader("💬 Chat Interface")
    
    # Chat history management controls
    if st.session_state.messages:
        col_delete, col_edit, col_clear = st.columns(3)
        
        with col_delete:
            if st.button("🗑️ Delete Last Exchange", help="Delete the last user message and assistant response"):
                # Find and remove the last user-assistant pair
                if len(st.session_state.messages) >= 2:
                    # Remove last two messages (user + assistant)
                    st.session_state.messages = st.session_state.messages[:-2]
                elif len(st.session_state.messages) == 1:
                    # Remove just the last message
                    st.session_state.messages = st.session_state.messages[:-1]
                st.rerun()
        
        with col_edit:
            if st.button("✏️ Edit Last Query", help="Edit and resend the last user message"):
                # Find the last user message
                last_user_msg = None
                last_user_idx = None
                for i in reversed(range(len(st.session_state.messages))):
                    if st.session_state.messages[i]["role"] == "user":
                        last_user_msg = st.session_state.messages[i]
                        last_user_idx = i
                        break
                
                if last_user_msg:
                    st.session_state.edit_mode = True
                    st.session_state.edit_message_idx = last_user_idx
                    st.session_state.edit_content = last_user_msg["content"]
                    st.rerun()
        
        with col_clear:
            if st.button("🧹 Clear All", help="Clear entire chat history"):
                st.session_state.messages = []
                st.session_state.tool_calls = []
                st.session_state.real_time_tool_calls = []
                st.session_state.conversation_id = str(uuid.uuid4())
                st.rerun()
    
    # Edit mode interface
    if st.session_state.get('edit_mode', False):
        st.markdown("### ✏️ Edit Message")
        st.info("🔄 You are in edit mode. Your edited message will replace the original and trigger a new response.")
        
        # Show original message for reference
        original_content = st.session_state.get('edit_content', '')
        if original_content:
            with st.expander("📝 Original Message", expanded=False):
                st.markdown(original_content)
        
        # Text area for editing
        edited_content = st.text_area(
            "Edit your message:",
            value=original_content,
            height=100,
            key="edit_text_area",
            help="Modify your message and click 'Send Edited Message' to get a new response"
        )
        
        col_send, col_cancel = st.columns(2)
        
        with col_send:
            if st.button("🚀 Send Edited Message", type="primary"):
                if edited_content.strip():
                    # Remove messages from the edit point onwards
                    edit_idx = st.session_state.get('edit_message_idx', 0)
                    st.session_state.messages = st.session_state.messages[:edit_idx]
                    
                    # Add the edited message
                    st.session_state.messages.append({
                        "role": "user", 
                        "content": edited_content.strip()
                    })
                    
                    # Clear edit mode
                    st.session_state.edit_mode = False
                    if 'edit_content' in st.session_state:
                        del st.session_state.edit_content
                    if 'edit_message_idx' in st.session_state:
                        del st.session_state.edit_message_idx
                    
                    # Set flag to process the edited message
                    st.session_state.process_edited_message = edited_content.strip()
                    st.rerun()
                else:
                    st.warning("⚠️ Message cannot be empty")
        
        with col_cancel:
            if st.button("❌ Cancel Edit"):
                st.session_state.edit_mode = False
                if 'edit_content' in st.session_state:
                    del st.session_state.edit_content
                if 'edit_message_idx' in st.session_state:
                    del st.session_state.edit_message_idx
                st.rerun()
        
        # Don't show regular chat input when in edit mode
        st.warning("💡 Tip: Cancel edit mode to send new messages or use the sidebar controls")
        st.stop()  # Stop execution here when in edit mode
    
    # Add live tool monitoring fragment - DISABLED due to fragment errors
    # if st.session_state.get('live_polling_enabled', True):
    #     live_tool_monitor()
    
    # Display conversation history
    for i, msg in enumerate(st.session_state.messages):
        if msg["role"] == "user":
            with st.chat_message("user"):
                # Add edit indicator for the last user message
                is_last_user = (i == len(st.session_state.messages) - 1 or 
                               (i == len(st.session_state.messages) - 2 and 
                                st.session_state.messages[-1]["role"] == "assistant"))
                
                if is_last_user and not st.session_state.get('edit_mode', False):
                    col_msg, col_edit = st.columns([4, 1])
                    with col_msg:
                        st.markdown(msg["content"])
                    with col_edit:
                        if st.button("✏️", key=f"edit_msg_{i}", help="Edit this message"):
                            st.session_state.edit_mode = True
                            st.session_state.edit_message_idx = i
                            st.session_state.edit_content = msg["content"]
                            st.rerun()
                else:
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
    
    # Handle edited messages
    if hasattr(st.session_state, 'process_edited_message'):
        user_input = st.session_state.process_edited_message
        delattr(st.session_state, 'process_edited_message')
    
    # Chat input controls for LLM overrides
    with st.expander("LLM Settings", expanded=False):
        colp, colm = st.columns(2)
        with colp:
            temp = st.slider("Temperature", 0.0, 1.5, 0.1, 0.1)
            max_tokens = st.number_input("Max tokens (output)", min_value=0, value=0, step=50, help="0 = provider default")
        with colm:
            model_name = st.text_input("Model", value="")
            provider_name = st.text_input("Provider", value="")
        system_other = st.text_area("Additional system instructions", value="", height=80)

    # Process user input (either new or edited)
    if user_input:
        # Only add to history if it's a new message (not an edited one being reprocessed)
        if not st.session_state.get('edit_mode', False):
            user_msg = {"role": "user", "content": user_input}
        else:
            # For edited messages, the message is already added to history in edit handling
            user_msg = st.session_state.messages[-1] if st.session_state.messages else {"role": "user", "content": user_input}
        
        # Handle file uploads if any (only for new messages, not edited ones)
        file_upload_results = []
        if uploaded_files and not st.session_state.get('edit_mode', False):
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
                        
                        # Show inspection results if available
                        if result.get('inspection') and result['inspection'].get('success'):
                            inspection = result['inspection']
                            data_info = inspection.get('data_info', {})
                            st.info(f"📊 Dataset detected: {data_info.get('num_rows', 'N/A')} rows, {data_info.get('num_columns', 'N/A')} columns")
                        
                        # Show clustering results if available
                        if result.get('clustering_result') and result['clustering_result'].get('success'):
                            clustering = result['clustering_result']
                            cluster_info = clustering.get('results', {})
                            silhouette = cluster_info.get('silhouette_score', 'N/A')
                            st.success(f"🎯 Auto-clustering completed: {cluster_info.get('n_clusters', 'N/A')} clusters (Silhouette: {silhouette})")
                            
                    elif result["status"] == "pending":
                        st.warning(f"⏳ Pending approval: {result['filename']}")
                    else:
                        st.error(f"❌ Failed to upload: {result['filename']} - {result.get('error', 'Unknown error')}")
            
            # Update user message with file info if files were uploaded
            if file_upload_results:
                user_msg["files"] = file_upload_results
        
        # Add user message to history only if it's a new message
        if not any(msg.get("content") == user_input and msg.get("role") == "user" for msg in st.session_state.messages[-2:]):
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
                    
                    # Enable live polling during query
                    st.session_state.query_in_progress = True
                    st.session_state.real_time_tool_calls = []  # Clear previous calls
                    
                    status_placeholder.info("🔍 Starting analysis...")
                    
                    response = run_async(agent.chat(
                        query=enhanced_query,
                        user_id=st.session_state.user_id,
                        conversation_id=st.session_state.conversation_id,
                        user_role=selected_role,
                        provider=(provider_name or None),
                        model=(model_name or None),
                        temperature=temp,
                        max_tokens_output=(max_tokens or None),
                        system_prompt_other=(system_other or None)
                    ))
                    
                    # Disable live polling after query completion
                    st.session_state.query_in_progress = False
                    
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
                                approval_emoji = " 🔐" if call.get('approved') else ""
                                st.markdown(f"• {status_emoji} {call['tool_name']} ({timestamp_short}){approval_emoji}")
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
                    # Ensure polling is disabled on error
                    st.session_state.query_in_progress = False
                    
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

with col2:
    # Tool approval and monitoring panel
    st.subheader("🔐 Tool Management")
    
    # Show pending approvals
    pending_count = len(st.session_state.get('pending_tool_calls', []))
    approved_count = len(st.session_state.get('approved_tool_calls', []))
    rejected_count = len(st.session_state.get('rejected_tool_calls', []))
    
    if pending_count > 0 or approved_count > 0 or rejected_count > 0:
        # Summary metrics
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            st.metric("⏳ Pending", pending_count)
        with col_b:
            st.metric("✅ Approved", approved_count)
        with col_c:
            st.metric("❌ Rejected", rejected_count)
        
        # Quick approval interface for pending tools
        if pending_count > 0:
            st.markdown("**Quick Actions:**")
            pending_calls = st.session_state.get('pending_tool_calls', [])
            
            for call in pending_calls[:3]:  # Show first 3
                with st.container():
                    st.markdown(f"🔧 **{call['tool_name']}** ({call.get('context', 'N/A')})")
                    
                    col_approve, col_reject = st.columns(2)
                    with col_approve:
                        if st.button("✅", key=f"quick_approve_{call['id']}"):
                            call['status'] = 'approved'
                            call['approved_at'] = datetime.utcnow().isoformat()
                st.info(f"+ {pending_count - 3} more pending...")
        
        # Execute approved tools button
        if approved_count > 0:
            if st.button("🚀 Execute All Approved", type="primary"):
                with st.spinner("Executing approved tools..."):
                    approved_calls = st.session_state.get('approved_tool_calls', [])
                    for call in approved_calls.copy():
                        try:
                            result = run_async(execute_approved_tool(call, agent))
                            st.success(f"✅ {call['tool_name']}")
                            st.session_state.approved_tool_calls.remove(call)
                        except Exception as e:
                            st.error(f"❌ {call['tool_name']}: {e}")
                    st.rerun()
    else:
        st.info("No tool approvals pending")
    
    # Display live operations
    display_live_operation_status()
    
    # Display tool calls
    with st.expander("🔧 Tool Execution History", expanded=False):
        display_tool_calls()

# Footer information
st.markdown("---")
st.markdown("**🔐 Tool Approval Features:**")
st.markdown("- **Manual Approval**: Enable to review all tool calls before execution")
st.markdown("- **Auto-approve Safe Tools**: Automatically approve read-only operations")
st.markdown("- **Bulk Actions**: Approve/reject multiple tools at once")
st.markdown("- **Execution Tracking**: Monitor approved tool execution with detailed logs")