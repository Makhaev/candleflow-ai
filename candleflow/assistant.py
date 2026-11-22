import asyncio
import csv
import io
import json
import re
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

from openai import OpenAIError
from pydantic import Field, ValidationError
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from candleflow import inventory as inv
from candleflow.auth import DomainError
from candleflow.chat_models import Artifact, Conversation, Memory, Message, Proposal
from candleflow.config import settings
from candleflow.db import SessionLocal
from candleflow.llm import FakeProvider, Final, OpenAIProvider, Provider, ProviderError, Restart, run_round
from candleflow.models import Recipe, User
from candleflow.privacy import Pseudonyms
from candleflow.rates import exchange_rate
from candleflow.schemas import Code, Input, JobInput, Period, Text

PROMPT = (Path(__file__).resolve().parent.parent / "prompts" / "assistant.md").read_text(encoding="utf-8")
MIMES = {
    "png": "image/png",
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


class StockArgs(Input):
    material_code: Code


class HistoryArgs(Input):
    batch_id: Annotated[int, Field(gt=0)]


class CostArgs(Input):
    job_id: Annotated[int, Field(gt=0)]


class ConsumptionArgs(Period):
    material_code: Code


class Preference(Input):
    key: Literal["currency", "workshop"]
    value: Text


TOOLS = {
    "stock": (StockArgs, "Общий и свободный остаток принятого материала по коду."),
    "shortages": (Period, "Дефициты производственных заданий за период включительно."),
    "batch_history": (HistoryArgs, "История партии по её номеру."),
    "job_cost": (CostArgs, "Зарезервированная или фактическая себестоимость задания в рублях."),
    "propose_job": (JobInput, "Подготовить предложение запланировать задание."),
    "consumption_dataset": (ConsumptionArgs, "Подготовить CSV расхода материала по датам для расчёта."),
    "job_cost_dataset": (CostArgs, "Подготовить CSV себестоимости по компонентам для сценарного расчёта."),
    "remember_preference": (Preference, "Запомнить явно указанное пользователем предпочтение."),
}


def conversation(db: Session, user_id: int, key: int, *, lock: bool = False) -> Conversation:
    query = select(Conversation).where(Conversation.id == key, Conversation.user_id == user_id)
    entry = db.scalar(query.with_for_update() if lock else query)
    if entry is None:
        raise DomainError("Диалог не найден", 404)
    return entry


def message(db: Session, user_id: int, key: int, *, lock: bool = False) -> Message:
    query = select(Message).join(Conversation).where(Message.id == key, Conversation.user_id == user_id)
    entry = db.scalar(query.with_for_update(of=Message) if lock else query)
    if entry is None:
        raise DomainError("Сообщение не найдено", 404)
    return entry


def message_view(db: Session, row: Message) -> dict:
    files = db.execute(
        select(Artifact.id, Artifact.filename, Artifact.mime).where(Artifact.message_id == row.id)
    )
    proposal = db.get(Proposal, row.id) if row.status == "complete" else None
    return {
        **inv.serialize(row),
        "files": [dict(file._mapping) for file in files],
        "proposal": inv.serialize(proposal) if proposal else None,
    }


def start(db: Session, user: User, key: int, question: str) -> int:
    entry = conversation(db, user.id, key, lock=True)
    cutoff = datetime.now(UTC) - timedelta(minutes=15)
    db.execute(
        update(Message)
        .where(Message.conversation_id == key, Message.status == "streaming", Message.created < cutoff)
        .values(status="interrupted")
    )
    if db.scalar(select(Message.id).where(Message.conversation_id == key, Message.status == "streaming")):
        raise DomainError("Дождитесь ответа или остановите генерацию")
    if not db.scalar(select(Message.id).where(Message.conversation_id == key).limit(1)):
        entry.title = question[:80]
    db.add(Message(conversation_id=key, role="user", body=question, status="complete"))
    db.flush()
    pending = Message(conversation_id=key, role="assistant", body="", status="streaming")
    db.add(pending)
    db.flush()
    return pending.id


def confirm(db: Session, user: User, message_id: int) -> dict:
    row = message(db, user.id, message_id, lock=True)
    proposal = db.get(Proposal, row.id, with_for_update=True)
    if row.status != "complete" or proposal is None:
        raise DomainError("Предложение недоступно", 404)
    inv.require(user, *inv.PRODUCTION)
    if proposal.job_id:
        return inv.job_detail(db, user, proposal.job_id)
    if proposal.expires <= datetime.now(UTC):
        raise DomainError("Срок подтверждения истёк. Подготовьте новое предложение.")
    job = inv.plan(db, user, JobInput.model_validate(proposal.args))
    proposal.job_id = job["id"]
    return job


def preferences(db: Session, user_id: int) -> list[Memory]:
    return list(db.scalars(select(Memory).where(Memory.user_id == user_id).order_by(Memory.id)))


def save_preference(db: Session, user: User, data: Preference) -> dict:
    if data.key == "currency" and data.value not in {"RUB", "EUR", "USD"}:
        raise DomainError("Валюта должна быть RUB, EUR или USD", 422)
    statement = insert(Memory).values(user_id=user.id, key=data.key, value=data.value)
    db.execute(
        statement.on_conflict_do_update(
            index_elements=[Memory.user_id, Memory.key], set_={"value": statement.excluded.value}
        )
    )
    return {"preference": data.model_dump()}


def csv_bytes(headers: list[str], rows: list) -> bytes:
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(headers)
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def invoke(
    db: Session, user: User, message_id: int, question: str, name: str, arguments: str, today: date
) -> tuple[dict, bytes | None]:
    if name not in TOOLS:
        raise DomainError("Инструмент недоступен", 422)
    data = TOOLS[name][0].model_validate_json(arguments)
    if name == "stock":
        return inv.stock(db, user, data.material_code), None
    if name == "shortages":
        return {"jobs": inv.shortages(db, user, data)[:50]}, None
    if name == "batch_history":
        result = inv.batch_history(db, user, data.batch_id)
        result["history"] = result["history"][-50:]
        return result, None
    if name in {"job_cost", "job_cost_dataset"}:
        result = inv.job_cost(db, user, data.job_id)
        if name == "job_cost":
            currency = next((row.value for row in preferences(db, user.id) if row.key == "currency"), "RUB")
            if currency != "RUB":
                rate = exchange_rate(currency, today)
                result["currency"] = currency
                result["converted"] = str((Decimal(result["total_rub"]) / rate).quantize(Decimal("0.01")))
                result["rate_on"] = str(today)
            return result, None
        rows = list(result["components_rub"].items())
        if not rows:
            raise DomainError("Для этого задания отсутствуют данные о затратах", 422)
        return {"dataset": "job_cost.csv", "rows": len(rows)}, csv_bytes(["material", "cost_rub"], rows)
    if name == "consumption_dataset":
        rows = inv.consumption(db, user, data, data.material_code)
        if not rows:
            raise DomainError("За указанный период нет данных о расходе этого материала", 422)
        payload = csv_bytes(["date", "quantity"], [(row["date"], row["quantity"]) for row in rows])
        return {"dataset": "consumption.csv", "rows": len(rows)}, payload
    if name == "propose_job":
        recipe = inv.recipe_for_plan(db, user, data)
        if not re.match(
            r"^\s*(?:пожалуйста[, ]+)?(?:запланиру(?:й|йте)|созда(?:й|йте)|спланиру(?:й|йте))\b",
            question,
            re.IGNORECASE,
        ):
            raise DomainError("Для планирования сформулируйте прямую просьбу", 422)
        if db.get(Proposal, message_id):
            raise DomainError("Для ответа уже подготовлено предложение")
        args = data.model_dump(mode="json")
        db.add(
            Proposal(
                message_id=message_id,
                args=args,
                recipe=recipe.name,
                expires=datetime.now(UTC) + timedelta(minutes=30),
            )
        )
        return {"proposal": {"recipe": recipe.name, **args}}, None
    if not re.match(r"^\s*(?:запомни|сохрани)\b", question, re.IGNORECASE):
        raise DomainError("Укажите явно, какое предпочтение нужно запомнить", 422)
    return save_preference(db, user, data), None


def context(db: Session, user_id: int, key: int, day: date) -> tuple[Pseudonyms, str, list[dict]]:
    aliases = Pseudonyms(db)
    facts = [{"key": row.key, "value": aliases.mask(row.value)} for row in preferences(db, user_id)]
    materials = [
        inv.serialize(row) for row in db.scalars(select(inv.Material).order_by(inv.Material.code).limit(200))
    ]
    recipes = [inv.serialize(row) for row in db.scalars(select(Recipe).order_by(Recipe.id).limit(200))]
    rows = list(
        db.scalars(
            select(Message)
            .where(
                Message.conversation_id == key,
                Message.status == "complete",
            )
            .order_by(Message.id.desc())
            .limit(20)
        )
    )
    instructions = (
        f"{PROMPT}\nDATE={day}\nMEMORY={json.dumps(facts, ensure_ascii=False)}"
        f"\nMATERIALS={json.dumps(aliases.mask(materials), ensure_ascii=False)}"
        f"\nRECIPES={json.dumps(aliases.mask(recipes), ensure_ascii=False)}"
    )
    items = [{"role": row.role, "content": aliases.mask(row.body)} for row in reversed(rows) if row.body]
    return aliases, instructions, items


async def save_artifacts(provider: Provider, message_id: int, output: list[dict]) -> bool:
    seen = set()
    for item in output:
        for part in item.get("content", []):
            for note in part.get("annotations", []):
                if note.get("type") != "container_file_citation":
                    continue
                key = (note["container_id"], note["file_id"])
                filename = note["filename"].replace("\\", "/").split("/")[-1][:120]
                ext = filename.lower().rsplit(".", 1)[-1]
                if key in seen or ext not in MIMES:
                    continue
                if len(seen) >= 5:
                    return False
                seen.add(key)
                try:
                    content = await provider.download(*key)
                except (OpenAIError, TimeoutError, ValueError):
                    return False
                with SessionLocal.begin() as db:
                    db.add(
                        Artifact(message_id=message_id, filename=filename, mime=MIMES[ext], content=content)
                    )
    return True


def snapshot(message_id: int, body: str, status: str, model: str | None, tools: list, code: list) -> dict:
    with SessionLocal.begin() as db:
        row = db.get(Message, message_id)
        row.body, row.status, row.model, row.tools, row.code = body, status, model, tools, code
        db.flush()
        return message_view(db, row) if status != "streaming" else {}


async def generate(
    user_id: int,
    key: int,
    message_id: int,
    question: str,
    today: date,
    attachment: bytes | None,
    provider: Provider | None = None,
) -> AsyncIterator[dict]:
    body, pending, status = "", "", "interrupted"
    active_model = None
    called, code, files = [], [], []
    aliases = None

    def on_model(model: str):
        nonlocal active_model
        active_model = model

    try:
        with SessionLocal() as db:
            aliases, instructions, items = context(db, user_id, key, today)
        yield {"type": "start", "id": message_id}
        offline = settings.ai_provider == "fake"
        provider = provider or (FakeProvider() if offline else OpenAIProvider())
        models = ("demo",) if offline else (settings.primary_model, settings.fallback_model)
        tools = [
            {
                "type": "function",
                "name": name,
                "description": description,
                "parameters": schema.model_json_schema(),
                "strict": False,
            }
            for name, (schema, description) in TOOLS.items()
        ]
        if attachment:
            files.append(await provider.upload("attachment.csv", attachment))
        for _ in range(8):
            output = []
            round_start = len(body)
            stream = run_round(provider, models, instructions, items, tools, files, on_model)
            try:
                async for event in stream:
                    if isinstance(event, Final):
                        output = event.output
                    elif isinstance(event, Restart):
                        body, pending = body[:round_start], ""
                        yield {"type": "reset", "text": body}
                    else:
                        pending += event
                        boundary = max(pending.rfind(" "), pending.rfind("\n")) + 1
                        if boundary:
                            chunk, pending = aliases.restore(pending[:boundary]), pending[boundary:]
                            body += chunk
                            yield {"type": "delta", "text": chunk}
            finally:
                await stream.aclose()
            if pending:
                chunk, pending = aliases.restore(pending), ""
                body += chunk
                yield {"type": "delta", "text": chunk}
            items.extend(output)
            calls = [item for item in output if item["type"] == "function_call"]
            for item in output:
                if item["type"] == "code_interpreter_call":
                    called.append("code_interpreter")
                    if item.get("code"):
                        code.append(aliases.restore(item["code"]))
            if not calls:
                if not await save_artifacts(provider, message_id, output):
                    notice = "\nНе удалось сохранить один из файлов расчёта."
                    body += notice
                    yield {"type": "delta", "text": notice}
                if not body:
                    body = "Ответ не получен. Уточните вопрос или повторите запрос."
                    yield {"type": "delta", "text": body}
                status = "complete"
                break
            for call in calls:
                name = call["name"]
                called.append(name)
                yield {"type": "tool", "name": name}
                try:
                    with SessionLocal.begin() as db:
                        actor = db.get(User, user_id)
                        result, dataset = invoke(
                            db, actor, message_id, question, name, call["arguments"], today
                        )
                except DomainError as exc:
                    result, dataset = {"error": str(exc)}, None
                except ValidationError:
                    result, dataset = {"error": "Проверьте параметры инструмента"}, None
                if dataset:
                    files.append(await provider.upload(result["dataset"], dataset))
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call["call_id"],
                        "output": json.dumps(aliases.mask(result), ensure_ascii=False),
                    }
                )
        else:
            raise ProviderError(False)
    except (ProviderError, OpenAIError, TimeoutError) as exc:
        del exc
        notice = "\nНе удалось получить ответ ассистента. Повторите запрос."
        body += notice
        status = "error"
        yield {"type": "delta", "text": notice}
    finally:
        if pending and aliases:
            body += aliases.restore(pending)
        final = snapshot(message_id, body.strip(), status, active_model, called, code)
        if provider:

            async def clean():
                for upload in files:
                    try:
                        await provider.delete(upload)
                    except (OpenAIError, TimeoutError):
                        continue
                await provider.close()

            # Shield cleanup if the HTTP client disconnects while awaiting OpenAI.
            cleanup = asyncio.create_task(clean())
            await asyncio.shield(cleanup)
    yield {"type": "done", "message": final}
