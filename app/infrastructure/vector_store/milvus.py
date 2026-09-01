from __future__ import annotations

from app.ports.vector_store import VectorDocument, VectorHit


class MilvusVectorStore:
    def __init__(self, uri: str = "http://127.0.0.1:19530", collection_name: str = "wealth_documents", dimension: int = 1024):
        self.uri=uri; self.collection_name=collection_name; self.dimension=dimension
        self._client=None
    def _get(self):
        if self._client is None:
            from pymilvus import MilvusClient
            self._client=MilvusClient(uri=self.uri)
        return self._client
    async def upsert(self, documents: list[VectorDocument]) -> None:
        raise NotImplementedError("Milvus schema/index initialization is the next migration step")
    async def search(self, vector: list[float], top_k: int = 5, filters: dict | None = None) -> list[VectorHit]:
        raise NotImplementedError("Milvus schema/index initialization is the next migration step")
    async def delete(self, ids: list[str]) -> None:
        raise NotImplementedError("Milvus schema/index initialization is the next migration step")
