from app.ports.vector_store import VectorDocument, VectorHit


class PgVectorStore:
    """Port-compatible placeholder; knowledge repository remains the first adapter."""
    def __init__(self, database): self.database = database
    async def upsert(self, documents: list[VectorDocument]) -> None: raise NotImplementedError("knowledge ingestion adapter not migrated yet")
    async def search(self, vector: list[float], top_k: int = 5, filters: dict | None = None) -> list[VectorHit]: raise NotImplementedError("knowledge search adapter not migrated yet")
    async def delete(self, ids: list[str]) -> None: raise NotImplementedError("knowledge delete adapter not migrated yet")
