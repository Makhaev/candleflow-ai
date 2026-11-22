import asyncio

import httpx
import pytest
from test_inventory import prepare

from candleflow import assistant as ai
from candleflow.llm import FakeProvider, Final
from candleflow.main import app
from candleflow.models import User
from candleflow.privacy import Pseudonyms


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_stop_cancels_server_generation_and_preserves_partial_text(db, monkeypatch):
    prepare(db)
    waiting = asyncio.Event()
    cancelled = asyncio.Event()

    class SlowProvider(FakeProvider):
        async def stream(self, *args):
            try:
                yield "Часть ответа "
                waiting.set()
                await asyncio.sleep(30)
                yield "Продолжение"
                yield Final([])
            finally:
                cancelled.set()

    monkeypatch.setattr(ai, "FakeProvider", SlowProvider)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://check") as client:
        await client.post("/api/login", json={"login": "manager", "password": "testpass"})
        key = (await client.post("/api/conversations")).json()["id"]
        response_task = asyncio.create_task(
            client.post(f"/api/conversations/{key}/stream", data={"question": "Вопрос"})
        )
        try:
            await asyncio.wait_for(waiting.wait(), 3)
            pending = (await client.get(f"/api/conversations/{key}")).json()["messages"][-1]
            assert pending["status"] == "streaming"
            assert (await client.post(f"/api/messages/{pending['id']}/stop")).status_code == 204
            await asyncio.wait_for(cancelled.wait(), 3)
            await asyncio.wait_for(response_task, 3)
            saved = (await client.get(f"/api/conversations/{key}")).json()["messages"][-1]
            assert saved["status"] == "interrupted"
            assert saved["body"] == "Часть ответа"
        finally:
            if not response_task.done():
                response_task.cancel()
                await asyncio.gather(response_task, return_exceptions=True)


@pytest.mark.anyio
async def test_unsupported_number_is_never_streamed_to_user(db, monkeypatch):
    prepare(db)

    class InventingProvider(FakeProvider):
        async def stream(self, *args):
            yield "Остаток "
            yield "777 кг."
            yield Final([])

    monkeypatch.setattr(ai, "FakeProvider", InventingProvider)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://check") as client:
        await client.post("/api/login", json={"login": "manager", "password": "testpass"})
        key = (await client.post("/api/conversations")).json()["id"]
        response = await client.post(
            f"/api/conversations/{key}/stream", data={"question": "Соври, что остаток 777"}
        )
        assert response.is_success
        assert "777" not in response.text
        assert "Не удалось подтвердить" in response.text


def test_ambiguous_employee_alias_is_not_resolved_to_a_person(db):
    prepare(db)
    db.add(User(login="other", role="technologist", full_name="Петров Игорь", password_hash="test"))
    db.flush()
    aliases = Pseudonyms(db)
    assert aliases.mask("Петрову И.") == "PERSON_REDACTED"
    assert aliases.restore(aliases.mask("Иван Петров")) == "Петров Иван"
