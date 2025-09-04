#!/usr/bin/env python3
"""
Direct file upload test - simplified version to test the complete workflow
"""
import asyncio
import base64
import json
import httpx
from datetime import datetime

async def test_direct_upload():
    """Test file upload directly using the MCP server endpoint"""
    
    # Sample CSV data
    csv_data = """name,age,salary
John,25,50000
Jane,30,60000
Bob,35,70000
Alice,28,55000
"""
    
    # Encode as base64
    encoded_content = base64.b64encode(csv_data.encode('utf-8')).decode('utf-8')
    
    # MCP server URL
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
            "id": "init_upload",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "upload_test", "version": "1.0.0"},
            },
        }
        
        print("🔌 Initializing MCP session...")
        init_res = await client.post(server_url, json=init_req, headers=headers)
        print(f"Init status: {init_res.status_code}")
        
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
        
        # Test upload_file tool call
        upload_req = {
            "jsonrpc": "2.0",
            "id": "upload_test",
            "method": "tools/call",
            "params": {
                "name": "upload_file",
                "arguments": {
                    "request": {
                        "filename": "test_data.csv",
                        "content": encoded_content,
                        "content_type": "text/csv",
                        "auto_analyze": True,
                        "metadata": {
                            "description": "Test dataset for upload workflow",
                            "source": "direct_test"
                        }
                    }
                }
            }
        }
        
        print("📤 Testing file upload...")
        upload_res = await client.post(server_url, json=upload_req, headers=headers)
        print(f"Upload status: {upload_res.status_code}")
        
        if upload_res.status_code == 200:
            try:
                # Handle SSE response format
                response_text = upload_res.text
                if response_text.startswith("event:"):
                    # Parse SSE format
                    for line in response_text.splitlines():
                        if line.startswith("data: "):
                            result = json.loads(line[6:])
                            break
                else:
                    result = upload_res.json()
                    
                print("✅ Upload response received:")
                print(json.dumps(result, indent=2, default=str))
                
                # Check if we got a file_id and quick_analysis
                if result.get("result", {}).get("success"):
                    file_id = result["result"].get("file_id")
                    quick_analysis = result["result"].get("quick_analysis")
                    storage_path = result["result"].get("storage_path")
                    
                    print(f"\n🎉 Upload successful!")
                    print(f"📁 File ID: {file_id}")
                    print(f"☁️ Storage path: {storage_path}")
                    
                    if quick_analysis:
                        print(f"📊 Quick analysis available:")
                        summary = quick_analysis.get("summary", {})
                        print(f"   - Rows: {summary.get('rows', 'N/A')}")
                        print(f"   - Columns: {summary.get('columns', 'N/A')}")
                        print(f"   - Numeric cols: {summary.get('numeric_columns', 'N/A')}")
                        print(f"   - Categorical cols: {summary.get('categorical_columns', 'N/A')}")
                    else:
                        print("⚠️ No quick analysis in response")
                        
                else:
                    print(f"❌ Upload failed: {result.get('result', {}).get('error')}")
                    
            except Exception as e:
                print(f"❌ Failed to parse response: {e}")
                print(f"Raw response: {upload_res.text}")
        else:
            print(f"❌ Upload request failed: {upload_res.text}")

if __name__ == "__main__":
    asyncio.run(test_direct_upload())
