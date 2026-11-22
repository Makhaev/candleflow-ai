from fastapi.testclient import TestClient
from test_assistant import sign_in
from test_inventory import prepare

from candleflow.api import COOKIE
from candleflow.main import app


def test_manager_audit_filters_paging_and_access(db):
    prepare(db)
    with TestClient(app) as client:
        assert client.get("/api/audit").status_code == 401
        sign_in(client, "warehouse")
        assert client.get("/api/audit").status_code == 403
        sign_in(client, "manager")
        date = client.get("/api/me").json()["today"]
        params = {"entity": "batches", "actor": "Орлова", "date_from": date, "date_to": date, "limit": 2}
        first = client.get("/api/audit", params=params)
        assert first.is_success
        assert first.json()["total"] == 4
        second = client.get("/api/audit", params={**params, "offset": 2}).json()
        rows = first.json()["items"] + second["items"]
        ids = [row["id"] for row in rows]
        assert ids == sorted(set(ids), reverse=True)
        assert all(row["entity"] == "batches" and row["actor"] == "Орлова Елена" for row in rows)
        assert any(row["before"] and row["before"]["status"] == "on_review" for row in rows)
        assert client.get("/api/audit", params={"actor": "%"}).json()["total"] == 0
        assert (
            client.get("/api/audit", params={"date_from": "2026-11-22", "date_to": "2026-11-21"}).status_code
            == 422
        )


def test_switch_account_replaces_cookie_and_keeps_failed_login_atomic(db):
    prepare(db)
    with TestClient(app) as client, TestClient(app) as stale:
        sign_in(client, "manager")
        old_cookie = client.cookies.get(COOKIE)
        stale.cookies.set(COOKIE, old_cookie)
        assert stale.get("/api/me").json()["role"] == "manager"
        assert client.post("/api/login", json={"login": "warehouse", "password": "wrong"}).status_code == 401
        assert client.get("/api/me").json()["role"] == "manager"
        response = client.post("/api/login", json={"login": "warehouse", "password": "testpass"})
        assert response.json()["role"] == "storekeeper"
        assert client.cookies.get(COOKIE) != old_cookie
        assert stale.get("/api/me").status_code == 401
        assert client.get("/api/audit").status_code == 403
