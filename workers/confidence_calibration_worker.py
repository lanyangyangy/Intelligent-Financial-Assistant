"""周期校准任务（Phase 4 F4.3）。

每周期执行：
  1. 全量重算客户画像标签置信度（BaseConfidenceCalcTool，按来源 + 时效衰减）
  2. 标记过期标签（valid_until 语义：风评过期 → 画像 EXPIRED，由
     profile_expiry_worker 负责，这里做标签级置信度校准）
  3. 输出校准统计

手动触发：POST /api/admin/recalculate-confidence（见 app/api/admin.py）。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from app.common.logging.config import get_logger
from app.core.settings import get_settings
from app.db.session import Database
from app.models.profile import CustomerProfileTag
from app.services.confidence_rank_service import BaseConfidenceCalcTool

logger = get_logger(__name__)

SOURCE_INITIAL_MAP = {
    "QUESTIONNAIRE": "风评问卷",
    "KYC": "AI对话提取",
    "SYSTEM_BEHAVIOR": "AI对话提取",
    "USER_STATED": "用户自述",
    "MANAGER_ENTERED": "用户自述",
    "DEFAULT": "默认值",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ConfidenceCalibrationWorker:
    """周期校准：重算所有 ACTIVE 标签置信度并回收过低的标签。"""

    def __init__(
        self, database: Database, interval_seconds: int = 7 * 24 * 3600
    ) -> None:
        self.database = database
        self.interval_seconds = interval_seconds
        self._stop = asyncio.Event()

    async def calibrate_once(self) -> dict:
        tool = BaseConfidenceCalcTool()
        now = utcnow()
        recalibrated = 0
        archived = 0
        async with self.database.session_factory() as session:
            rows = list(
                (
                    await session.execute(
                        select(CustomerProfileTag).where(
                            CustomerProfileTag.status == "ACTIVE"
                        )
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                source = SOURCE_INITIAL_MAP.get(row.source_type.upper(), "默认值")
                age_days = max(0, (now - (row.updated_at or row.effective_at)).days)
                freshness = max(0.0, 1.0 - age_days / 365.0 * 0.2)  # 年衰减 20%
                new_confidence = tool.calc(
                    source=source,
                    freshness_decay=freshness,
                    conflict_count=1 if row.status == "NEEDS_REVIEW" else 0,
                    evidence_count=1,
                )
                row.confidence = round(new_confidence, 4)
                row.updated_at = now
                recalibrated += 1
                if new_confidence < 0.3 and age_days > 180:
                    # 低置信 + 超龄 → 归档淘汰（F4.2 遗忘机制）
                    row.status = "ARCHIVED"
                    archived += 1
            await session.commit()
        return {
            "recalibrated": recalibrated,
            "archived": archived,
            "run_at": now.isoformat(),
        }

    async def run_forever(self) -> None:
        logger.info(
            "confidence calibration worker started interval=%ss",
            self.interval_seconds,
        )
        while not self._stop.is_set():
            try:
                result = await self.calibrate_once()
                logger.info(
                    "confidence calibration done recalibrated=%s archived=%s",
                    result["recalibrated"],
                    result["archived"],
                )
            except Exception:  # noqa: BLE001
                logger.exception("confidence calibration failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def stop(self) -> None:
        self._stop.set()


async def main() -> None:
    settings = get_settings()
    database = Database(settings)
    worker = ConfidenceCalibrationWorker(database)
    try:
        await worker.run_forever()
    finally:
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(main())
