"""Outbox dispatcher: sends completion reports to the marking service with retries."""

from datetime import datetime, timedelta

import httpx
from sqlalchemy import func, select

from candleflow.config import settings
from candleflow.db import SessionLocal
from candleflow.models import Attempt, Outbox

BATCH = 10


def deliver_one(client: httpx.Client, now: datetime) -> bool:
    with SessionLocal.begin() as db:
        item = db.scalar(
            select(Outbox)
            .where(
                Outbox.delivered.is_(False),
                Outbox.next_at <= now,
            )
            .order_by(Outbox.next_at, Outbox.job_id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if item is None:
            return False
        count = db.scalar(select(func.count()).select_from(Attempt).where(Attempt.job_id == item.job_id))
        number = count + 1
        try:
            response = client.post(
                settings.marking_url + "/reports",
                json=item.payload,
                headers={"Idempotency-Key": item.idempotency_key},
                timeout=3,
            )
            outcome = str(response.status_code)
            item.delivered = response.is_success
        except httpx.TimeoutException:
            outcome = "timeout"
        except httpx.RequestError:
            outcome = "unavailable"
        db.add(Attempt(job_id=item.job_id, number=number, outcome=outcome))
        if not item.delivered:
            item.next_at = now + timedelta(seconds=min(900, 5 * 2 ** min(number - 1, 8)))
    return True


def dispatch_due(now_factory) -> int:
    """Deliver up to BATCH due reports; returns how many were attempted."""
    sent = 0
    with httpx.Client() as client:
        while sent < BATCH and deliver_one(client, now_factory()):
            sent += 1
    return sent
