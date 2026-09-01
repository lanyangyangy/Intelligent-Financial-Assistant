from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class KnowledgeBaseResponse(BaseModel):
    id: str
    name: str
    description: str
    status: str


class DocumentCreateRequest(BaseModel):
    knowledge_base_id: str | None = None
    document_key: str = Field(min_length=1, max_length=1000)
    file_name: str = Field(min_length=1, max_length=255)
    source_path: str = Field(min_length=1, max_length=1000)
    file_type: str = Field(min_length=1, max_length=32)
    file_size: int = Field(default=0, ge=0)
    content_hash: str = Field(min_length=64, max_length=64)
    category: str = Field(default="general", max_length=64)
    permission_level: str = Field(default="public", max_length=32)


class DocumentIngestResponse(BaseModel):
    document_id: str
    status: str
    event_id: str | None = None


class DocumentResponse(BaseModel):
    id: str
    knowledge_base_id: str
    document_key: str
    file_name: str
    source_path: str
    file_type: str
    file_size: int
    content_hash: str
    category: str
    permission_level: str
    status: str
    created_at: datetime
    updated_at: datetime


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=50)
    knowledge_base_id: str | None = None
    category: str | None = None


class KnowledgeSearchHit(BaseModel):
    id: str
    content: str
    title_path: str
    document_id: str
    version_id: str
    chunk_index: int
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeSearchResponse(BaseModel):
    query: str
    hits: list[KnowledgeSearchHit]
    retrieval_mode: str
    embedding_dimension: int
