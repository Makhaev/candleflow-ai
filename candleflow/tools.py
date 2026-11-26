"""Assistant tools: a registry of functions the model may call.

Each tool declares its name, a Pydantic schema for the arguments and a description. The
schema is sent to the model as JSON Schema and validates the model's arguments like any
external input. Handlers call the same service layer as the REST buttons, with the current
user's permissions; actions only create a pending proposal that the user must confirm.
"""

import csv
import io
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Annotated, Any

from pydantic import Field
from sqlalchemy.orm import Session

from candleflow import inventory as inv
from candleflow import memory
from candleflow.auth import DomainError
from candleflow.chat_models import PendingAction
from candleflow.models import User
from candleflow.rates import mock_quote
from candleflow.schemas import Code, Input, JobInput, Period, Pieces

# The user's own message must ask for the action. A proposal still needs the
# "Подтвердить" click, so this only blocks actions the user never mentioned.
PLAN_INTENT = re.compile(r"(?<!\w)(?:за|с)?планир\w*|(?<!\w)созда(?:й|йте|ть)(?!\w)", re.IGNORECASE)
MEMORY_INTENT = re.compile(r"(?<!\w)(?:запомн|сохран)\w*", re.IGNORECASE)
PROPOSAL_TTL = timedelta(minutes=30)
LIST_LIMIT = 50

ToolResult = tuple[dict, bytes | None]  # JSON for the model and an optional CSV for Code Interpreter


@dataclass(frozen=True)
class ToolContext:
    db: Session
    user: User
    message_id: int
    question: str
    today: date


@dataclass(frozen=True)
class Tool:
    name: str
    args: type[Input]
    description: str
    handler: Callable[[ToolContext, Any], ToolResult]

    def spec(self) -> dict:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.args.model_json_schema(),
            "strict": False,
        }


REGISTRY: dict[str, Tool] = {}


def tool(name: str, args: type[Input], description: str):
    def register(handler: Callable[[ToolContext, Any], ToolResult]):
        REGISTRY[name] = Tool(name, args, description, handler)
        return handler

    return register


def invoke(
    db: Session, user: User, message_id: int, question: str, name: str, arguments: str, today: date
) -> ToolResult:
    entry = REGISTRY.get(name)
    if entry is None:
        raise DomainError("Инструмент недоступен", 422)
    inv.require(user, *inv.READERS)
    data = entry.args.model_validate_json(arguments)
    return entry.handler(ToolContext(db, user, message_id, question, today), data)


def specs() -> list[dict]:
    return [entry.spec() for entry in REGISTRY.values()]


# Argument schemas


class StockArgs(Input):
    material_code: Code


class HistoryArgs(Input):
    batch_id: Annotated[int, Field(gt=0)]


class CostArgs(Input):
    job_id: Annotated[int, Field(gt=0)]


class ShortageArgs(Input):
    date_from: date
    date_to: date


class JobPlanArgs(Input):
    recipe_id: Annotated[int, Field(gt=0)]
    quantity: Pieces
    planned_date: date


class ConsumptionArgs(Period):
    material_code: Code


# Read tools: executed immediately


@tool(
    "get_material_stock",
    StockArgs,
    "Физический остаток по всем партиям, резерв и свободный остаток по принятым партиям.",
)
def material_stock(ctx: ToolContext, data: StockArgs) -> ToolResult:
    return inv.stock(ctx.db, ctx.user, data.material_code), None


@tool("get_production_shortages", ShortageArgs, "Дефициты производственных заданий за период включительно.")
def production_shortages(ctx: ToolContext, data: ShortageArgs) -> ToolResult:
    period = Period(start=data.date_from, end=data.date_to)
    return {"jobs": inv.shortages(ctx.db, ctx.user, period)[:LIST_LIMIT]}, None


@tool("get_batch_history", HistoryArgs, "История партии по её номеру.")
def batch_history(ctx: ToolContext, data: HistoryArgs) -> ToolResult:
    result = inv.batch_history(ctx.db, ctx.user, data.batch_id)
    result["history"] = result["history"][-LIST_LIMIT:]
    return result, None


@tool("calculate_job_cost", CostArgs, "Зарезервированная или фактическая себестоимость задания в рублях.")
def job_cost(ctx: ToolContext, data: CostArgs) -> ToolResult:
    result = inv.job_cost(ctx.db, ctx.user, data.job_id)
    currency = memory.preferred_currency(ctx.db, ctx.user.id)
    if currency != "RUB":
        quote = mock_quote(currency, ctx.today)
        with localcontext() as arithmetic:
            arithmetic.prec = 64
            converted = (Decimal(result["total_rub"]) / quote.rate).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_EVEN
            )
        result.update(currency=currency, converted=str(converted), rate_on=str(quote.day))
    return result, None


# Dataset tools: build a pseudonymised CSV for Code Interpreter instead of exposing the database


def csv_bytes(headers: list[str], rows: list) -> bytes:
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(headers)
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


@tool("consumption_dataset", ConsumptionArgs, "Подготовить CSV расхода материала по датам для расчёта.")
def consumption_dataset(ctx: ToolContext, data: ConsumptionArgs) -> ToolResult:
    rows = inv.consumption(ctx.db, ctx.user, data, data.material_code)
    if not rows:
        raise DomainError("За указанный период нет данных о расходе этого материала", 422)
    payload = csv_bytes(["date", "quantity"], [(row["date"], row["quantity"]) for row in rows])
    return {"dataset": "consumption.csv", "rows": len(rows)}, payload


@tool("job_cost_dataset", CostArgs, "Подготовить CSV себестоимости по компонентам для сценарного расчёта.")
def job_cost_dataset(ctx: ToolContext, data: CostArgs) -> ToolResult:
    rows = list(inv.job_cost(ctx.db, ctx.user, data.job_id)["components_rub"].items())
    if not rows:
        raise DomainError("Для этого задания отсутствуют данные о затратах", 422)
    return {"dataset": "job_cost.csv", "rows": len(rows)}, csv_bytes(["material", "cost_rub"], rows)


# Action tools: only store a proposal; data changes after the user presses «Подтвердить»


def propose(ctx: ToolContext, action: str, args: dict, recipe: str = "") -> None:
    if ctx.db.get(PendingAction, ctx.message_id):
        raise DomainError("Для ответа уже подготовлено предложение")
    ctx.db.add(
        PendingAction(
            message_id=ctx.message_id,
            action=action,
            args=args,
            recipe=recipe,
            expires=datetime.now(UTC) + PROPOSAL_TTL,
        )
    )


@tool("create_and_plan_job", JobPlanArgs, "Подготовить предложение запланировать задание.")
def plan_job(ctx: ToolContext, data: JobPlanArgs) -> ToolResult:
    job = JobInput(recipe_id=data.recipe_id, quantity=data.quantity, planned_on=data.planned_date)
    preview = inv.plan_preview(ctx.db, ctx.user, job)  # also checks the planning role
    if not PLAN_INTENT.search(ctx.question):
        raise DomainError("Для планирования сформулируйте прямую просьбу", 422)
    args = job.model_dump(mode="json")
    propose(ctx, "plan_job", args, preview["recipe"])
    return {"proposal": {**args, **preview}}, None


@tool(
    "remember_preference",
    memory.Preference,
    "Предложить сохранить явно указанное пользователем предпочтение после подтверждения.",
)
def remember_preference(ctx: ToolContext, data: memory.Preference) -> ToolResult:
    if not MEMORY_INTENT.search(ctx.question):
        raise DomainError("Укажите явно, какое предпочтение нужно запомнить", 422)
    args = memory.validate(data).model_dump(mode="json")
    propose(ctx, "remember_preference", args)
    return {"pending_preference": args}, None
