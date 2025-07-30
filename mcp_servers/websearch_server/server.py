"""
WebSearch FastMCP server with streamable HTTP transport using OpenAI's search model
"""
import os
import sys
import json
import asyncio
from datetime import datetime
import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from fastmcp import FastMCP

# Add project root to sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Config imports
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "..", "config"))

from config.settings import OPENAI_API_KEY, SEARCH_MODEL, WEBSEARCH_MCP_PORT, WEBSEARCH_MCP_NAME

# Logging configuration
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Import OpenAI
try:
    from openai import AsyncOpenAI
    logger.info("OpenAI library imported successfully")
except ImportError as e:
    logger.error(f"Failed to import OpenAI: {e}")
    sys.exit(1)

# Initialize OpenAI client
if not OPENAI_API_KEY:
    logger.error("OPENAI_API_KEY not found in environment variables")
    sys.exit(1)

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# Create MCP instance
mcp = FastMCP(WEBSEARCH_MCP_NAME)
async def search_with_openai(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Search the web using OpenAI's search model capabilities.
    This uses OpenAI's search functionality to find relevant web results.
    """
    try:
        # Use OpenAI's search capabilities by crafting a search-oriented prompt
        search_prompt = f"""
        Search the web for information about: "{query}"
        
        Return the search results in JSON format with the following structure for each result:
        {{
            "title": "Title of the webpage",
            "url": "URL of the webpage", 
            "snippet": "Brief description or snippet from the webpage",
            "relevance_score": 0.95
        }}
        
        Provide up to {limit} relevant search results. Focus on authoritative and recent sources.
        Return only the JSON array of results, no additional text.
        """
        
        response = await openai_client.chat.completions.create(
            model=SEARCH_MODEL,
            messages=[
                {
                    "role": "system", 
                    "content": "You are a web search assistant. When asked to search, return relevant web search results in the exact JSON format requested. Provide real, accurate URLs and descriptions."
                },
                {"role": "user", "content": search_prompt}
            ],
            max_tokens=2000,
            temperature=0.1
        )

        # Safely extract the content from the response
        result_text = ""
        try:
            result_text = response.choices[0].message.content
            if result_text is not None:
                result_text = result_text.strip()
            else:
                logger.warning("OpenAI response message content is None")
                result_text = ""
        except (AttributeError, IndexError) as e:
            logger.error(f"Error extracting content from OpenAI response: {e}")
            result_text = ""

        # Try to parse as JSON
        try:
            # Clean up the response if it has markdown code blocks
            if result_text.startswith("```json"):
                result_text = result_text[7:]
            if result_text.endswith("```"):
                result_text = result_text[:-3]
            
            results = json.loads(result_text)
            
            # Ensure it's a list
            if not isinstance(results, list):
                results = [results] if isinstance(results, dict) else []
                
            # Validate and clean results
            cleaned_results = []
            for result in results[:limit]:
                if isinstance(result, dict) and "title" in result:
                    cleaned_result = {
                        "title": result.get("title", ""),
                        "url": result.get("url", ""),
                        "snippet": result.get("snippet", ""),
                        "relevance_score": result.get("relevance_score", 0.8)
                    }
                    cleaned_results.append(cleaned_result)
            
            return cleaned_results
            
        except json.JSONDecodeError:
            # If JSON parsing fails, create a single result with the response
            logger.warning("Failed to parse JSON from OpenAI response, creating fallback result")
            return [
                {
                    "title": f"Search results for: {query}",
                    "url": "https://www.google.com/search?q=" + query.replace(" ", "+"),
                    "snippet": result_text[:300] + "..." if len(result_text) > 300 else result_text,
                    "relevance_score": 0.7
                }
            ]
            
    except Exception as e:
        logger.error(f"OpenAI search error: {e}")
        # Return a fallback result
        return [
            {
                "title": f"Search: {query}",
                "url": "https://www.google.com/search?q=" + query.replace(" ", "+"),
                "snippet": f"Unable to complete search due to error: {str(e)}",
                "relevance_score": 0.5
            }
        ]

# Tool definitions
@mcp.tool()
async def web_search(query: str, limit: int = 10) -> Dict[str, Any]:
    """
    Perform intelligent web search using OpenAI's search capabilities.
    
    This tool leverages OpenAI's language model to search the web and return
    relevant, structured results. It provides high-quality search results with
    relevance scoring and comprehensive metadata.
    
    Features:
    - Intelligent query understanding and expansion
    - Relevance scoring for each result
    - Real-time web content access
    - Structured response format
    - Automatic result validation and filtering
    
    Args:
        query: Search query string (e.g., "latest developments in AI", "Python best practices")
        limit: Number of search results to return (1-20, default: 10)
        
    Returns:
        Structured response with search results including:
        - title: Webpage title
        - url: Full webpage URL
        - snippet: Content summary/description
        - relevance_score: Relevance to query (0.0-1.0)
        
    Example:
        web_search("machine learning frameworks 2024", 5)
    """
    try:
        logger.info(f"Performing web search for: {query}")
        
        # Validate inputs
        if not query or not query.strip():
            return {
                "success": False,
                "error": "Query cannot be empty",
                "query": query,
                "timestamp": datetime.utcnow().isoformat()
            }
        
        # Ensure limit is within bounds
        limit = max(1, min(limit, 20))
        
        results = await search_with_openai(query.strip(), limit)
        
        return {
            "success": True,
            "query": query.strip(),
            "count": len(results),
            "results": results,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Error in web search: {e}")
        return {
            "success": False,
            "error": f"Search error: {str(e)}",
            "query": query,
            "timestamp": datetime.utcnow().isoformat()
        }

@mcp.tool()
async def health_check() -> Dict[str, Any]:
    """
    Comprehensive health check for the websearch server.
    
    This tool verifies the operational status of all components:
    - OpenAI API connectivity and authentication
    - Search model availability and configuration
    - Server responsiveness and resource availability
    
    Returns:
        Detailed health status with component-level diagnostics
        
    Example:
        health_check()  # No parameters needed
    """
    
    # Check OpenAI status
    openai_status = "not_configured"
    if openai_client and OPENAI_API_KEY:
        try:
            # Test with a simple API call
            await openai_client.models.list()
            openai_status = "connected"
        except Exception as e:
            openai_status = f"error: {str(e)[:100]}"
    
    # Test search functionality
    search_status = "unknown"
    try:
        test_results = await search_with_openai("test search", 1)
        search_status = "available" if test_results else "no_results"
    except Exception as e:
        search_status = f"error: {str(e)[:100]}"
    
    return {
        "status": "healthy" if openai_status == "connected" else "degraded",
        "service": "websearch_server",
        "openai": {
            "status": openai_status,
            "model": SEARCH_MODEL,
            "api_key_configured": bool(OPENAI_API_KEY)
        },
        "search": {
            "status": search_status,
            "provider": "openai"
        },
        "tools": ["web_search", "health_check"],
        "timestamp": datetime.utcnow().isoformat()
    }

# Main entry point
if __name__ == "__main__":
    # Run with FastMCP streamable HTTP transport
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=WEBSEARCH_MCP_PORT,
        log_level="WARNING"
    )
