from fastapi import APIRouter, Depends, Request

from app.common.exceptions import ResourceNotFoundError
from app.common.middleware.trace import get_trace_id
from app.common.response import ApiResponse
from app.common.security.auth import require_permission
from app.schemas.tasks import TaskCreateRequest, TaskResponse

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("", response_model=ApiResponse[TaskResponse], status_code=202)
async def create_task(request: Request, payload: TaskCreateRequest, _user=Depends(require_permission("order:write"))) -> ApiResponse[TaskResponse]:
    task = await request.app.state.task_service.create_task(payload)
    return ApiResponse(data=task, trace_id=get_trace_id())


@router.get("/{task_id}", response_model=ApiResponse[TaskResponse])
async def get_task(request: Request, task_id: str, _user=Depends(require_permission("order:read"))) -> ApiResponse[TaskResponse]:
    task = await request.app.state.task_service.get_task(task_id)
    if task is None:
        raise ResourceNotFoundError("task not found", code="TASK_NOT_FOUND")
    return ApiResponse(data=task, trace_id=get_trace_id())
