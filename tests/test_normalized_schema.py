from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from test_assistant import ask, dialog, sign_in
from test_inventory import DAY, prepare

from candleflow import inventory as inv
from candleflow.chat_models import PseudonymMapping, ToolExecution
from candleflow.main import app
from candleflow.models import Allocation, Batch, ExchangeRate, Receipt, StockMovement, Supplier
from candleflow.schemas import Completion, JobInput, ReceiptInput


def test_receipt_groups_batches_and_reuses_supplier_and_quote(db):
    user, _ = prepare(db, ("3",))
    document = {
        "supplier": "Восковой двор, ООО",
        "received_on": str(DAY),
        "lines": [{"material_code": "SOY", "quantity": "2.5", "unit_price": "2", "currency": "USD"}] * 2,
    }
    first, second = inv.receive(db, user, ReceiptInput.model_validate(document))
    assert first["receipt_id"] == second["receipt_id"]
    assert first["exchange_rate_id"] == second["exchange_rate_id"]
    assert first["supplier"] == "ООО Восковой двор"
    assert db.scalar(select(func.count()).select_from(Supplier)) == 1
    assert (
        db.scalar(select(func.count()).select_from(ExchangeRate).where(ExchangeRate.currency == "USD")) == 1
    )
    receipt = db.get(Receipt, first["receipt_id"])
    assert db.scalar(
        select(PseudonymMapping.alias).where(PseudonymMapping.supplier_id == receipt.supplier_id)
    )
    assert [row["id"] for row in inv.receipt_view(db, user, receipt.id)["batches"]] == [
        first["id"],
        second["id"],
    ]
    with pytest.raises(IntegrityError), db.begin_nested():
        db.get(Batch, first["id"]).quantity = Decimal(-1)
        db.flush()


def test_ledger_reconciles_physical_and_reserved_balances(db):
    user, recipe = prepare(db, ("3", "4"))
    data = JobInput(recipe_id=recipe, quantity=5, planned_on=DAY)
    first = inv.plan(db, user, data)
    inv.job_transition(db, user, first["id"], "cancel")
    second = inv.plan(db, user, data)
    inv.job_transition(db, user, second["id"], "start")
    inv.complete(db, user, second["id"], Completion(actual={"SOY": Decimal(6)}), DAY)
    records = inv.history(db, user, "production_jobs", second["id"], 0, 100)["movements"]
    assert {row["kind"] for row in records} == {"reserve", "issue", "overrun"}
    assert db.get(Allocation, (first["id"], 1)).state == "released"
    assert db.get(Allocation, (second["id"], 1)).state == "settled"
    for batch in db.scalars(select(Batch)):
        physical, reserved = db.execute(
            select(func.sum(StockMovement.physical_delta), func.sum(StockMovement.reserved_delta)).where(
                StockMovement.batch_id == batch.id
            )
        ).one()
        assert (physical, reserved) == (batch.quantity, batch.reserved)


def test_receipt_and_history_rest_and_tool_executions(db):
    prepare(db)
    with TestClient(app) as client:
        sign_in(client, "manager")
        receipts = client.get("/api/receipts").json()
        assert len(receipts) == 2
        details = client.get(f"/api/receipts/{receipts[0]['id']}").json()
        assert details["batches"][0]["receipt_id"] == details["id"]
        assert client.get("/api/batches/1/history").json()["movements"][0]["kind"] == "receipt"
        reply = ask(client, dialog(client), "Какой остаток соевого воска?")
        entry = db.get(ToolExecution, (reply["id"], 1))
        assert entry.name == "get_material_stock"
        assert entry.status == "complete"
        assert reply["tool_calls"][0]["name"] == entry.name


def test_openapi_documents_cookie_errors_and_stream():
    schema = app.openapi()
    assert schema["components"]["securitySchemes"]["CandleFlowSession"]["in"] == "cookie"
    complete = schema["paths"]["/api/jobs/{job_id}/complete"]["post"]
    assert complete["responses"]["409"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ErrorView"
    )
    assert "/api/jobs/{job_id}/start" in schema["paths"]
    assert "/api/jobs/{job_id}/cancel" in schema["paths"]
    stream = schema["paths"]["/api/conversations/{key}/stream"]["post"]
    assert "text/event-stream" in stream["responses"]["200"]["content"]
    assert "multipart/form-data" in stream["requestBody"]["content"]
    assert schema["paths"]["/api/files/{key}"]["get"]["responses"]["200"]["content"]["image/png"]
