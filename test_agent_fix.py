#!/usr/bin/env python3
"""
Test script to verify the agent fix
"""
import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from agent.agent import LogAnalyticsAgent

async def test_agent():
    """Test the fixed agent"""
    print("🧪 Testing fixed agent...")
    
    try:
        # Create and initialize agent
        agent = LogAnalyticsAgent()
        await agent.initialize()
        print("✅ Agent initialized successfully")
        
        # Test a simple query
        result = await agent.analyze(
            query="Hello, can you help me?",
            user_id="test_user",
            conversation_id="test_session"
        )
        print(f"✅ Simple query result: {result[:100]}...")
        
        # Test a query that requires tools
        result2 = await agent.analyze(
            query="Search for information about Python programming",
            user_id="test_user", 
            conversation_id="test_session"
        )
        print(f"✅ Tool-based query result: {result2[:100]}...")
        
        print("🎉 All tests passed!")
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_agent())
