#!/usr/bin/env python3
"""
Test script to validate the cleaned MCP client architecture
"""
import asyncio
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

async def test_mcp_client_architecture():
    """Test the cleaned MCP client architecture"""
    print("🧪 Testing cleaned MCP client architecture...")
    
    try:
        # Test 1: Import the cleaned client
        print("\n1️⃣ Testing imports...")
        from mcp_client.client import MCPLogAnalyticsClient, MCPClient, create_client, create_mcp_client
        print("✅ All client imports successful")
        
        # Test 2: Create basic MCP client
        print("\n2️⃣ Testing basic MCP client...")
        basic_client = await create_client()
        print("✅ Basic MCP client created successfully")
        
        # Test 3: Check server configuration
        print("\n3️⃣ Testing server configuration...")
        servers = basic_client.servers
        print(f"✅ Found {len(servers)} configured servers:")
        for server_id, server in servers.items():
            print(f"   - {server.name}: {len(server.tools)} tools")
        
        # Test 4: Test agent integration (without actually connecting to servers)
        print("\n4️⃣ Testing agent integration...")
        try:
            enhanced_client = MCPClient(provider="openai", model="gpt-4o-mini")
            await enhanced_client.initialize()
            print("✅ Enhanced MCP client with agent support created")
            
            # Test if agent is properly initialized
            if enhanced_client.agent:
                print("✅ Agent is properly integrated and initialized")
            else:
                print("⚠️ Agent not available (might be due to missing dependencies)")
            
            await enhanced_client.cleanup()
            
        except Exception as e:
            print(f"⚠️ Agent integration test failed: {e}")
        
        # Test 5: Verify tool methods exist
        print("\n5️⃣ Testing MCP client methods...")
        methods_to_test = [
            'store_logs', 'get_logs_by_date', 'web_search', 
            'upload_file', 'get_server_status', '_call_tool'
        ]
        
        for method in methods_to_test:
            if hasattr(basic_client, method):
                print(f"✅ Method {method} exists")
            else:
                print(f"❌ Method {method} missing")
        
        await basic_client.close()
        print("\n🎉 Architecture test completed successfully!")
        
        # Test 6: Verify no duplicate agent classes
        print("\n6️⃣ Checking for architecture cleanliness...")
        try:
            from mcp_client.client import MCPLLMAgent
            print("❌ Found duplicate MCPLLMAgent in client.py - this should be removed!")
        except ImportError:
            print("✅ No duplicate agent classes found in client.py")
        
        return True
        
    except Exception as e:
        print(f"\n❌ Architecture test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = asyncio.run(test_mcp_client_architecture())
    sys.exit(0 if success else 1)
