"""
SciREX tools implementation
"""
import os
import json
import base64
import io
import uuid
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime

import pandas as pd
import numpy as np
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from sklearn.metrics import silhouette_score, accuracy_score, mean_squared_error
import joblib

# Try to import SciREX (with fallback)
try:
    from scirex.core.models import NeuralNetRegressor, NeuralNetClassifier
    from scirex.core.physics import FastVPINN
    from scirex.utils import save_model, load_model
    SCIREX_AVAILABLE = True
except ImportError:
    logger.warning("SciREX not installed. Using sklearn fallbacks.")
    SCIREX_AVAILABLE = False
    
    # Fallback implementations
    from sklearn.neural_network import MLPRegressor as NeuralNetRegressor
    from sklearn.neural_network import MLPClassifier as NeuralNetClassifier
    
    def save_model(model, path):
        joblib.dump(model, path)
    
    def load_model(path):
        return joblib.load(path)

logger = logging.getLogger(__name__)

class SciREXTools:
    """Scientific computing and ML tools"""
    
    def __init__(self):
        self.data_dir = Path(os.getenv("SCIREX_DATA_DIR", "/tmp/scirex_data"))
        self.model_dir = Path(os.getenv("SCIREX_MODEL_DIR", "/tmp/scirex_models"))
        
        # Create directories
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.model_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"SciREX data directory: {self.data_dir}")
        logger.info(f"SciREX model directory: {self.model_dir}")
    
    def _dataset_path(self, dataset_id: str) -> Path:
        """Get dataset file path"""
        return self.data_dir / f"{dataset_id}.csv"
    
    def _metadata_path(self, dataset_id: str) -> Path:
        """Get metadata file path"""
        return self.data_dir / f"{dataset_id}.meta.json"
    
    def _model_path(self, model_id: str) -> Path:
        """Get model file path"""
        return self.model_dir / f"{model_id}.pkl"
    
    def _model_meta_path(self, model_id: str) -> Path:
        """Get model metadata file path"""
        return self.model_dir / f"{model_id}.meta.json"
    
    async def upload_dataset(self, user_id: str, filename: str, content_base64: str, 
                           metadata: Dict[str, Any] = None) -> Dict[str, Any]:
        """Upload and store a dataset"""
        try:
            # Decode content
            content_bytes = base64.b64decode(content_base64)
            
            # Determine file type and read
            if filename.endswith('.json'):
                df = pd.read_json(io.BytesIO(content_bytes))
            elif filename.endswith('.csv'):
                df = pd.read_csv(io.StringIO(content_bytes.decode('utf-8')))
            elif filename.endswith('.xlsx') or filename.endswith('.xls'):
                df = pd.read_excel(io.BytesIO(content_bytes))
            else:
                # Try CSV as default
                df = pd.read_csv(io.StringIO(content_bytes.decode('utf-8')))
            
            # Generate dataset ID
            dataset_id = f"{user_id}_{uuid.uuid4().hex[:8]}"
            
            # Save dataset
            dataset_path = self._dataset_path(dataset_id)
            df.to_csv(dataset_path, index=False)
            
            # Analyze dataset
            numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
            categorical_cols = df.select_dtypes(include=['object']).columns.tolist()
            
            # Save metadata
            meta = {
                "dataset_id": dataset_id,
                "user_id": user_id,
                "filename": filename,
                "uploaded_at": datetime.utcnow().isoformat(),
                "shape": list(df.shape),
                "columns": list(df.columns),
                "numeric_columns": numeric_cols,
                "categorical_columns": categorical_cols,
                "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
                "null_counts": df.isnull().sum().to_dict(),
                "user_metadata": metadata or {}
            }
            
            with open(self._metadata_path(dataset_id), 'w') as f:
                json.dump(meta, f, indent=2)
            
            logger.info(f"Dataset uploaded: {dataset_id}")
            
            return {
                "success": True,
                "dataset_id": dataset_id,
                "shape": meta["shape"],
                "columns": meta["columns"],
                "numeric_columns": numeric_cols,
                "categorical_columns": categorical_cols
            }
            
        except Exception as e:
            logger.error(f"Error uploading dataset: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def list_datasets(self, user_id: str, limit: int = 10) -> Dict[str, Any]:
        """List datasets for a user"""
        try:
            datasets = []
            
            # Find all metadata files
            for meta_file in sorted(self.data_dir.glob("*.meta.json"), 
                                  key=lambda x: x.stat().st_mtime, 
                                  reverse=True):
                try:
                    with open(meta_file) as f:
                        meta = json.load(f)
                    
                    if meta.get("user_id") == user_id:
                        datasets.append({
                            "dataset_id": meta["dataset_id"],
                            "filename": meta["filename"],
                            "uploaded_at": meta["uploaded_at"],
                            "shape": meta["shape"],
                            "columns": meta["columns"]
                        })
                        
                        if len(datasets) >= limit:
                            break
                            
                except Exception as e:
                    logger.error(f"Error reading metadata {meta_file}: {e}")
                    continue
            
            return {
                "success": True,
                "datasets": datasets,
                "count": len(datasets)
            }
            
        except Exception as e:
            logger.error(f"Error listing datasets: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def train_kmeans(self, dataset_id: str, n_clusters: int = 3, 
                          features: Optional[List[str]] = None) -> Dict[str, Any]:
        """Train K-means clustering"""
        try:
            # Load dataset
            df = pd.read_csv(self._dataset_path(dataset_id))
            
            # Load metadata
            with open(self._metadata_path(dataset_id)) as f:
                meta = json.load(f)
            
            # Select features
            if features:
                X = df[features]
            else:
                # Use all numeric columns
                numeric_cols = meta["numeric_columns"]
                if not numeric_cols:
                    return {
                        "success": False,
                        "error": "No numeric columns found in dataset"
                    }
                X = df[numeric_cols]
            
            # Handle missing values
            X = X.fillna(X.mean())
            
            # Scale features
            scaler = StandardScaler()
            X_scaled = scaler.fit_transform(X)
            
            # Train K-means
            kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
            clusters = kmeans.fit_predict(X_scaled)
            
            # Calculate metrics
            if len(X) > n_clusters:
                silhouette = float(silhouette_score(X_scaled, clusters))
            else:
                silhouette = 0.0
            
            inertia = float(kmeans.inertia_)
            
            # Add cluster assignments to dataframe
            df['cluster'] = clusters
            
            # Calculate cluster statistics
            cluster_stats = {}
            for cluster_id in range(n_clusters):
                cluster_mask = clusters == cluster_id
                cluster_size = int(cluster_mask.sum())
                
                # Calculate mean values for each feature
                cluster_means = {}
                for col in X.columns:
                    cluster_means[col] = float(X.loc[cluster_mask, col].mean())
                
                cluster_stats[f"cluster_{cluster_id}"] = {
                    "size": cluster_size,
                    "percentage": float(cluster_size / len(X) * 100),
                    "center": kmeans.cluster_centers_[cluster_id].tolist(),
                    "feature_means": cluster_means
                }
            
            # Save model
            model_id = f"kmeans_{dataset_id}_{uuid.uuid4().hex[:8]}"
            model_data = {
                "model": kmeans,
                "scaler": scaler,
                "features": list(X.columns),
                "n_clusters": n_clusters
            }
            
            save_model(model_data, self._model_path(model_id))
            
            # Save model metadata
            model_meta = {
                "model_id": model_id,
                "model_type": "kmeans",
                "dataset_id": dataset_id,
                "trained_at": datetime.utcnow().isoformat(),
                "parameters": {
                    "n_clusters": n_clusters,
                    "features": list(X.columns)
                },
                "metrics": {
                    "silhouette_score": silhouette,
                    "inertia": inertia
                },
                "cluster_stats": cluster_stats
            }
            
            with open(self._model_meta_path(model_id), 'w') as f:
                json.dump(model_meta, f, indent=2)
            
            # Save clustered dataset
            clustered_path = self.data_dir / f"{dataset_id}_clustered.csv"
            df.to_csv(clustered_path, index=False)
            
            logger.info(f"K-means model trained: {model_id}")
            
            return {
                "success": True,
                "model_id": model_id,
                "n_clusters": n_clusters,
                "features_used": list(X.columns),
                "metrics": {
                    "silhouette_score": silhouette,
                    "inertia": inertia
                },
                "cluster_stats": cluster_stats,
                "clustered_dataset_path": str(clustered_path)
            }
            
        except Exception as e:
            logger.error(f"Error training K-means: {e}")