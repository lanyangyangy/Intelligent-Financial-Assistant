"""P0 dependency and framework checks."""

from __future__ import annotations

import importlib.metadata as metadata

REQUIRED = [
    "fastapi",
    "SQLAlchemy",
    "asyncpg",
    "redis",
    "pgvector",
    "PyJWT",
    "langchain",
    "langchain-core",
    "langgraph",
]


def main() -> int:
    errors: list[str] = []
    for package in REQUIRED:
        try:
            print(f"{package}=={metadata.version(package)}")
        except metadata.PackageNotFoundError:
            errors.append(f"{package}: missing")
    try:
        from langchain_core.tools import tool  # noqa: F401
        from langgraph.graph import END, START, StateGraph  # noqa: F401
        print("langchain/langgraph imports: ok")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"framework import failed: {type(exc).__name__}: {exc}")
    if errors:
        print("P0 dependency check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("P0 dependency check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
