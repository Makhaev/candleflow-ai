import json
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Annotated

from anyio import CancelScope, create_memory_object_stream, create_task_group, to_thread
from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select

from candleflow import api_views as view
from candleflow import assistant as ai
from candleflow import inventory as inv
from candleflow.api import DB, User, today
from candleflow.auth import DomainError
from candleflow.chat_models import Artifact, Conversation, Memory, Message
from candleflow.privacy import Pseudonyms, sanitize_upload

router = APIRouter(prefix="/api", tags=["Ассистент"], responses=view.errors(401, 422, 503))


@router.get("/conversations", response_model=list[view.ConversationView], summary="Список моих диалогов")
async def conversations(db: DB, user: User):
    query = select(Conversation).where(Conversation.user_id == user.id).order_by(Conversation.id.desc())
    return [inv.serialize(row) for row in await db.scalars(query.limit(100))]


@router.post(
    "/conversations", status_code=201, response_model=view.ConversationView, summary="Создать диалог"
)
async def new_conversation(db: DB, user: User):
    row = Conversation(user_id=user.id, title="Новый диалог")
    db.add(row)
    await db.commit()
    return inv.serialize(row)


@router.get(
    "/conversations/{key}",
    response_model=view.ConversationDetails,
    responses=view.errors(404),
    summary="Диалог со всеми сообщениями",
)
async def get_conversation(key: int, db: DB, user: User):
    row = await db.run_sync(ai.conversation, user.id, key)
    messages = await db.scalars(select(Message).where(Message.conversation_id == key).order_by(Message.id))
    return {
        **inv.serialize(row),
        "messages": await db.run_sync(lambda sync: [ai.message_view(sync, item) for item in messages]),
    }


@router.post(
    "/conversations/{key}/stream",
    response_class=StreamingResponse,
    summary="Отправить сообщение и получать ответ по SSE",
    description="Multipart-поля question и upload. Поток передаёт data как JSON. События start (id), delta (text), "
    "tool (name), reset (text, retry), done (message). При остановке статус доступен в диалоге.",
    responses={
        **view.errors(404, 409, 413),
        200: {
            "content": {
                "text/event-stream": {
                    "schema": {"type": "string"},
                    "example": 'data: {"type":"delta","text":"Склад"}\n\n',
                }
            }
        },
    },
)
async def stream(
    key: int,
    db: DB,
    user: User,
    question: Annotated[str, Form(min_length=1, max_length=5000)],
    upload: Annotated[UploadFile | None, File()] = None,
):
    await db.run_sync(ai.conversation, user.id, key)
    question = question.strip()
    if not question:
        raise DomainError("Введите вопрос", 422)
    attachment = None
    if upload:
        data = await upload.read(2_000_001)
        await upload.close()
        if len(data) > 2_000_000:
            raise DomainError("Размер файла превышает 2 МБ", 413)
        attachment = await to_thread.run_sync(
            sanitize_upload, upload.filename or "", data, await db.run_sync(Pseudonyms)
        )
    pending = await db.run_sync(ai.start, user, key, question)
    actor = user.id
    await db.commit()

    async def events() -> AsyncIterator[bytes]:
        send, receive = create_memory_object_stream[dict](4)
        async with create_task_group() as tasks:

            async def produce():
                async with send:
                    with CancelScope() as scope:
                        tasks.start_soon(ai.watch_stop, pending, scope)
                        async with aclosing(
                            ai.generate(actor, key, pending, question, today(), attachment)
                        ) as iterator:
                            async for item in iterator:
                                await send.send(item)

            tasks.start_soon(produce)
            async with receive:
                async for item in receive:
                    yield ("data: " + json.dumps(item, ensure_ascii=False) + "\n\n").encode("utf-8")
            tasks.cancel_scope.cancel()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/messages/{key}/stop", status_code=204, responses=view.errors(404), summary="Остановить генерацию"
)
async def stop(key: int, db: DB, user: User):
    await db.run_sync(ai.request_stop, user.id, key)
    await db.commit()
    return Response(status_code=204)


@router.post(
    "/messages/{key}/confirm",
    response_model=view.JobView | view.ConfirmedView,
    responses=view.errors(403, 404, 409),
    summary="Подтвердить действие один раз",
)
async def confirm(key: int, db: DB, user: User):
    result = await db.run_sync(ai.confirm, user, key)
    await db.commit()
    return result


@router.get("/memory", response_model=list[view.MemoryView], summary="Мои сохранённые предпочтения")
async def memory(db: DB, user: User):
    return [inv.serialize(row) for row in await db.run_sync(ai.preferences, user.id)]


@router.delete(
    "/memory/{key}", status_code=204, responses=view.errors(404), summary="Удалить своё предпочтение"
)
async def delete_memory(key: int, db: DB, user: User):
    row = await db.scalar(select(Memory).where(Memory.id == key, Memory.user_id == user.id))
    if row is None:
        raise DomainError("Факт не найден", 404)
    await db.delete(row)
    await db.commit()
    return Response(status_code=204)


@router.get("/artifacts/{key}", include_in_schema=False)
@router.get(
    "/files/{key}",
    response_class=Response,
    summary="Скачать файл своего диалога",
    responses={
        **view.errors(404),
        200: {
            "content": {
                mime: {"schema": {"type": "string", "format": "binary"}} for mime in ai.MIMES.values()
            },
            "headers": {"Content-Disposition": {"schema": {"type": "string"}}},
        },
    },
)
async def download(key: int, db: DB, user: User):
    row = await db.get(Artifact, key)
    if row is None:
        raise DomainError("Файл не найден", 404)
    await db.run_sync(ai.message, user.id, row.message_id)
    from urllib.parse import quote

    return Response(
        row.content,
        media_type=row.mime,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(row.filename)}",
            "X-Content-Type-Options": "nosniff",
        },
    )
