from app.core.settings import Settings
from app.db.health import DatabaseHealth
from app.infrastructure.knowledge_graph import KnowledgeGraphService
from app.infrastructure.qwen import QwenProvider
from app.infrastructure.redis_client import RedisClient


class HealthService:
    def __init__(
        self,
        database_health: DatabaseHealth,
        redis_client: RedisClient,
        qwen: QwenProvider,
        graph: KnowledgeGraphService,
        settings: Settings,
    ) -> None:
        self.database_health = database_health
        self.redis = redis_client
        self.qwen = qwen
        self.graph = graph
        self.settings = settings

    async def check_all(self) -> dict[str, dict[str, str]]:
        return {
            "postgresql": await self.database_health.check(),
            "redis": await self.redis.check(),
            "qwen": await self.qwen.check_config(),
            "embedding": await self.qwen.check_embedding(),
            "neo4j": await self.graph.check(),
        }
