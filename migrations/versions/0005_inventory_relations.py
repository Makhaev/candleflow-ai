"""Normalize receipts and quotes, record stock movements, and preserve inventory IDs."""

import re
import unicodedata

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

RENAMES = (
    ("ingredients", "recipe_items"),
    ("jobs", "production_jobs"),
    ("components", "job_requirements"),
    ("allocations", "reservations"),
    ("audit", "audit_logs"),
    ("outbox", "outbox_events"),
)
LEGAL_FORMS = {"ооо", "ао", "пао", "ип", "зао", "oao", "llc", "ltd"}

CHECKS = (
    ("users_role_check", "users", "role IN ('storekeeper', 'technologist', 'manager')"),
    (
        "batches_prices_check",
        "batches",
        "unit_price >= 0 AND unit_price < 'Infinity'::numeric AND unit_cost >= 0 AND unit_cost < 'Infinity'::numeric",
    ),
    ("batches_finite_quantity", "batches", "received < 'Infinity'::numeric"),
    ("batches_accepted_reserve", "batches", "status = 'accepted' OR reserved = 0"),
    ("production_jobs_finite_quantity", "production_jobs", "quantity < 'Infinity'::numeric"),
    (
        "production_jobs_completion_check",
        "production_jobs",
        "(status = 'completed') = (completed_on IS NOT NULL)",
    ),
    ("requirements_positive", "job_requirements", "required > 0 AND required < 'Infinity'::numeric"),
    ("recipe_items_finite", "recipe_items", "per_unit < 'Infinity'::numeric"),
    (
        "reservations_finite",
        "reservations",
        "reserved < 'Infinity'::numeric AND issued < 'Infinity'::numeric AND consumed < 'Infinity'::numeric",
    ),
    (
        "reservations_state_check",
        "reservations",
        (
            "(state = 'active' AND reserved > 0 AND issued = 0 AND consumed = 0) OR "
            "(state = 'issued' AND reserved = 0 AND issued > 0 AND consumed = 0) OR "
            "(state = 'released' AND reserved = 0 AND issued = 0 AND consumed = 0) OR "
            "(state = 'settled' AND reserved = 0)"
        ),
    ),
    ("marking_attempts_number", "marking_attempts", "number > 0"),
)
INDEXES = (
    ("ix_receipts_supplier_date", "receipts", ("supplier_id", "received_on"), {}),
    (
        "ix_batches_fifo",
        "batches",
        ("material_code", "received_on", "id"),
        {"postgresql_where": sa.text("status = 'accepted'")},
    ),
    ("ix_batches_status_date", "batches", ("status", "received_on", "id"), {}),
    ("ix_batches_receipt_id", "batches", ("receipt_id",), {}),
    ("ix_batches_exchange_rate_id", "batches", ("exchange_rate_id",), {}),
    ("ix_production_jobs_status_date", "production_jobs", ("status", "planned_on", "id"), {}),
    ("ix_reservations_batch", "reservations", ("batch_id",), {}),
    ("ix_stock_movements_batch", "stock_movements", ("batch_id", "id"), {}),
    ("ix_stock_movements_job", "stock_movements", ("job_id", "id"), {}),
    ("ix_audit_logs_entity", "audit_logs", ("entity", "entity_id", "id"), {}),
    (
        "ix_outbox_events_pending",
        "outbox_events",
        ("next_at", "job_id"),
        {"postgresql_where": sa.text("NOT delivered")},
    ),
)


def backfill_receipts():
    connection = op.get_bind()
    suppliers = connection.execute(
        sa.text("SELECT supplier, MIN(id) AS first FROM batches GROUP BY supplier ORDER BY first")
    ).all()
    canonical = {}
    for name, first in suppliers:
        words = re.findall(r"\w+", unicodedata.normalize("NFKC", name).casefold())
        key = (
            " ".join(word for word in words if word not in LEGAL_FORMS) or " ".join(words) or name.casefold()
        )
        if key not in canonical:
            canonical[key] = first
            connection.execute(
                sa.text("INSERT INTO suppliers (id,name,normalized_name) VALUES (:id,:name,:key)"),
                {"id": first, "name": name, "key": key},
            )
        connection.execute(
            sa.text(
                "INSERT INTO receipts (id,supplier_id,received_on) SELECT id,:id,received_on FROM batches WHERE supplier=:name"
            ),
            {"id": canonical[key], "name": name},
        )
    for table in ("suppliers", "receipts"):
        op.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), COALESCE(MAX(id),1), COUNT(*)>0) FROM {table}"
        )


def upgrade():
    for old, new in RENAMES:
        op.rename_table(old, new)
    op.create_table(
        "suppliers",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("normalized_name", sa.String(), nullable=False, unique=True),
    )
    op.create_table(
        "receipts",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("received_on", sa.Date(), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id")),
        sa.UniqueConstraint("id", "received_on"),
    )
    op.create_table(
        "exchange_rates",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("quoted_on", sa.Date(), nullable=False),
        sa.Column("rate", sa.Numeric(), nullable=False),
        sa.CheckConstraint("currency IN ('RUB', 'EUR', 'USD')", name="exchange_rates_currency_check"),
        sa.CheckConstraint("rate > 0 AND rate < 'Infinity'::numeric", name="exchange_rates_positive"),
        sa.UniqueConstraint("currency", "quoted_on", "rate"),
    )
    op.add_column("batches", sa.Column("receipt_id", sa.Integer()))
    op.add_column("batches", sa.Column("exchange_rate_id", sa.Integer()))
    backfill_receipts()
    op.execute("UPDATE batches SET receipt_id = id")
    op.execute(
        "INSERT INTO exchange_rates (currency,quoted_on,rate) SELECT DISTINCT currency,exchange_rate_on,exchange_rate FROM batches"
    )
    op.execute("""
        UPDATE batches b SET exchange_rate_id = r.id FROM exchange_rates r
        WHERE (r.currency,r.quoted_on,r.rate) = (b.currency,b.exchange_rate_on,b.exchange_rate)
    """)
    op.alter_column("batches", "receipt_id", nullable=False)
    op.alter_column("batches", "exchange_rate_id", nullable=False)
    op.create_foreign_key(
        "batches_receipt_date_fkey",
        "batches",
        "receipts",
        ["receipt_id", "received_on"],
        ["id", "received_on"],
    )
    op.create_foreign_key(
        "batches_exchange_rate_id_fkey", "batches", "exchange_rates", ["exchange_rate_id"], ["id"]
    )
    op.drop_constraint("batches_currency_check", "batches", type_="check")
    op.drop_constraint("batches_check1", "batches", type_="check")
    for name in ("supplier", "currency", "exchange_rate", "exchange_rate_on"):
        op.drop_column("batches", name)
    op.add_column("reservations", sa.Column("state", sa.String()))
    op.execute("""
        UPDATE reservations r SET state = CASE WHEN r.reserved > 0 THEN 'active'
            WHEN j.status = 'completed' THEN 'settled' WHEN r.issued > 0 THEN 'issued' ELSE 'released' END
        FROM production_jobs j WHERE j.id = r.job_id
    """)
    op.alter_column("reservations", "state", nullable=False)
    op.add_column("audit_logs", sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id")))
    op.execute("""
        UPDATE audit_logs a SET actor_id = u.id FROM
        (SELECT full_name, MIN(id) AS id FROM users GROUP BY full_name HAVING COUNT(*) = 1) u
        WHERE a.actor = u.full_name
    """)
    op.execute("UPDATE audit_logs SET entity = 'production_jobs' WHERE entity = 'jobs'")
    op.create_table(
        "stock_movements",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("batch_id", sa.Integer(), sa.ForeignKey("batches.id"), nullable=False),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("production_jobs.id")),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("physical_delta", sa.Numeric(), nullable=False),
        sa.Column("reserved_delta", sa.Numeric(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "(kind = 'opening' AND physical_delta >= 0 AND reserved_delta >= 0 AND physical_delta >= reserved_delta) OR "
            "(kind = 'receipt' AND physical_delta > 0 AND reserved_delta = 0) OR "
            "(kind = 'reserve' AND physical_delta = 0 AND reserved_delta > 0) OR "
            "(kind = 'release' AND physical_delta = 0 AND reserved_delta < 0) OR "
            "(kind = 'issue' AND physical_delta < 0 AND physical_delta = reserved_delta) OR "
            "(kind = 'return' AND physical_delta > 0 AND reserved_delta = 0) OR "
            "(kind = 'overrun' AND physical_delta < 0 AND reserved_delta = 0)",
            name="stock_movements_delta_check",
        ),
        sa.CheckConstraint(
            "(job_id IS NULL) = (kind IN ('opening', 'receipt'))", name="stock_movements_job_check"
        ),
        sa.CheckConstraint(
            "abs(physical_delta) < 'Infinity'::numeric AND abs(reserved_delta) < 'Infinity'::numeric",
            name="stock_movements_finite",
        ),
    )
    op.execute(
        "INSERT INTO stock_movements (batch_id,kind,physical_delta,reserved_delta) SELECT id,'opening',quantity,reserved FROM batches ORDER BY id"
    )
    for name, table, clause in CHECKS:
        op.create_check_constraint(name, table, clause)
    for name, table, columns, options in INDEXES:
        op.create_index(name, table, columns, **options)


def downgrade():
    for name, table, _, _ in reversed(INDEXES):
        op.drop_index(name, table_name=table)
    for name, table, _ in reversed(CHECKS):
        op.drop_constraint(name, table, type_="check")
    op.drop_table("stock_movements")
    op.drop_column("audit_logs", "actor_id")
    op.execute("UPDATE audit_logs SET entity = 'jobs' WHERE entity = 'production_jobs'")
    op.drop_column("reservations", "state")
    columns = (
        ("supplier", sa.String()),
        ("currency", sa.String()),
        ("exchange_rate", sa.Numeric()),
        ("exchange_rate_on", sa.Date()),
    )
    for name, datatype in columns:
        op.add_column("batches", sa.Column(name, datatype))
    op.execute("""
        UPDATE batches b SET supplier = s.name, currency = r.currency, exchange_rate = r.rate,
                             exchange_rate_on = r.quoted_on
        FROM receipts p JOIN suppliers s ON s.id = p.supplier_id, exchange_rates r
        WHERE p.id = b.receipt_id AND r.id = b.exchange_rate_id
    """)
    for name, _ in columns:
        op.alter_column("batches", name, nullable=False)
    op.create_check_constraint("batches_currency_check", "batches", "currency IN ('RUB', 'EUR', 'USD')")
    op.create_check_constraint(
        "batches_check1", "batches", "unit_price >= 0 AND exchange_rate > 0 AND unit_cost >= 0"
    )
    op.drop_column("batches", "exchange_rate_id")
    op.drop_column("batches", "receipt_id")
    op.drop_table("exchange_rates")
    op.drop_table("receipts")
    op.drop_table("suppliers")
    for old, new in reversed(RENAMES):
        op.rename_table(new, old)
