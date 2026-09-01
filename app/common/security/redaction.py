from app.common.middleware.trace import get_trace_id


def redact(value: str | None, *, keep_start: int = 3, keep_end: int = 4) -> str | None:
    if value is None:
        return None
    if len(value) <= keep_start + keep_end:
        return "*" * len(value)
    return value[:keep_start] + "*" * (len(value) - keep_start - keep_end) + value[-keep_end:]


def audit_context() -> dict[str, str]:
    return {"trace_id": get_trace_id()}
