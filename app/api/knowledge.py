from datetime import UTC

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from app.common.exceptions import ResourceNotFoundError
from app.common.middleware.trace import get_trace_id
from app.common.response import ApiResponse
from app.common.security.auth import require_permission
from app.infrastructure.qwen import QwenProvider
from app.models.knowledge import KnowledgeDocument
from app.repositories.knowledge import KnowledgeRepository
from app.schemas.knowledge import (
    DocumentCreateRequest,
    DocumentIngestResponse,
    DocumentResponse,
    KnowledgeBaseResponse,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.get("/default", response_model=ApiResponse[KnowledgeBaseResponse])
async def get_default_knowledge_base(
    request: Request, _user=Depends(require_permission("product:read"))
) -> ApiResponse[KnowledgeBaseResponse]:
    repository = KnowledgeRepository(request.app.state.database)
    base = await repository.ensure_default_base()
    return ApiResponse(
        data=KnowledgeBaseResponse(
            id=str(base.id),
            name=base.name,
            description=base.description,
            status=base.status,
        ),
        trace_id=get_trace_id(),
    )


@router.post(
    "/documents", response_model=ApiResponse[DocumentResponse], status_code=201
)
async def create_document(
    request: Request,
    payload: DocumentCreateRequest,
    _user=Depends(require_permission("product:write")),
) -> ApiResponse[DocumentResponse]:
    repository = KnowledgeRepository(request.app.state.database)
    base = (
        await repository.ensure_default_base()
        if payload.knowledge_base_id is None
        else await repository.get_base(payload.knowledge_base_id)
    )
    if base is None:
        raise ResourceNotFoundError(
            "knowledge base not found", code="KNOWLEDGE_BASE_NOT_FOUND"
        )
    document = await repository.create_document(
        knowledge_base_id=str(base.id),
        document_key=payload.document_key,
        file_name=payload.file_name,
        source_path=payload.source_path,
        file_type=payload.file_type,
        file_size=payload.file_size,
        content_hash=payload.content_hash,
        category=payload.category,
        permission_level=payload.permission_level,
    )
    return ApiResponse(
        data=DocumentResponse.model_validate(document, from_attributes=True),
        trace_id=get_trace_id(),
    )


@router.post("/search", response_model=ApiResponse[KnowledgeSearchResponse])
async def search_knowledge(
    request: Request,
    payload: KnowledgeSearchRequest,
    _user=Depends(require_permission("product:read")),
) -> ApiResponse[KnowledgeSearchResponse]:
    repository = KnowledgeRepository(request.app.state.database)
    provider = QwenProvider(request.app.state.settings)
    try:
        query_embedding = await provider.embed([payload.query])
        pairs = await repository.search_hybrid(
            query=payload.query,
            query_embedding=query_embedding[0],
            top_k=payload.top_k,
            knowledge_base_id=payload.knowledge_base_id,
            category=payload.category,
        )
        mode = "hybrid-ftS-pgvector"
    except Exception:
        pairs = [
            (hit, 1.0)
            for hit in await repository.search_text(
                query=payload.query,
                top_k=payload.top_k,
                knowledge_base_id=payload.knowledge_base_id,
                category=payload.category,
            )
        ]
        mode = "text-ilike-fallback"
    finally:
        await provider.close()
    response = KnowledgeSearchResponse(
        query=payload.query,
        retrieval_mode=mode,
        embedding_dimension=request.app.state.settings.embedding_dimension,
        hits=[
            {
                "id": str(hit.id),
                "content": hit.content,
                "title_path": hit.title_path,
                "document_id": str(hit.document_id),
                "version_id": str(hit.version_id),
                "chunk_index": hit.chunk_index,
                "score": score,
                "metadata": hit.metadata_json,
            }
            for hit, score in pairs
        ],
    )
    return ApiResponse(data=response, trace_id=get_trace_id())


@router.post(
    "/documents/{document_id}/ingest",
    response_model=ApiResponse[DocumentIngestResponse],
    status_code=202,
)
async def ingest_document(
    request: Request,
    document_id: str,
    _user=Depends(require_permission("product:write")),
) -> ApiResponse[DocumentIngestResponse]:
    repository = KnowledgeRepository(request.app.state.database)
    document = await repository.get_document(document_id)
    if document is None:
        raise ResourceNotFoundError(
            "knowledge document not found", code="KNOWLEDGE_DOCUMENT_NOT_FOUND"
        )
    event_id = await repository.enqueue_ingestion(document_id)
    return ApiResponse(
        data=DocumentIngestResponse(
            document_id=document_id, status="queued", event_id=event_id
        ),
        trace_id=get_trace_id(),
    )


@router.get("/documents", response_model=ApiResponse[list[DocumentResponse]])
async def list_documents(
    request: Request, _user=Depends(require_permission("product:read"))
) -> ApiResponse[list[DocumentResponse]]:
    """知识库文档列表（F1.2：GET /api/knowledge/list）。"""
    async with request.app.state.database.session_factory() as session:
        rows = list(
            (
                await session.execute(
                    select(KnowledgeDocument)
                    .where(KnowledgeDocument.deleted_at.is_(None))
                    .order_by(KnowledgeDocument.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
    return ApiResponse(
        data=[DocumentResponse.model_validate(x, from_attributes=True) for x in rows],
        trace_id=get_trace_id(),
    )


@router.delete("/documents/{document_id}", response_model=ApiResponse[dict])
async def delete_document(
    request: Request,
    document_id: str,
    _user=Depends(require_permission("product:write")),
) -> ApiResponse[dict]:
    """删除知识文档（F1.2：同步标记元数据过期 + 软删除）。"""
    from datetime import datetime

    repository = KnowledgeRepository(request.app.state.database)
    document = await repository.get_document(document_id)
    if document is None:
        raise ResourceNotFoundError(
            "knowledge document not found", code="KNOWLEDGE_DOCUMENT_NOT_FOUND"
        )
    async with request.app.state.database.session_factory() as session:
        doc = (
            await session.execute(
                select(KnowledgeDocument).where(KnowledgeDocument.id == document.id)
            )
        ).scalar_one()
        doc.deleted_at = datetime.now(UTC)
        doc.status = "deleted"
        await session.commit()
    return ApiResponse(
        data={"document_id": document_id, "status": "deleted"}, trace_id=get_trace_id()
    )
