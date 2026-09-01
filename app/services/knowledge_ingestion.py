from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from app.infrastructure.qwen import QwenProvider
from app.repositories.knowledge import KnowledgeRepository


@dataclass(frozen=True)
class IngestionResult:
    document_id: str
    version_id: str
    chunk_count: int
    status: str


def _chunks(text: str, *, size: int = 512, overlap: int = 64) -> list[str]:
    """F1.2 分块策略：512 token/块，overlap 64。按标题层级优先切分。

    优先按空行/换行切分（保持语义完整），大段落再按 size 切片并保留 overlap。
    FAQ 问答对（制表符分隔）每行独立成块，保证"一问一答"完整命中。
    """
    normalized = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if not normalized:
        return []
    # FAQ 问答对格式检测：>60% 的行含制表符（问题\t答案）→ 每行独立 chunk
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    if lines and sum(1 for line in lines if "\t" in line) / len(lines) > 0.6:
        faq_chunks: list[str] = []
        for line in lines:
            parts = line.split("\t", 1)
            faq_chunks.append(
                f"Q: {parts[0]}\nA: {parts[1]}" if len(parts) == 2 else line
            )
        return faq_chunks
    paragraphs = [p.strip() for p in normalized.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buffer = ""
    for paragraph in paragraphs:
        # 标题行（# / ## / ###）单独成块，保留层级信息
        candidate = f"{buffer}\n\n{paragraph}".strip() if buffer else paragraph
        if len(candidate) <= size:
            buffer = candidate
            continue
        # 段落超过 size：按固定窗口切分并带 overlap
        if buffer.strip():
            chunks.append(buffer.strip())
            buffer = ""
        step = max(1, size - overlap)
        for i in range(0, len(paragraph), step):
            chunks.append(paragraph[i : i + size])
    if buffer.strip():
        chunks.append(buffer.strip())
    return [c for c in chunks if c.strip()]


class KnowledgeIngestionService:
    def __init__(
        self, repository: KnowledgeRepository, embedding: QwenProvider
    ) -> None:
        self.repository = repository
        self.embedding = embedding

    async def ingest_document(self, document_id: str) -> IngestionResult:
        document = await self.repository.get_document(document_id)
        if document is None:
            raise ValueError("knowledge document not found")
        version = None
        try:
            path = Path(document.source_path)
            text = path.read_text(encoding="utf-8")
            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            version = await self.repository.create_version(
                document_id=document_id,
                content_hash=content_hash,
                parser_type="plain_text",
            )
            parts = _chunks(text)
            vectors: list[list[float]] = []
            for start in range(0, len(parts), 4):
                vectors.extend(await self.embedding.embed(parts[start : start + 4]))
            if len(vectors) != len(parts):
                raise ValueError("embedding response count does not match chunk count")
            rows = [
                {
                    "chunk_index": index,
                    "content": content,
                    "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    "title_path": "",
                    "metadata": {"parser_type": "plain_text"},
                    "embedding": vector,
                }
                for index, (content, vector) in enumerate(zip(parts, vectors))
            ]
            count = await self.repository.save_chunks(
                document_id=document_id, version_id=str(version.id), chunks=rows
            )
            await self.repository.activate_version(
                document_id=document_id, version_id=str(version.id), chunk_count=count
            )
            return IngestionResult(
                document_id=document_id,
                version_id=str(version.id),
                chunk_count=count,
                status="active",
            )
        except Exception as exc:
            await self.repository.mark_document_failed(
                document_id=document_id,
                version_id=str(version.id) if version else None,
                error_message=f"{type(exc).__name__}: {exc}",
            )
            raise
