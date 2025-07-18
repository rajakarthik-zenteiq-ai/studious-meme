# Thin adapter over SciREX registry
import os, uuid, json, pandas as pd
from fastmcp import FastMCP
from typing import Any, Dict, List

from scirex.core.ml.supervised.classification import registry as cls_registry
from scirex.core.ml.unsupervised.clustering import registry as clu_registry
from scirex.core.scivpinn.fastvpinn import FastVPINN

from config import DATA_DIR, MODEL_DIR

_models: Dict[str, Any] = {}

def _save_dataframe(csv_text: str) -> str:
    ds_id = str(uuid.uuid4())
    path = os.path.join(DATA_DIR, f"{ds_id}.csv")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(csv_text)
    return path

def _persist_model(mid: str, model: Any):
    _models[mid] = model
    disk_path = os.path.join(MODEL_DIR, f"{mid}.pkl")
    if hasattr(model, "save"):
        model.save(disk_path)

def register_tools(mcp: FastMCP):
    @mcp.tool(name="list_models")
    async def list_models() -> List[str]:
        return sorted(
            list(cls_registry.keys()) +
            list(clu_registry.keys()) +
            ["fastvpinn"]
        )

    @mcp.tool(name="perform_classification")
    async def perform_classification(
        user_id: str,
        model_name: str,
        dataset_csv: str,
        target_column: str,
        **hyper_params,
    ) -> Dict[str, Any]:
        if model_name not in cls_registry:
            raise ValueError(f"Unknown classifier: {model_name}")
        path = _save_dataframe(dataset_csv)
        df = pd.read_csv(path)
        X = df.drop(columns=[target_column]).values
        y = df[target_column].values
        clf = cls_registry[model_name](**hyper_params)
        metrics = clf.fit(X, y)
        mid = f"{user_id}__{model_name}__{uuid.uuid4().hex}"
        _persist_model(mid, clf)
        return {"model_id": mid, "metrics": metrics}

    @mcp.tool(name="perform_clustering")
    async def perform_clustering(
        user_id: str,
        model_name: str,
        dataset_csv: str,
        **hyper_params,
    ) -> Dict[str, Any]:
        if model_name not in clu_registry:
            raise ValueError(f"Unknown clustering model: {model_name}")
        path = _save_dataframe(dataset_csv)
        X = pd.read_csv(path).values
        cluster = clu_registry[model_name](**hyper_params)
        metrics = cluster.fit(X)
        mid = f"{user_id}__{model_name}__{uuid.uuid4().hex}"
        _persist_model(mid, cluster)
        return {"model_id": mid, "metrics": metrics}

    @mcp.tool(name="perform_fastvpinns")
    async def perform_fastvpinns(
        user_id: str,
        pde_spec: str,
        solver_params: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        spec = json.loads(pde_spec)
        solver = FastVPINN.from_json(spec, **(solver_params or {}))
        history = solver.train()
        mid = f"{user_id}__fastvpinn__{uuid.uuid4().hex}"
        _persist_model(mid, solver)
        return {"model_id": mid, "training_history": history}

    @mcp.tool(name="query_model")
    async def query_model(model_id: str, samples: List[List[float]]) -> Dict[str, Any]:
        model = _models.get(model_id)
        if model is None:
            raise KeyError(f"Model {model_id} not found")
        if hasattr(model, "infer"):
            preds = model.infer(samples)
        else:
            preds = model.predict(samples).tolist()
        return {"model_id": model_id, "predictions": preds}
