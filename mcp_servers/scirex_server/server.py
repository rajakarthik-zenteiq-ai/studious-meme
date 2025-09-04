"""
SciREX-style FastMCP server providing ML tools for tabular data.

LLM-friendly tool names and detailed argument schemas following FastMCP v2 best practices:
- cluster_data: Unsupervised clustering (KMeans, DBSCAN, Agglomerative)
- classify_data: Supervised classification with train/test split
- train_model: Train and persist a model (KNN, Logistic Regression, SVM, RandomForest, MLP)
- predict_labels: Run inference using a saved model
- list_user_models: List available saved models
- server_health: Health and environment status

All tools accept clear, LLM-friendly arguments with descriptions suitable for automatic
schema generation and tool selection.
"""

from __future__ import annotations

import os
import sys
import json
import time
import uuid
import logging
from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel, Field
from fastmcp import FastMCP, Context

# ML stack (scikit-learn based fallback implementation)
import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, f1_score, silhouette_score
from sklearn.cluster import KMeans, DBSCAN, AgglomerativeClustering
from sklearn.linear_model import LogisticRegression
from sklearn.svm import SVC
from sklearn.ensemble import RandomForestClassifier
from sklearn.neural_network import MLPClassifier
from sklearn.neighbors import KNeighborsClassifier
import joblib

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("scirex_mcp")

# Paths
HERE = os.path.dirname(__file__)
TEMP_DATA_DIR = os.getenv("SCIREX_DATA_DIR", "/tmp/user_datasets")
TEMP_MODEL_DIR = os.getenv("SCIREX_MODEL_DIR", "/tmp/user_models")
SCIREX_MCP_PORT = int(os.getenv("SCIREX_MCP_PORT", "8150"))

os.makedirs(TEMP_DATA_DIR, exist_ok=True)
os.makedirs(TEMP_MODEL_DIR, exist_ok=True)

# ----------------------------------------------------------------------------
# Pydantic Schemas (LLM-friendly)
# ----------------------------------------------------------------------------

class TabularInput(BaseModel):
    """Tabular dataset input.

    Provide data as a list of JSON records or as CSV text. One of `records` or
    `csv_text` must be provided.
    """

    records: Optional[list[dict[str, Any]]] = Field(
        default=None,
        description=(
            "Dataset as a list of JSON objects where each object represents a row. "
            "Example: {\"age\": 23, \"income\": 54000}, ..."
        ),
    )
    csv_text: Optional[str] = Field(
        default=None,
        description=(
            "Dataset as CSV-formatted text, including a header row. "
            "Example: 'age,income\n23,54000\n...'."
        ),
    )
    features: Optional[list[str]] = Field(
        default=None,
        description=(
            "Explicit list of feature/column names to use as inputs. If omitted, "
            "all numeric columns are used automatically."
        ),
    )
    target: Optional[str] = Field(
        default=None,
        description=(
            "Target/label column name for supervised tasks (classification, training, "
            "prediction). Leave null for unsupervised clustering."
        ),
    )
    normalize: bool = Field(
        default=True,
        description="Whether to standardize features (recommended).",
    )

    def to_dataframe(self) -> pd.DataFrame:
        if self.records is None and (self.csv_text is None or self.csv_text.strip() == ""):
            raise ValueError("Provide either 'records' or 'csv_text'.")
        if self.records:
            df = pd.DataFrame(self.records)
        else:
            from io import StringIO

            df = pd.read_csv(StringIO(self.csv_text or ""))
        if df.empty:
            raise ValueError("Dataset is empty.")
        return df


class ClusteringRequest(BaseModel):
    """Parameters for clustering tabular data.

    Use this for grouping similar rows without labels.
    """

    input: TabularInput = Field(..., description="Tabular dataset and options.")
    algorithm: Literal["kmeans", "dbscan", "agglomerative"] = Field(
        default="kmeans",
        description="Clustering algorithm to use.",
    )
    n_clusters: int = Field(
        default=3,
        ge=2,
        description="Number of clusters (used by kmeans/agglomerative).",
    )
    # Auto-k selection inspired by SciREX examples
    auto_k: Optional[Literal["silhouette", "elbow"]] = Field(
        default=None,
        description=(
            "If set for kmeans, automatically choose k in [2, max_k] using the given method. "
            "Silhouette picks the best score; Elbow uses inertia's elbow heuristic."
        ),
    )
    max_k: int = Field(
        default=10,
        ge=2,
        description="Upper bound for automatic k search (when auto_k is set).",
    )
    random_state: Optional[int] = Field(
        default=42,
        description="Random seed for reproducibility (kmeans).",
    )
    eps: float = Field(
        default=0.5,
        description="DBSCAN epsilon: neighborhood radius for clustering.",
    )
    min_samples: int = Field(
        default=5,
        ge=1,
        description="DBSCAN: minimum number of samples per neighborhood.",
    )


class ClassificationRequest(BaseModel):
    """Parameters for supervised classification with a train/test split."""

    input: TabularInput = Field(..., description="Tabular dataset including target column.")
    model_type: Literal[
        "logistic_regression",
        "svm",
        "random_forest",
        "mlp",
        "knn",
    ] = Field(default="logistic_regression", description="Classifier type.")
    test_size: float = Field(
        default=0.2,
        gt=0.0,
        lt=1.0,
        description="Fraction of data to hold out for testing.",
    )
    random_state: Optional[int] = Field(
        default=42, description="Random seed for the train/test split."
    )
    model_params: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional model hyperparameters (scikit-learn style).",
    )


class TrainModelRequest(BaseModel):
    """Train and persist a model for later predictions."""

    input: TabularInput = Field(..., description="Training dataset with target column.")
    model_type: Literal[
        "logistic_regression",
        "svm",
        "random_forest",
        "mlp",
        "knn",
    ] = Field(default="random_forest", description="Classifier type to train.")
    model_params: dict[str, Any] = Field(
        default_factory=dict, description="Optional model hyperparameters."
    )
    user_id: Optional[str] = Field(
        default=None,
        description="Optional user identifier to namespace saved models.",
    )


class PredictRequest(BaseModel):
    """Run inference using a previously saved model."""

    model_id: str = Field(..., description="Model identifier returned by train_model.")
    input: TabularInput = Field(
        ..., description="Input data for prediction (without the target column)."
    )


class ListModelsRequest(BaseModel):
    user_id: Optional[str] = Field(
        default=None, description="Optional user identifier to filter models."
    )


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def _df_from_input(inp: TabularInput) -> pd.DataFrame:
    df = inp.to_dataframe()
    # Auto-select features if not provided: numeric columns only
    if inp.features:
        missing = [c for c in inp.features if c not in df.columns]
        if missing:
            raise ValueError(f"Features not found in data: {missing}")
        X = df[inp.features].copy()
    else:
        X = df.select_dtypes(include=[np.number]).copy()
        if X.shape[1] == 0:
            raise ValueError("No numeric columns found; provide 'features'.")
    y = None
    if inp.target:
        if inp.target not in df.columns:
            raise ValueError(f"Target column '{inp.target}' not found in data.")
        y = df[inp.target].copy()
    return X, y, df


def _make_classifier(model_type: str, params: dict[str, Any]) -> Any:
    if model_type == "logistic_regression":
        base = LogisticRegression(max_iter=1000, **params)
    elif model_type == "svm":
        base = SVC(probability=True, **params)
    elif model_type == "random_forest":
        base = RandomForestClassifier(**params)
    elif model_type == "mlp":
        base = MLPClassifier(max_iter=500, **params)
    elif model_type == "knn":
        base = KNeighborsClassifier(**params)
    else:
        raise ValueError(f"Unsupported model_type: {model_type}")
    pipe_steps = []
    pipe_steps.append(("scaler", StandardScaler()))
    pipe_steps.append(("model", base))
    return Pipeline(pipe_steps)


def _save_model(pipe: Any, meta: dict[str, Any], user_id: Optional[str]) -> str:
    ts = int(time.time())
    uid = uuid.uuid4().hex[:8]
    ns = user_id or "public"
    os.makedirs(os.path.join(TEMP_MODEL_DIR, ns), exist_ok=True)
    model_id = f"{ns}-{meta.get('model_type','model')}-{uid}-{ts}"
    path = os.path.join(TEMP_MODEL_DIR, ns, f"{model_id}.joblib")
    joblib.dump({"pipeline": pipe, "meta": meta}, path)
    return model_id


def _load_model(model_id: str) -> dict[str, Any]:
    # Find file in any namespace
    for ns in os.listdir(TEMP_MODEL_DIR):
        try:
            path = os.path.join(TEMP_MODEL_DIR, ns, f"{model_id}.joblib")
            if os.path.exists(path):
                return joblib.load(path)
        except Exception:  # continue search
            pass
    # Also support full path id (ns included)
    for root, _dirs, files in os.walk(TEMP_MODEL_DIR):
        for f in files:
            if f.startswith(model_id) and f.endswith(".joblib"):
                return joblib.load(os.path.join(root, f))
    raise FileNotFoundError(f"Model '{model_id}' not found in {TEMP_MODEL_DIR}.")


# ----------------------------------------------------------------------------
# FastMCP Server & Tools
# ----------------------------------------------------------------------------

mcp = FastMCP("SciREX ML Server")


@mcp.tool()
async def cluster_data(request: dict, ctx) -> dict:
    """Cluster similar rows in a tabular dataset.

    Use this for grouping customers, log lines, or events by behavior. Provide JSON
    records or CSV text. Features default to numeric columns.

    Arguments:
    - request.input.records or request.input.csv_text: The dataset
    - request.input.features: Optional list of feature columns
    - request.input.normalize: Whether to standardize features (default true)
    - request.algorithm: 'kmeans', 'dbscan', or 'agglomerative'
    - request.n_clusters: Number of clusters (kmeans/agglomerative)
    - request.auto_k, request.max_k: Auto-select k for kmeans (silhouette/elbow)
    - request.eps, request.min_samples: DBSCAN parameters

    Returns a JSON object with cluster labels, metrics, and basic summaries.
    """
    # Validate incoming payload into model to avoid forward-ref issues in annotations
    request = ClusteringRequest.model_validate(request)

    X, _y, df = _df_from_input(request.input)

    steps = []
    if request.input.normalize:
        steps.append(("scaler", StandardScaler()))

    algo = request.algorithm
    selected_k: Optional[int] = None
    auto_k_method = request.auto_k

    if algo == "kmeans":
        # Auto-k selection if requested
        if auto_k_method in {"silhouette", "elbow"}:
            k_range = list(range(2, max(3, int(request.max_k) + 1)))
            best_score = -np.inf
            best_k = request.n_clusters or 3
            inertias: dict[int, float] = {}
            sils: dict[int, float] = {}
            for k in k_range:
                try:
                    km = KMeans(n_clusters=k, n_init="auto", random_state=request.random_state)
                    pipe_tmp = Pipeline(steps + [("cluster", km)])
                    labels_tmp = pipe_tmp.fit_predict(X)
                    if len(set(labels_tmp)) < 2:
                        continue
                    sil = silhouette_score(X, labels_tmp)
                    sils[k] = sil
                    inertias[k] = float(pipe_tmp.named_steps["cluster"].inertia_)
                    if auto_k_method == "silhouette" and sil > best_score:
                        best_score = sil
                        best_k = k
                except Exception:
                    continue
            if auto_k_method == "elbow" and inertias:
                # Simple elbow: choose k where relative inertia drop diminishes
                ks = sorted(inertias.keys())
                drops = {ks[i]: inertias[ks[i - 1]] - inertias[ks[i]] for i in range(1, len(ks))}
                # pick k with max second derivative approx (drop difference)
                best_k = request.n_clusters or 3
                best_delta = -np.inf
                for i in range(2, len(ks)):
                    d1 = inertias[ks[i - 2]] - inertias[ks[i - 1]]
                    d2 = inertias[ks[i - 1]] - inertias[ks[i]]
                    delta = d1 - d2
                    if delta > best_delta:
                        best_delta = delta
                        best_k = ks[i]
            selected_k = int(best_k)
        n_k = int(selected_k or request.n_clusters)
        model = KMeans(n_clusters=n_k, n_init="auto", random_state=request.random_state)
    elif algo == "dbscan":
        model = DBSCAN(eps=request.eps, min_samples=request.min_samples)
    elif algo == "agglomerative":
        model = AgglomerativeClustering(n_clusters=request.n_clusters)
    else:
        raise ValueError(f"Unsupported algorithm: {algo}")

    pipe = Pipeline(steps + [("cluster", model)])
    labels = pipe.fit_predict(X)

    result: dict[str, Any] = {
        "algorithm": algo,
        "n_rows": int(df.shape[0]),
        "n_features": int(X.shape[1]),
        "labels": labels.tolist(),
        "label_counts": {int(k): int(v) for k, v in pd.Series(labels).value_counts().items()},
    }
    if selected_k is not None:
        result["selected_k"] = int(selected_k)
        result["auto_k_method"] = auto_k_method
    # Optional metrics
    try:
        if len(set(labels)) > 1 and df.shape[0] > 2:
            result["silhouette_score"] = float(silhouette_score(X, labels))
            # Extra clustering metrics inspired by SciREX docs
            from sklearn.metrics import calinski_harabasz_score, davies_bouldin_score
            result["calinski_harabasz"] = float(calinski_harabasz_score(X, labels))
            result["davies_bouldin"] = float(davies_bouldin_score(X, labels))
    except Exception as e:
        await ctx.info(f"Cluster metrics not available: {e}")

    return result


@mcp.tool()
async def classify_data(request: dict, ctx) -> dict:
    """Train and evaluate a classifier with a train/test split.

    Arguments:
    - request.input: Dataset with target column specified in `input.target`
    - request.model_type: One of 'logistic_regression', 'svm', 'random_forest', 'mlp', 'knn'
    - request.test_size: Fraction of data to hold out for testing.
    - request.model_params: Optional classifier hyperparameters

    Returns accuracy and F1 on the test set along with basic metadata.
    """
    request = ClassificationRequest.model_validate(request)

    X, y, df = _df_from_input(request.input)

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=request.test_size, random_state=request.random_state, stratify=y if y is not None else None
    )

    pipe = _make_classifier(request.model_type, request.model_params)
    pipe.fit(X_train, y_train)

    y_pred = pipe.predict(X_test)
    acc = float(accuracy_score(y_test, y_pred))
    f1 = float(f1_score(y_test, y_pred, average="weighted"))

    await ctx.info(f"Trained {request.model_type} on {len(y_train)} rows. Test accuracy={acc:.3f}")

    return {
        "model_type": request.model_type,
        "n_rows": int(df.shape[0]),
        "n_features": int(X.shape[1]),
        "test_size": request.test_size,
        "accuracy": acc,
        "f1_weighted": f1,
    }


@mcp.tool()
async def train_model(request: dict, ctx) -> dict:
    """Train and persist a model for later predictions.

    Arguments:
    - request.input: Dataset with `target` column
    - request.model_type: 'logistic_regression' | 'svm' | 'random_forest' | 'mlp' | 'knn'
    - request.model_params: Optional hyperparameters
    - request.user_id: Optional user namespace for saved models

    Returns a `model_id` you can use with `predict_labels`.
    """
    request = TrainModelRequest.model_validate(request)

    if not request.input.target:
        raise ValueError("Training requires 'input.target' to be set.")

    X, y, df = _df_from_input(request.input)

    pipe = _make_classifier(request.model_type, request.model_params)
    pipe.fit(X, y)

    meta = {
        "model_type": request.model_type,
        "n_rows": int(df.shape[0]),
        "n_features": int(X.shape[1]),
    }
    model_id = _save_model(pipe, meta, request.user_id)

    await ctx.info(f"Saved model {model_id}")

    return {"model_id": model_id, "meta": meta}


@mcp.tool()
async def predict_labels(request: dict, ctx) -> dict:
    """Run inference using a saved model.

    Arguments:
    - request.model_id: Identifier from `train_model`
    - request.input: Data to predict on (no target column required)

    Returns predicted labels and, if available, class probabilities.
    """
    request = PredictRequest.model_validate(request)

    mdl = _load_model(request.model_id)
    pipe = mdl["pipeline"]
    X, _y, df = _df_from_input(request.input)

    preds = pipe.predict(X)
    result: Dict[str, Any] = {
        "model_id": request.model_id,
        "n_rows": int(df.shape[0]),
        "predictions": preds.tolist(),
    }

    # Probabilities when available
    try:
        if hasattr(pipe, "predict_proba"):
            probs = pipe.predict_proba(X)
            result["probabilities"] = probs.tolist()
    except Exception as e:
        await ctx.info(f"Probabilities not available: {e}")

    return result


@mcp.tool()
async def list_user_models(request: dict, _ctx) -> dict:
    """List saved models.

    Arguments:
    - request.user_id: Optional user namespace. If omitted, all namespaces are listed.

    Returns model ids with basic metadata, grouped by namespace.
    """
    request = ListModelsRequest.model_validate(request)
    namespaces = []
    if request.user_id:
        namespaces = [request.user_id] if os.path.isdir(os.path.join(TEMP_MODEL_DIR, request.user_id)) else []
    else:
        namespaces = [d for d in os.listdir(TEMP_MODEL_DIR) if os.path.isdir(os.path.join(TEMP_MODEL_DIR, d))]

    out: dict[str, list[dict[str, Any]]] = {}
    for ns in namespaces:
        ns_path = os.path.join(TEMP_MODEL_DIR, ns)
        models: list[dict[str, Any]] = []
        for f in os.listdir(ns_path):
            if f.endswith(".joblib"):
                model_id = f[:-7]
                try:
                    obj = joblib.load(os.path.join(ns_path, f))
                    meta = obj.get("meta", {})
                except Exception:
                    meta = {}
                models.append({"model_id": model_id, "meta": meta})
        out[ns] = models

    return {"namespaces": out}


@mcp.tool()
async def server_health(_payload: dict | None, _ctx) -> dict:
    """Return server health and environment details.

    Helpful for connectivity checks from clients.
    """
    return {
        "status": "ok",
        "port": SCIREX_MCP_PORT,
        "data_dir": TEMP_DATA_DIR,
        "model_dir": TEMP_MODEL_DIR,
        "sklearn": True,
        "fastmcp": True,
    }


if __name__ == "__main__":
    # Run as streamable HTTP server for easy remote access
    logger.info(f"Starting SciREX MCP server on 0.0.0.0:{SCIREX_MCP_PORT} (path=/mcp)")
    mcp.run(transport="http", host="0.0.0.0", port=SCIREX_MCP_PORT, path="/mcp")


# ── Backward-compatible aliases with LLM-friendly docs ─────────────────────--
@mcp.tool()
async def cluster_analysis(
    dataset_id: str | None = None,
    records: list[dict] | None = None,
    csv_text: str | None = None,
    features: list[str] | None = None,
    algorithm: Literal["kmeans", "dbscan", "agglomerative"] = "kmeans",
    n_clusters: int = 3,
    auto_k: Optional[Literal["silhouette", "elbow"]] = None,
    max_k: int = 10,
    eps: float = 0.5,
    min_samples: int = 5,
    normalize: bool = True,
    user_id: str | None = None,
    ctx = None,
) -> dict:
    """Cluster similar rows in a tabular dataset (alias of cluster_data).

    Choose one of: dataset_id, records, or csv_text.
    """
    # Build TabularInput by resolving dataset_id when provided
    ti: TabularInput
    if dataset_id and not records and not csv_text:
        # Attempt to locate a staged CSV file in TEMP_DATA_DIR containing dataset_id
        csv_str: Optional[str] = None
        try:
            for root, _dirs, files in os.walk(TEMP_DATA_DIR):
                for f in files:
                    if dataset_id in f and f.endswith(('.csv', '.txt', '.data')):
                        path = os.path.join(root, f)
                        with open(path, 'r', encoding='utf-8') as fh:
                            csv_str = fh.read()
                        break
                if csv_str:
                    break
        except Exception as e:
            await ctx.info(f"Could not read staged file for dataset_id={dataset_id}: {e}")
        ti = TabularInput(csv_text=csv_str, records=None, features=features, normalize=normalize)
    else:
        ti = TabularInput(csv_text=csv_text, records=records, features=features, normalize=normalize)

    req = ClusteringRequest(input=ti, algorithm=algorithm, n_clusters=n_clusters, auto_k=auto_k, max_k=max_k, eps=eps, min_samples=min_samples)
    return await cluster_data(req, ctx)


@mcp.tool()
async def classification_analysis(
    target: str,
    dataset_id: str | None = None,
    records: list[dict] | None = None,
    csv_text: str | None = None,
    features: list[str] | None = None,
    model_type: Literal["logistic_regression", "svm", "random_forest", "mlp", "knn"] = "logistic_regression",
    test_size: float = 0.2,
    random_state: int | None = 42,
    model_params: dict | None = None,
    normalize: bool = True,
    ctx = None,
) -> dict:
    """Train and evaluate a classifier (alias of classify_data)."""
    ti: TabularInput
    if dataset_id and not records and not csv_text:
        csv_str: Optional[str] = None
        try:
            for root, _dirs, files in os.walk(TEMP_DATA_DIR):
                for f in files:
                    if dataset_id in f and f.endswith(('.csv', '.txt', '.data')):
                        path = os.path.join(root, f)
                        with open(path, 'r', encoding='utf-8') as fh:
                            csv_str = fh.read()
                        break
                if csv_str:
                    break
        except Exception as e:
            await ctx.info(f"Could not read staged file for dataset_id={dataset_id}: {e}")
        ti = TabularInput(csv_text=csv_str, records=None, features=features, target=target, normalize=normalize)
    else:
        ti = TabularInput(csv_text=csv_text, records=records, features=features, target=target, normalize=normalize)

    req = ClassificationRequest(
        input=ti,
        model_type=model_type,
        test_size=test_size,
        random_state=random_state,
        model_params=model_params or {},
    )
    return await classify_data(req, ctx)


@mcp.tool()
async def predict(
    model_id: str,
    dataset_id: str | None = None,
    records: list[dict] | None = None,
    csv_text: str | None = None,
    features: list[str] | None = None,
    normalize: bool = True,
    ctx = None,
) -> dict:
    """Predict labels using a saved model (alias of predict_labels)."""
    ti: TabularInput
    if dataset_id and not records and not csv_text:
        csv_str: Optional[str] = None
        try:
            for root, _dirs, files in os.walk(TEMP_DATA_DIR):
                for f in files:
                    if dataset_id in f and f.endswith(('.csv', '.txt', '.data')):
                        path = os.path.join(root, f)
                        with open(path, 'r', encoding='utf-8') as fh:
                            csv_str = fh.read()
                        break
                if csv_str:
                    break
        except Exception as e:
            await ctx.info(f"Could not read staged file for dataset_id={dataset_id}: {e}")
        ti = TabularInput(csv_text=csv_str, records=None, features=features, normalize=normalize)
    else:
        ti = TabularInput(csv_text=csv_text, records=records, features=features, normalize=normalize)

    req = PredictRequest(model_id=model_id, input=ti)
    return await predict_labels(req, ctx)


@mcp.tool()
async def health_check(_payload: dict | None, ctx) -> dict:
    """Health check (alias of server_health)."""
    return await server_health(_payload, ctx)


@mcp.tool()
async def list_models(user_id: str | None = None, ctx = None) -> dict:
    """List models (alias of list_user_models)."""
    return await list_user_models(ListModelsRequest(user_id=user_id), ctx)
