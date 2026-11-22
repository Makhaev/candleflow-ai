"""Normalize assistant calls, files, memories and canonical aliases."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

CHECKS = (
    ("messages_role_check", "messages", "role IN ('user', 'assistant')"),
    ("messages_status_check", "messages", "status IN ('streaming','complete','interrupted','error')"),
    ("pending_actions_kind_check", "pending_actions", "action IN ('plan_job', 'remember_preference')"),
    (
        "pending_actions_job_check",
        "pending_actions",
        "job_id IS NULL OR (action = 'plan_job' AND confirmed_at IS NOT NULL)",
    ),
    ("assistant_memories_key_check", "assistant_memories", "key IN ('currency', 'workshop')"),
    (
        "assistant_files_mime_check",
        "assistant_files",
        "mime IN ('image/png', 'text/csv', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')",
    ),
)


def upgrade():
    op.rename_table("memory", "assistant_memories")
    op.rename_table("artifacts", "assistant_files")
    for name, table, condition in CHECKS:
        op.create_check_constraint(name, table, condition)
    op.create_table(
        "tool_executions",
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("messages.id"), primary_key=True),
        sa.Column("ordinal", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("model", sa.String()),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("ordinal > 0", name="tool_executions_ordinal_check"),
    )
    op.execute("""
        INSERT INTO tool_executions (message_id,ordinal,name,model,status,at)
        SELECT m.id, t.ordinal, t.value->>'name', COALESCE(t.value->>'model',m.model),
               COALESCE(t.value->>'status','unknown'), m.created
        FROM messages m CROSS JOIN LATERAL jsonb_array_elements(m.tool_calls) WITH ORDINALITY t(value,ordinal)
    """)
    op.execute("""
        INSERT INTO tool_executions (message_id,ordinal,name,model,status,at)
        SELECT m.id, t.ordinal, t.name, m.model, 'unknown', m.created
        FROM messages m CROSS JOIN LATERAL jsonb_array_elements_text(m.tools) WITH ORDINALITY t(name,ordinal)
        WHERE jsonb_array_length(m.tool_calls) = 0
    """)
    op.drop_column("messages", "tool_calls")
    op.drop_column("messages", "tools")
    op.create_table(
        "pseudonym_mappings",
        sa.Column("alias", sa.String(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), unique=True),
        sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("suppliers.id"), unique=True),
        sa.CheckConstraint(
            "(user_id IS NOT NULL) <> (supplier_id IS NOT NULL)", name="pseudonym_owner_check"
        ),
    )
    op.execute("INSERT INTO pseudonym_mappings (alias, user_id) SELECT 'EMPLOYEE_' || id, id FROM users")
    op.execute(
        "INSERT INTO pseudonym_mappings (alias, supplier_id) SELECT 'SUPPLIER_' || id, id FROM suppliers"
    )
    op.execute("""
        CREATE FUNCTION register_canonical_pseudonym() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_TABLE_NAME = 'users' THEN
                INSERT INTO pseudonym_mappings (alias,user_id) VALUES ('EMPLOYEE_' || NEW.id, NEW.id);
            ELSE
                INSERT INTO pseudonym_mappings (alias,supplier_id) VALUES ('SUPPLIER_' || NEW.id, NEW.id);
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    for table in ("users", "suppliers"):
        op.execute(
            f"CREATE TRIGGER {table}_pseudonym AFTER INSERT ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION register_canonical_pseudonym()"
        )


def downgrade():
    for table in ("users", "suppliers"):
        op.execute(f"DROP TRIGGER {table}_pseudonym ON {table}")
    op.execute("DROP FUNCTION register_canonical_pseudonym()")
    op.drop_table("pseudonym_mappings")
    op.add_column("messages", sa.Column("tools", JSONB(), nullable=False, server_default="[]"))
    op.add_column("messages", sa.Column("tool_calls", JSONB(), nullable=False, server_default="[]"))
    op.execute("""
        UPDATE messages m SET tools = t.names, tool_calls = t.calls
        FROM (SELECT message_id, jsonb_agg(name ORDER BY ordinal) AS names,
                     jsonb_agg(jsonb_build_object('name',name,'model',model,'status',status) ORDER BY ordinal) AS calls
              FROM tool_executions GROUP BY message_id) t
        WHERE m.id = t.message_id
    """)
    op.alter_column("messages", "tools", server_default=None)
    op.drop_table("tool_executions")
    for name, table, _ in reversed(CHECKS):
        op.drop_constraint(name, table, type_="check")
    op.rename_table("assistant_files", "artifacts")
    op.rename_table("assistant_memories", "memory")
