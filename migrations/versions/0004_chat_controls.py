"""Persist stop requests, provider attempts and confirmed preferences."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "messages", sa.Column("stop_requested", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column("messages", sa.Column("attempts", JSONB(), nullable=False, server_default="[]"))
    op.add_column("messages", sa.Column("tool_calls", JSONB(), nullable=False, server_default="[]"))
    op.add_column(
        "pending_actions", sa.Column("action", sa.String(), nullable=False, server_default="plan_job")
    )
    op.add_column("pending_actions", sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        "UPDATE pending_actions SET confirmed_at = expires - INTERVAL '30 minutes' WHERE job_id IS NOT NULL"
    )
    op.add_column("outbox", sa.Column("idempotency_key", sa.String(), nullable=True))
    op.execute("UPDATE outbox SET idempotency_key = 'candleflow-job-' || job_id")
    op.alter_column("outbox", "idempotency_key", nullable=False)
    op.create_unique_constraint("outbox_idempotency_key_key", "outbox", ["idempotency_key"])


def downgrade():
    op.drop_constraint("outbox_idempotency_key_key", "outbox", type_="unique")
    op.drop_column("outbox", "idempotency_key")
    op.execute("DELETE FROM pending_actions WHERE action <> 'plan_job'")
    op.drop_column("pending_actions", "confirmed_at")
    op.drop_column("pending_actions", "action")
    op.drop_column("messages", "tool_calls")
    op.drop_column("messages", "attempts")
    op.drop_column("messages", "stop_requested")
