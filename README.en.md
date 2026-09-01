# Intelligent Wealth Manager

A full-stack AI Agent system for wealth-management workflows. It combines five domain agents (customer service, investment advisory, risk monitoring, data analysis, and business operations) behind a role-aware Supervisor router, with RAG, GraphRAG, guarded NL2SQL, customer profiling, memory, confirmation workflows, and cross-agent risk events.

> This repository is a research and demonstration project, not a production financial system. Live requests currently use deterministic Supervisor routing. The LangGraph prototype in the repository contains a single Supervisor node and is not yet wired as an autonomous planning loop.

**Highlights**

- 🔀 **Deterministic Supervisor routing**: five domain agents behind one role-aware router; customer/employee boundaries, ambiguity handling, and privilege fallback are fully rule-based and covered by 20 routing regression cases
- 🛡️ **Guarded NL2SQL**: read-only validation, injection blocking (DROP/UPDATE/DELETE/SELECT INTO/FOR UPDATE), table whitelist, mandatory LIMIT capping
- 🔐 **Three-layer protection for high-risk operations**: intent × role permission matrix, tiered confirmation thresholds (retail 10K/50K, private 100K/500K), idempotency keys
- 📉 **Graceful degradation**: unconfigured Neo4j / model keys are reported as `skipped`, GraphRAG falls back to plain RAG without blocking the main flow
- 📊 **Reproducible engineering**: 106 tests across unit/integration/e2e, 100 deterministic offline eval cases, GitHub Actions CI, trace-ID observability

See the complete [Chinese README](README.md), [demo accounts](docs/DEMO_ACCOUNTS.md), and [data analyst design](docs/数据分析Agent需求分析.md).

## Stack

- Python 3.12, FastAPI, Pydantic, SQLAlchemy Async, Alembic
- LangChain, LangGraph, Qwen/DashScope
- PostgreSQL 16, pgvector, Redis 7, optional Neo4j
- Vue 3, TypeScript, Vite
- pytest and Docker Compose

## Quick start on Windows

Requirements: PowerShell 5.1+, Python 3.12+, Node.js 20+, and Docker Desktop.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_dev.ps1
```

Then open:

- Frontend: <http://127.0.0.1:5173>
- API docs: <http://127.0.0.1:8000/docs>

Stop all services:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\stop_dev.ps1
```

Copy `.env.example` to `.env` and set `DASHSCOPE_API_KEY` to enable model-backed responses. Local rules and templates provide partial degradation when the model is unavailable.

## Tests

Tests are layered by dependency and selected with pytest markers (see `pyproject.toml`):

| Layer | marker | Dependencies |
| --- | --- | --- |
| Unit | `unit` | none (Mock/Fake) |
| Integration | `integration` | PostgreSQL + Redis via Docker Compose |
| E2E smoke | `e2e` | full HTTP service (`test_http_*.py` run as scripts) |

```powershell
.\venv\Scripts\python.exe -m pytest -m unit -q          # no external services
.\venv\Scripts\python.exe -m pytest -m integration -q   # after docker compose up
.\\venv\\Scripts\\python.exe eval\\run_eval.py              # Agent offline eval (100 cases)
```

Integration tests auto-skip when PostgreSQL/Redis are unreachable, so the default `pytest` run is stable even without Docker. CI (`.github/workflows/ci.yml`) runs Ruff, unit tests, integration tests, Agent eval, and the frontend build.

## License

[MIT](LICENSE)
