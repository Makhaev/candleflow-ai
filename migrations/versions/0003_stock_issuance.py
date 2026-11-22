"""Store rate dates, pending actions and source-batch issuance."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("batches_status_check", "batches", type_="check")
    op.drop_constraint("jobs_status_check", "jobs", type_="check")
    op.add_column("batches", sa.Column("exchange_rate_on", sa.Date(), nullable=True))
    op.add_column("allocations", sa.Column("issued", sa.Numeric(), nullable=False, server_default="0"))
    op.execute("""
        UPDATE batches SET exchange_rate_on = received_on -
            CASE WHEN currency = 'RUB' THEN 0
                 ELSE GREATEST(EXTRACT(ISODOW FROM received_on)::int - 5, 0) END
    """)
    op.alter_column("batches", "exchange_rate_on", nullable=False)
    op.execute("UPDATE batches SET status = 'on_review' WHERE status = 'inspection'")
    op.execute("UPDATE users SET role = 'storekeeper' WHERE role = 'warehouse'")
    op.execute("""
        UPDATE batches AS b SET quantity = b.quantity - x.amount, reserved = b.reserved - x.amount
        FROM (
           SELECT a.batch_id, SUM(a.reserved) AS amount
           FROM allocations a JOIN jobs j ON a.job_id = j.id
           WHERE j.status = 'running' GROUP BY a.batch_id
        ) x WHERE x.batch_id = b.id
    """)
    op.execute("""
        UPDATE allocations AS a SET issued = a.reserved, reserved = 0
        FROM jobs j WHERE a.job_id = j.id AND j.status = 'running'
    """)
    op.execute("UPDATE jobs SET status = 'in_progress' WHERE status = 'running'")
    op.execute("UPDATE jobs SET status = 'planned' WHERE status = 'shortage'")
    op.create_check_constraint(
        "batches_status_check", "batches", "status IN ('on_review', 'accepted', 'rejected')"
    )
    op.create_check_constraint(
        "jobs_status_check", "jobs", "status IN ('draft', 'planned', 'in_progress', 'completed', 'cancelled')"
    )
    op.create_check_constraint("allocations_issued_check", "allocations", "issued >= 0")
    op.alter_column("allocations", "issued", server_default=None)
    op.rename_table("proposals", "pending_actions")


def downgrade():
    op.rename_table("pending_actions", "proposals")
    op.drop_constraint("batches_status_check", "batches", type_="check")
    op.drop_constraint("jobs_status_check", "jobs", type_="check")
    op.execute("""
        UPDATE batches AS b SET quantity = b.quantity + x.amount, reserved = b.reserved + x.amount
        FROM (
           SELECT a.batch_id, SUM(a.issued) AS amount
           FROM allocations a JOIN jobs j ON a.job_id = j.id
           WHERE j.status = 'in_progress' GROUP BY a.batch_id
        ) x WHERE x.batch_id = b.id
    """)
    op.execute("""
        UPDATE allocations AS a SET reserved = a.issued
        FROM jobs j WHERE a.job_id = j.id AND j.status = 'in_progress'
    """)
    op.execute("UPDATE jobs SET status = 'running' WHERE status = 'in_progress'")
    op.execute("""
        UPDATE jobs j SET status = 'shortage' WHERE j.status IN ('planned', 'draft') AND
        (j.status = 'draft' OR EXISTS (
            SELECT 1 FROM components c WHERE c.job_id = j.id AND c.required > COALESCE((
                SELECT SUM(a.reserved) FROM allocations a JOIN batches b ON b.id = a.batch_id
                WHERE a.job_id = j.id AND b.material_code = c.material_code
            ), 0)
        ))
    """)
    op.execute("UPDATE users SET role = 'warehouse' WHERE role = 'storekeeper'")
    op.execute("UPDATE batches SET status = 'inspection' WHERE status = 'on_review'")
    op.create_check_constraint(
        "batches_status_check", "batches", "status IN ('inspection', 'accepted', 'rejected')"
    )
    op.create_check_constraint(
        "jobs_status_check", "jobs", "status IN ('planned', 'shortage', 'running', 'completed', 'cancelled')"
    )
    op.drop_constraint("allocations_issued_check", "allocations", type_="check")
    op.drop_column("allocations", "issued")
    op.drop_column("batches", "exchange_rate_on")
