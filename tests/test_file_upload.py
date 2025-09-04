#!/usr/bin/env python3
"""
Test file upload functionality end-to-end
"""
import asyncio
import base64
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from agent.mcp_agent import MCPAgent
from agent.rbac_system import get_rbac_manager, UserRole

async def test_file_upload():
    """Test file upload functionality through the agent"""
    print("🧪 Testing file upload functionality...")
    
    # Create test CSV data
    test_csv_content = """customer_id,age,annual_income,spending_score
1,39,15,39
2,81,88,10
3,6,77,76
4,77,26,16
5,40,49,73
6,76,77,77
7,94,73,35
8,3,39,26
9,72,54,18
10,38,15,48"""
    
    print(f"📄 Created test CSV with {len(test_csv_content.split(chr(10)))} lines")
    
    # Initialize agent as ADMIN
    rbac = get_rbac_manager()
    user_context = rbac.create_user_context("test_user", UserRole.ADMIN)
    
    agent = MCPAgent()
    await agent.initialize(user_context)
    
    print(f"🔧 Agent initialized with {len(agent.available_tools)} tools")
    
    # List available tools to check upload_file is available
    tool_names = [getattr(tool, 'name', str(tool)) for tool in agent.available_tools]
    upload_tools = [name for name in tool_names if 'upload' in name.lower()]
    print(f"📤 Upload tools available: {upload_tools}")
    
    if not upload_tools:
        print("❌ No upload tools found! Check MCP server connectivity.")
        return False
    
    # Test direct tool invocation
    try:
        # Encode content to base64
        encoded_content = base64.b64encode(test_csv_content.encode('utf-8')).decode('utf-8')
        
        # Prepare upload arguments
        upload_args = {
            "request": {
                "filename": "test_customers.csv",
                "content": encoded_content,
                "content_type": "text/csv",
                "user_id": "test_user",
                "file_size": len(test_csv_content),
                "metadata": {
                    "upload_source": "test_script",
                    "original_filename": "test_customers.csv"
                }
            }
        }
        
        print("📤 Attempting file upload via upload_file tool...")
        result = await agent.invoke_tool("upload_file", upload_args, UserRole.ADMIN)
        
        print(f"✅ Upload result: {result}")
        return True
        
    except Exception as e:
        print(f"❌ Upload failed: {e}")
        
        # Try alternative approach via chat
        print("🔄 Trying upload via chat interface...")
        try:
            chat_query = f"""Upload a test CSV file called 'test_customers.csv' with this content:
{test_csv_content}

File details:
- Size: {len(test_csv_content)} bytes
- Type: text/csv
- User: test_user
"""
            
            response = await agent.chat(
                query=chat_query,
                user_id="test_user",
                conversation_id="test_upload",
                user_role=UserRole.ADMIN
            )
            
            print(f"💬 Chat response: {response}")
            return "success" in response.lower()
            
        except Exception as chat_error:
            print(f"❌ Chat upload also failed: {chat_error}")
            return False

async def test_mongodb_connectivity():
    """Test MongoDB server connectivity specifically"""
    print("🔍 Testing MongoDB MCP server connectivity...")
    
    try:
        import httpx
        
        # Test MongoDB MCP server health
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream, application/json"
        }
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Initialize session
            init_request = {
                "jsonrpc": "2.0",
                "id": "test_init",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "clientInfo": {"name": "test-client", "version": "1.0.0"}
                }
            }
            
            response = await client.post("http://localhost:8100/mcp/", json=init_request, headers=headers)
            
            if response.status_code == 200:
                session_id = response.headers.get('mcp-session-id')
                print(f"✅ MongoDB MCP server responding, session: {session_id}")
                
                # Test health check
                if session_id:
                    session_headers = {**headers, "mcp-session-id": session_id}
                    
                    # Send initialized notification
                    notify_request = {"jsonrpc": "2.0", "method": "notifications/initialized"}
                    await client.post("http://localhost:8100/mcp/", json=notify_request, headers=session_headers)
                    
                    # Test health check tool
                    health_request = {
                        "jsonrpc": "2.0",
                        "id": "test_health",
                        "method": "tools/call",
                        "params": {"name": "health_check", "arguments": {}}
                    }
                    
                    health_response = await client.post("http://localhost:8100/mcp/", json=health_request, headers=session_headers)
                    print(f"🏥 Health check response: {health_response.status_code}")
                    
                    if health_response.status_code == 200:
                        print("✅ MongoDB MCP server is healthy")
                        return True
                    else:
                        print(f"❌ Health check failed: {health_response.text}")
                        return False
                else:
                    print("❌ No session ID received")
                    return False
            else:
                print(f"❌ MongoDB MCP server not responding: {response.status_code}")
                return False
                
    except Exception as e:
        print(f"❌ MongoDB connectivity test failed: {e}")
        return False

if __name__ == "__main__":
    async def main():
        print("🚀 Starting file upload diagnostics...\n")
        
        # Test MongoDB connectivity first
        mongo_ok = await test_mongodb_connectivity()
        print()
        
        if mongo_ok:
            # Test file upload
            upload_ok = await test_file_upload()
            print()
            
            if upload_ok:
                print("✅ File upload test passed!")
            else:
                print("❌ File upload test failed!")
        else:
            print("❌ Cannot test file upload - MongoDB MCP server not accessible")
    
    asyncio.run(main())
