"""Lightweight, in-process dataset analyzer used by Mongo MCP server.
Performs fast profiling on small to medium CSV/JSON datasets.
Designed to avoid heavy dependencies (no ydata-profiling) and finish < ~2s for typical files.
"""
from __future__ import annotations
import io
import math
import statistics
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd  # requirements already include pandas
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans

@dataclass
class ColumnSummary:
    name: str
    dtype: str
    non_null: int
    nulls: int
    unique: int
    sample: List[Any]
    stats: Optional[Dict[str, Any]] = None

@dataclass
class DatasetSummary:
    rows: int
    columns: int
    memory_mb: float
    numeric_columns: int
    categorical_columns: int
    columns_detail: List[ColumnSummary]
    cluster_preview: Optional[Dict[str, Any]] = None

MAX_ROWS_SAMPLE = 5000  # limit processing for very large files
MAX_CLUSTERS = 8


def _safe_numeric(series: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(series)


def profile_dataframe(df: pd.DataFrame) -> DatasetSummary:
    rows, cols = df.shape
    mem_mb = float(df.memory_usage(deep=True).sum() / (1024 ** 2))
    summaries: List[ColumnSummary] = []
    numeric_cols = 0
    cat_cols = 0
    for col in df.columns:
        s = df[col]
        non_null = s.notna().sum()
        nulls = s.isna().sum()
        unique = s.nunique(dropna=True)
        sample_vals = s.dropna().head(3).tolist()
        stats = None
        if _safe_numeric(s):
            numeric_cols += 1
            try:
                desc = s.describe()
                stats = {
                    "min": _to_py(desc.get("min")),
                    "max": _to_py(desc.get("max")),
                    "mean": _to_py(desc.get("mean")),
                    "std": _to_py(desc.get("std")),
                    "p25": _to_py(s.quantile(0.25)),
                    "p50": _to_py(s.quantile(0.5)),
                    "p75": _to_py(s.quantile(0.75)),
                }
            except Exception:
                pass
        else:
            cat_cols += 1
        summaries.append(ColumnSummary(
            name=col,
            dtype=str(s.dtype),
            non_null=int(non_null),
            nulls=int(nulls),
            unique=int(unique),
            sample=sample_vals,
            stats=stats
        ))
    return DatasetSummary(
        rows=int(rows),
        columns=int(cols),
        memory_mb=round(mem_mb, 3),
        numeric_columns=numeric_cols,
        categorical_columns=cat_cols,
        columns_detail=summaries
    )


def _to_py(v: Any) -> Any:
    if isinstance(v, (pd.Timestamp, pd.Timedelta)):
        return str(v)
    if isinstance(v, (pd.Series, pd.DataFrame)):
        return v.to_dict()
    if hasattr(v, 'item'):
        try:
            return v.item()
        except Exception:
            return str(v)
    return v


def run_kmeans_preview(df: pd.DataFrame, max_clusters: int = MAX_CLUSTERS) -> Optional[Dict[str, Any]]:
    numeric_df = df.select_dtypes(include=['number']).dropna(axis=1, how='all')
    if numeric_df.empty or numeric_df.shape[1] < 1:
        return None
    # Limit rows
    work_df = numeric_df.head(MAX_ROWS_SAMPLE).copy()
    try:
        scaler = StandardScaler()
        X = scaler.fit_transform(work_df)
        k = min(4, max(2, min(max_clusters, len(work_df)//10)))
        model = KMeans(n_clusters=k, n_init='auto', random_state=42)
        labels = model.fit_predict(X)
        counts = pd.Series(labels).value_counts().to_dict()
        centers = model.cluster_centers_[:3].tolist()  # preview only
        return {
            "k": k,
            "counts": {str(k_): int(v) for k_, v in counts.items()},
            "centers_preview": centers,
            "features_used": list(work_df.columns)
        }
    except Exception:
        return None


def analyze_bytes(data: bytes, filename: str) -> Dict[str, Any]:
    """Analyze dataset bytes and return a JSON-serializable summary. Clustering is disabled."""
    name_lower = filename.lower()
    df: Optional[pd.DataFrame] = None
    try:
        if name_lower.endswith('.csv'):
            df = pd.read_csv(io.BytesIO(data))
        elif name_lower.endswith('.json'):
            import json
            obj = json.loads(data.decode('utf-8'))
            if isinstance(obj, list):
                df = pd.DataFrame(obj)
            else:
                df = pd.json_normalize(obj)
        elif name_lower.endswith(('.tsv', '.txt')):
            df = pd.read_csv(io.BytesIO(data), sep='\t')
    except Exception as e:
        return {"success": False, "error": f"Failed to parse dataset: {e}"}

    if df is None or df.empty:
        return {"success": False, "error": "Unsupported or empty dataset"}

    summary = profile_dataframe(df)
    result = {
        "success": True,
        "summary": {
            **{k: getattr(summary, k) for k in ['rows','columns','memory_mb','numeric_columns','categorical_columns']},
            "columns": [asdict(c) for c in summary.columns_detail[:40]]  # limit
        }
    }
    return result
