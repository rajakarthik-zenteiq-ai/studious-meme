import streamlit as st
import asyncio
import base64
import nest_asyncio
from agent.agent import LogAnalyticsAgent
from mcp_client.client import MCPLogAnalyticsClient

# Enable nested asyncio loops
nest_asyncio.apply()

st.set_page_config(page_title="MCP Log Analytics Agent", page_icon="🤖", layout="wide")
st.title("🤖 MCP Log Analytics Agent UI")

# Session state for conversation
if "messages" not in st.session_state:
    st.session_state["messages"] = []

# Async runner for Streamlit with proper event loop handling
def run_async(coro):
    try:
        # Use current event loop if available, otherwise create new one
        loop = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        
        if loop is None:
            return asyncio.run(coro)
        else:
            # We're in an existing event loop, use nest_asyncio
            task = asyncio.create_task(coro)
            return loop.run_until_complete(task)
    except Exception as e:
        st.error(f"Async execution error: {e}")
        raise

# Initialize agent and client in session state to ensure they're created in correct event loop
async def initialize_services():
    """Initialize agent and client in the current event loop"""
    if "client_initialized" not in st.session_state:
        client = MCPLogAnalyticsClient()
        await client.initialize()
        st.session_state["client"] = client
        st.session_state["client_initialized"] = True
    
    if "agent_initialized" not in st.session_state:
        agent = LogAnalyticsAgent()
        await agent.initialize()
        st.session_state["agent"] = agent
        st.session_state["agent_initialized"] = True
    
    return st.session_state["client"], st.session_state["agent"]

# Initialize services
try:
    client, agent = run_async(initialize_services())
except Exception as e:
    st.error(f"Failed to initialize services: {e}")
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
            # Call the agent with proper error handling
            response = run_async(agent.analyze(
                query=user_input,
                user_id="demo_user",
                conversation_id="demo_convo",
                attachments=attachments
            ))
            st.session_state["messages"].append({"role": "agent", "content": response})
        except Exception as e:
            st.error(f"Error processing query: {e}")
            st.session_state["messages"].append({
                "role": "agent", 
                "content": f"Sorry, I encountered an error: {str(e)}"
            })
        st.rerun()