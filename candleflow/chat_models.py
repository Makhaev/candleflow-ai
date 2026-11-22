from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, LargeBinary, UniqueConstraint, func, text
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
    tools: Mapped[list] = mapped_column(JSONB, default=list)
    code: Mapped[list] = mapped_column(JSONB, default=list)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Proposal(Base):
    __tablename__ = "proposals"
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), primary_key=True)
    args: Mapped[dict] = mapped_column(JSONB)
    recipe: Mapped[str]
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"))
    expires: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Memory(Base):
    __tablename__ = "memory"
    __table_args__ = (UniqueConstraint("user_id", "key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    key: Mapped[str]
    value: Mapped[str]


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), index=True)
    filename: Mapped[str]
    mime: Mapped[str]
    content: Mapped[bytes] = mapped_column(LargeBinary)
