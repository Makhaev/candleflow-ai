from datetime import date
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from test_inventory import DAY, prepare

from candleflow import inventory as inv
from candleflow.api import get_rate_client
from candleflow.auth import DomainError
from candleflow.main import app
from candleflow.models import Allocation, Batch, Outbox
from candleflow.rate_mock import app as rates
from candleflow.schemas import Completion, JobInput


def test_draft_issues_then_returns_surplus_to_original_batch(db):
    user, recipe = prepare(db)
    draft = inv.create_draft(db, user, JobInput(recipe_id=recipe, quantity=5, planned_on=DAY))
    job_id = draft["id"]
    assert draft["status"] == "draft"
    assert Decimal(inv.stock(db, user, "SOY")["free"]) == 7
    assert inv.plan_draft(db, user, job_id)["status"] == "planned"
    assert Decimal(inv.stock(db, user, "SOY")["reserved"]) == 5
    started = inv.job_transition(db, user, job_id, "start")
    assert started["status"] == "in_progress"
    assert Decimal(started["components"][0]["issued"]) == 5
    assert Decimal(inv.stock(db, user, "SOY")["reserved"]) == 0
    assert Decimal(inv.stock(db, user, "SOY")["physical"]) == 2
    assert db.get(Allocation, (job_id, 2)).issued == 4
    assert db.get(Allocation, (job_id, 1)).issued == 1
    with pytest.raises(DomainError, match="до запуска"):
        inv.job_transition(db, user, job_id, "cancel")
    inv.complete(db, user, job_id, Completion(actual={"SOY": Decimal(3)}), DAY)
    assert db.get(Batch, 2).quantity == 1
    assert db.get(Batch, 1).quantity == 3


def test_completion_with_insufficient_extra_rolls_back(db):
    user, recipe = prepare(db, ("6",))
    job = inv.plan(db, user, JobInput(recipe_id=recipe, quantity=5, planned_on=DAY))
    inv.job_transition(db, user, job["id"], "start")
    db.commit()
    with pytest.raises(DomainError, match="Не хватает"):
        inv.complete(db, user, job["id"], Completion(actual={"SOY": Decimal(7)}), DAY)
    db.rollback()
    assert inv.job_detail(db, user, job["id"])["status"] == "in_progress"
    assert db.get(Batch, 1).quantity == 1
    assert db.get(Allocation, (job["id"], 1)).consumed == 0
    assert db.get(Outbox, job["id"]) is None


def test_receipt_saves_rate_from_previous_friday_and_audits_it(db):
    prepare(db)

    async def local_rates():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=rates)) as client:
            yield client

    app.dependency_overrides[get_rate_client] = local_rates
    try:
        with TestClient(app) as client:
            assert client.post("/api/login", json={"login": "warehouse", "password": "testpass"}).is_success
            response = client.post(
                "/api/receipts",
                json={
                    "supplier": "ООО Восковой двор",
                    "received_on": str(DAY),
                    "lines": [
                        {"material_code": "SOY", "quantity": "3", "unit_price": "2", "currency": "USD"}
                    ],
                },
            )
            assert response.status_code == 201, response.text
            (batch,) = response.json()
            assert batch["status"] == "on_review"
            assert batch["exchange_rate_on"] == str(date(2026, 10, 2))
            assert Decimal(batch["unit_cost"]) == 2 * Decimal(batch["exchange_rate"])
            filtered = client.get("/api/batches", params={"exchange_rate_on": batch["exchange_rate_on"]})
            assert any(row["id"] == batch["id"] for row in filtered.json()["items"])
            history = client.get(f"/api/batches/{batch['id']}").json()["history"]
            assert history[0]["after"]["exchange_rate_on"] == batch["exchange_rate_on"]
            stock = client.get("/api/stock/SOY").json()
            assert Decimal(stock["physical"]) == 10
            assert Decimal(stock["free"]) == 7
            assert Decimal(stock["reserved"]) == 0
    finally:
        app.dependency_overrides.pop(get_rate_client)
