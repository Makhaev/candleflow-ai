"""Celery application: periodic outbox delivery and housekeeping."""

from datetime import UTC, datetime

from celery import Celery
from sqlalchemy import delete

from candleflow import marking
from candleflow.config import settings
from candleflow.db import SessionLocal
from candleflow.models import LoginSession

celery = Celery("candleflow", broker=settings.redis_url)
celery.conf.update(
    task_ignore_result=True,
    task_serializer="json",
    accept_content=["json"],
    beat_schedule={
        "dispatch-marking": {"task": "marking.dispatch", "schedule": 3},
        "purge-sessions": {"task": "maintenance.purge_sessions", "schedule": 3600},
    },
)


def now() -> datetime:
    return datetime.now(UTC)


@celery.task(name="marking.dispatch")
def dispatch() -> int:
    return marking.dispatch_due(now)


@celery.task(name="maintenance.purge_sessions")
def purge_sessions() -> int:
    with SessionLocal.begin() as db:
        return db.execute(delete(LoginSession).where(LoginSession.expires_at <= now())).rowcount
