from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Numeric,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from candleflow.db import Base

AMOUNT = Numeric()


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('storekeeper', 'technologist', 'manager')", name="users_role_check"),
    )
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


class Supplier(Base):
    __tablename__ = "suppliers"
    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    name: Mapped[str]
    normalized_name: Mapped[str] = mapped_column(unique=True)


class Receipt(Base):
    __tablename__ = "receipts"
    __table_args__ = (
        UniqueConstraint("id", "received_on"),
        Index("ix_receipts_supplier_date", "supplier_id", "received_on"),
    )
    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"))
    received_on: Mapped[date]
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    supplier: Mapped[Supplier] = relationship()


class ExchangeRate(Base):
    __tablename__ = "exchange_rates"
    __table_args__ = (
        UniqueConstraint("currency", "quoted_on", "rate"),
        CheckConstraint("currency IN ('RUB', 'EUR', 'USD')", name="exchange_rates_currency_check"),
        CheckConstraint("rate > 0 AND rate < 'Infinity'::numeric", name="exchange_rates_positive"),
    )
    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    currency: Mapped[str]
    quoted_on: Mapped[date]
    rate: Mapped[Decimal] = mapped_column(AMOUNT)


class Batch(Base):
    __tablename__ = "batches"
    __table_args__ = (
        CheckConstraint("received > 0 AND quantity >= reserved AND reserved >= 0 AND quantity <= received"),
        CheckConstraint(
            "unit_price >= 0 AND unit_price < 'Infinity'::numeric AND unit_cost >= 0 "
            "AND unit_cost < 'Infinity'::numeric",
            name="batches_prices_check",
        ),
        CheckConstraint("received < 'Infinity'::numeric", name="batches_finite_quantity"),
        CheckConstraint("status = 'accepted' OR reserved = 0", name="batches_accepted_reserve"),
        ForeignKeyConstraint(("receipt_id", "received_on"), ("receipts.id", "receipts.received_on")),
        Index(
            "ix_batches_fifo",
            "material_code",
            "received_on",
            "id",
            postgresql_where=text("status = 'accepted'"),
        ),
        Index("ix_batches_status_date", "status", "received_on", "id"),
        CheckConstraint("status IN ('on_review', 'accepted', 'rejected')"),
    )
    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    material_code: Mapped[str] = mapped_column(ForeignKey("materials.code"), index=True)
    receipt_id: Mapped[int] = mapped_column(index=True)
    exchange_rate_id: Mapped[int] = mapped_column(ForeignKey("exchange_rates.id"), index=True)
    receipt: Mapped[Receipt] = relationship()
    quote: Mapped[ExchangeRate] = relationship()
    received_on: Mapped[date]
    received: Mapped[Decimal] = mapped_column(AMOUNT)
    quantity: Mapped[Decimal] = mapped_column(AMOUNT)
    reserved: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal(0))
    unit_price: Mapped[Decimal] = mapped_column(AMOUNT)
    unit_cost: Mapped[Decimal] = mapped_column(AMOUNT)
    status: Mapped[str]
    comment: Mapped[str] = mapped_column(default="")


class Recipe(Base):
    __tablename__ = "recipes"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)


class Ingredient(Base):
    __tablename__ = "recipe_items"
    __table_args__ = (
        CheckConstraint("per_unit > 0"),
        CheckConstraint("per_unit < 'Infinity'::numeric", name="recipe_items_finite"),
    )
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id"), primary_key=True)
    material_code: Mapped[str] = mapped_column(ForeignKey("materials.code"), primary_key=True)
    per_unit: Mapped[Decimal] = mapped_column(AMOUNT)


class Job(Base):
    __tablename__ = "production_jobs"
    __table_args__ = (
        CheckConstraint("quantity > 0 AND quantity = trunc(quantity)"),
        CheckConstraint("quantity < 'Infinity'::numeric", name="production_jobs_finite_quantity"),
        CheckConstraint(
            "(status = 'completed') = (completed_on IS NOT NULL)", name="production_jobs_completion_check"
        ),
        Index("ix_jobs_planned_on", "planned_on"),
        Index("ix_production_jobs_status_date", "status", "planned_on", "id"),
        CheckConstraint("status IN ('draft', 'planned', 'in_progress', 'completed', 'cancelled')"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id"))
    quantity: Mapped[Decimal] = mapped_column(AMOUNT)
    planned_on: Mapped[date]
    completed_on: Mapped[date | None]
    status: Mapped[str]


class Component(Base):
    __tablename__ = "job_requirements"
    __table_args__ = (
        CheckConstraint("required > 0 AND required < 'Infinity'::numeric", name="requirements_positive"),
    )
    job_id: Mapped[int] = mapped_column(ForeignKey("production_jobs.id"), primary_key=True)
    material_code: Mapped[str] = mapped_column(ForeignKey("materials.code"), primary_key=True)
    required: Mapped[Decimal] = mapped_column(AMOUNT)


class Allocation(Base):
    __tablename__ = "reservations"
    __table_args__ = (
        CheckConstraint("reserved >= 0 AND consumed >= 0"),
        CheckConstraint("issued >= 0"),
        CheckConstraint(
            "reserved < 'Infinity'::numeric AND issued < 'Infinity'::numeric "
            "AND consumed < 'Infinity'::numeric",
            name="reservations_finite",
        ),
        CheckConstraint(
            "(state = 'active' AND reserved > 0 AND issued = 0 AND consumed = 0) OR "
            "(state = 'issued' AND reserved = 0 AND issued > 0 AND consumed = 0) OR "
            "(state = 'released' AND reserved = 0 AND issued = 0 AND consumed = 0) OR "
            "(state = 'settled' AND reserved = 0)",
            name="reservations_state_check",
        ),
        Index("ix_reservations_batch", "batch_id"),
    )
    job_id: Mapped[int] = mapped_column(ForeignKey("production_jobs.id"), primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), primary_key=True)
    reserved: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal(0))
    issued: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal(0))
    consumed: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal(0))
    state: Mapped[str]


class StockMovement(Base):
    __tablename__ = "stock_movements"
    __table_args__ = (
        CheckConstraint(
            "(kind = 'opening' AND physical_delta >= 0 AND reserved_delta >= 0 AND physical_delta >= reserved_delta) OR "
            "(kind = 'receipt' AND physical_delta > 0 AND reserved_delta = 0) OR "
            "(kind = 'reserve' AND physical_delta = 0 AND reserved_delta > 0) OR "
            "(kind = 'release' AND physical_delta = 0 AND reserved_delta < 0) OR "
            "(kind = 'issue' AND physical_delta < 0 AND physical_delta = reserved_delta) OR "
            "(kind = 'return' AND physical_delta > 0 AND reserved_delta = 0) OR "
            "(kind = 'overrun' AND physical_delta < 0 AND reserved_delta = 0)",
            name="stock_movements_delta_check",
        ),
        CheckConstraint(
            "(job_id IS NULL) = (kind IN ('opening', 'receipt'))", name="stock_movements_job_check"
        ),
        CheckConstraint(
            "abs(physical_delta) < 'Infinity'::numeric AND abs(reserved_delta) < 'Infinity'::numeric",
            name="stock_movements_finite",
        ),
        Index("ix_stock_movements_batch", "batch_id", "id"),
        Index("ix_stock_movements_job", "job_id", "id"),
    )
    id: Mapped[int] = mapped_column(BigInteger(), Identity(), primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"))
    job_id: Mapped[int | None] = mapped_column(ForeignKey("production_jobs.id"))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str]
    physical_delta: Mapped[Decimal] = mapped_column(AMOUNT)
    reserved_delta: Mapped[Decimal] = mapped_column(AMOUNT)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Audit(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_entity", "entity", "entity_id", "id"),
        Index("ix_audit_logs_at_id", "at", "id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    actor: Mapped[str]
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    entity: Mapped[str]
    entity_id: Mapped[str]
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict] = mapped_column(JSONB)


class Outbox(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        Index("ix_outbox_events_pending", "next_at", "job_id", postgresql_where=text("NOT delivered")),
    )
    job_id: Mapped[int] = mapped_column(ForeignKey("production_jobs.id"), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    idempotency_key: Mapped[str] = mapped_column(unique=True)
    next_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    delivered: Mapped[bool] = mapped_column(default=False)


class Attempt(Base):
    __tablename__ = "marking_attempts"
    __table_args__ = (
        UniqueConstraint("job_id", "number"),
        CheckConstraint("number > 0", name="marking_attempts_number"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("outbox_events.job_id"))
    number: Mapped[int]
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    outcome: Mapped[str]


class MarkingReceipt(Base):
    __tablename__ = "marking_receipts"
    key: Mapped[str] = mapped_column(primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB)
