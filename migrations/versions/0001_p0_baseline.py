"""register existing P0 and P1 runtime schema"""

from typing import Sequence, Union

from alembic import op

revision: str = "0001_p0_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # Existing P0/P1 tables were created by the runtime bootstrap. This revision
    # only registers the baseline for databases initialized before Alembic.
    op.execute("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)")


def downgrade() -> None:
    pass
