# mcp_client/workflows.py

import json
import asyncio
from datetime import datetime
from typing import Any
from .client import MCPLogAnalyticsClient
from .config import extract_date

class Workflows:
    """
    High-level orchestrations that combine multiple MCP tool calls.
    """

    def __init__(self, client: MCPLogAnalyticsClient):
        self.cli = client

    async def analyze_logs_comprehensive(self, date: str, user_id: str | None = None) -> dict:
        """
        1. Fetch face/activity logs from MongoDB.
        2. Perform semantic search in Milvus.
        3. Write analysis metadata to Neo4j.
        4. Augment with web search context.
        """
        result = {
            "date": date,
            "user_id": user_id,
            "timestamp": datetime.utcnow().isoformat(),
            "logs": None,
            "similar_patterns": None,
            "external_context": None
        }

        # 2. MongoDB: assume a tool that returns merged logs by date
        logs = await self.cli._call_tool("mongodb", "get_logs_by_date", date=date, user_id=user_id)
        result["logs"] = logs

        # 3. Milvus semantic search
        summary_query = f"Logs summary for {date}"
        emb = await self.cli._call_tool("websearch", "get_embedding", text=summary_query)
        sims = await self.cli._call_tool("milvus", "milvus_search", query_vector=emb, top_k=5)
        result["similar_patterns"] = sims

        # 4. Neo4j metadata write
        meta = {
            "entity_type": "Analysis",
            "entity_id": f"{date}_{user_id or 'ALL'}",
            "properties": {
                "log_count": len(logs),
                "ran_at": result["timestamp"]
            }
        }
        await self.cli._call_tool("neo4j", "memory_write", **meta)

        # 5. Web search augmentation
        ext = await self.cli._call_tool("websearch", "web_search", query=f"analytics insights {date}", num_results=3)
        result["external_context"] = ext

        return result

    async def train_model(self, user_id: str, dataset_csv: str, model_name: str = "kmeans_clustering") -> dict:
        """
        1. Upload dataset to MongoDB (GridFS).
        2. Train via SciRex.
        3. Record model info in Neo4j.
        """
        # 1. Upload
        file_id = await self.cli._call_tool("mongodb", "upload_file", filename=f"{user_id}.csv", data=dataset_csv)
        # 2. Train
        tr = await self.cli._call_tool(
            "scirex", "train_model",
            user_id=user_id, model_name=model_name, dataset_csv=dataset_csv
        )
        # 3. Neo4j write
        node = {
            "entity_type": "Model",
            "entity_id": tr["model_id"],
            "properties": {
                "user_id": user_id,
                "dataset_file": file_id,
                "trained_at": datetime.utcnow().isoformat()
            }
        }
        await self.cli._call_tool("neo4j", "memory_write", **node)
        return tr

    async def process_query(self, text: str) -> Any:
        """
        Dispatch based on keywords:
        - 'report' + date → analyze_logs_comprehensive
        - 'train' + 'model' → train_model
        - 'search' → web search
        """
        lower = text.lower()
        dt = extract_date(text)

        if "report" in lower and dt:
            return await self.analyze_logs_comprehensive(dt)
        elif "train" in lower and "model" in lower:
            # In real usage, prompt for dataset CSV or ID
            raise NotImplementedError("Please call train_model() directly with data.")
        elif "search" in lower:
            return await self.cli._call_tool("websearch", "web_search", query=text, num_results=5)

        return {"error": "Could not understand query."}
