from fastapi import APIRouter, Request

from app.common.middleware.trace import get_trace_id
from app.common.response import ApiResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=ApiResponse[dict])
async def health(request: Request) -> ApiResponse[dict]:
    checks = await request.app.state.health_service.check_all()
    status = "ok" if all(item["status"] in {"ok", "configured", "skipped"} for item in checks.values()) else "degraded"
    return ApiResponse(data={"status": status, "checks": checks}, trace_id=get_trace_id())
