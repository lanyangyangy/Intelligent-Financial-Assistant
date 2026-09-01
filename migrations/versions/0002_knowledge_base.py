"""register P1 knowledge schema"""

from typing import Sequence, Union

from alembic import op

revision: str = "0002_knowledge_base"
down_revision: Union[str, None] = "0001_p0_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE INDEX IF NOT EXISTS ix_knowledge_chunk_embedding_hnsw ON knowledge_chunk USING hnsw (embedding vector_cosine_ops)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunk_embedding_hnsw")
