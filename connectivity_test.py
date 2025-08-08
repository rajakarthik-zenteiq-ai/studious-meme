#!/usr/bin/env python3
"""
Simple MCP connectivity test for production setup
"""
import asyncio
import httpx
import sys

# Test endpoints
ENDPOINTS = {
    "MongoDB": "http://localhost:8100/mcp/",
    "WebSearch": "http://localhost:8140/mcp/", 
    "Milvus": "http://localhost:8110/mcp/",
    "SciREX": "http://localhost:8150/mcp/"
}

async def test_connectivity(startup_mode=False):
    """Test connectivity to all MCP servers"""
    print("🧪 Testing MCP server connectivity...")
    
    all_healthy = True
    
    async with httpx.AsyncClient(timeout=5.0) as client:
        for service, url in ENDPOINTS.items():
            try:
                response = await client.get(url)
                # MCP servers return 406 for normal GET requests - this means they're working
                if response.status_code == 406 and "Not Acceptable" in response.text:
                    print(f"✅ {service}: MCP server responding correctly")
                elif response.status_code == 200:
                    print(f"✅ {service}: Connected")
                else:
                    print(f"⚠️ {service}: Unexpected status {response.status_code}")
                    all_healthy = False
            except Exception as e:
                print(f"❌ {service}: Connection failed - {e}")
                all_healthy = False
    
    if all_healthy:
        print("\n🎉 All MCP servers are healthy!")
        return 0
    else:
        if startup_mode:
            print("\n⚠️ Some services are still starting up - this is normal")
            print("🔄 You can test connectivity later with: make test")
            return 0  # Don't fail during startup
        else:
            print("\n⚠️ Some services may need more time to start")
            return 1

if __name__ == "__main__":
    # Check if this is being run during startup
    startup_mode = "--startup" in sys.argv
    exit_code = asyncio.run(test_connectivity(startup_mode))
    sys.exit(exit_code)
