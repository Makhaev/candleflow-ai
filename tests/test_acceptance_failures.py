import asyncio
import json
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_assistant import ask, dialog, sign_in
from test_inventory import DAY, prepare

from candleflow import assistant as ai
from candleflow.chat_models import ToolExecution
from candleflow.llm import FakeProvider
from candleflow.main import app
from candleflow.models import Allocation, Batch, Job, StockMovement, User


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_shortage_and_cancellation_through_workbench_api(db):
    _, recipe = prepare(db, ("6",))
    with TestClient(app) as client:
        sign_in(client, "manager")
        job = client.post(
            "/api/jobs",
            json={
                "recipe_id": recipe,
                "quantity": "8",
                "planned_on": str(DAY),
            },
        ).json()
        job_path = f"/api/jobs/{job['id']}"
        denied = client.post(job_path + "/start")
        assert denied.status_code == 409
        assert "Не хватает" in denied.json()["detail"]
        assert client.get(job_path).json()["status"] == "planned"
        batch = db.get(Batch, 1)
        assert batch.quantity == batch.reserved == 6
        cancelled = client.post(job_path + "/cancel")
        assert cancelled.json()["status"] == "cancelled"
        assert client.post(job_path + "/cancel").status_code == 409
        assert client.get("/api/stock/SOY").json()["free"] == "6"
        db.refresh(batch)
        assert batch.quantity == 6 and batch.reserved == 0
        assert db.get(Allocation, (job["id"], batch.id)).state == "released"
        moves = db.execute(
            select(StockMovement.kind, StockMovement.physical_delta, StockMovement.reserved_delta)
            .where(StockMovement.job_id == job["id"])
            .order_by(StockMovement.id)
        ).all()
        assert moves == [("reserve", 0, 6), ("release", 0, -6)]


def test_workbench_role_matrix_for_inventory_and_production(db):
    _, recipe = prepare(db)
    db.add(
        User(
            login="tech",
            role="technologist",
            full_name="Технолог Тестовый",
            password_hash=db.get(User, 2).password_hash,
        )
    )
    db.commit()
    material = {"code": "DYE", "name": "Краситель", "unit": "г"}
    draft = {"recipe_id": recipe, "quantity": "1", "planned_on": str(DAY)}
    with TestClient(app) as warehouse, TestClient(app) as tech:
        sign_in(warehouse, "warehouse")
        sign_in(tech, "tech")
        assert warehouse.post("/api/materials", json=material).status_code == 201
        assert tech.post("/api/materials", json=material).status_code == 403
        assert warehouse.post("/api/jobs/drafts", json=draft).status_code == 403
        allowed = tech.post("/api/jobs/drafts", json=draft)
        assert allowed.status_code == 201
        job_id = allowed.json()["id"]
        assert warehouse.post(f"/api/jobs/{job_id}/plan").status_code == 403
        assert tech.post(f"/api/jobs/{job_id}/plan").json()["status"] == "planned"


@pytest.mark.anyio
async def test_concurrent_confirmation_creates_one_job_and_one_reservation(db):
    prepare(db)
    with TestClient(app) as client:
        sign_in(client, "manager")
        message = ask(client, dialog(client), "Запланируй 3 свечи Лаванда на 2026-12-01")
        cookie = dict(client.cookies)
    path = f"/api/messages/{message['id']}/confirm"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://check", cookies=cookie
    ) as client:
        first, second = await asyncio.gather(client.post(path), client.post(path))
    assert first.is_success and second.is_success
    assert first.json()["id"] == second.json()["id"]
    assert db.scalar(select(func.count()).select_from(Job)) == 1
    assert db.scalar(select(func.sum(Allocation.reserved))) == Decimal(3)


@pytest.mark.parametrize(
    ("failure", "notice"),
    [
        ("execution", "Не удалось выполнить код расчёта"),
        ("download", "Не удалось сохранить один из файлов расчёта"),
    ],
)
def test_code_interpreter_failure_keeps_chat_usable(db, monkeypatch, failure, notice):
    prepare(db)

    class InterpreterFault(FakeProvider):
        @staticmethod
        def interpreter():
            if failure == "execution":
                return "", [{"type": "code_interpreter_call", "status": "failed", "code": "raise ValueError"}]
            return FakeProvider.interpreter()

        async def download(self, container_id, file_id):
            raise TimeoutError("simulated unavailable artifact")

    monkeypatch.setattr(ai, "FakeProvider", InterpreterFault)
    with TestClient(app) as client:
        sign_in(client, "manager")
        key = dialog(client)
        failed = ask(
            client,
            key,
            "Проанализируй таблицу",
            {"upload": ("source.csv", b"date,quantity\n2026-10-01,2", "text/csv")},
        )
        assert failed["status"] == "complete"
        assert failed["tools"] == ["code_interpreter"]
        assert not failed["files"]
        assert notice in failed["body"]
        if failure == "execution":
            assert db.get(ToolExecution, (failed["id"], 1)).status == "failed"
        follow_up = ask(client, key, "Какой остаток соевого воска?")
        assert follow_up["status"] == "complete"
        assert follow_up["tools"] == ["get_material_stock"]
        assert "Соевый воск" in follow_up["body"]
        history = client.get(f"/api/conversations/{key}").json()["messages"]
        assert [row["id"] for row in history if row["role"] == "assistant"] == [failed["id"], follow_up["id"]]


def test_provider_receives_aliases_and_ui_gets_restored_names(db, monkeypatch):
    prepare(db)
    observed = []

    class RecordingProvider(FakeProvider):
        async def stream(self, model, instructions, items, tools, files):
            observed.append(json.dumps(items, ensure_ascii=False))
            async for event in super().stream(model, instructions, items, tools, files):
                yield event

    monkeypatch.setattr(ai, "FakeProvider", RecordingProvider)
    with TestClient(app) as client:
        sign_in(client, "manager")
        answer = ask(client, dialog(client), "Покажи историю партии 1. Петрову И. Восковой двор, ООО")
    assert answer["tools"] == ["get_batch_history"]
    sent = "\n".join(observed)
    assert "EMPLOYEE_2" in sent and "SUPPLIER_1" in sent
    assert all(name not in sent for name in ("Петрову", "Восковой двор", "Орлова Елена"))
    assert "ООО Восковой двор" in answer["body"] and "Орлова Елена" in answer["body"]


def test_provider_does_not_receive_contact_details_or_numeric_identifiers(db, monkeypatch):
    requests = []
    attachments = []

    class RecordingProvider(FakeProvider):
        async def upload(self, filename, content):
            attachments.append((filename, content.decode()))
            return await super().upload(filename, content)

        async def stream(self, model, instructions, items, tools, files):
            requests.append(json.dumps(items, ensure_ascii=False))
            async for event in super().stream(model, instructions, items, tools, files):
                yield event

    monkeypatch.setattr(ai, "FakeProvider", RecordingProvider)
    with TestClient(app) as client:
        sign_in(client, "manager")
        answer = ask(
            client,
            dialog(client),
            "Проанализируй таблицу. Контакты: ivan@example.org и +7 (911) 222-33-44.",
            {
                "upload": (
                    "private.csv",
                    b"date,quantity,employee_id,phone\n2026-10-01,2,123456789012,79112223344",
                    "text/csv",
                )
            },
        )
    assert answer["status"] == "complete"
    assert "CONTACT_REDACTED" in "\n".join(requests)
    assert all(value not in "\n".join(requests) for value in ("ivan@example.org", "222-33-44"))
    assert len(attachments) == 1
    filename, content = attachments[0]
    assert filename == "attachment.csv"
    assert content.splitlines() == [
        "date,quantity,column_3,column_4",
        "2026-10-01,2,CATEGORY_1,CATEGORY_2",
    ]
