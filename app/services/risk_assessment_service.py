import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.profile import CustomerRiskAssessment
from app.repositories.profile import SqlAlchemyRiskAssessmentRepository


class RiskAssessmentService:
    def __init__(self, repository=None): self.repository = repository or SqlAlchemyRiskAssessmentRepository()
    async def ensure_default(self, session: AsyncSession, user_id: str) -> CustomerRiskAssessment:
        existing = await self.repository.get_active(session, user_id)
        if existing is not None:
            return existing
        answers = {"source": "system_default", "note": "未完成正式测评，按最低风险等级保守处理"}
        item = CustomerRiskAssessment(id=str(uuid4()), user_id=user_id, risk_level="C1", score=0, answers_json=json.dumps(answers, ensure_ascii=False), status="provisional", source_type="system_default", assessed_at=datetime.now(UTC), expires_at=None)
        return await self.repository.save(session, item)

    def score(self, answers: dict) -> tuple[int, str, list[str]]:
        score = round((answers["max_loss_tolerance"] * 0.25) + (answers["risk_willingness"] * 0.25) + min(100, answers["investment_experience_years"] * 10) * 0.2 + min(100, answers["investment_horizon_years"] * 10) * 0.15 + {"low": 0, "medium": 50, "high": 100}[answers["liquidity_need"]] * 0.15)
        if answers["investment_goal"] == "capital_preservation": score -= 15
        elif answers["investment_goal"] in {"growth", "aggressive"}: score += 10
        score = max(0, min(100, int(score)))
        level = "C1" if score < 20 else "C2" if score < 40 else "C3" if score < 60 else "C4" if score < 80 else "C5"
        reasons = [f"问卷得分 {score}，对应风险等级 {level}"]
        return score, level, reasons

    async def assess(self, session: AsyncSession, user_id: str, answers: dict) -> CustomerRiskAssessment:
        score, level, _ = self.score(answers)
        await self.repository.supersede_active(session, user_id)
        item = CustomerRiskAssessment(id=str(uuid4()), user_id=user_id, risk_level=level, score=score, answers_json=json.dumps(answers, ensure_ascii=False), status="active", source_type="questionnaire", assessed_at=datetime.now(UTC), expires_at=datetime.now(UTC) + timedelta(days=365))
        return await self.repository.save(session, item)
