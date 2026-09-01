from contextvars import ContextVar
from time import perf_counter
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.common.logging import set_log_context
from app.common.logging.config import get_logger

trace_id_context: ContextVar[str] = ContextVar("trace_id", default="")
logger = get_logger(__name__)

def get_trace_id() -> str:
    return trace_id_context.get()


class TraceIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        trace_id = request.headers.get("X-Trace-Id") or str(uuid4())
        token = trace_id_context.set(trace_id)
        set_log_context(trace_id=trace_id, path=request.url.path, method=request.method)
        started = perf_counter()
        try:
            response = await call_next(request)
            response.headers["X-Trace-Id"] = trace_id
            logger.info("http_request_completed status=%s duration_ms=%.2f", response.status_code, (perf_counter() - started) * 1000)
            return response
        except Exception:
            logger.exception("http_request_failed duration_ms=%.2f", (perf_counter() - started) * 1000)
            raise
        finally:
            trace_id_context.reset(token)
