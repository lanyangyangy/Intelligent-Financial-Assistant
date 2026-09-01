from fastapi import APIRouter

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.chat_stream import router as chat_stream_router
from app.api.graph import router as graph_router
from app.api.health import router as health_router
from app.api.knowledge import router as knowledge_router
from app.api.profile import router as profile_router
from app.api.profile_console import router as profile_console_router
from app.api.profile_enhanced import router as profile_enhanced_router
from app.api.risk import router as risk_router
from app.api.tasks import router as tasks_router
from app.api.trading import router as trading_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(tasks_router)
api_router.include_router(profile_router)
api_router.include_router(profile_enhanced_router)
api_router.include_router(profile_console_router)
api_router.include_router(knowledge_router)
api_router.include_router(risk_router)
api_router.include_router(trading_router)
api_router.include_router(admin_router)
api_router.include_router(chat_router)
api_router.include_router(chat_stream_router)
api_router.include_router(graph_router)
