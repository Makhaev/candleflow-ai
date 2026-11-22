"""Index the manager's audit timeline."""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_audit_logs_at_id", "audit_logs", ["at", "id"])


def downgrade():
    op.drop_index("ix_audit_logs_at_id", table_name="audit_logs")
