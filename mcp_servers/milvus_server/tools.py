from fastmcp import FastMCP
from pymilvus import connections, Collection
from config import MILVUS_HOST, MILVUS_PORT, COLLECTION_NAME

# Connect once
connections.connect("default", host=MILVUS_HOST, port=MILVUS_PORT)
collection = Collection(COLLECTION_NAME)

def register_tools(mcp: FastMCP):
    @mcp.tool()
    async def milvus_search(query_vector: list[float], top_k: int = 5):
        """Return top_k nearest logs (id + text)."""
        res = collection.search(
            data=[query_vector],
            anns_field="embedding",
            param={"metric_type":"L2","params":{"nprobe":10}},
            limit=top_k,
            output_fields=["log_id","log_text"]
        )
        return [
            {"log_id": r.entity.get("log_id"), "log_text": r.entity.get("log_text")}
            for r in res[0]
        ]

    @mcp.tool()
    async def add_vectors(log_ids: list[int], texts: list[str], vectors: list[list[float]]):
        """Insert new vectors into the collection."""
        entities = [
            {"log_id": id_, "log_text": txt, "embedding": vec}
            for id_, txt, vec in zip(log_ids, texts, vectors)
        ]
        return True
