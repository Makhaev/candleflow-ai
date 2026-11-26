import asyncio
import json
import re
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from anyio import CancelScope, move_on_after
from openai import OpenAIError
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from candleflow import inventory as inv
from candleflow import memory
from candleflow import tools as registry
from candleflow.auth import DomainError
from candleflow.chat_models import Artifact, Conversation, Message, PendingAction, ToolExecution
from candleflow.config import settings
from candleflow.db import AsyncSessionLocal
from candleflow.grounding import interpreter_numbers, numbers, supported
from candleflow.llm import FakeProvider, Final, OpenAIProvider, Provider, ProviderError, Restart, run_round
from candleflow.models import Recipe, User
from candleflow.privacy import Pseudonyms
from candleflow.schemas import JobInput

PROMPT = (Path(__file__).resolve().parent.parent / "prompts" / "assistant.md").read_text(encoding="utf-8")
MIMES = {
    "png": "image/png",
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
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
    executions = list(
        db.scalars(
            select(ToolExecution).where(ToolExecution.message_id == row.id).order_by(ToolExecution.ordinal)
        )
    )
    proposal = db.get(PendingAction, row.id) if row.status == "complete" else None
    return {
        **inv.serialize(row),
        "files": [dict(file._mapping) for file in files],
        "tools": [item.name for item in executions],
        "tool_calls": [inv.serialize(item) for item in executions],
        "proposal": proposal_view(db, proposal) if proposal else None,
    }


def proposal_view(db: Session, proposal: PendingAction) -> dict:
    result = inv.serialize(proposal)
    result["preview"] = None
    if proposal.action == "plan_job" and proposal.job_id is None:
        # Recomputed on every view, so the card shows what confirming *now* would reserve.
        args = JobInput.model_validate(proposal.args)
        result["preview"] = inv.requirement_preview(db, args.recipe_id, args.quantity)
    return result


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
    proposal = db.get(PendingAction, row.id, with_for_update=True)
    if row.status != "complete" or proposal is None:
        raise DomainError("Предложение недоступно", 404)
    if proposal.action == "plan_job":
        inv.require(user, *inv.PRODUCTION)
        if proposal.job_id:
            return inv.job_detail(db, user, proposal.job_id)
    else:
        inv.require(user, *inv.READERS)
        if proposal.confirmed_at:
            return {"confirmed": True}
    now = datetime.now(UTC)
    if proposal.expires <= now:
        raise DomainError("Срок подтверждения истёк. Подготовьте новое предложение.")
    if proposal.action == "remember_preference":
        memory.save_preference(db, user, memory.Preference.model_validate(proposal.args))
        proposal.confirmed_at = now
        return {"confirmed": True}
    job = inv.plan(db, user, JobInput.model_validate(proposal.args))
    proposal.job_id = job["id"]
    proposal.confirmed_at = now
    return job


def context(db: Session, user_id: int, key: int, day: date) -> tuple[Pseudonyms, str, list[dict]]:
    aliases = Pseudonyms(db)
    facts = [{"key": row.key, "value": aliases.mask(row.value)} for row in memory.preferences(db, user_id)]
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
    instructions = f"{PROMPT}\nDATE={day}"
    data_context = aliases.mask({"memory": facts, "materials": materials, "recipes": recipes})
    items = [{"role": "user", "content": "CONTEXT=" + json.dumps(data_context, ensure_ascii=False)}]
    items.extend({"role": row.role, "content": aliases.mask(row.body)} for row in reversed(rows) if row.body)
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
                async with AsyncSessionLocal.begin() as db:
                    db.add(
                        Artifact(message_id=message_id, filename=filename, mime=MIMES[ext], content=content)
                    )
    return True


async def snapshot(
    message_id: int,
    body: str,
    status: str,
    model: str | None,
    code: list,
    attempts: list,
) -> dict:
    async with AsyncSessionLocal.begin() as db:
        row = await db.get(Message, message_id, with_for_update=True)
        row.body = body
        row.status = "interrupted" if row.stop_requested else status
        row.model, row.code, row.attempts = model, code, attempts
        await db.flush()
        return await db.run_sync(message_view, row)


async def record_tool(message_id: int, ordinal: int, name: str, model: str | None, status: str) -> None:
    async with AsyncSessionLocal.begin() as db:
        db.add(ToolExecution(message_id=message_id, ordinal=ordinal, name=name, model=model, status=status))


async def finish_tool(message_id: int, ordinal: int, status: str) -> None:
    async with AsyncSessionLocal.begin() as db:
        entry = await db.get(ToolExecution, (message_id, ordinal))
        entry.status = status


def request_stop(db: Session, user_id: int, message_id: int) -> None:
    row = message(db, user_id, message_id, lock=True)
    if row.role == "assistant" and row.status == "streaming":
        row.stop_requested = True


async def watch_stop(message_id: int, scope: CancelScope) -> None:
    while True:
        await asyncio.sleep(0.25)
        async with AsyncSessionLocal() as db:
            stopped = await db.scalar(select(Message.stop_requested).where(Message.id == message_id))
        if stopped:
            scope.cancel()
            return


async def generate(
    user_id: int,
    key: int,
    message_id: int,
    question: str,
    today: date,
    attachment: bytes | None,
    provider: Provider | None = None,
) -> AsyncIterator[dict]:
    body, pending, guarded, status = "", "", "", "interrupted"
    active_model = selected_model = None
    code, files, attempts = [], [], []
    ordinal = 0
    evidence: set[Decimal] = set()

    def on_model(model: str):
        nonlocal active_model, selected_model
        active_model = selected_model = model
        if not attempts or attempts[-1] != model:
            attempts.append(model)

    try:
        async with AsyncSessionLocal() as db:
            aliases, instructions, items = await db.run_sync(context, user_id, key, today)
        yield {"type": "start", "id": message_id}
        offline = settings.ai_provider == "fake"
        provider = provider or (FakeProvider() if offline else OpenAIProvider())
        models = ("demo",) if offline else (settings.primary_model, settings.fallback_model)
        tools = registry.specs()
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
                        if event.model:
                            active_model = event.model
                    elif isinstance(event, Restart):
                        body, pending, guarded = body[:round_start], "", ""
                        yield {"type": "reset", "text": body, "retry": True}
                    else:
                        pending += event
                        boundary = max(pending.rfind(" "), pending.rfind("\n")) + 1
                        if not boundary:
                            continue
                        chunk, pending = pending[:boundary], pending[boundary:]
                        # Stream at once when every number is already backed by a tool result;
                        # otherwise hold the rest of the round until interpreter logs arrive.
                        if guarded or not supported(numbers(chunk), evidence):
                            guarded += chunk
                        else:
                            shown = aliases.restore(chunk)
                            body += shown
                            yield {"type": "delta", "text": shown}
            finally:
                await stream.aclose()
            if len(models) > 1 and selected_model == models[-1]:
                models = (selected_model,)
            guarded += pending
            pending = ""
            evidence.update(interpreter_numbers(output))
            if guarded:
                if not supported(numbers(guarded), evidence):
                    body = body[:round_start]
                    yield {"type": "reset", "text": body, "retry": False}
                    guarded = "Не удалось подтвердить числовые данные по результатам инструментов."
                for piece in re.findall(r"\S+\s*|\s+", guarded):
                    shown = aliases.restore(piece)
                    body += shown
                    yield {"type": "delta", "text": shown}
            guarded = ""
            items.extend(output)
            calls = [item for item in output if item["type"] == "function_call"]
            for item in output:
                if item["type"] == "code_interpreter_call":
                    ordinal += 1
                    await record_tool(
                        message_id, ordinal, "code_interpreter", active_model, item.get("status") or "unknown"
                    )
                    if item.get("code"):
                        code.append(aliases.mask(item["code"]))
                    if item.get("status") in {"failed", "incomplete"}:
                        notice = "\nНе удалось выполнить код расчёта."
                        body += notice
                        yield {"type": "delta", "text": notice}
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
                name = call["name"] if call["name"] in registry.REGISTRY else "unknown"
                ordinal += 1
                await record_tool(message_id, ordinal, name, active_model, "interrupted")
                yield {"type": "tool", "name": name}
                try:
                    async with AsyncSessionLocal.begin() as db:
                        actor = await db.get(User, user_id)
                        result, dataset = await db.run_sync(
                            registry.invoke, actor, message_id, question, name, call["arguments"], today
                        )
                except DomainError as exc:
                    result, dataset = {"error": str(exc)}, None
                except ValidationError:
                    result, dataset = {"error": "Проверьте параметры инструмента"}, None
                safe_result = aliases.mask(result)
                await finish_tool(message_id, ordinal, "error" if "error" in result else "complete")
                if dataset:
                    files.append(await provider.upload(result["dataset"], dataset))
                if "error" not in result:
                    evidence.update(numbers(safe_result))
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call["call_id"],
                        "output": json.dumps(safe_result, ensure_ascii=False),
                    }
                )
        else:
            raise ProviderError(False)
    except (ProviderError, OpenAIError, TimeoutError):
        notice = "\nНе удалось получить ответ ассистента. Повторите запрос."
        body += notice
        status = "error"
        yield {"type": "delta", "text": notice}
    finally:
        if pending and not guarded and not numbers(pending):
            body += aliases.restore(pending)
        with CancelScope(shield=True):
            final = await snapshot(message_id, body.strip(), status, active_model, code, attempts)
        if provider:
            with move_on_after(8, shield=True):
                try:
                    for upload in files:
                        try:
                            await provider.delete(upload)
                        except (OpenAIError, TimeoutError):
                            continue
                finally:
                    await provider.close()
    yield {"type": "done", "message": final}
