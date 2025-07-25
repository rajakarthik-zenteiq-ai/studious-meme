import streamlit as st
import asyncio
import base64
import threading
import concurrent.futures
from concurrent.futures import Future
from agent.agent import LogAnalyticsAgent
from mcp_client.client import MCPLogAnalyticsClient

st.set_page_config(page_title="MCP Log Analytics Agent", page_icon="🤖", layout="wide")
st.title("🤖 MCP Log Analytics Agent UI")

# Session state for conversation
if "messages" not in st.session_state:
    st.session_state["messages"] = []

# Global event loop and executor
@st.cache_resource
def get_event_loop_executor():
    """Create a dedicated thread with event loop for async operations"""
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    loop_future = executor.submit(_create_event_loop)
    loop = loop_future.result()  # Wait for loop to be created
    return executor, loop

def _create_event_loop():
    """Create and return a new event loop in a thread"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # Keep the loop running in the background
    def run_forever():
        try:
            loop.run_forever()
        except Exception as e:
            st.error(f"Event loop error: {e}")
    
    thread = threading.Thread(target=run_forever, daemon=True)
    thread.start()
    return loop

# Get the persistent executor and loop
executor, event_loop = get_event_loop_executor()

def run_async(coro):
    """Run coroutine in the dedicated event loop"""
    try:
        # Submit the coroutine to our dedicated event loop
        future = asyncio.run_coroutine_threadsafe(coro, event_loop)
        return future.result(timeout=60)  # 60 second timeout
    except concurrent.futures.TimeoutError:
        st.error("Request timed out after 60 seconds")
        raise
    except Exception as e:
        st.error(f"Async execution error: {e}")
        raise

# Initialize agent and client with proper event loop isolation
@st.cache_resource
def initialize_services():
    """Initialize agent and client in the dedicated event loop"""
    try:
        async def _init():
            client = MCPLogAnalyticsClient()
            await client.initialize()
            
            agent = LogAnalyticsAgent()
            await agent.initialize()
            
            return client, agent
        
        # Run initialization in our dedicated event loop
        client, agent = run_async(_init())
        return client, agent
        
    except Exception as e:
        st.error(f"Failed to initialize services: {e}")
        st.error("Please check that MCP servers are running.")
        raise

# Initialize services
try:
    client, agent = initialize_services()
    st.success("✅ MCP services initialized successfully!")
except Exception as e:
    st.error(f"❌ Failed to initialize services: {e}")
    st.error("Please check that MCP servers are running.")
    st.stop()

# File upload
uploaded_file = st.file_uploader("Upload a file (optional)", type=["csv", "json", "txt", "log"])
attachments = []
if uploaded_file is not None:
    file_bytes = uploaded_file.read()
    attachments.append({
        "name": uploaded_file.name,
        "content": file_bytes,
        "type": uploaded_file.type
    })

# Chat input
user_input = st.text_input("Enter your query:", key="user_input")
submit = st.button("Send")

# Display conversation history
st.markdown("---")
st.subheader("Conversation History")
for msg in st.session_state["messages"]:
    if msg["role"] == "user":
        st.markdown(f"**You:** {msg['content']}")
    else:
        st.markdown(f"**Agent:** {msg['content']}")

# Handle user input
if submit and user_input:
    st.session_state["messages"].append({"role": "user", "content": user_input})
    with st.spinner("Agent is thinking..."):
        try:
            # Process query in the dedicated event loop
            response = run_async(agent.analyze(
                query=user_input,
                user_id="demo_user",
                conversation_id="demo_convo",
                attachments=attachments
            ))
            st.session_state["messages"].append({"role": "agent", "content": response})
            st.success("✅ Query processed successfully!")
        except Exception as e:
            error_msg = f"Error processing query: {str(e)}"
            st.error(f"❌ {error_msg}")
            st.session_state["messages"].append({
                "role": "agent", 
                "content": f"Sorry, I encountered an error: {str(e)}"
            })
        st.rerun()