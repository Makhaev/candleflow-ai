from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from candleflow import inventory as inv
from candleflow.api import app
from candleflow.auth import DomainError
from candleflow.db import SessionLocal
from candleflow.models import Allocation, Batch, Outbox, User
from candleflow.rates import exchange_rate
from candleflow.schemas import Completion, JobInput, MaterialInput, ReceiptInput, RecipeInput

DAY = date(2026, 10, 4)


def prepare(db, amounts=("3", "4")):
    user = db.get(User, 1)
    inv.add_material(db, user, MaterialInput(code="SOY", name="Соевый воск", unit="кг"))
    recipe = inv.add_recipe(db, user, RecipeInput(name="Лаванда", ingredients={"SOY": Decimal(1)}))
    for index, amount in enumerate(amounts):
        (batch,) = inv.receive(
            db,
            user,
            ReceiptInput.model_validate(
                {
                    "supplier": "ООО Восковой двор",
                    "received_on": date(2026, 10, 2 - index),
                    "lines": [
                        {
                            "material_code": "SOY",
                            "quantity": amount,
                            "unit_price": str(index + 1),
                            "currency": "RUB",
                        }
                    ],
                }
            ),
        )
        inv.inspect_batch(db, user, batch["id"], "accepted")
    db.commit()
    return user, recipe["id"]


def test_weekend_rate():
    assert exchange_rate("USD", DAY) == exchange_rate("USD", date(2026, 10, 2))


def test_fifo_and_shortage(db):
    user, recipe = prepare(db)
    first = inv.plan(db, user, JobInput(recipe_id=recipe, quantity=5, planned_on=DAY))
    rows = dict(db.execute(select(Allocation.batch_id, Allocation.reserved)).all())
    assert rows == {2: Decimal(4), 1: Decimal(1)}
    second = inv.plan(db, user, JobInput(recipe_id=recipe, quantity=4, planned_on=DAY))
    assert second["components"][0]["signal"] == "yellow"
    assert Decimal(second["components"][0]["missing"]) == 2
    with pytest.raises(DomainError, match="Не хватает"):
        inv.job_transition(db, user, second["id"], "start")
    inv.job_transition(db, user, first["id"], "cancel")
    assert Decimal(inv.stock(db, user, "SOY")["free"]) == 5


def test_parallel_reserves(db):
    _, recipe = prepare(db, ("6",))
    barrier = Barrier(2)

    def reserve():
        with SessionLocal.begin() as session:
            user = session.get(User, 1)
            barrier.wait(timeout=5)
            return inv.plan(session, user, JobInput(recipe_id=recipe, quantity=5, planned_on=DAY))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reserve(), range(2)))
    assert sorted(result["status"] for result in results) == ["planned", "shortage"]
    assert sum(Decimal(result["components"][0]["reserved"]) for result in results) == 6
    assert db.get(Batch, 1).reserved == 6


@pytest.mark.parametrize(("actual", "remaining", "cost"), [("3", "4", "6"), ("6", "1", "10")])
def test_completion_balances_and_cost(db, actual, remaining, cost):
    user, recipe = prepare(db)
    job = inv.plan(db, user, JobInput(recipe_id=recipe, quantity=5, planned_on=DAY))
    inv.job_transition(db, user, job["id"], "start")
    inv.complete(db, user, job["id"], Completion(actual={"SOY": Decimal(actual)}), DAY)
    db.commit()
    assert Decimal(inv.stock(db, user, "SOY")["free"]) == Decimal(remaining)
    assert Decimal(inv.job_cost(db, user, job["id"])["total_rub"]) == Decimal(cost)
    assert db.get(Outbox, job["id"]).payload["quantity"] == "5"


def test_api_auth_and_batch_filter(db):
    prepare(db)
    with TestClient(app) as client:
        assert client.get("/api/batches").status_code == 401
        assert client.post("/api/login", json={"login": "warehouse", "password": "testpass"}).is_success
        page = client.get("/api/batches", params={"sort": "received_on", "descending": "false", "limit": 1})
        assert page.json()["items"][0]["id"] == 2
        assert page.json()["total"] == 2
        assert (
            client.post(
                "/api/jobs", json={"recipe_id": 1, "quantity": "3", "planned_on": str(DAY)}
            ).status_code
            == 403
        )
        assert client.get("/docs").is_success
