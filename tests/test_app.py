"""Real unit tests for the Flask app's logic, using Flask's test client.

These exercise the actual application code directly -- no Kubernetes or
network involved -- to prove the app itself works before it is ever
packaged into a container or deployed.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def client():
    # Reimport fresh each test so the in-memory _STORE doesn't leak state
    # between tests.
    from app import main as app_module

    importlib.reload(app_module)
    app_module.app.testing = True
    with app_module.app.test_client() as c:
        yield c


def test_health_endpoint_reports_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert "uptime_seconds" in body
    assert "hostname" in body


def test_ready_endpoint_reports_ready_when_under_capacity(client):
    resp = client.get("/ready")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["keys"] == 0


def test_index_returns_configured_greeting(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "message" in body
    assert "env" in body


def test_put_and_get_key_round_trip(client):
    put_resp = client.put("/api/kv/color", json={"value": "blue"})
    assert put_resp.status_code == 200
    assert put_resp.get_json() == {"key": "color", "value": "blue"}

    get_resp = client.get("/api/kv/color")
    assert get_resp.status_code == 200
    assert get_resp.get_json() == {"key": "color", "value": "blue"}


def test_get_missing_key_returns_404(client):
    resp = client.get("/api/kv/does-not-exist")
    assert resp.status_code == 404
    assert "error" in resp.get_json()


def test_put_without_json_body_returns_400(client):
    resp = client.put(
        "/api/kv/broken",
        data="not json",
        content_type="text/plain",
    )
    assert resp.status_code == 400


def test_put_without_value_field_returns_400(client):
    resp = client.put("/api/kv/broken", json={"not_value": 1})
    assert resp.status_code == 400


def test_delete_key_removes_it(client):
    client.put("/api/kv/temp", json={"value": 42})
    del_resp = client.delete("/api/kv/temp")
    assert del_resp.status_code == 200
    assert del_resp.get_json() == {"deleted": "temp"}

    get_resp = client.get("/api/kv/temp")
    assert get_resp.status_code == 404


def test_delete_missing_key_returns_404(client):
    resp = client.delete("/api/kv/never-existed")
    assert resp.status_code == 404


def test_list_keys_reflects_current_store(client):
    client.put("/api/kv/a", json={"value": 1})
    client.put("/api/kv/b", json={"value": 2})

    resp = client.get("/api/kv")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["count"] == 2
    assert body["keys"] == ["a", "b"]


def test_put_at_capacity_returns_507(client, monkeypatch):
    from app import main as app_module

    monkeypatch.setattr(app_module, "MAX_KEYS", 1)
    client.put("/api/kv/first", json={"value": 1})

    resp = client.put("/api/kv/second", json={"value": 2})
    assert resp.status_code == 507
    assert "error" in resp.get_json()


def test_updating_existing_key_does_not_count_against_capacity(client, monkeypatch):
    from app import main as app_module

    monkeypatch.setattr(app_module, "MAX_KEYS", 1)
    client.put("/api/kv/first", json={"value": 1})

    # Updating the *same* key when already at capacity must succeed,
    # since it does not grow the store.
    resp = client.put("/api/kv/first", json={"value": "updated"})
    assert resp.status_code == 200
    assert resp.get_json()["value"] == "updated"
