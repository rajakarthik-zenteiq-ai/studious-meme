#!/usr/bin/env python3
"""
Quick test for the web search functionality
"""
import asyncio
import logging
from agent.agent import LogAnalyticsAgent

logging.basicConfig(level=logging.INFO)

async def test_web_search():
    print("🔧 Testing Web Search Tool")
    print("=" * 40)
    
    agent = LogAnalyticsAgent()
    await agent.initialize()
    
    print(f"✅ Agent initialized with {len(agent.tools)} tools")
    
    # Test a query that should use web search
    try:
        response = await agent.analyze(
            query="Search for Python programming tutorials",
            user_id="test_user",
            conversation_id="test_conv"
        )
        print("✅ Web search test completed")
        print(f"Response length: {len(response)} characters")
        print(f"First 200 chars: {response[:200]}...")
        return True
    except Exception as e:
        print(f"❌ Web search test failed: {e}")
        return False

if __name__ == "__main__":
    asyncio.run(test_web_search())
