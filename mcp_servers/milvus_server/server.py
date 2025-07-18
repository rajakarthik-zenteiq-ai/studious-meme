"""
Milvus FastMCP server with streamable HTTP transport
"""
import os
import sys
import logging
from datetime import datetime
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

from config.settings import MILVUS_HOST, MILVUS_PORT, MILVUS_COLLECTION, MILVUS_MCP_PORT

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create MCP instance
mcp = FastMCP("milvus_tools")

# Initialize Milvus connection
milvus_connected = False
try:
    from pymilvus import connections, Collection, utility
    connections.connect(
        alias="default",
        host=MILVUS_HOST,
        port=MILVUS_PORT
    )
    milvus_connected = True
    logger.info("✅ Milvus client initialized")
except Exception as e:
    logger.error(f"Failed to initialize Milvus client: {e}")

# Pydantic models
class VectorQuery(BaseModel):
    query_vector: List[float] = Field(..., description="Query vector for similarity search")
    limit: int = Field(default=10, ge=1, le=100, description="Number of results to return")
    collection_name: str = Field(default=MILVUS_COLLECTION, description="Collection name")

class InsertVectors(BaseModel):
    vectors: List[List[float]] = Field(..., description="Vectors to insert")
    metadata: Optional[List[Dict[str, Any]]] = Field(default=None, description="Metadata for vectors")
    collection_name: str = Field(default=MILVUS_COLLECTION, description="Collection name")

# Tool definitions
@mcp.tool()
async def search_similar(query: VectorQuery) -> Dict[str, Any]:
    """Search for similar vectors in Milvus collection."""
    if not milvus_connected:
        return {
            "success": False,
            "error": "Milvus client not connected"
        }
    
    try:
        # Check if collection exists
        if not utility.has_collection(query.collection_name):
            return {
                "success": False,
                "error": f"Collection {query.collection_name} does not exist"
            }
        
        collection = Collection(query.collection_name)
        collection.load()
        
        # Perform search
        search_results = collection.search(
            data=[query.query_vector],
            anns_field="vector",
            param={"metric_type": "L2", "params": {"nprobe": 10}},
            limit=query.limit,
            output_fields=["id"]
        )

        results = []
        for hits in search_results: # type: ignore
            for hit in hits:
                results.append({
                    "id": hit.id,
                    "distance": hit.distance,
                    "entity": hit.entity
                })
        
        return {
            "success": True,
            "collection": query.collection_name,
            "count": len(results),
            "results": results
        }
    except Exception as e:
        logger.error(f"Error in vector search: {e}")
        return {
            "success": False,
            "error": f"Search error: {str(e)}"
        }

@mcp.tool()
async def insert_vectors(request: InsertVectors) -> Dict[str, Any]:
    """Insert vectors into Milvus collection."""
    if not milvus_connected:
        return {
            "success": False,
            "error": "Milvus client not connected"
        }
    
    try:
        # Check if collection exists
        if not utility.has_collection(request.collection_name):
            return {
                "success": False,
                "error": f"Collection {request.collection_name} does not exist"
            }
        
        collection = Collection(request.collection_name)
        
        # Prepare data for insertion
        entities = [
            list(range(len(request.vectors))),  # IDs
            request.vectors  # Vectors
        ]
        
        # Insert data
        mr = collection.insert(entities)
        collection.flush()
        
        return {
            "success": True,
            "collection": request.collection_name,
            "inserted_count": len(request.vectors),
            "insert_ids": mr.primary_keys
        }
    except Exception as e:
        logger.error(f"Error inserting vectors: {e}")
        return {
            "success": False,
            "error": f"Insert error: {str(e)}"
        }

@mcp.tool()
async def get_collection_stats() -> Dict[str, Any]:
    """Get statistics for all collections."""
    if not milvus_connected:
        return {
            "success": False,
            "error": "Milvus client not connected"
        }
    
    try:
        collections = utility.list_collections()
        stats = {}
        
        for collection_name in collections:
            collection = Collection(collection_name)
            stats[collection_name] = {
                "num_entities": collection.num_entities,
                "description": collection.description
            }
        
        return {
            "success": True,
            "collections": stats,
            "total_collections": len(collections)
        }
    except Exception as e:
        logger.error(f"Error getting collection stats: {e}")
        return {
            "success": False,
            "error": f"Stats error: {str(e)}"
        }

@mcp.tool()
async def health_check() -> Dict[str, Any]:
    """Health check for the Milvus server."""
    status = "healthy" if milvus_connected else "unhealthy"
    
    collection_count = 0
    if milvus_connected:
        try:
            collections = utility.list_collections()
            collection_count = len(collections)
        except:
            status = "degraded"
    
    return {
        "status": status,
        "service": "milvus_server",
        "connected": milvus_connected,
        "host": MILVUS_HOST,
        "port": MILVUS_PORT,
        "collection_count": collection_count,
        "tools": ["search_similar", "insert_vectors", "get_collection_stats", "health_check"],
        "timestamp": datetime.utcnow().isoformat()
    }

# Main entry point
if __name__ == "__main__":
    logger.info(f"Starting Milvus MCP Server on port {MILVUS_MCP_PORT}")
    
    # Run with FastMCP streamable HTTP transport
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=MILVUS_MCP_PORT,
        log_level="INFO"
    )
