from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select

from app.core.settings import Settings
from app.db.session import Database
from app.models.outbox import OutboxEvent
from app.models.task import AsyncTask
from app.schemas.tasks import TaskCreateRequest, TaskResponse


class TaskService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    async def create_task(self, request: TaskCreateRequest) -> TaskResponse:
        task_id = str(uuid4())
        outbox_id = str(uuid4())
        async with self.database.session_factory() as session:
            if request.idempotency_key:
                result = await session.execute(select(AsyncTask).where(AsyncTask.idempotency_key == request.idempotency_key))
                existing = result.scalar_one_or_none()
                if existing is not None:
                    return self._response(existing)
            task = AsyncTask(
                id=task_id,
                task_type=request.task_type,
                aggregate_type=request.aggregate_type,
                aggregate_id=request.aggregate_id if request.aggregate_id else None,
                idempotency_key=request.idempotency_key,
                status="queued",
                max_attempts=self.settings.task_max_attempts,
                payload_json=request.payload,
            )
            event = OutboxEvent(
                id=outbox_id,
                event_type="task.created",
                aggregate_type=request.aggregate_type,
                aggregate_id=request.aggregate_id if request.aggregate_id else None,
                payload_json={
                    "task_id": task_id,
                    "task_type": request.task_type,
                    "payload": request.payload,
                },
                status="pending",
            )
            session.add_all([task, event])
            await session.commit()
            await session.refresh(task)
            return self._response(task)

    async def get_task(self, task_id: str) -> TaskResponse | None:
        try:
            UUID(task_id)
        except ValueError:
            return None
        async with self.database.session_factory() as session:
            result = await session.execute(select(AsyncTask).where(AsyncTask.id == task_id))
            task = result.scalar_one_or_none()
            return self._response(task) if task else None

    @staticmethod
    def _response(task: AsyncTask) -> TaskResponse:
        created_at = task.created_at or datetime.now(UTC)
        return TaskResponse(
            id=str(task.id),
            task_type=task.task_type,
            status=task.status,
            attempt=task.attempt,
            max_attempts=task.max_attempts,
            created_at=created_at,
        )
