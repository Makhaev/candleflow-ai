from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from test_inventory import DAY, prepare

from candleflow import inventory as inv
from candleflow import worker
from candleflow.db import engine
from candleflow.main import app
from candleflow.models import LoginSession
from candleflow.rate_limit import MemoryStore, limiter
from candleflow.schemas import JobInput


@pytest.fixture
def statements():
    seen = []

    def count(*_):
        seen.append(1)

    event.listen(engine, "before_cursor_execute", count)
    yield seen
    event.remove(engine, "before_cursor_execute", count)


def test_job_list_query_count_does_not_grow_with_jobs(db, statements):
    user, recipe = prepare(db)
    for _ in range(12):
        inv.create_draft(db, user, JobInput(recipe_id=recipe, quantity=1, planned_on=DAY))
    db.commit()
    statements.clear()
    page = inv.job_list(db, user, 0, 50)
    assert page["total"] == len(page["items"]) == 12 and all(job["components"] for job in page["items"])
    assert len(statements) <= 5
    assert inv.job_list(db, user, 0, 50, "planned") == {"total": 0, "items": []}


def test_login_attempts_are_rate_limited(db, monkeypatch):
    monkeypatch.setattr(limiter, "store", MemoryStore())
    monkeypatch.setattr(limiter, "enabled", True)
    with TestClient(app) as client:
        wrong = {"login": "manager", "password": "wrong-password"}
        assert all(client.post("/api/login", json=wrong).status_code == 401 for _ in range(10))
        blocked = client.post("/api/login", json={"login": "manager", "password": "testpass"})
        assert blocked.status_code == 429
        assert blocked.headers["Retry-After"] == "300"
        assert "Слишком много попыток" in blocked.json()["detail"]
        other = client.post("/api/login", json={"login": "warehouse", "password": "testpass"})
        assert other.is_success


def test_unexpected_error_is_readable_and_traceable(db, monkeypatch):
    def broken(*_):
        raise RuntimeError("boom")

    monkeypatch.setattr(inv, "material_view", broken)
    with TestClient(app, raise_server_exceptions=False) as client:
        client.post("/api/login", json={"login": "manager", "password": "testpass"})
        ok = client.get("/api/materials")
        assert len(ok.headers["X-Request-ID"]) == 32
        traced = client.get("/api/me", headers={"X-Request-ID": "support-ticket-42"})
        assert traced.headers["X-Request-ID"] == "support-ticket-42"
        response = client.get("/api/materials/SOY", headers={"X-Request-ID": "abcdef12-incident"})
        assert response.status_code == 500
        assert response.json()["detail"].startswith("Не удалось выполнить запрос")
        assert "abcdef12" in response.json()["detail"]
        assert "boom" not in response.text


def test_expired_sessions_are_purged(db):
    now = datetime.now(UTC)
    db.add_all(
        [
            LoginSession(token_hash="expired", user_id=1, expires_at=now - timedelta(minutes=1)),
            LoginSession(token_hash="active", user_id=1, expires_at=now + timedelta(hours=1)),
        ]
    )
    db.commit()
    assert worker.purge_sessions() == 1
    assert db.scalar(select(func.count()).select_from(LoginSession)) == 1
