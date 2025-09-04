#!/usr/bin/env python3
"""
Test health check to verify database connectivity
"""
import asyncio
import json
import httpx

async def test_health_check():
    """Test health check tool to verify database connection"""
    
    server_url = "http://localhost:8100/mcp/"
    
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream, application/json",
        "X-User-ID": "test_user_admin"
    }
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Initialize session
        init_req = {
            "jsonrpc": "2.0",
            "id": "init_health",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "health_test", "version": "1.0.0"},
            },
        }
        
        print("🔌 Initializing MCP session...")
        init_res = await client.post(server_url, json=init_req, headers=headers)
        
        if init_res.status_code != 200:
            print(f"❌ Initialization failed: {init_res.text}")
            return
            
        session_id = init_res.headers.get("mcp-session-id")
        if not session_id:
            print("❌ No session ID returned")
            return
            
        print(f"✅ Session initialized: {session_id}")
        
        # Add session ID to headers
        headers["mcp-session-id"] = session_id
        
        # Send initialized notification
        try:
            await client.post(server_url, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=headers)
        except Exception as e:
            print(f"⚠️ Initialized notification failed: {e}")
        
        # Test health_check tool call
        health_req = {
            "jsonrpc": "2.0",
            "id": "health_test",
            "method": "tools/call",
            "params": {
                "name": "health_check",
                "arguments": {}
            }
        }
        
        print("🏥 Testing health check...")
        health_res = await client.post(server_url, json=health_req, headers=headers)
        print(f"Health check status: {health_res.status_code}")
        
        if health_res.status_code == 200:
            try:
                # Handle SSE response format
                response_text = health_res.text
                if response_text.startswith("event:"):
                    # Parse SSE format
                    for line in response_text.splitlines():
                        if line.startswith("data: "):
                            result = json.loads(line[6:])
                            break
                else:
                    result = health_res.json()
                    
                print("✅ Health check response received:")
                print(json.dumps(result, indent=2, default=str))
                
                # Check database status
                if result.get("result", {}).get("success"):
                    database_info = result["result"].get("database", {})
                    storage_info = result["result"].get("storage", {})
                    
                    print(f"\n🎉 Health check successful!")
                    print(f"📊 Database connected: {database_info.get('connected', False)}")
                    print(f"🗄️ Database name: {database_info.get('name', 'N/A')}")
                    print(f"☁️ S3 status: {storage_info.get('s3_status', 'N/A')}")
                    
                    collections = database_info.get('collections', {})
                    if collections:
                        print(f"📁 Collections status:")
                        for collection, status in collections.items():
                            print(f"   - {collection}: {status}")
                    
                else:
                    print(f"❌ Health check failed: {result.get('result', {}).get('error')}")
                    
            except Exception as e:
                print(f"❌ Failed to parse response: {e}")
                print(f"Raw response: {health_res.text}")
        else:
            print(f"❌ Health check request failed: {health_res.text}")

if __name__ == "__main__":
    asyncio.run(test_health_check())
