"""Track demo data versions independently from application accounts."""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "demo_seed_runs",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("reference_day", sa.Date(), nullable=False),
    )


def downgrade():
    op.drop_table("demo_seed_runs")
