#!/usr/bin/env python3
"""
Interactive MCP Client CLI
"""
import asyncio
import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp_client.client import create_mcp_client

async def interactive_mode():
    """Run interactive CLI mode"""
    print("🚀 MCP Log Analytics Interactive CLI")
    print("=" * 50)
    
    client = await create_mcp_client()
    
    try:
        # Health check on startup
        health = await client.health_check()
        print("📊 Server Status:")
        for server, status in health.items():
            icon = "✅" if status["status"] == "healthy" else "❌"
            print(f"  {icon} {server}: {status['status']}")
        print()
        
        print("Type 'quit' to exit, 'help' for commands")
        
        while True:
            query = input("\nQuery> ").strip()
            
            if not query:
                continue
                
            if query.lower() in ["quit", "exit", "q"]:
                break
                
            if query.lower() == "help":
                print("""
Available commands:
- quit/exit/q: Exit the program
- health: Check server health
- Any other text: Process as query
                """)
                continue
                
            if query.lower() == "health":
                health = await client.health_check()
                print(json.dumps(health, indent=2))
                continue
            
            try:
                print("\n🤖 Processing...")
                response = await client.process_query(query)
                print("\n" + "=" * 50)
                print("Response:")
                print(response)
                print("=" * 50)
                
            except Exception as e:
                print(f"\n❌ Error: {e}")
                
    finally:
        await client.cleanup()
        print("\n👋 Goodbye!")

async def main():
    """Main entry point"""
    if len(sys.argv) > 1:
        # Single query mode
        client = await create_mcp_client()
        try:
            query = " ".join(sys.argv[1:])
            response = await client.process_query(query)
            print(response)
        finally:
            await client.cleanup()
    else:
        # Interactive mode
        await interactive_mode()

if __name__ == "__main__":
    asyncio.run(main())