"""
Example usage demonstrating the MCP LLM Agent with LangGraph
"""
import asyncio
import os
from typing import Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Import the enhanced MCP client
from mcp_client.client import MCPClient, create_mcp_client

async def test_mcp_llm_agent():
    """Test the MCP LLM Agent with LangGraph-based routing"""
    print("🤖 Testing MCP LLM Agent with LangGraph")
    print("=" * 50)
    
    # Create client with OpenAI as default provider
    client = await create_mcp_client(provider="openai", model="gpt-4o-mini")
    
    try:
        # Test 1: Query that should use web search tool
        print("\n📊 Test 1: Web Search Query")
        query1 = "Search for the latest news about artificial intelligence and machine learning"
        print(f"Query: {query1}")
        
        response1 = await client.process_query(query1)
        print(f"Response: {response1[:200]}..." if len(response1) > 200 else f"Response: {response1}")
        
        # Test 2: Query that should use log storage
        print("\n📊 Test 2: Log Storage Query")
        query2 = "Store a log entry about this conversation with level INFO"
        print(f"Query: {query2}")
        
        response2 = await client.process_query(query2)
        print(f"Response: {response2[:200]}..." if len(response2) > 200 else f"Response: {response2}")
        
        # Test 3: Query that should use cache operations
        print("\n📊 Test 3: Cache Operations Query")
        query3 = "Set a cache value with key 'session_id' and value 'abc123' with TTL of 3600 seconds"
        print(f"Query: {query3}")
        
        response3 = await client.process_query(query3)
        print(f"Response: {response3[:200]}..." if len(response3) > 200 else f"Response: {response3}")
        
        # Test 4: Query that should use text summarization
        print("\n📊 Test 4: Text Summarization Query")
        query4 = "Summarize this text: 'Artificial intelligence is revolutionizing industries across the globe. From healthcare to finance, AI technologies are enabling automation, improving decision-making, and creating new opportunities for innovation.'"
        print(f"Query: {query4}")
        
        response4 = await client.process_query(query4)
        print(f"Response: {response4[:200]}..." if len(response4) > 200 else f"Response: {response4}")
        
        # Test 5: Multi-step query that should use multiple tools
        print("\n📊 Test 5: Multi-step Query")
        query5 = "Search for information about Python programming, then store a log about what you found"
        print(f"Query: {query5}")
        
        response5 = await client.process_query(query5)
        print(f"Response: {response5[:200]}..." if len(response5) > 200 else f"Response: {response5}")
        
        # Test 6: Show available tools
        print("\n📊 Test 6: Available Tools")
        tools = await client.get_mcp_tools()
        print(f"Available tools: {len(tools)}")
        for i, tool in enumerate(tools[:5]):  # Show first 5 tools
            func = tool.get("function", {})
            name = func.get("name", "unknown")
            desc = func.get("description", "no description")
            print(f"  {i+1}. {name}: {desc}")
        
    except Exception as e:
        print(f"❌ Error during testing: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        await client.cleanup()

async def test_provider_switching():
    """Test switching between different LLM providers"""
    print("\n🔄 Testing Provider Switching")
    print("=" * 30)
    
    providers_to_test = ["openai"]  # Add "gemini", "vllm" if configured
    
    for provider in providers_to_test:
        print(f"\n🤖 Testing with {provider.upper()}")
        try:
            client = await create_mcp_client(provider=provider, model="gpt-4o-mini")
            
            query = "What tools are available to help me with data analysis?"
            print(f"Query: {query}")
            
            response = await client.process_query(query)
            print(f"Response: {response[:150]}..." if len(response) > 150 else f"Response: {response}")
            
            await client.cleanup()
            
        except Exception as e:
            print(f"❌ Error with {provider}: {e}")

async def test_agent_workflow():
    """Test the LangGraph agent workflow directly"""
    print("\n🔀 Testing LangGraph Agent Workflow")
    print("=" * 35)
    
    client = await create_mcp_client()
    
    try:
        # Access the internal MCP LLM agent
        agent = client.mcp_llm_agent
        
        if agent:
            print("✅ MCP LLM Agent initialized")
            
            # Test direct agent call
            query = "Help me understand what tools I have access to"
            tools = await client.get_mcp_tools()
            
            print(f"📊 Available tools: {len(tools)}")
            print(f"🤖 Query: {query}")
            
            response = await agent.process_query(
                query=query,
                provider="openai",
                model="gpt-4o-mini",
                tools=tools,
                tool_choice="auto"
            )
            
            print(f"📝 Agent Response: {response}")
        else:
            print("❌ MCP LLM Agent not initialized")
    
    except Exception as e:
        print(f"❌ Error testing agent workflow: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        await client.cleanup()

async def main():
    """Main test function"""
    print("🚀 MCP LLM Agent Testing Suite")
    print("=" * 50)
    
    # Check if API keys are set
    if not os.getenv("OPENAI_API_KEY"):
        print("⚠️ Warning: OPENAI_API_KEY not set. Some tests may fail.")
    
    try:
        await test_mcp_llm_agent()
        await test_provider_switching()
        await test_agent_workflow()
        
    except Exception as e:
        print(f"❌ Error in main: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n✅ Testing completed!")

if __name__ == "__main__":
    asyncio.run(main())
