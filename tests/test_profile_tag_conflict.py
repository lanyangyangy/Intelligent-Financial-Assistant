"""F2.1 标签冲突治理 + 审计落库 单元测试。

覆盖场景：
  1. 创建标签（无旧值）→ CREATED
  2. 相同来源新数据覆盖旧数据 → UPDATED_SAME_SOURCE + 冲突审计 RESOLVED
  3. 来源优先级高的覆盖低的（AI > 用户自述）→ REPLACED_LOWER_PRIORITY + 审计
  4. 来源优先级低的被忽略 → IGNORED_LOWER_PRIORITY + 审计
  5. 相同优先级不同值 → NEEDS_REVIEW + OPEN 冲突待人工复核
  6. 冲突人工解析 → 选定值写入生效标签并关闭记录
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from app.models.profile import CustomerProfileTag, CustomerProfileTagConflict
from app.profile_domain.conversation_extractors import RuleDemoConversationExtractor
from app.profile_domain.tag_governance import ExtractedProfileTag, ProfileTagCode
from app.services.profile_tag_service import (
    TagConflictService,
    TagGovernanceService,
    TagQueryService,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _tag(
    code: ProfileTagCode, value: object, confidence: str, quote: str
) -> ExtractedProfileTag:
    return ExtractedProfileTag(
        tag_code=code,
        tag_value=value,
        confidence=Decimal(confidence),
        evidence_quote=quote,
    )


def _existing(
    code: str,
    value: str,
    *,
    source: str,
    method: str,
    confidence: float = 0.4,
    status: str = "ACTIVE",
) -> CustomerProfileTag:
    import json

    return CustomerProfileTag(
        id=str(uuid4()),
        user_id="customer-1",
        tag_code=code,
        tag_value_json=json.dumps(value, ensure_ascii=False),
        confidence=confidence,
        source_type=source,
        extraction_method=method,
        status=status,
        evidence_quote="old evidence",
        effective_at=_now(),
        updated_at=_now(),
    )


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class FakeSession:
    """极简 AsyncSession 桩：execute 返回注入行，add/flush 记录行为。"""

    def __init__(self, rows=None):
        self.rows = rows or []
        self.added: list[object] = []
        self.flushed = False

    async def execute(self, _stmt):
        return FakeResult(self.rows)

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flushed = True


def _run(coro):
    return asyncio.run(coro)


def test_create_tag_when_no_existing():
    session = FakeSession()
    apps = _run(
        TagGovernanceService().apply_tags(
            session,
            "customer-1",
            [_tag(ProfileTagCode.OCCUPATION, "engineer", "0.6", "我是工程师")],
            source_type="USER_STATED",
            extraction_method="AI",
        )
    )
    assert apps[0]["decision"] == "CREATED"
    assert len(session.added) == 1  # 只有标签，无冲突记录
    assert session.flushed


def test_source_initial_confidence_is_enforced_at_persistence_boundary():
    session = FakeSession()
    _run(
        TagGovernanceService().apply_tags(
            session,
            "customer-1",
            [_tag(ProfileTagCode.OCCUPATION, "engineer", "0.99", "我是工程师")],
            source_type="USER_STATED",
            extraction_method="AI",
        )
    )
    assert session.added[0].confidence == 0.6

    questionnaire_session = FakeSession()
    _run(
        TagGovernanceService().apply_tags(
            questionnaire_session,
            "customer-1",
            [_tag(ProfileTagCode.OCCUPATION, "doctor", "0.2", "问卷：医生")],
            source_type="QUESTIONNAIRE",
            extraction_method="DIRECT",
        )
    )
    assert questionnaire_session.added[0].confidence == 0.9

    system_session = FakeSession()
    _run(
        TagGovernanceService().apply_tags(
            system_session,
            "customer-1",
            [_tag(ProfileTagCode.INVESTMENT_GOAL, "HIGH_RETURN", "0.95", "系统规则")],
            source_type="SYSTEM_BEHAVIOR",
            extraction_method="RULE",
        )
    )
    assert system_session.added[0].confidence == 0.2


def test_query_normalizes_legacy_tag_confidence_for_profile_cards():
    system_tag = _existing(
        "TOTAL_ASSETS",
        "800000",
        source="SYSTEM_BEHAVIOR",
        method="RULE",
        confidence=0.95,
    )
    ai_tag = _existing(
        "OCCUPATION",
        "engineer",
        source="USER_STATED",
        method="AI",
        confidence=0.4,
    )

    tags = _run(TagQueryService().list_tags(FakeSession([system_tag, ai_tag]), "customer-1"))
    by_code = {tag["tag_code"]: tag for tag in tags}

    assert by_code["TOTAL_ASSETS"]["confidence"] == 0.2
    assert by_code["OCCUPATION"]["confidence"] == 0.6


def test_conversation_extraction_writes_conflict_for_existing_questionnaire_value():
    async def run():
        extraction = await RuleDemoConversationExtractor().extract(
            "我的真实投资经验是1年。"
        )
        tag = next(
            tag
            for tag in extraction.tags
            if tag.tag_code is ProfileTagCode.INVESTMENT_EXPERIENCE_YEARS
        )
        session = FakeSession(
            [
                _existing(
                    "INVESTMENT_EXPERIENCE_YEARS",
                    "6",
                    source="QUESTIONNAIRE",
                    method="DIRECT",
                    confidence=0.9,
                )
            ]
        )
        applications = await TagGovernanceService().apply_tags(
            session,
            "customer-1",
            [tag],
            source_type="USER_STATED",
            extraction_method="RULE",
        )
        return tag, applications, session

    tag, applications, session = _run(run())
    assert tag.tag_value == 1.0
    assert applications[0]["decision"] == "IGNORED_LOWER_PRIORITY"
    assert applications[0]["confidence"] == 0.4
    assert applications[0]["conflict_id"]
    assert session.rows[0].tag_value_json == '"6"'


def test_same_source_new_value_overwrites_and_audits():
    now = _now()
    existing = _existing(
        "OCCUPATION", "engineer", source="USER_STATED", method="AI", confidence=0.4
    )
    session = FakeSession([existing])
    apps = _run(
        TagGovernanceService().apply_tags(
            session,
            "customer-1",
            [_tag(ProfileTagCode.OCCUPATION, "doctor", "0.6", "我其实是医生")],
            source_type="USER_STATED",
            extraction_method="AI",
            now=now,
        )
    )
    assert apps[0]["decision"] == "UPDATED_SAME_SOURCE"
    # 新数据覆盖旧数据
    import json

    assert json.loads(existing.tag_value_json) == "doctor"
    assert existing.confidence == 0.6
    # 冲突审计记录已落库
    conflicts = [
        obj for obj in session.added if isinstance(obj, CustomerProfileTagConflict)
    ]
    assert len(conflicts) == 1
    assert conflicts[0].status == "RESOLVED"
    assert conflicts[0].resolution == "AUTO_SAME_SOURCE_OVERWRITTEN"
    assert json.loads(conflicts[0].left_value_json) == "engineer"
    assert json.loads(conflicts[0].right_value_json) == "doctor"
    assert apps[0]["conflict_id"] == conflicts[0].id


def test_higher_priority_source_replaces_lower():
    existing = _existing("OCCUPATION", "engineer", source="USER_STATED", method="AI")
    session = FakeSession([existing])
    apps = _run(
        TagGovernanceService().apply_tags(
            session,
            "customer-1",
            [_tag(ProfileTagCode.OCCUPATION, "civil_servant", "0.9", "问卷：公务员")],
            source_type="QUESTIONNAIRE",
            extraction_method="DIRECT",
        )
    )
    assert apps[0]["decision"] == "REPLACED_LOWER_PRIORITY"
    import json

    assert json.loads(existing.tag_value_json) == "civil_servant"
    assert existing.source_type == "QUESTIONNAIRE"
    conflicts = [
        obj for obj in session.added if isinstance(obj, CustomerProfileTagConflict)
    ]
    assert conflicts[0].resolution == "AUTO_HIGHER_PRIORITY_REPLACED"
    assert conflicts[0].status == "RESOLVED"
    assert conflicts[0].left_confidence == 0.6
    assert conflicts[0].right_confidence == 0.9


def test_ai_source_replaces_system_derived_tag():
    existing = _existing(
        "INVESTMENT_EXPERIENCE_YEARS",
        "12",
        source="SYSTEM_BEHAVIOR",
        method="RULE",
        confidence=0.2,
    )
    session = FakeSession([existing])
    apps = _run(
        TagGovernanceService().apply_tags(
            session,
            "customer-1",
            [
                _tag(
                    ProfileTagCode.INVESTMENT_EXPERIENCE_YEARS,
                    2,
                    "0.6",
                    "我的真实投资经验是2年",
                )
            ],
            source_type="USER_STATED",
            extraction_method="AI",
        )
    )
    assert apps[0]["decision"] == "REPLACED_LOWER_PRIORITY"
    import json

    assert json.loads(existing.tag_value_json) == 2
    assert existing.source_type == "USER_STATED"
    assert existing.extraction_method == "AI"
    assert existing.confidence == 0.6


def test_lower_priority_source_ignored_but_audited():
    existing = _existing(
        "OCCUPATION", "civil_servant", source="QUESTIONNAIRE", method="DIRECT"
    )
    session = FakeSession([existing])
    apps = _run(
        TagGovernanceService().apply_tags(
            session,
            "customer-1",
            [_tag(ProfileTagCode.OCCUPATION, "engineer", "0.6", "对话里说工程师")],
            source_type="USER_STATED",
            extraction_method="AI",
        )
    )
    assert apps[0]["decision"] == "IGNORED_LOWER_PRIORITY"
    import json

    # 旧值保持不变
    assert json.loads(existing.tag_value_json) == "civil_servant"
    conflicts = [
        obj for obj in session.added if isinstance(obj, CustomerProfileTagConflict)
    ]
    assert conflicts[0].resolution == "AUTO_LOWER_PRIORITY_IGNORED"
    assert conflicts[0].status == "RESOLVED"


def test_equal_priority_conflict_open_for_review():
    existing = _existing(
        "OCCUPATION", "engineer", source="USER_STATED", method="RULE", confidence=0.4
    )
    session = FakeSession([existing])
    apps = _run(
        TagGovernanceService().apply_tags(
            session,
            "customer-1",
            [_tag(ProfileTagCode.OCCUPATION, "doctor", "0.6", "手动录入：医生")],
            source_type="USER_STATED",
            extraction_method="MANUAL",
        )
    )
    assert apps[0]["decision"] == "NEEDS_REVIEW"
    assert existing.status == "NEEDS_REVIEW"
    conflicts = [
        obj for obj in session.added if isinstance(obj, CustomerProfileTagConflict)
    ]
    assert conflicts[0].status == "OPEN"
    assert conflicts[0].requires_customer_confirmation is True
    assert conflicts[0].resolution is None


def test_resolve_conflict_promotes_selected_side():
    conflict = CustomerProfileTagConflict(
        id=str(uuid4()),
        user_id="customer-1",
        tag_code="OCCUPATION",
        left_value_json='"engineer"',
        right_value_json='"doctor"',
        left_source="USER_STATED",
        right_source="USER_STATED",
        left_method="RULE",
        right_method="MANUAL",
        left_confidence=0.4,
        right_confidence=0.6,
        status="OPEN",
        resolution=None,
        resolved_by=None,
        trace_id="trace-1",
        detected_at=_now(),
        resolved_at=None,
        requires_customer_confirmation=True,
    )
    tag = _existing("OCCUPATION", "engineer", source="USER_STATED", method="RULE")

    class ResolveSession(FakeSession):
        async def execute(self, stmt):
            if "customer_profile_tag_conflict" in str(stmt):
                return FakeResult([conflict])
            return FakeResult([tag])

    view = _run(
        TagConflictService().resolve_conflict(
            ResolveSession(),
            conflict.id,
            user_id="customer-1",
            selected_side="right",
            resolution_note="客户确认为医生",
        )
    )
    assert view.status == "RESOLVED"
    assert "MANUAL_RIGHT" in view.resolution
    import json

    assert json.loads(tag.tag_value_json) == "doctor"
    assert tag.source_type == "USER_STATED"
    assert tag.status == "ACTIVE"
