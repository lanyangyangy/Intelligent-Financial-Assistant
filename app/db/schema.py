from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.base import Base
from app.models.audit import AuditLog  # noqa: F401
from app.models.auth import Permission, RefreshSession, Role, User  # noqa: F401
from app.models.conversation import ConversationArchive  # noqa: F401
from app.models.knowledge import (  # noqa: F401
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentVersion,
)
from app.models.operator import OperatorRequestDedupe  # noqa: F401
from app.models.profile import (  # noqa: F401
    CustomerAssetSnapshot,
    CustomerEnterpriseVerification,
    CustomerHolding,
    CustomerProfile,
    CustomerProfileTag,
    CustomerProfileTagConflict,
    CustomerProfileVersion,
    CustomerRiskAssessment,
    CustomerSubjectiveProfile,
    Product,
    ProductSuitabilityRule,
    RiskRule,
)
from app.models.risk import RiskAlert, WorkOrder  # noqa: F401
from app.models.trading import Account, Order, OrderStatusHistory, Trade  # noqa: F401


async def ensure_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.run_sync(Base.metadata.create_all)
        statements = (
            "ALTER TABLE async_task ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "ALTER TABLE outbox_event ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "ALTER TABLE product ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ",
            "ALTER TABLE product ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "ALTER TABLE product ADD COLUMN IF NOT EXISTS target_customer_type VARCHAR(64) NOT NULL DEFAULT 'individual'",
            "ALTER TABLE product ADD COLUMN IF NOT EXISTS target_customer_tiers TEXT NOT NULL DEFAULT 'ordinary,gold,platinum,diamond,private_bank'",
            "ALTER TABLE product ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "ALTER TABLE customer_profile ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "ALTER TABLE customer_profile ADD COLUMN IF NOT EXISTS investment_horizon_years INTEGER",
            "ALTER TABLE customer_profile ADD COLUMN IF NOT EXISTS liquidity_preference VARCHAR(16) NOT NULL DEFAULT 'medium'",
            "ALTER TABLE customer_risk_assessment ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "ALTER TABLE customer_profile ADD COLUMN IF NOT EXISTS customer_tier VARCHAR(64) NOT NULL DEFAULT 'ordinary'",
            "ALTER TABLE customer_asset_snapshot ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "ALTER TABLE customer_holding ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "ALTER TABLE customer_holding ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "ALTER TABLE product_suitability_rule ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "ALTER TABLE product_suitability_rule ADD COLUMN IF NOT EXISTS minimum_customer_tier VARCHAR(64) NOT NULL DEFAULT 'ordinary'",
            "ALTER TABLE product_suitability_rule ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "ALTER TABLE risk_rule ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "ALTER TABLE risk_rule ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(128)",
            # ---- 画像增强列（移植自外部用户画像数据分析后端）----
            "ALTER TABLE customer_profile ADD COLUMN IF NOT EXISTS profile_status VARCHAR(32) NOT NULL DEFAULT 'PROVISIONAL'",
            "ALTER TABLE customer_profile ADD COLUMN IF NOT EXISTS profile_version INTEGER NOT NULL DEFAULT 1",
            "ALTER TABLE customer_profile ADD COLUMN IF NOT EXISTS suitability_confidence NUMERIC(5,4) NOT NULL DEFAULT 0",
            "ALTER TABLE customer_profile ADD COLUMN IF NOT EXISTS recommendation_confidence NUMERIC(5,4) NOT NULL DEFAULT 0",
            "ALTER TABLE customer_profile ADD COLUMN IF NOT EXISTS max_allowed_product_risk VARCHAR(8) NOT NULL DEFAULT 'R1'",
            "ALTER TABLE customer_profile ADD COLUMN IF NOT EXISTS restriction_codes_json TEXT NOT NULL DEFAULT '[]'",
            "ALTER TABLE customer_profile ADD COLUMN IF NOT EXISTS evidence_json TEXT NOT NULL DEFAULT '{}'",
            "ALTER TABLE customer_profile ADD COLUMN IF NOT EXISTS dimension_scores_json TEXT NOT NULL DEFAULT '{}'",
            "ALTER TABLE customer_profile ADD COLUMN IF NOT EXISTS model_risk_score INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE customer_profile ADD COLUMN IF NOT EXISTS risk_level VARCHAR(8) NOT NULL DEFAULT 'C1'",
            "ALTER TABLE customer_profile ADD COLUMN IF NOT EXISTS risk_score INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE customer_profile ADD COLUMN IF NOT EXISTS education_level VARCHAR(32) NOT NULL DEFAULT ''",
            "ALTER TABLE customer_profile ADD COLUMN IF NOT EXISTS annual_income NUMERIC(18,2)",
            "CREATE INDEX IF NOT EXISTS ix_customer_profile_tag_user ON customer_profile_tag (user_id, tag_code)",
        )
        for statement in statements:
            await connection.execute(text(statement))
        await connection.execute(
            text("""
            DELETE FROM orders duplicate
            USING orders keeper
            WHERE duplicate.user_id = keeper.user_id
              AND duplicate.idempotency_key IS NOT NULL
              AND duplicate.idempotency_key = keeper.idempotency_key
              AND duplicate.id > keeper.id
        """)
        )
        await connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_orders_user_idempotency_key ON orders (user_id, idempotency_key) WHERE idempotency_key IS NOT NULL"
            )
        )
        await connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_knowledge_chunk_embedding_hnsw ON knowledge_chunk USING hnsw (embedding vector_cosine_ops)"
            )
        )
