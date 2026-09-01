from __future__ import annotations

import math
import os
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from functools import lru_cache
from typing import TypeVar
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select, update

from app.core.settings import get_settings
from app.db.session import Database
from app.models.knowledge import (
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentVersion,
)
from app.models.outbox import OutboxEvent

T = TypeVar("T")

STRONG_ENTITY_PATTERN = re.compile(
    r"(?<![A-Za-z0-9-])(?:WM|AML|SUIT|CUST)-[A-Z0-9-]+(?![A-Za-z0-9-])|恒信[\u4e00-\u9fffA-Za-z0-9（）() ]{0,30}?\d{3}号",
    re.IGNORECASE,
)
LATIN_TOKEN_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*|\d+(?:\.\d+)?")
CJK_SEGMENT_PATTERN = re.compile(r"[\u4e00-\u9fff]+")
FIELD_INTENT_ALIASES = {
    "risk_level": ("风险等级",),
    "financial_assets": ("金融资产", "资产"),
    "recent_event": ("最近事件", "最新事件", "事件"),
    "preference": ("偏好",),
}
RECENT_EVENT_VALUES = ("测评临近过期", "中风险预警", "投诉回访", "大额申购复核")
MONEY_PATTERN = re.compile(r"\d{1,3}(?:,\d{3})+\s*元")


def _extract_strong_entities(text: str) -> list[str]:
    entities: list[str] = []
    seen: set[str] = set()
    for match in STRONG_ENTITY_PATTERN.findall(text):
        entity = match.strip()
        key = entity.lower()
        if entity and key not in seen:
            seen.add(key)
            entities.append(entity)
    return entities


def _tokenize_for_bm25(text: str) -> list[str]:
    lowered = text.lower()
    tokens = LATIN_TOKEN_PATTERN.findall(lowered)
    for segment in CJK_SEGMENT_PATTERN.findall(text):
        if len(segment) == 1:
            tokens.append(segment)
            continue
        tokens.extend(segment[index : index + 2] for index in range(len(segment) - 1))
        if len(segment) >= 3:
            tokens.extend(segment[index : index + 3] for index in range(len(segment) - 2))
    return tokens


def _bm25_rank(query: str, documents: dict[str, str], *, k1: float = 1.5, b: float = 0.75) -> list[tuple[str, float]]:
    query_terms = list(dict.fromkeys(_tokenize_for_bm25(query)))
    if not query_terms or not documents:
        return []

    tokenized = {doc_id: _tokenize_for_bm25(content) for doc_id, content in documents.items()}
    doc_lengths = {doc_id: len(tokens) for doc_id, tokens in tokenized.items()}
    avgdl = sum(doc_lengths.values()) / max(len(doc_lengths), 1)
    avgdl = avgdl or 1.0

    doc_freq: Counter[str] = Counter()
    for tokens in tokenized.values():
        doc_freq.update(set(tokens))

    total_docs = len(documents)
    ranked: list[tuple[str, float]] = []
    for doc_id, tokens in tokenized.items():
        if not tokens:
            continue
        frequencies = Counter(tokens)
        score = 0.0
        for term in query_terms:
            frequency = frequencies.get(term, 0)
            if frequency == 0:
                continue
            idf = math.log(1 + (total_docs - doc_freq[term] + 0.5) / (doc_freq[term] + 0.5))
            denominator = frequency + k1 * (1 - b + b * doc_lengths[doc_id] / avgdl)
            score += idf * frequency * (k1 + 1) / denominator
        if score > 0:
            ranked.append((doc_id, score))
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked


def _rrf_fuse(rankings: list[list[str]], weights: list[float], *, rank_constant: int = 60) -> dict[str, float]:
    scores: dict[str, float] = defaultdict(float)
    for ranking, weight in zip(rankings, weights, strict=False):
        seen: set[str] = set()
        for rank, item_id in enumerate(ranking, start=1):
            if item_id in seen:
                continue
            seen.add(item_id)
            scores[item_id] += weight / (rank_constant + rank)
    return dict(scores)


def _field_intent_score(query: str, content: str) -> int:
    score = 0
    for aliases in FIELD_INTENT_ALIASES.values():
        query_mentions_field = any(alias in query for alias in aliases)
        content_has_field = any(alias in content for alias in aliases)
        if query_mentions_field and content_has_field:
            score += 1
    if any(alias in query for alias in FIELD_INTENT_ALIASES["recent_event"]):
        if any(value in content for value in RECENT_EVENT_VALUES):
            score += 3
        if re.search(r"\|\s*无\s*\|", content):
            score += 3
        if "CUST-" in query and "| CUST-" in content:
            score += 2
    return score


def _entity_window_intent_score(query: str, content: str, entities: list[str]) -> int:
    score = 0
    wants_assets = any(alias in query for alias in FIELD_INTENT_ALIASES["financial_assets"])
    wants_event = any(alias in query for alias in FIELD_INTENT_ALIASES["recent_event"])
    lowered = content.lower()
    for entity in entities:
        position = lowered.find(entity.lower())
        if position < 0:
            continue
        window = content[max(0, position - 80) : position + 260]
        if wants_assets and MONEY_PATTERN.search(window):
            score += 2
        if wants_event and any(value in window for value in RECENT_EVENT_VALUES):
            score += 4
        if wants_event and re.search(r"\|\s*无\s*\|", window):
            score += 4
    return score


class BgeReranker:
    def __init__(self, *, enabled: bool, model_name: str, allow_download: bool = False) -> None:
        self.enabled = enabled
        self.model_name = model_name
        self.allow_download = allow_download
        self.model = None
        self.backend = "disabled"
        if not enabled:
            return
        if not allow_download:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        try:
            from FlagEmbedding import FlagReranker  # type: ignore[import-not-found]

            self.model = FlagReranker(model_name, use_fp16=True)
            self.backend = "FlagEmbedding"
            return
        except Exception:
            pass
        try:
            from sentence_transformers import CrossEncoder  # type: ignore[import-not-found]

            self.model = CrossEncoder(model_name)
            self.backend = "sentence-transformers"
        except Exception:
            self.model = None
            self.backend = "unavailable"

    @property
    def available(self) -> bool:
        return self.model is not None

    def rerank(
        self,
        query: str,
        candidates: list[tuple[T, float]],
        *,
        content_getter: Callable[[T], str],
    ) -> list[tuple[T, float]]:
        if not self.available or len(candidates) < 2:
            return candidates

        pairs = [[query, content_getter(item)] for item, _score in candidates]
        if self.backend == "FlagEmbedding":
            raw_scores = self.model.compute_score(pairs, normalize=True)
        else:
            raw_scores = self.model.predict(pairs)
        if isinstance(raw_scores, float):
            raw_scores = [raw_scores]
        rescored = [
            (item, float(score))
            for (item, _old_score), score in zip(candidates, raw_scores, strict=False)
        ]
        rescored.sort(key=lambda item: item[1], reverse=True)
        return rescored


@lru_cache(maxsize=8)
def _get_bge_reranker(enabled: bool, model_name: str, allow_download: bool = False) -> BgeReranker:
    return BgeReranker(enabled=enabled, model_name=model_name, allow_download=allow_download)


class KnowledgeRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def ensure_default_base(self) -> KnowledgeBase:
        async with self.database.session_factory() as session:
            result = await session.execute(select(KnowledgeBase).where(KnowledgeBase.name == "default"))
            base = result.scalar_one_or_none()
            if base is None:
                base = KnowledgeBase(id=str(uuid4()), name="default", description="默认知识库", status="active")
                session.add(base)
                await session.commit()
                await session.refresh(base)
            return base

    async def get_base(self, base_id: str) -> KnowledgeBase | None:
        try:
            UUID(base_id)
        except ValueError:
            return None
        async with self.database.session_factory() as session:
            result = await session.execute(select(KnowledgeBase).where(KnowledgeBase.id == base_id, KnowledgeBase.status == "active"))
            return result.scalar_one_or_none()

    async def create_document(self, *, knowledge_base_id: str, document_key: str, file_name: str, source_path: str, file_type: str, file_size: int, content_hash: str, category: str = "general", permission_level: str = "public") -> KnowledgeDocument:
        async with self.database.session_factory() as session:
            document = KnowledgeDocument(id=str(uuid4()), knowledge_base_id=knowledge_base_id, document_key=document_key, file_name=file_name, source_path=source_path, file_type=file_type, file_size=file_size, content_hash=content_hash, category=category, permission_level=permission_level)
            session.add(document)
            await session.flush()
            session.add(OutboxEvent(id=str(uuid4()), event_type="knowledge.document_created", aggregate_type="knowledge_document", aggregate_id=str(document.id), payload_json={"document_id": str(document.id)}, status="pending"))
            await session.commit()
            await session.refresh(document)
            return document

    async def enqueue_ingestion(self, document_id: str) -> str:
        async with self.database.session_factory() as session:
            event = OutboxEvent(id=str(uuid4()), event_type="knowledge.document_created", aggregate_type="knowledge_document", aggregate_id=document_id, payload_json={"document_id": document_id}, status="pending")
            session.add(event)
            await session.commit()
            return str(event.id)

    async def get_document(self, document_id: str) -> KnowledgeDocument | None:
        try:
            UUID(document_id)
        except ValueError:
            return None
        async with self.database.session_factory() as session:
            result = await session.execute(select(KnowledgeDocument).where(KnowledgeDocument.id == document_id, KnowledgeDocument.deleted_at.is_(None)))
            return result.scalar_one_or_none()

    async def create_version(self, *, document_id: str, content_hash: str, parser_type: str) -> KnowledgeDocumentVersion:
        async with self.database.session_factory() as session:
            result = await session.execute(select(KnowledgeDocumentVersion.version_no).where(KnowledgeDocumentVersion.document_id == document_id).order_by(KnowledgeDocumentVersion.version_no.desc()).limit(1))
            latest = result.scalar_one_or_none() or 0
            version = KnowledgeDocumentVersion(id=str(uuid4()), document_id=document_id, version_no=latest + 1, content_hash=content_hash, parser_type=parser_type)
            session.add(version)
            await session.commit()
            await session.refresh(version)
            return version

    async def save_chunks(self, *, document_id: str, version_id: str, chunks: list[dict]) -> int:
        async with self.database.session_factory() as session:
            rows = [KnowledgeChunk(id=str(uuid4()), document_id=document_id, version_id=version_id, chunk_index=int(chunk["chunk_index"]), content=chunk["content"], content_hash=chunk["content_hash"], title_path=chunk.get("title_path", ""), page_number=chunk.get("page_number"), metadata_json=chunk.get("metadata", {}), embedding=chunk.get("embedding")) for chunk in chunks]
            session.add_all(rows)
            await session.commit()
            return len(rows)

    async def activate_version(self, *, document_id: str, version_id: str, chunk_count: int) -> None:
        async with self.database.session_factory() as session:
            await session.execute(update(KnowledgeDocumentVersion).where(KnowledgeDocumentVersion.document_id == document_id).values(status="superseded", deleted_at=None))
            await session.execute(update(KnowledgeDocumentVersion).where(KnowledgeDocumentVersion.id == version_id).values(status="active", chunk_count=chunk_count, activated_at=func.now()))
            await session.execute(update(KnowledgeDocument).where(KnowledgeDocument.id == document_id).values(status="active", current_version_id=version_id, error_message=None))
            await session.commit()

    async def mark_document_failed(self, *, document_id: str, version_id: str | None, error_message: str) -> None:
        async with self.database.session_factory() as session:
            await session.execute(update(KnowledgeDocument).where(KnowledgeDocument.id == document_id).values(status="failed", error_message=error_message[:4000]))
            if version_id:
                await session.execute(update(KnowledgeDocumentVersion).where(KnowledgeDocumentVersion.id == version_id).values(status="failed", error_message=error_message[:4000]))
            await session.commit()

    async def search_hybrid(self, *, query: str, query_embedding: list[float] | None, top_k: int, knowledge_base_id: str | None = None, category: str | None = None) -> list[tuple[KnowledgeChunk, float]]:
        settings = get_settings()
        candidate_k = max(top_k * 12, settings.hybrid_candidate_k)
        chunks_by_id: dict[str, KnowledgeChunk] = {}
        rankings: list[list[str]] = []
        weights: list[float] = []
        fallback_scores: dict[str, float] = defaultdict(float)

        def active_statement(*entities):
            statement = select(*entities).join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id).join(KnowledgeDocumentVersion, KnowledgeDocumentVersion.id == KnowledgeChunk.version_id).where(KnowledgeChunk.status == "active", KnowledgeDocument.status == "active", KnowledgeDocument.deleted_at.is_(None), KnowledgeDocumentVersion.status == "active")
            if knowledge_base_id:
                statement = statement.where(KnowledgeDocument.knowledge_base_id == knowledge_base_id)
            if category:
                statement = statement.where(KnowledgeDocument.category == category)
            return statement

        def remember(chunk: KnowledgeChunk, score: float = 0.0) -> str:
            chunk_id = str(chunk.id)
            chunks_by_id[chunk_id] = chunk
            fallback_scores[chunk_id] = max(fallback_scores[chunk_id], score)
            return chunk_id

        async with self.database.session_factory() as session:
            if query_embedding:
                semantic = (1.0 - KnowledgeChunk.embedding.cosine_distance(query_embedding)).label("semantic_score")
                statement = active_statement(KnowledgeChunk, semantic).where(KnowledgeChunk.embedding.is_not(None)).order_by(semantic.desc()).limit(candidate_k)
                result = await session.execute(statement)
                vector_rank: list[str] = []
                for chunk, score in result.all():
                    vector_rank.append(remember(chunk, float(score or 0.0)))
                if vector_rank:
                    rankings.append(vector_rank)
                    weights.append(settings.hybrid_vector_weight)

            text_rank = func.ts_rank_cd(func.to_tsvector("simple", KnowledgeChunk.content), func.plainto_tsquery("simple", query)).label("text_rank")
            statement = active_statement(KnowledgeChunk, text_rank).order_by(text_rank.desc()).limit(candidate_k)
            result = await session.execute(statement)
            keyword_rank: list[str] = []
            for chunk, score in result.all():
                score_value = float(score or 0.0)
                if score_value <= 0:
                    continue
                keyword_rank.append(remember(chunk, score_value))
            if keyword_rank:
                rankings.append(keyword_rank)
                weights.append(settings.hybrid_keyword_weight)

            entities = _extract_strong_entities(query)
            if entities:
                exact_conditions = [KnowledgeChunk.content.ilike(f"%{entity}%") for entity in entities]
                statement = active_statement(KnowledgeChunk).where(or_(*exact_conditions)).limit(candidate_k)
                result = await session.execute(statement)
                exact_chunks = list(result.scalars().all())
                adjacent_conditions = [
                    and_(
                        KnowledgeChunk.document_id == chunk.document_id,
                        KnowledgeChunk.chunk_index.in_([chunk.chunk_index - 1, chunk.chunk_index + 1]),
                    )
                    for chunk in exact_chunks
                ]
                if adjacent_conditions:
                    statement = active_statement(KnowledgeChunk).where(or_(*adjacent_conditions)).limit(candidate_k)
                    result = await session.execute(statement)
                    exact_chunks.extend(result.scalars().all())
                exact_by_id = {str(chunk.id): chunk for chunk in exact_chunks}
                exact_bm25 = dict(_bm25_rank(query, {chunk_id: chunk.content for chunk_id, chunk in exact_by_id.items()}))
                exact_chunks.sort(
                    key=lambda chunk: (
                        -sum(1 for entity in entities if entity.lower() in chunk.content.lower()),
                        -_entity_window_intent_score(query, chunk.content, entities),
                        -_field_intent_score(query, chunk.content),
                        -exact_bm25.get(str(chunk.id), 0.0),
                        min(
                            (chunk.content.lower().find(entity.lower()) for entity in entities if entity.lower() in chunk.content.lower()),
                            default=len(chunk.content),
                        ),
                    )
                )
                exact_rank = [remember(chunk, 1.0) for chunk in dict.fromkeys(exact_chunks)]
                if exact_rank:
                    rankings.append(exact_rank)
                    weights.append(settings.hybrid_exact_weight)

            bm25_pool_size = max(candidate_k, settings.hybrid_bm25_pool_size)
            statement = active_statement(KnowledgeChunk).limit(bm25_pool_size)
            result = await session.execute(statement)
            bm25_pool = list(result.scalars().all())
            bm25_documents = {str(chunk.id): chunk.content for chunk in bm25_pool}
            bm25_chunks = {str(chunk.id): chunk for chunk in bm25_pool}
            bm25_ranked = _bm25_rank(query, bm25_documents)
            bm25_rank = []
            for chunk_id, score in bm25_ranked[:candidate_k]:
                chunk = bm25_chunks.get(chunk_id)
                if chunk is None:
                    continue
                bm25_rank.append(remember(chunk, score))
            if bm25_rank:
                rankings.append(bm25_rank)
                weights.append(settings.hybrid_bm25_weight)

        if not rankings:
            return []

        fused_scores = _rrf_fuse(rankings, weights)
        entities = _extract_strong_entities(query)
        for chunk_id, score in list(fused_scores.items()):
            chunk = chunks_by_id.get(chunk_id)
            if chunk is not None:
                fused_scores[chunk_id] = (
                    score
                    + settings.hybrid_field_intent_weight * _field_intent_score(query, chunk.content)
                    + settings.hybrid_entity_window_weight * _entity_window_intent_score(query, chunk.content, entities)
                )
        ranked_ids = sorted(
            fused_scores,
            key=lambda chunk_id: (fused_scores[chunk_id], fallback_scores.get(chunk_id, 0.0)),
            reverse=True,
        )
        rerank_window = max(top_k, settings.bge_reranker_top_n)
        fused_candidates = [(chunks_by_id[chunk_id], fused_scores[chunk_id]) for chunk_id in ranked_ids[:rerank_window]]
        reranker = _get_bge_reranker(
            settings.bge_reranker_enabled,
            settings.bge_reranker_model,
            settings.bge_reranker_allow_download,
        )
        reranked = reranker.rerank(query, fused_candidates, content_getter=lambda chunk: chunk.content)
        return reranked[:top_k]

    async def search_text(self, *, query: str, top_k: int, knowledge_base_id: str | None = None, category: str | None = None) -> list[KnowledgeChunk]:
        async with self.database.session_factory() as session:
            statement = select(KnowledgeChunk).join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id).join(KnowledgeDocumentVersion, KnowledgeDocumentVersion.id == KnowledgeChunk.version_id).where(KnowledgeChunk.status == "active", KnowledgeDocument.status == "active", KnowledgeDocument.deleted_at.is_(None), KnowledgeDocumentVersion.status == "active")
            if knowledge_base_id:
                statement = statement.where(KnowledgeDocument.knowledge_base_id == knowledge_base_id)
            if category:
                statement = statement.where(KnowledgeDocument.category == category)
            statement = statement.where(KnowledgeChunk.content.ilike(f"%{query}%")).limit(top_k)
            result = await session.execute(statement)
            return list(result.scalars().all())
