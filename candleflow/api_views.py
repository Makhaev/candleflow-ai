from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from candleflow.schemas import JobInput, MaterialInput

Currency = Literal["RUB", "USD", "EUR"]
BatchStatus = Literal["on_review", "accepted", "rejected"]
JobStatus = Literal["draft", "planned", "in_progress", "completed", "cancelled"]


class ErrorView(BaseModel):
    detail: str


ERRORS = {
    401: {"model": ErrorView, "description": "Требуется вход или сессия истекла"},
    403: {"model": ErrorView, "description": "Недостаточно прав"},
    404: {"model": ErrorView, "description": "Запись не найдена или недоступна"},
    409: {"model": ErrorView, "description": "Конфликт состояния или ограничения данных"},
    413: {"model": ErrorView, "description": "Файл превышает установленный лимит"},
    422: {"model": ErrorView, "description": "Проверьте формат или значения полей"},
    503: {"model": ErrorView, "description": "База данных или внешний сервис временно недоступны"},
}


def errors(*codes: int) -> dict:
    return {code: ERRORS[code] for code in codes}


class UserView(BaseModel):
    id: int
    name: str
    role: Literal["storekeeper", "technologist", "manager"]


class SessionView(UserView):
    today: date
    week_start: date


class OkView(BaseModel):
    ok: Literal[True]


class HealthView(OkView):
    at: datetime


class MaterialView(MaterialInput):
    pass


class RecipeView(BaseModel):
    id: int
    name: str


class RecipeDetails(RecipeView):
    ingredients: dict[str, Decimal]


class BatchView(BaseModel):
    id: int
    receipt_id: int
    supplier: str
    material_code: str
    received_on: date
    received: Decimal
    quantity: Decimal
    reserved: Decimal
    unit_price: Decimal
    currency: Currency
    exchange_rate_id: int
    exchange_rate: Decimal
    exchange_rate_on: date
    unit_cost: Decimal
    status: BatchStatus
    comment: str


class BatchPage(BaseModel):
    total: int
    items: list[BatchView]


class ReceiptView(BaseModel):
    id: int
    supplier_id: int
    supplier: str
    received_on: date
    created_by: int | None


class ReceiptDetails(ReceiptView):
    batches: list[BatchView]


class AuditView(BaseModel):
    id: int
    actor_id: int | None = None
    actor: str
    at: datetime
    entity: str
    entity_id: str
    before: dict[str, Any] | None
    after: dict[str, Any]


class AuditPage(BaseModel):
    total: int
    items: list[AuditView]


class MovementView(BaseModel):
    id: int
    batch_id: int
    job_id: int | None
    actor_id: int | None
    kind: Literal["opening", "receipt", "reserve", "release", "issue", "return", "overrun"]
    physical_delta: Decimal
    reserved_delta: Decimal
    at: datetime


class HistoryView(BaseModel):
    history: list[AuditView]
    movements: list[MovementView]


class BatchDetails(BaseModel):
    batch: BatchView
    history: list[AuditView]


class StockView(BaseModel):
    code: str
    name: str
    unit: str
    total: Decimal = Field(description="Физический остаток принятых партий")
    physical: Decimal = Field(description="Физический остаток всех партий")
    reserved: Decimal
    free: Decimal


class JobRequirementView(BaseModel):
    code: str
    name: str
    unit: str
    required: Decimal
    reserved: Decimal
    issued: Decimal
    consumed: Decimal
    missing: Decimal
    signal: Literal["green", "yellow", "red"]


class JobView(BaseModel):
    id: int
    recipe_id: int
    recipe: str
    quantity: Decimal
    planned_on: date
    completed_on: date | None
    status: JobStatus
    components: list[JobRequirementView]


class CostView(BaseModel):
    job_id: int
    kind: Literal["reserved", "issued", "actual"]
    status: JobStatus
    total_rub: Decimal
    components_rub: dict[str, Decimal]


class ConversationView(BaseModel):
    id: int
    user_id: int
    title: str
    created: datetime


class FileView(BaseModel):
    id: int
    filename: str
    mime: str


class ExecutionView(BaseModel):
    message_id: int
    ordinal: int
    name: str
    model: str | None
    status: str
    at: datetime


class PreferenceView(BaseModel):
    key: Literal["currency", "workshop"]
    value: str


class MemoryView(PreferenceView):
    id: int
    user_id: int


class PendingActionView(BaseModel):
    message_id: int
    action: Literal["plan_job", "remember_preference"]
    args: JobInput | PreferenceView
    recipe: str
    expires: datetime
    job_id: int | None
    confirmed_at: datetime | None


class MessageView(BaseModel):
    id: int
    conversation_id: int
    role: Literal["user", "assistant"]
    body: str
    status: Literal["streaming", "complete", "interrupted", "error"]
    model: str | None
    tools: list[str]
    tool_calls: list[ExecutionView]
    attempts: list[str]
    code: list[str]
    stop_requested: bool
    created: datetime
    files: list[FileView]
    proposal: PendingActionView | None


class ConversationDetails(ConversationView):
    messages: list[MessageView]


class ConfirmedView(BaseModel):
    confirmed: Literal[True]
