from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Identity, Numeric, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from candleflow.db import Base

AMOUNT = Numeric()


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(unique=True)
    full_name: Mapped[str]
    role: Mapped[str]
    password_hash: Mapped[str]


class LoginSession(Base):
    __tablename__ = "login_sessions"
    token_hash: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Material(Base):
    __tablename__ = "materials"
    code: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str]
    unit: Mapped[str]


class Batch(Base):
    __tablename__ = "batches"
    __table_args__ = (
        CheckConstraint("received > 0 AND quantity >= reserved AND reserved >= 0 AND quantity <= received"),
        CheckConstraint("unit_price >= 0 AND exchange_rate > 0 AND unit_cost >= 0"),
        CheckConstraint("currency IN ('RUB', 'EUR', 'USD')"),
        CheckConstraint("status IN ('inspection', 'accepted', 'rejected')"),
    )
    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    material_code: Mapped[str] = mapped_column(ForeignKey("materials.code"), index=True)
    supplier: Mapped[str]
    received_on: Mapped[date]
    received: Mapped[Decimal] = mapped_column(AMOUNT)
    quantity: Mapped[Decimal] = mapped_column(AMOUNT)
    reserved: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal(0))
    unit_price: Mapped[Decimal] = mapped_column(AMOUNT)
    currency: Mapped[str]
    exchange_rate: Mapped[Decimal] = mapped_column(AMOUNT)
    unit_cost: Mapped[Decimal] = mapped_column(AMOUNT)
    status: Mapped[str]
    comment: Mapped[str] = mapped_column(default="")


class Recipe(Base):
    __tablename__ = "recipes"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)


class Ingredient(Base):
    __tablename__ = "ingredients"
    __table_args__ = (CheckConstraint("per_unit > 0"),)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id"), primary_key=True)
    material_code: Mapped[str] = mapped_column(ForeignKey("materials.code"), primary_key=True)
    per_unit: Mapped[Decimal] = mapped_column(AMOUNT)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("quantity > 0 AND quantity = trunc(quantity)"),
        CheckConstraint("status IN ('planned', 'shortage', 'running', 'completed', 'cancelled')"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id"))
    quantity: Mapped[Decimal] = mapped_column(AMOUNT)
    planned_on: Mapped[date] = mapped_column(index=True)
    completed_on: Mapped[date | None]
    status: Mapped[str]


class Component(Base):
    __tablename__ = "components"
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), primary_key=True)
    material_code: Mapped[str] = mapped_column(ForeignKey("materials.code"), primary_key=True)
    required: Mapped[Decimal] = mapped_column(AMOUNT)


class Allocation(Base):
    __tablename__ = "allocations"
    __table_args__ = (CheckConstraint("reserved >= 0 AND consumed >= 0"),)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), primary_key=True)
    reserved: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal(0))
    consumed: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal(0))


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str]
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    entity: Mapped[str]
    entity_id: Mapped[str]
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict] = mapped_column(JSONB)


class Outbox(Base):
    __tablename__ = "outbox"
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    next_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    delivered: Mapped[bool] = mapped_column(default=False)


class Attempt(Base):
    __tablename__ = "marking_attempts"
    __table_args__ = (UniqueConstraint("job_id", "number"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("outbox.job_id"))
    number: Mapped[int]
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    outcome: Mapped[str]


class MarkingReceipt(Base):
    __tablename__ = "marking_receipts"
    key: Mapped[str] = mapped_column(primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB)
