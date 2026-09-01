from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.settings import get_settings  # noqa: E402
from app.db.session import Database  # noqa: E402
from app.infrastructure.qwen import QwenProvider  # noqa: E402
from app.models.knowledge import KnowledgeDocument  # noqa: E402
from app.repositories.knowledge import KnowledgeRepository  # noqa: E402
from app.services.knowledge_ingestion import _chunks  # noqa: E402
from scripts.seed_knowledge_docs import build_document_specs  # noqa: E402


@dataclass(frozen=True)
class DirectIngestResult:
    file_name: str
    chunk_count: int
    version_no: int


async def _embed_batches(
    provider: QwenProvider, texts: list[str], batch_size: int
) -> list[list[float]]:
    vectors: list[list[float]] = []
    batch_size = max(1, min(batch_size, 10))
    if provider._client is None:
        raise RuntimeError("DASHSCOPE_API_KEY is not configured")
    settings = provider.settings
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        response = await provider._client.embeddings.create(
            model=settings.qwen_embedding_model,
            input=batch,
            dimensions=settings.embedding_dimension,
        )
        vectors.extend(item.embedding for item in response.data)
        print(f"  embedded {min(start + len(batch), len(texts))}/{len(texts)}")
    if len(vectors) != len(texts):
        raise ValueError("embedding response count does not match chunk count")
    if any(len(vector) != settings.embedding_dimension for vector in vectors):
        raise ValueError(f"embedding dimension must be {settings.embedding_dimension}")
    return vectors


async def _ensure_document(
    db: Database, repo: KnowledgeRepository, base_id: str, spec: dict
) -> KnowledgeDocument:
    path: Path = spec["path"]
    content_hash = hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
    async with db.session_factory() as session:
        document = (
            await session.execute(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.knowledge_base_id == base_id,
                    KnowledgeDocument.document_key == spec["document_key"],
                    KnowledgeDocument.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if document is not None:
            document.file_name = spec["file_name"]
            document.source_path = str(path)
            document.file_type = spec["file_type"]
            document.file_size = path.stat().st_size
            document.content_hash = content_hash
            document.category = spec["category"]
            document.status = "processing"
            document.error_message = None
            await session.commit()
            await session.refresh(document)
            return document

    return await repo.create_document(
        knowledge_base_id=base_id,
        document_key=spec["document_key"],
        file_name=spec["file_name"],
        source_path=str(path),
        file_type=spec["file_type"],
        file_size=path.stat().st_size,
        content_hash=content_hash,
        category=spec["category"],
        permission_level="public",
    )


async def ingest_all(limit: int | None = None, batch_size: int = 10) -> list[DirectIngestResult]:
    settings = get_settings()
    db = Database(settings)
    repo = KnowledgeRepository(db)
    provider = QwenProvider(settings)
    results: list[DirectIngestResult] = []
    try:
        base = await repo.ensure_default_base()
        specs = [spec for spec in build_document_specs() if spec["path"].exists()]
        if limit is not None:
            specs = specs[:limit]
        for index, spec in enumerate(specs, start=1):
            path: Path = spec["path"]
            text = path.read_text(encoding="utf-8")
            parts = _chunks(text)
            print(f"[{index}/{len(specs)}] {spec['file_name']} chunks={len(parts)}")
            document = await _ensure_document(db, repo, str(base.id), spec)
            version = await repo.create_version(
                document_id=str(document.id),
                content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                parser_type="plain_text",
            )
            vectors = await _embed_batches(provider, parts, batch_size=batch_size)
            rows = [
                {
                    "chunk_index": chunk_index,
                    "content": content,
                    "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    "title_path": "",
                    "metadata": {"parser_type": "plain_text", "source_path": str(path)},
                    "embedding": vector,
                }
                for chunk_index, (content, vector) in enumerate(zip(parts, vectors))
            ]
            chunk_count = await repo.save_chunks(
                document_id=str(document.id), version_id=str(version.id), chunks=rows
            )
            await repo.activate_version(
                document_id=str(document.id),
                version_id=str(version.id),
                chunk_count=chunk_count,
            )
            results.append(
                DirectIngestResult(
                    file_name=spec["file_name"],
                    chunk_count=chunk_count,
                    version_no=version.version_no,
                )
            )
            print(f"  active version={version.version_no}, chunks={chunk_count}")
    finally:
        await provider.close()
        await db.dispose()
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Directly ingest knowledge docs into pgvector")
    parser.add_argument("--limit", type=int, help="Only ingest the first N documents")
    parser.add_argument("--batch-size", type=int, default=10, help="Embedding request batch size")
    args = parser.parse_args()
    results = asyncio.run(ingest_all(limit=args.limit, batch_size=args.batch_size))
    print(f"ingested_documents={len(results)}")
    print(f"ingested_chunks={sum(result.chunk_count for result in results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
