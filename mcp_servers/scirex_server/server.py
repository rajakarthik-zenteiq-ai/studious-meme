"""
SciREX FastMCP server with streamable HTTP transport
"""
import os
import sys
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from fastmcp import FastMCP

# Config imports
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "..", "config"))

from settings import SCIREX_MCP_PORT

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create MCP instance
mcp = FastMCP("scirex_tools")

# Pydantic models
class TrainModelRequest(BaseModel):
    dataset_name: str = Field(..., description="Name of the dataset to train on")
    model_type: str = Field(default="neural_network", description="Type of model to train")
    parameters: Optional[Dict[str, Any]] = Field(default=None, description="Model parameters")

class PredictRequest(BaseModel):
    model_name: str = Field(..., description="Name of the trained model")
    input_data: List[float] = Field(..., description="Input data for prediction")

class ClusteringRequest(BaseModel):
    data: List[List[float]] = Field(..., description="Data points to cluster")
    n_clusters: int = Field(default=3, ge=2, le=20, description="Number of clusters")
    algorithm: str = Field(default="kmeans", description="Clustering algorithm")

# Tool definitions
@mcp.tool()
async def train_neural_network(request: TrainModelRequest) -> Dict[str, Any]:
    """Train a neural network model."""
    try:
        # Simulate training process
        logger.info(f"Training {request.model_type} on dataset {request.dataset_name}")
        
        # This would normally involve actual ML training
        model_id = f"model_{request.dataset_name}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        
        return {
            "success": True,
            "model_id": model_id,
            "model_type": request.model_type,
            "dataset": request.dataset_name,
            "status": "trained",
            "parameters": request.parameters or {}
        }
    except Exception as e:
        logger.error(f"Error training model: {e}")
        return {
            "success": False,
            "error": f"Training error: {str(e)}"
        }

@mcp.tool()
async def perform_clustering(request: ClusteringRequest) -> Dict[str, Any]:
    """Perform clustering on data points."""
    try:
        # Simulate clustering
        logger.info(f"Performing {request.algorithm} clustering with {request.n_clusters} clusters")
        
        # Simple mock clustering results
        n_points = len(request.data)
        labels = [i % request.n_clusters for i in range(n_points)]
        
        # Calculate mock centroids
        centroids = []
        for cluster_id in range(request.n_clusters):
            centroid = [0.0] * len(request.data[0]) if request.data else []
            centroids.append(centroid)
        
        return {
            "success": True,
            "algorithm": request.algorithm,
            "n_clusters": request.n_clusters,
            "n_points": n_points,
            "labels": labels,
            "centroids": centroids
        }
    except Exception as e:
        logger.error(f"Error in clustering: {e}")
        return {
            "success": False,
            "error": f"Clustering error: {str(e)}"
        }

@mcp.tool()
async def predict(request: PredictRequest) -> Dict[str, Any]:
    """Make predictions using a trained model."""
    try:
        # Simulate prediction
        logger.info(f"Making prediction with model {request.model_name}")
        
        # Mock prediction result
        prediction = sum(request.input_data) / len(request.input_data)  # Simple average
        
        return {
            "success": True,
            "model_name": request.model_name,
            "prediction": prediction,
            "confidence": 0.85,  # Mock confidence
            "input_shape": len(request.input_data)
        }
    except Exception as e:
        logger.error(f"Error in prediction: {e}")
        return {
            "success": False,
            "error": f"Prediction error: {str(e)}"
        }

@mcp.tool()
async def list_models() -> Dict[str, Any]:
    """List all available trained models."""
    try:
        # Mock model list
        models = [
            {
                "id": "model_sample_20250717_001",
                "name": "Sample Neural Network",
                "type": "neural_network",
                "status": "trained",
                "created": "2025-07-17T10:00:00Z"
            },
            {
                "id": "model_iris_20250717_002",
                "name": "Iris Classifier",
                "type": "classification",
                "status": "trained",
                "created": "2025-07-17T11:00:00Z"
            }
        ]
        
        return {
            "success": True,
            "models": models,
            "count": len(models)
        }
    except Exception as e:
        logger.error(f"Error listing models: {e}")
        return {
            "success": False,
            "error": f"List error: {str(e)}"
        }

@mcp.tool()
async def health_check() -> Dict[str, Any]:
    """Health check for the SciREX server."""
    return {
        "status": "healthy",
        "service": "scirex_server",
        "features": ["neural_networks", "clustering", "prediction"],
        "tools": ["train_neural_network", "perform_clustering", "predict", "list_models", "health_check"],
        "timestamp": datetime.utcnow().isoformat()
    }

# Main entry point
if __name__ == "__main__":
    logger.info(f"Starting SciREX MCP Server on port {SCIREX_MCP_PORT}")
    
    # Run with FastMCP streamable HTTP transport
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=SCIREX_MCP_PORT,
        log_level="INFO"
    )
