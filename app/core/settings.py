from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "wealth-manager"
    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    log_level: str = "INFO"

    database_url: str = (
        "postgresql+asyncpg://wealth:wealth_dev@127.0.0.1:5433/wealth_manager"
    )
    redis_url: str = "redis://127.0.0.1:6380/0"
    redis_stream_prefix: str = "stream:wealth"
    redis_consumer_group: str = "wealth-workers"
    redis_consumer_name: str = "worker-1"
    database_backend: str = "postgresql"
    vector_store_backend: str = "pgvector"
    agent_backend: str = "local"
    milvus_uri: str = "http://127.0.0.1:19530"
    milvus_collection: str = "wealth_documents"
    mysql_url: str = "mysql+aiomysql://wealth:wealth_dev@127.0.0.1:3306/wealth_manager"
    # ---- Neo4j 知识图谱（Phase 3 F3.1）----
    neo4j_uri: str = "bolt://127.0.0.1:17687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "password123"
    neo4j_enabled: bool = False

    dashscope_api_key: str = Field(default="", repr=False)
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_chat_model: str = "qwen-plus"
    deepseek_api_key: str = Field(default="", repr=False)
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_chat_model: str = "deepseek-v4-flash"
    deepseek_thinking_enabled: bool = False
    model_router_default: str = "qwen"
    qwen_embedding_model: str = "qwen3.7-text-embedding"
    embedding_dimension: int = 1024
    embedding_smoke_check: bool = False
    hybrid_candidate_k: int = 80
    hybrid_bm25_pool_size: int = 2000
    hybrid_vector_weight: float = 1.0
    hybrid_keyword_weight: float = 0.8
    hybrid_bm25_weight: float = 1.2
    hybrid_exact_weight: float = 2.5
    hybrid_field_intent_weight: float = 0.004
    hybrid_entity_window_weight: float = 0.006
    bge_reranker_enabled: bool = False
    bge_reranker_model: str = "BAAI/bge-reranker-base"
    bge_reranker_top_n: int = 30
    bge_reranker_allow_download: bool = False

    jwt_secret: str = Field(
        default="change-me-in-development-please-rotate-32", repr=False
    )
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440
    refresh_token_expire_days: int = 30
    demo_accounts_enabled: bool = True
    employee_registration_code: str = Field(default="WEALTH-EMPLOYEE-DEV", repr=False)
    memory_sync_timeout_ms: int = 2500
    task_max_attempts: int = 3
    task_block_ms: int = 5000
    profile_console_base_url: str = "http://127.0.0.1:8003"
    profile_console_bff_url: str = "http://127.0.0.1:8000/api/v1/profile-console"
    profile_console_bridge_key: str = Field(default="", repr=False)

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.profile-console.local"),
        env_file_encoding="utf-8",
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
    )

    def validate_p0(self) -> None:
        if self.app_env in {"production", "prod"} and self.demo_accounts_enabled:
            raise ValueError("DEMO_ACCOUNTS_ENABLED must be false in production")
        if self.embedding_dimension != 1024:
            raise ValueError("EMBEDDING_DIMENSION must be 1024")
        if self.model_router_default not in {"qwen", "deepseek"}:
            raise ValueError("MODEL_ROUTER_DEFAULT must be 'qwen' or 'deepseek'")
        if self.memory_sync_timeout_ms <= 0:
            raise ValueError("MEMORY_SYNC_TIMEOUT_MS must be positive")
        if self.task_max_attempts <= 0:
            raise ValueError("TASK_MAX_ATTEMPTS must be positive")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_p0()
    return settings
