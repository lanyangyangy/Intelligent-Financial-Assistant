import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.agents import AGENT_CLASSES
from app.agents.advisor_agent import AdvisorAgent
from app.agents.analytics_agent import DataAnalystAgent
from app.agents.graph import route_message
from app.agents.operations_agent import BusinessOperatorAgent
from app.agents.orchestrator import AgentOrchestrator
from app.api import api_router
from app.common.exceptions import (
    AppException,
    app_exception_handler,
    validation_exception_handler,
)
from app.common.logging import configure_logging
from app.common.logging.config import get_logger
from app.common.middleware.trace import TraceIdMiddleware
from app.core.settings import Settings, get_settings
from app.db.health import DatabaseHealth
from app.db.schema import ensure_schema
from app.db.session import Database
from app.infrastructure.deepseek import DeepSeekProvider
from app.infrastructure.knowledge_graph import KnowledgeGraphService
from app.infrastructure.model_router import ModelRouter
from app.infrastructure.qwen import QwenProvider
from app.infrastructure.redis_client import RedisClient
from app.services.agent_event_service import CrossAgentEventSubscriber
from app.services.auth_seed import ensure_auth_seed
from app.services.auth_service import AuthService
from app.services.demo_customer_seed import ensure_demo_customer_profiles
from app.services.demo_product_seed import ensure_demo_products
from app.services.health_service import HealthService
from app.services.profile_seed import ensure_profile_seed
from app.services.task_service import TaskService
from app.services.trading_seed import ensure_trading_seed

logger = get_logger(__name__)


def create_model_router(
    settings: Settings,
) -> tuple[QwenProvider, DeepSeekProvider, ModelRouter]:
    qwen = QwenProvider(settings)
    deepseek = DeepSeekProvider(settings)
    router = ModelRouter(
        qwen=qwen,
        deepseek=deepseek,
        default_provider=settings.model_router_default,
    )
    return qwen, deepseek, router


async def close_application_resources(
    event_subscriber,
    graph,
    redis_client,
    model_router,
    database,
) -> None:
    close_actions = (
        ("event_subscriber", event_subscriber.stop),
        ("knowledge_graph", graph.close),
        ("redis", redis_client.close),
        ("model_router", model_router.close),
        ("database", database.dispose),
    )
    for resource_name, close in close_actions:
        try:
            await close()
        except Exception:  # noqa: BLE001 - all resources must receive a close attempt
            logger.exception("application_resource_close_failed resource=%s", resource_name)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    database = Database(settings)
    redis_client = RedisClient(settings)
    await ensure_schema(database.engine)
    await ensure_auth_seed(database, settings)
    await ensure_profile_seed(database)
    await ensure_trading_seed(database)
    await ensure_demo_products(database)
    await ensure_demo_customer_profiles(database)

    # 自动种子数据：空库时自动灌入全部演示数据（AUTO_SEED=1 强制 / =0 禁用）
    try:
        from app.services.auto_seed import run_auto_seed

        await run_auto_seed(database, settings)
    except Exception:  # noqa: BLE001 - seed 失败不阻断 API 启动
        logger.exception("auto_seed_failed")

    app.state.settings = settings
    app.state.database = database
    app.state.redis = redis_client
    app.state.task_service = TaskService(database, settings)
    app.state.auth_service = AuthService(database, settings)
    # Neo4j 知识图谱（Phase 3）——不可用时自动降级为纯 RAG
    graph = KnowledgeGraphService(settings)
    if settings.neo4j_enabled:
        await graph.connect()
    app.state.knowledge_graph = graph
    # five domain agents wired into the orchestrator + supervisor router
    qwen, deepseek, model_router = create_model_router(settings)
    app.state.qwen = qwen
    app.state.deepseek = deepseek
    app.state.model_router = model_router
    app.state.health_service = HealthService(
        DatabaseHealth(database), redis_client, qwen, graph, settings
    )
    orchestrator = AgentOrchestrator()
    for agent_class in AGENT_CLASSES:
        kwargs: dict = {}
        if agent_class is AdvisorAgent:
            kwargs["knowledge_graph"] = graph
        if agent_class is BusinessOperatorAgent:
            kwargs["knowledge_graph"] = graph
        if agent_class is DataAnalystAgent:
            kwargs["cache"] = redis_client
        orchestrator.register(agent_class(database, settings, llm=model_router, **kwargs))
    app.state.agent_orchestrator = orchestrator
    app.state.supervisor_router = route_message
    event_subscriber = CrossAgentEventSubscriber(database, redis_client.client)
    app.state.cross_agent_event_subscriber = event_subscriber
    try:
        await event_subscriber.start()
    except Exception:  # noqa: BLE001 - Redis unavailable must not block API startup
        logger.exception("cross_agent_event_subscriber_start_failed")

    # Phase 4 F4.3 周期校准任务：每周重算所有 ACTIVE 标签置信度并归档
    # 过期/低置信标签（worker 自带 7 天循环，无需 APScheduler）。
    calibration_task: asyncio.Task | None = None
    try:
        from workers.confidence_calibration_worker import ConfidenceCalibrationWorker

        calibration_worker = ConfidenceCalibrationWorker(database)
        app.state.confidence_calibration_worker = calibration_worker
        # 启动时先跑一次（便于验证），随后每周自动校准
        calibration_task = asyncio.create_task(
            calibration_worker.run_forever(), name="confidence-calibration"
        )
    except Exception:  # noqa: BLE001 - 校准任务失败不阻断启动
        logger.exception("confidence_calibration_worker_start_failed")

    try:
        yield
    finally:
        if calibration_task is not None:
            calibration_task.cancel()
            with suppress(asyncio.CancelledError):
                await calibration_task
        await close_application_resources(
            event_subscriber,
            graph,
            redis_client,
            model_router,
            database,
        )


app = FastAPI(title="Wealth Manager API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:8003",
        "http://localhost:8003",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(TraceIdMiddleware)
app.add_exception_handler(AppException, app_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.include_router(api_router)
# F2.2 风险评估模块：按需求路径挂载 /api/risk/*
from app.api.risk import router as risk_router  # noqa: E402

app.include_router(risk_router, prefix="/api")
# F2.1 客户画像系统：按需求路径挂载 /api/profile/*（create/get/put/conflicts）
from app.api.profile_f21 import router as profile_f21_router  # noqa: E402

app.include_router(profile_f21_router, prefix="/api")
# 业务操作 Agent 结构化端点：/api/operation/*（需求 6.6 补齐）
from app.api.operation import router as operation_router  # noqa: E402

app.include_router(operation_router, prefix="/api")
