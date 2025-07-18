#!/usr/bin/env python3
"""
Test MCP tool calling and async fixes
"""
import asyncio
import logging
from agent.agent import LogAnalyticsAgent

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_web_search():
    """Test web search tool specifically"""
    try:
        agent = LogAnalyticsAgent()
        await agent.initialize()
        
        print(f"✅ Agent initialized with {len(agent.tools)} tools")
        
        # Test a query that should trigger web search
        response = await agent.analyze(
            query="Search for recent news about artificial intelligence and machine learning",
            user_id="test_user",
            conversation_id="test_conv"
        )
        print(f"✅ Web search query successful")
        print(f"Response: {response[:300]}...")
        
        return True
    except Exception as e:
        print(f"❌ Web search test failed: {e}")
        logger.exception("Web search test error")
        return False

async def test_tool_usage():
    """Test various tool usage scenarios"""
    try:
        agent = LogAnalyticsAgent()
        await agent.initialize()
        
        test_queries = [
            "What tools do you have available?",
            "Can you search for information about Python programming?",
            "List the capabilities you have.",
        ]
        
        for i, query in enumerate(test_queries, 1):
            print(f"\n🔍 Test {i}: {query}")
            try:
                response = await agent.analyze(
                    query=query,
                    user_id="test_user",
                    conversation_id="test_conv"
                )
                print(f"✅ Response: {response[:150]}...")
            except Exception as e:
                print(f"❌ Query failed: {e}")
        
        return True
    except Exception as e:
        print(f"❌ Tool usage test failed: {e}")
        logger.exception("Tool usage test error")
        return False

async def main():
    print("🔧 Testing MCP Tool Calling and Async Fixes")
    print("=" * 60)
    
    # Test web search specifically
    print("\n🌐 Testing web search functionality...")
    await test_web_search()
    
    # Test general tool usage
    print("\n🛠️ Testing general tool usage...")
    await test_tool_usage()

if __name__ == "__main__":
    asyncio.run(main())
