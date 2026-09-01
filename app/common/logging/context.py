from contextvars import ContextVar

_log_context: ContextVar[dict[str, str]] = ContextVar("log_context", default={})


def set_log_context(**values: str) -> None:
    current = dict(_log_context.get())
    current.update({key: value for key, value in values.items() if value})
    _log_context.set(current)


def get_log_context() -> dict[str, str]:
    return dict(_log_context.get())
