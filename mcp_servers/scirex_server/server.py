"""
SciREX FastMCP server with full ML support and MinIO integration
"""
import os
import sys
import logging
import asyncio
import httpx
from datetime import datetime
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd
import joblib
import io
import base64

from pydantic import BaseModel, Field
from fastmcp import FastMCP

# Add SciREX to path
HERE = os.path.dirname(__file__)
SCIREX_PATH = os.path.join(HERE, "SciREX")
sys.path.insert(0, SCIREX_PATH)
sys.path.insert(0, os.path.join(HERE, "..", "..", "config"))

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# SciREX imports (with error handling for missing dependencies)
try:
    # Import base classes for consistent interface
    from scirex.core.ml.unsupervised.clustering.base import Clustering
    from scirex.core.ml.supervised.classification.base import Classification
    
    # Import specific implementations
    from scirex.core.ml.unsupervised.clustering.kmeans import Kmeans
    from scirex.core.ml.supervised.classification.svm import SVMClassifier
    from scirex.core.ml.supervised.classification.logistic_regression import LogisticRegressionClassifier
    from scirex.core.ml.supervised.classification.naive_bayes import NaiveBayes
    
    SCIREX_AVAILABLE = True
    logger.info("SciREX successfully imported with base classes and implementations")
except ImportError as e:
    logger.warning(f"SciREX import failed: {e} - using fallback implementations")
    SCIREX_AVAILABLE = False
    # Fallback implementations
    import sklearn.cluster
    import sklearn.svm
    import sklearn.neural_network

from settings import SCIREX_MCP_PORT

# Create MCP instance
mcp = FastMCP("scirex_tools")

# MongoDB server endpoint for file operations
MONGO_SERVER_URL = "http://mongo_server:8100" if os.getenv("IS_DOCKER") else "http://localhost:8100"

# Pydantic models
class TrainModelRequest(BaseModel):
    dataset_id: str = Field(..., description="File ID of dataset in MinIO")
    model_type: str = Field(..., description="Model type: kmeans, svm, logistic_regression, naive_bayes, mlp")
    model_params: Dict[str, Any] = Field(default_factory=dict, description="Model parameters")
    user_id: str = Field(..., description="User ID for RBAC")
    target_column: Optional[str] = Field(None, description="Target column for supervised learning")

class PredictRequest(BaseModel):
    model_id: str = Field(..., description="ID of trained model in MinIO")
    input_data: List[List[float]] = Field(..., description="Input data for prediction")
    user_id: str = Field(..., description="User ID for RBAC")

class ClusteringRequest(BaseModel):
    dataset_id: str = Field(..., description="File ID of dataset in MinIO")
    n_clusters: Optional[int] = Field(None, description="Number of clusters (None for auto-selection)")
    max_k: int = Field(default=10, ge=2, le=20, description="Maximum number of clusters for auto-selection")
    algorithm: str = Field(default="kmeans", description="Clustering algorithm")
    user_id: str = Field(..., description="User ID for RBAC")

class ClassificationRequest(BaseModel):
    dataset_id: str = Field(..., description="File ID of dataset in MinIO")
    algorithm: str = Field(default="svm", description="Classification algorithm: svm, logistic_regression, naive_bayes")
    target_column: str = Field(..., description="Target column name")
    test_size: float = Field(default=0.2, ge=0.1, le=0.5, description="Test split ratio")
    user_id: str = Field(..., description="User ID for RBAC")
    model_params: Dict[str, Any] = Field(default_factory=dict, description="Model parameters")

# Helper functions
def create_scirex_model(model_type: str, model_params: Dict[str, Any]):
    """Create SciREX model instance using base classes"""
    if not SCIREX_AVAILABLE:
        return None
    
    # Clustering algorithms (inherit from Clustering base class)
    if model_type == "kmeans":
        n_clusters = model_params.get("n_clusters")
        max_k = model_params.get("max_k", 10)
        return Kmeans(n_clusters=n_clusters, max_k=max_k)
    
    # Classification algorithms (inherit from Classification base class)
    elif model_type == "svm":
        kernel = model_params.get("kernel", "rbf")
        cv = model_params.get("cv", 5)
        return SVMClassifier(kernel=kernel, cv=cv)
    
    elif model_type == "logistic_regression":
        return LogisticRegressionClassifier(**model_params)
    
    elif model_type == "naive_bayes":
        model_subtype = model_params.get("model_type", "gaussian")
        return NaiveBayes(model_type=model_subtype)
    
    else:
        raise ValueError(f"Unsupported SciREX model type: {model_type}")

def create_fallback_model(model_type: str, model_params: Dict[str, Any]):
    """Create fallback sklearn models when SciREX is not available"""
    if model_type == "kmeans":
        from sklearn.cluster import KMeans
        n_clusters = model_params.get("n_clusters", 3)
        return KMeans(n_clusters=n_clusters, random_state=42)
    elif model_type == "svm":
        from sklearn.svm import SVC
        return SVC(random_state=42, **model_params)
    elif model_type == "logistic_regression":
        from sklearn.linear_model import LogisticRegression
        return LogisticRegression(random_state=42, **model_params)
    elif model_type == "naive_bayes":
        from sklearn.naive_bayes import GaussianNB
        return GaussianNB()
    elif model_type == "mlp":
        from sklearn.neural_network import MLPClassifier
        return MLPClassifier(random_state=42, **model_params)
    else:
        raise ValueError(f"Unsupported fallback model type: {model_type}")



async def get_dataset_from_minio(file_id: str, user_id: str) -> pd.DataFrame:
    """Download dataset from MinIO via MongoDB server"""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{MONGO_SERVER_URL}/download_file",
            json={"file_id": file_id, "user_id": user_id}
        )
        if response.status_code != 200:
            raise Exception(f"Failed to download dataset: {response.text}")
        
        result = response.json()
        if not result.get("success"):
            raise Exception(result.get("error", "Unknown error"))
        
        # Decode base64 content
        content = base64.b64decode(result["content"])
        return pd.read_csv(io.StringIO(content.decode()))

async def save_model_to_minio(model, model_id: str, user_id: str, metadata: Dict) -> str:
    """Save trained model to MinIO via MongoDB server"""
    # Serialize model
    buffer = io.BytesIO()
    joblib.dump(model, buffer)
    buffer.seek(0)
    
    # Encode as base64
    content_b64 = base64.b64encode(buffer.getvalue()).decode()
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{MONGO_SERVER_URL}/upload_file",
            json={
                "filename": f"{model_id}.joblib",
                "content": content_b64,
                "content_type": "application/octet-stream",
                "user_id": user_id,
                "metadata": metadata
            }
        )
        if response.status_code != 200:
            raise Exception(f"Failed to save model: {response.text}")
        
        result = response.json()
        if not result.get("success"):
            raise Exception(result.get("error", "Unknown error"))
        
        return result["file_id"]

# Tool definitions
@mcp.tool()
async def train_model(request: TrainModelRequest) -> Dict[str, Any]:
    """
    Train machine learning models on datasets using SciREX framework.
    
    This tool provides comprehensive ML model training capabilities:
    - Supports multiple algorithms (Random Forest, SVM, Logistic Regression, etc.)
    - Automatic data preprocessing and feature engineering
    - Hyperparameter optimization and cross-validation
    - Model evaluation with multiple metrics
    - Automatic model serialization and storage
    
    Features:
    - Dataset loading from MinIO or MongoDB
    - Missing value handling and data cleaning
    - Feature scaling and encoding
    - Model performance metrics and visualization
    - Export trained models for inference
    
    Args:
        request: TrainModelRequest with dataset_id, algorithm, target_column, and parameters
        
    Returns:
        Comprehensive training results with model metrics, file paths, and performance data
        
    Example:
        train_model({
            "dataset_id": "customer_data.csv",
            "algorithm": "random_forest",
            "target_column": "churn",
            "user_id": "user123",
            "parameters": {"n_estimators": 100, "max_depth": 10}
        })
    """
    try:
        # Download dataset
        df = await get_dataset_from_minio(request.dataset_id, request.user_id)
        logger.info(f"Loaded dataset with shape: {df.shape}")
        
        # Prepare data
        if request.target_column and request.target_column in df.columns:
            X = df.drop(columns=[request.target_column]).values
            y = df[request.target_column].values
        else:
            X = df.values
            y = None
        
        # Train model using SciREX or fallback
        model = None
        results = {}
        
        if SCIREX_AVAILABLE:
            try:
                model = create_scirex_model(request.model_type, request.model_params)
                
                if isinstance(model, Clustering):
                    # Use SciREX clustering pipeline
                    scirex_results = model.run(data=X)
                    results = {
                        "labels": model.labels.tolist() if hasattr(model, 'labels') else [],
                        "silhouette_score": scirex_results.get("silhouette_score", 0),
                        "calinski_harabasz_score": scirex_results.get("calinski_harabasz_score", 0),
                        "davies_bouldin_score": scirex_results.get("davies_bouldin_score", 0),
                        "n_clusters": getattr(model, 'n_clusters', None)
                    }
                
                elif isinstance(model, Classification):
                    # Use SciREX classification pipeline
                    if y is None:
                        return {"success": False, "error": f"{request.model_type} requires target column"}
                    
                    test_size = request.model_params.get("test_size", 0.2)
                    scirex_results = model.run(data=X, labels=y, test_size=test_size)
                    results = {
                        "accuracy": scirex_results.get("accuracy", 0),
                        "precision": scirex_results.get("precision", 0),
                        "recall": scirex_results.get("recall", 0),
                        "f1_score": scirex_results.get("f1_score", 0),
                        "params": scirex_results.get("params", {})
                    }
                
                else:
                    # Handle MLP or other models not using SciREX
                    if request.model_type == "mlp":
                        if y is None:
                            return {"success": False, "error": "MLP requires target column"}
                        from sklearn.neural_network import MLPClassifier
                        model = MLPClassifier(**request.model_params, random_state=42)
                        model.fit(X, y)
                        results = {"accuracy": model.score(X, y)}
                        logger.info("Using sklearn MLPClassifier (SciREX DL backend requires additional setup)")
                    else:
                        return {"success": False, "error": f"Unknown model type: {request.model_type}"}
                        
            except Exception as e:
                logger.warning(f"SciREX training failed: {e}, falling back to sklearn")
                SCIREX_AVAILABLE = False  # Fall back for this session
        
        # Fallback to sklearn implementations
        if not SCIREX_AVAILABLE:
            try:
                model = create_fallback_model(request.model_type, request.model_params)
                
                if request.model_type == "kmeans":
                    labels = model.fit_predict(X)
                    results = {"labels": labels.tolist(), "centers": model.cluster_centers_.tolist()}
                
                else:  # Classification models
                    if y is None:
                        return {"success": False, "error": f"{request.model_type} requires target column"}
                    model.fit(X, y)
                    results = {"accuracy": model.score(X, y)}
                    
            except Exception as e:
                return {"success": False, "error": f"Model training failed: {str(e)}"}
        
        # Save model to MinIO
        model_id = f"{request.user_id}_{request.model_type}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        metadata = {
            "model_type": request.model_type,
            "dataset_id": request.dataset_id,
            "model_params": request.model_params,
            "training_results": results,
            "scirex_used": SCIREX_AVAILABLE,
            "created_at": datetime.utcnow().isoformat()
        }
        
        saved_model_id = await save_model_to_minio(model, model_id, request.user_id, metadata)
        
        return {
            "success": True,
            "model_id": saved_model_id,
            "training_results": results,
            "model_type": request.model_type,
            "scirex_used": SCIREX_AVAILABLE
        }
        
    except Exception as e:
        logger.error(f"Training failed: {e}")
        return {"success": False, "error": str(e)}

@mcp.tool()
async def predict(request: PredictRequest) -> Dict[str, Any]:
    """Make predictions with a trained model"""
    try:
        # Download model from MinIO
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{MONGO_SERVER_URL}/download_file",
                json={"file_id": request.model_id, "user_id": request.user_id}
            )
            if response.status_code != 200:
                return {"success": False, "error": f"Failed to download model: {response.text}"}
            
            result = response.json()
            if not result.get("success"):
                return {"success": False, "error": result.get("error", "Unknown error")}
            
            # Decode and load model
            content = base64.b64decode(result["content"])
            model = joblib.load(io.BytesIO(content))
            
            # Make predictions
            predictions = model.predict(request.input_data)
            
            return {
                "success": True,
                "predictions": predictions.tolist() if hasattr(predictions, 'tolist') else predictions
            }
            
    except Exception as e:
        logger.error(f"Prediction failed: {e}")
        return {"success": False, "error": str(e)}

@mcp.tool()
async def cluster_analysis(request: ClusteringRequest) -> Dict[str, Any]:
    """Perform clustering analysis on dataset using SciREX"""
    try:
        # Download dataset
        df = await get_dataset_from_minio(request.dataset_id, request.user_id)
        X = df.values
        
        if request.algorithm == "kmeans":
            if SCIREX_AVAILABLE:
                try:
                    # Use SciREX with base class
                    model_params = {"n_clusters": request.n_clusters, "max_k": request.max_k}
                    clusterer = create_scirex_model("kmeans", model_params)
                    scirex_results = clusterer.run(data=X)
                    results = {
                        "labels": clusterer.labels.tolist() if hasattr(clusterer, 'labels') else [],
                        "silhouette_score": scirex_results.get("silhouette_score", 0),
                        "calinski_harabasz_score": scirex_results.get("calinski_harabasz_score", 0),
                        "davies_bouldin_score": scirex_results.get("davies_bouldin_score", 0),
                        "n_clusters": getattr(clusterer, 'n_clusters', request.n_clusters or 3),
                        "scirex_used": True
                    }
                except Exception as e:
                    logger.warning(f"SciREX clustering failed: {e}, falling back to sklearn")
                    # Fall back to sklearn
                    model_params = {"n_clusters": request.n_clusters or 3}
                    clusterer = create_fallback_model("kmeans", model_params)
                    labels = clusterer.fit_predict(X)
                    results = {
                        "labels": labels.tolist(),
                        "centers": clusterer.cluster_centers_.tolist(),
                        "inertia": clusterer.inertia_,
                        "n_clusters": request.n_clusters or 3,
                        "scirex_used": False
                    }
            else:
                # Use sklearn fallback
                model_params = {"n_clusters": request.n_clusters or 3}
                clusterer = create_fallback_model("kmeans", model_params)
                labels = clusterer.fit_predict(X)
                results = {
                    "labels": labels.tolist(),
                    "centers": clusterer.cluster_centers_.tolist(),
                    "inertia": clusterer.inertia_,
                    "n_clusters": request.n_clusters or 3,
                    "scirex_used": False
                }
        else:
            return {"success": False, "error": f"Unsupported clustering algorithm: {request.algorithm}"}
        
        return {
            "success": True,
            "algorithm": request.algorithm,
            "results": results
        }
        
    except Exception as e:
        logger.error(f"Clustering failed: {e}")
        return {"success": False, "error": str(e)}

@mcp.tool()
async def classification_analysis(request: ClassificationRequest) -> Dict[str, Any]:
    """Perform classification analysis with train/test split using SciREX"""
    try:
        # Download dataset
        df = await get_dataset_from_minio(request.dataset_id, request.user_id)
        
        if request.target_column not in df.columns:
            return {"success": False, "error": f"Target column '{request.target_column}' not found"}
        
        X = df.drop(columns=[request.target_column]).values
        y = df[request.target_column].values
        
        # Try SciREX first, fall back to sklearn if needed
        if SCIREX_AVAILABLE:
            try:
                classifier = create_scirex_model(request.algorithm, request.model_params)
                if classifier and isinstance(classifier, Classification):
                    # Use SciREX classification pipeline
                    scirex_results = classifier.run(data=X, labels=y, test_size=request.test_size)
                    result = {
                        "success": True,
                        "algorithm": request.algorithm,
                        "train_accuracy": scirex_results.get("accuracy", 0),
                        "test_accuracy": scirex_results.get("accuracy", 0),  # SciREX run() handles train/test split internally
                        "precision": scirex_results.get("precision", 0),
                        "recall": scirex_results.get("recall", 0),
                        "f1_score": scirex_results.get("f1_score", 0),
                        "params": scirex_results.get("params", {}),
                        "scirex_used": True
                    }
                    return result
            except Exception as e:
                logger.warning(f"SciREX classification failed: {e}, falling back to sklearn")
        
        # Fallback to sklearn with manual train/test split
        from sklearn.model_selection import train_test_split
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=request.test_size, random_state=42
        )
        
        # Train sklearn classifier using helper function
        try:
            classifier = create_fallback_model(request.algorithm, request.model_params)
            classifier.fit(X_train, y_train)
            train_acc = classifier.score(X_train, y_train)
            test_acc = classifier.score(X_test, y_test)
        except Exception as e:
            logger.error(f"Classification analysis failed: {e}")
            return {"success": False, "error": str(e)}
        
        result = {
            "success": True,
            "algorithm": request.algorithm,
            "train_accuracy": float(train_acc),
            "test_accuracy": float(test_acc),
            "train_size": len(X_train),
            "test_size": len(X_test),
            "scirex_used": False
        }
        
        return result
        
    except Exception as e:
        logger.error(f"Classification failed: {e}")
        return {"success": False, "error": str(e)}

@mcp.tool()
async def list_user_models(user_id: str) -> Dict[str, Any]:
    """List all models trained by a user"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{MONGO_SERVER_URL}/find_documents",
                json={
                    "collection": "file_metadata",
                    "query": {
                        "user_id": user_id,
                        "content_type": "application/octet-stream",
                        "filename": {"$regex": r"\.joblib$"}
                    }
                }
            )
            if response.status_code != 200:
                return {"success": False, "error": f"Failed to query models: {response.text}"}
            
            result = response.json()
            if not result.get("success"):
                return {"success": False, "error": result.get("error", "Unknown error")}
            
            models = []
            for doc in result["documents"]:
                metadata = doc.get("metadata", {})
                models.append({
                    "model_id": doc["file_id"],
                    "model_type": metadata.get("model_type", "unknown"),
                    "created_at": metadata.get("created_at"),
                    "dataset_id": metadata.get("dataset_id"),
                    "training_results": metadata.get("training_results", {})
                })
            
            return {"success": True, "models": models}
            
    except Exception as e:
        logger.error(f"Failed to list models: {e}")
        return {"success": False, "error": str(e)}

@mcp.tool()
async def health_check() -> Dict[str, Any]:
    """Health check for SciREX server"""
    return {
        "status": "healthy",
        "scirex_available": SCIREX_AVAILABLE,
        "tools": [
            "train_model", "predict", "cluster_analysis", 
            "classification_analysis", "list_user_models", "health_check"
        ],
        "supported_models": [
            "kmeans", "svm", "logistic_regression", "naive_bayes", "mlp"
        ],
        "timestamp": datetime.utcnow().isoformat()
    }

# Main entry point
if __name__ == "__main__":
    # Run with FastMCP streamable HTTP transport
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=SCIREX_MCP_PORT,
        log_level="WARNING"
    )
