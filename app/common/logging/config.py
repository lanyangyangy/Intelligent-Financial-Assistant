from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from app.common.logging.context import get_log_context


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        context = get_log_context()
        record.trace_id = context.get("trace_id") or "-"
        record.path = context.get("path", "-")
        record.method = context.get("method", "-")
        record.component = getattr(record, "component", record.name)
        return True


def configure_logging(level: str, *, log_dir: str = "logs") -> None:
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | trace=%(trace_id)s | "
        "component=%(component)s | %(method)s %(path)s | %(name)s | %(message)s"
    )
    context_filter = ContextFilter()
    console = logging.StreamHandler()
    console.addFilter(context_filter)
    console.setFormatter(formatter)
    app_file = TimedRotatingFileHandler(log_path / "app.log", when="midnight", interval=1, backupCount=14, encoding="utf-8")
    app_file.addFilter(context_filter)
    app_file.setFormatter(formatter)
    error_file = TimedRotatingFileHandler(log_path / "error.log", when="midnight", interval=1, backupCount=30, encoding="utf-8")
    error_file.setLevel(logging.ERROR)
    error_file.addFilter(context_filter)
    error_file.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(console)
    root.addHandler(app_file)
    root.addHandler(error_file)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
