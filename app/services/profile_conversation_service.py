from __future__ import annotations

from app.models.profile import CustomerProfile
from app.profile_domain.conversation_extractors import create_conversation_extractor
from app.services.profile_cache_service import ProfileCacheService
from app.services.profile_calculation_service import ProfileCalculationService
from app.services.profile_tag_service import TagGovernanceService


class ProfileConversationService:
    """将用户对话抽取为画像标签，并统一走标签治理与画像重算。"""

    def __init__(self, database, settings, qwen, redis=None) -> None:
        self.database = database
        self.settings = settings
        self.qwen = qwen
        self.redis = redis

    async def extract_and_apply(self, user_id: str, conversation_text: str) -> dict:
        extractor = create_conversation_extractor(self.settings, self.qwen)
        extraction = await extractor.extract(conversation_text)
        extraction_method = (
            "AI"
            if extraction.extraction_mode.value == "OPENAI_COMPATIBLE"
            else "RULE"
        )

        async with self.database.session_factory() as session:
            applications = await TagGovernanceService().apply_tags(
                session,
                user_id,
                extraction.tags,
                source_type="USER_STATED",
                extraction_method=extraction_method,
            )
            try:
                await ProfileCalculationService().calculate(session, user_id)
            except ValueError:
                # 新注册用户可能还没有完整基础资料；标签仍应先落库。
                pass
            profile = await _load_profile(session, user_id)
            await session.commit()

        if self.redis is not None:
            await ProfileCacheService(self.redis).invalidate(user_id)

        application_by_code = {item["tag_code"]: item for item in applications}
        tags = []
        for tag in extraction.tags:
            item = application_by_code.get(tag.tag_code.value, {})
            tags.append(
                {
                    **tag.model_dump(mode="json"),
                    "confidence": item.get("confidence", float(tag.confidence)),
                }
            )

        conflict_ids = [
            item["conflict_id"]
            for item in applications
            if item.get("conflict_id")
        ]
        return {
            "summary": extraction.summary,
            "extraction_mode": extraction.extraction_mode.value,
            "model_name": extraction.model_name,
            "prompt_version": extraction.prompt_version,
            "tags": tags,
            "applications": applications,
            "conflict_ids": conflict_ids,
            "profile_status": profile.profile_status if profile else None,
        }


async def _load_profile(session, user_id: str) -> CustomerProfile | None:
    from sqlalchemy import select

    return (
        await session.execute(
            select(CustomerProfile).where(CustomerProfile.user_id == user_id)
        )
    ).scalar_one_or_none()
