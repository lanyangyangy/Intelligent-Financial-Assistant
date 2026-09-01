from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.profile import CustomerProfileTag, CustomerProfileTagConflict
from app.profile_domain.tag_governance import (
    ExtractedProfileTag,
    TagConflictView,
    TagDecision,
    source_priority,
)


def _source_initial_confidence(source_type: str, extraction_method: str) -> float | None:
    """Return the contractual initial confidence for a tag source."""
    source = source_type.upper()
    method = extraction_method.upper()
    if source in {"KYC", "QUESTIONNAIRE", "EXTERNAL_VERIFIED"}:
        return 0.90
    if source == "USER_STATED" and method == "AI":
        return 0.60
    if source == "USER_STATED" and method in {"DIRECT", "MANUAL", "RULE"}:
        return 0.40
    if source in {"DEFAULT", "SYSTEM_BEHAVIOR"}:
        # 系统规则生成的标签没有问卷或用户明确陈述作为依据，按默认值治理。
        return 0.20
    # 未识别的来源没有更强证据，统一按契约中的默认值处理。
    return 0.20


def _effective_confidence(
    source_type: str, extraction_method: str, fallback: float | Decimal | None = None
) -> float | None:
    """Return the source-governed confidence, falling back only for unknown sources."""
    initial = _source_initial_confidence(source_type, extraction_method)
    if initial is not None:
        return initial
    return float(fallback) if fallback is not None else None


def _governed_tag(
    tag: ExtractedProfileTag, source_type: str, extraction_method: str
) -> ExtractedProfileTag:
    initial = _source_initial_confidence(source_type, extraction_method)
    if initial is None:
        return tag
    return tag.model_copy(update={"confidence": Decimal(str(initial))})


def utcnow() -> datetime:
    return datetime.now(UTC)


class TagGovernanceService:
    """Apply extracted tags with source-priority conflict governance.

    F2.1 冲突处理策略：
      1. 来源置信度高的覆盖低的（source_priority：KYC/问卷 400 > AI 300 > 自述 200 > 默认 100）
      2. 相同来源（source_type + extraction_method 一致）则新数据覆盖旧数据
      3. 所有冲突均写入 customer_profile_tag_conflict 审计表；同优先级不同值保留为
         OPEN 待人工复核，其余自动解析并记录 RESOLVED。
    """

    async def apply_tags(
        self,
        session: AsyncSession,
        user_id: str,
        extracted: list[ExtractedProfileTag],
        *,
        source_type: str = "USER_STATED",
        extraction_method: str = "AI",
        now: datetime | None = None,
        trace_id: str | None = None,
    ) -> list[dict]:
        now = now or utcnow()
        trace_id = trace_id or str(uuid4())
        applications: list[dict] = []
        existing_rows = list(
            (
                await session.execute(
                    select(CustomerProfileTag).where(
                        CustomerProfileTag.user_id == user_id,
                        CustomerProfileTag.status == "ACTIVE",
                    )
                )
            )
            .scalars()
            .all()
        )
        existing_by_code: dict[str, CustomerProfileTag] = {
            row.tag_code: row for row in existing_rows
        }

        for tag in extracted:
            tag = _governed_tag(tag, source_type, extraction_method)
            code = tag.tag_code.value
            existing = existing_by_code.get(code)
            incoming_priority = source_priority(source_type, extraction_method)
            decision = TagDecision.CREATED
            conflict_id: str | None = None
            row: CustomerProfileTag | None = None

            if existing is None:
                row = CustomerProfileTag(
                    id=str(uuid4()),
                    user_id=user_id,
                    tag_code=code,
                    tag_value_json=json.dumps(
                        tag.tag_value, ensure_ascii=False, default=str
                    ),
                    confidence=float(tag.confidence),
                    source_type=source_type,
                    extraction_method=extraction_method,
                    status="ACTIVE",
                    evidence_quote=tag.evidence_quote[:500],
                    effective_at=now,
                )
                session.add(row)
            else:
                existing_priority = source_priority(
                    existing.source_type, existing.extraction_method
                )
                same_source = (
                    existing.source_type == source_type
                    and existing.extraction_method == extraction_method
                )
                same_value = self._same_value(existing.tag_value_json, tag.tag_value)
                if same_value:
                    # 相同值也必须重新按来源初始化，避免历史值（如 0.85/0.40）继续污染画像。
                    decision = TagDecision.UPDATED_SAME_SOURCE
                    existing.confidence = float(tag.confidence)
                    existing.status = "ACTIVE"
                    existing.evidence_quote = tag.evidence_quote[:500]
                    existing.updated_at = now
                elif same_source:
                    # 相同来源 → 新数据覆盖旧数据，并保留冲突审计记录
                    decision = TagDecision.UPDATED_SAME_SOURCE
                    conflict_id = await self._record_conflict(
                        session,
                        user_id=user_id,
                        tag_code=code,
                        existing=existing,
                        incoming=tag,
                        source_type=source_type,
                        extraction_method=extraction_method,
                        status="RESOLVED",
                        resolution="AUTO_SAME_SOURCE_OVERWRITTEN",
                        resolved_by="SYSTEM",
                        now=now,
                        trace_id=trace_id,
                    )
                    existing.tag_value_json = json.dumps(
                        tag.tag_value, ensure_ascii=False, default=str
                    )
                    existing.confidence = float(tag.confidence)
                    existing.evidence_quote = tag.evidence_quote[:500]
                    existing.status = "ACTIVE"
                    existing.updated_at = now
                elif existing_priority > incoming_priority:
                    # 旧来源优先级更高 → 新值被忽略，记录审计
                    decision = TagDecision.IGNORED_LOWER_PRIORITY
                    conflict_id = await self._record_conflict(
                        session,
                        user_id=user_id,
                        tag_code=code,
                        existing=existing,
                        incoming=tag,
                        source_type=source_type,
                        extraction_method=extraction_method,
                        status="RESOLVED",
                        resolution="AUTO_LOWER_PRIORITY_IGNORED",
                        resolved_by="SYSTEM",
                        now=now,
                        trace_id=trace_id,
                    )
                elif existing_priority < incoming_priority:
                    # 新来源优先级更高 → 覆盖旧值，记录审计
                    decision = TagDecision.REPLACED_LOWER_PRIORITY
                    conflict_id = await self._record_conflict(
                        session,
                        user_id=user_id,
                        tag_code=code,
                        existing=existing,
                        incoming=tag,
                        source_type=source_type,
                        extraction_method=extraction_method,
                        status="RESOLVED",
                        resolution="AUTO_HIGHER_PRIORITY_REPLACED",
                        resolved_by="SYSTEM",
                        now=now,
                        trace_id=trace_id,
                    )
                    existing.tag_value_json = json.dumps(
                        tag.tag_value, ensure_ascii=False, default=str
                    )
                    existing.confidence = float(tag.confidence)
                    existing.source_type = source_type
                    existing.extraction_method = extraction_method
                    existing.evidence_quote = tag.evidence_quote[:500]
                    existing.status = "ACTIVE"
                    existing.updated_at = now
                else:
                    # 同优先级不同值 → 保留为 OPEN 待人工复核
                    decision = TagDecision.NEEDS_REVIEW
                    conflict_id = await self._record_conflict(
                        session,
                        user_id=user_id,
                        tag_code=code,
                        existing=existing,
                        incoming=tag,
                        source_type=source_type,
                        extraction_method=extraction_method,
                        status="OPEN",
                        resolution=None,
                        resolved_by=None,
                        now=now,
                        trace_id=trace_id,
                        requires_confirmation=True,
                    )
                    existing.status = "NEEDS_REVIEW"
                    existing.updated_at = now
                row = existing

            applications.append(
                {
                    "tag_code": code,
                    "decision": decision.value,
                    "value": tag.tag_value,
                    "confidence": float(tag.confidence),
                    "conflict_id": conflict_id,
                }
            )
            if decision in {TagDecision.CREATED, TagDecision.REPLACED_LOWER_PRIORITY}:
                existing_by_code[code] = row

        await session.flush()
        return applications

    @staticmethod
    def _same_value(existing_json: str, incoming: object) -> bool:
        try:
            existing = json.loads(existing_json)
        except (json.JSONDecodeError, TypeError):
            return False
        return existing == incoming

    @staticmethod
    async def _record_conflict(
        session: AsyncSession,
        *,
        user_id: str,
        tag_code: str,
        existing: CustomerProfileTag,
        incoming: ExtractedProfileTag,
        source_type: str,
        extraction_method: str,
        status: str,
        resolution: str | None,
        resolved_by: str | None,
        now: datetime,
        trace_id: str,
        requires_confirmation: bool = False,
    ) -> str:
        """写入 customer_profile_tag_conflict 审计记录，返回 conflict_id。"""
        conflict = CustomerProfileTagConflict(
            id=str(uuid4()),
            user_id=user_id,
            tag_code=tag_code,
            left_value_json=existing.tag_value_json,
            right_value_json=json.dumps(
                incoming.tag_value, ensure_ascii=False, default=str
            ),
            left_source=existing.source_type,
            right_source=source_type,
            left_method=existing.extraction_method,
            right_method=extraction_method,
            left_confidence=_effective_confidence(
                existing.source_type,
                existing.extraction_method,
                existing.confidence,
            ),
            right_confidence=_effective_confidence(
                source_type, extraction_method, incoming.confidence
            ),
            status=status,
            resolution=resolution,
            resolved_by=resolved_by,
            trace_id=trace_id,
            detected_at=now,
            resolved_at=now if status == "RESOLVED" else None,
            requires_customer_confirmation=requires_confirmation,
        )
        session.add(conflict)
        return conflict.id


class TagConflictService:
    """标签冲突查询与人工解析（F2.1 冲突审计）。"""

    async def list_conflicts(
        self, session: AsyncSession, user_id: str, status: str | None = None
    ) -> list[TagConflictView]:
        query = select(CustomerProfileTagConflict).where(
            CustomerProfileTagConflict.user_id == user_id
        )
        if status:
            query = query.where(CustomerProfileTagConflict.status == status)
        rows = list(
            (
                await session.execute(
                    query.order_by(CustomerProfileTagConflict.detected_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return [self._to_view(row) for row in rows]

    async def resolve_conflict(
        self,
        session: AsyncSession,
        conflict_id: str,
        *,
        user_id: str,
        selected_side: str,
        resolution_note: str = "",
    ) -> TagConflictView:
        """人工解析 OPEN 冲突：选定一侧值写入生效标签，并关闭冲突记录。"""
        row = (
            await session.execute(
                select(CustomerProfileTagConflict).where(
                    CustomerProfileTagConflict.id == conflict_id,
                    CustomerProfileTagConflict.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise ValueError("tag conflict not found")
        if row.status != "OPEN":
            return self._to_view(row)

        side = selected_side.lower()
        if side not in {"left", "right"}:
            raise ValueError("selected_side must be 'left' or 'right'")
        winner_value = row.left_value_json if side == "left" else row.right_value_json
        winner_source = row.left_source if side == "left" else row.right_source
        winner_method = row.left_method if side == "left" else row.right_method
        winner_confidence = _effective_confidence(
            winner_source,
            winner_method,
            row.left_confidence if side == "left" else row.right_confidence,
        )

        tag = (
            await session.execute(
                select(CustomerProfileTag).where(
                    CustomerProfileTag.user_id == user_id,
                    CustomerProfileTag.tag_code == row.tag_code,
                )
            )
        ).scalar_one_or_none()
        if tag is not None:
            tag.tag_value_json = winner_value
            tag.source_type = winner_source
            tag.extraction_method = winner_method
            tag.confidence = winner_confidence
            tag.status = "ACTIVE"
            tag.updated_at = utcnow()

        row.status = "RESOLVED"
        row.resolution = f"MANUAL_{side.upper()}" + (
            f": {resolution_note}" if resolution_note else ""
        )
        row.resolved_by = user_id
        row.resolved_at = utcnow()
        row.requires_customer_confirmation = False
        return self._to_view(row)

    @staticmethod
    def _to_view(row: CustomerProfileTagConflict) -> TagConflictView:
        return TagConflictView(
            conflict_id=row.id,
            customer_id=row.user_id,
            tag_code=row.tag_code,
            left_value=_load_json(row.left_value_json),
            right_value=_load_json(row.right_value_json),
            left_source=row.left_source,
            right_source=row.right_source,
            left_method=row.left_method,
            right_method=row.right_method,
            left_confidence=_effective_confidence(
                row.left_source, row.left_method, row.left_confidence
            ),
            right_confidence=_effective_confidence(
                row.right_source, row.right_method, row.right_confidence
            ),
            status=row.status,
            resolution=row.resolution,
            detected_at=row.detected_at,
            resolved_at=row.resolved_at,
        )


def _load_json(value: str) -> object:
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


class TagQueryService:
    """Read profile tags for a user, optionally projected onto profile fields."""

    async def list_tags(self, session: AsyncSession, user_id: str) -> list[dict]:
        rows = list(
            (
                await session.execute(
                    select(CustomerProfileTag)
                    .where(CustomerProfileTag.user_id == user_id)
                    .order_by(CustomerProfileTag.effective_at.desc())
                )
            )
            .scalars()
            .all()
        )
        out = []
        for row in rows:
            try:
                value = json.loads(row.tag_value_json)
            except (json.JSONDecodeError, TypeError):
                value = row.tag_value_json
            out.append(
                {
                    "tag_code": row.tag_code,
                    "value": value,
                    "confidence": _effective_confidence(
                        row.source_type, row.extraction_method, row.confidence
                    ),
                    "source_type": row.source_type,
                    "extraction_method": row.extraction_method,
                    "status": row.status,
                    "evidence_quote": row.evidence_quote,
                    "effective_at": row.effective_at.isoformat()
                    if row.effective_at
                    else None,
                }
            )
        return out

    async def get_effective_value(
        self, session: AsyncSession, user_id: str, tag_code: str
    ) -> object | None:
        row = (
            (
                await session.execute(
                    select(CustomerProfileTag)
                    .where(
                        CustomerProfileTag.user_id == user_id,
                        CustomerProfileTag.tag_code == tag_code,
                        CustomerProfileTag.status == "ACTIVE",
                    )
                    .order_by(CustomerProfileTag.effective_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            return None
        try:
            return json.loads(row.tag_value_json)
        except (json.JSONDecodeError, TypeError):
            return row.tag_value_json
