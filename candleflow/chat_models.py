from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from candleflow.db import Base


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str]
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="messages_role_check"),
        CheckConstraint(
            "status IN ('streaming','complete','interrupted','error')", name="messages_status_check"
        ),
        Index(
            "one_stream_per_conversation",
            "conversation_id",
            unique=True,
            postgresql_where=text("status = 'streaming'"),
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str]
    body: Mapped[str]
    status: Mapped[str]
    model: Mapped[str | None]
    code: Mapped[list] = mapped_column(JSONB, default=list)
    attempts: Mapped[list] = mapped_column(JSONB, server_default="[]")
    stop_requested: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ToolExecution(Base):
    __tablename__ = "tool_executions"
    __table_args__ = (CheckConstraint("ordinal > 0", name="tool_executions_ordinal_check"),)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), primary_key=True)
    ordinal: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    model: Mapped[str | None]
    status: Mapped[str]
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PseudonymMapping(Base):
    __tablename__ = "pseudonym_mappings"
    __table_args__ = (
        CheckConstraint("(user_id IS NOT NULL) <> (supplier_id IS NOT NULL)", name="pseudonym_owner_check"),
    )
    alias: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), unique=True)
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"), unique=True)


class PendingAction(Base):
    __tablename__ = "pending_actions"
    __table_args__ = (
        CheckConstraint("action IN ('plan_job', 'remember_preference')", name="pending_actions_kind_check"),
        CheckConstraint(
            "job_id IS NULL OR (action = 'plan_job' AND confirmed_at IS NOT NULL)",
            name="pending_actions_job_check",
        ),
    )
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), primary_key=True)
    args: Mapped[dict] = mapped_column(JSONB)
    recipe: Mapped[str]
    job_id: Mapped[int | None] = mapped_column(ForeignKey("production_jobs.id"))
    action: Mapped[str] = mapped_column(server_default="plan_job")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Memory(Base):
    __tablename__ = "assistant_memories"
    __table_args__ = (
        UniqueConstraint("user_id", "key"),
        CheckConstraint("key IN ('currency', 'workshop')", name="assistant_memories_key_check"),
        Index("ix_memory_user_id", "user_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    key: Mapped[str]
    value: Mapped[str]


class Artifact(Base):
    __tablename__ = "assistant_files"
    __table_args__ = (
        CheckConstraint(
            "mime IN ('image/png', 'text/csv', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')",
            name="assistant_files_mime_check",
        ),
        Index("ix_artifacts_message_id", "message_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"))
    filename: Mapped[str]
    mime: Mapped[str]
    content: Mapped[bytes] = mapped_column(LargeBinary)
