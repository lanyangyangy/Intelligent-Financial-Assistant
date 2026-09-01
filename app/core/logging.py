from app.common.logging import configure_logging


def configure_legacy_logging(level: str) -> None:
    configure_logging(level)
