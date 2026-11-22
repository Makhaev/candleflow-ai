from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from sqlalchemy import select
from test_inventory import DAY, prepare

from candleflow import inventory as inv
from candleflow.marking import deliver_one
from candleflow.marking_mock import store_receipt
from candleflow.models import Attempt, MarkingReceipt, Outbox
from candleflow.schemas import Completion, JobInput


def test_marking_retries_timeout_after_receiver_commit_without_duplicate(db):
    user, recipe = prepare(db)
    job = inv.plan(db, user, JobInput(recipe_id=recipe, quantity=2, planned_on=DAY))
    inv.job_transition(db, user, job["id"], "start")
    inv.complete(db, user, job["id"], Completion(actual={"SOY": Decimal(2)}), DAY)
    db.commit()
    payload = db.get(Outbox, job["id"]).payload
    keys = []

    def transport(request):
        key = request.headers["Idempotency-Key"]
        keys.append(key)
        if len(keys) == 1:
            return httpx.Response(500)
        stored = store_receipt(key, payload)
        if stored:
            raise httpx.ReadTimeout("simulated late reply")
        return httpx.Response(200)

    moment = datetime.now(UTC)
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        assert deliver_one(client, moment)
        assert not deliver_one(client, moment)
        assert deliver_one(client, moment + timedelta(seconds=5))
        assert not deliver_one(client, moment + timedelta(seconds=14))
        assert deliver_one(client, moment + timedelta(seconds=15))
        assert not deliver_one(client, moment + timedelta(days=1))
    assert len(set(keys)) == 1
    assert len(db.scalars(select(MarkingReceipt)).all()) == 1
    assert db.scalars(select(Attempt.outcome).order_by(Attempt.number)).all() == ["500", "timeout", "200"]
