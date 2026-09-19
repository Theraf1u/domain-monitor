"""/api/v1/events and /api/v1/nodes/heartbeat auth, the per-node rate
limiter, and the fleet-wide sending_enabled gate.

Uses the session-shared `client` fixture from conftest.py (see its
docstring for why this can't be a fresh TestClient per module)."""
from __future__ import annotations

import pytest

from conftest import admin_headers

SAMPLE_BATCH = {"events": [{"domain": "example.com", "source": "tls_sni", "hits": 1}]}


@pytest.fixture
def node_token(client):
    r = client.post("/api/v1/nodes", json={}, headers=admin_headers())
    assert r.status_code == 200
    return r.json()["token"]


def test_missing_bearer_token_rejected(client):
    r = client.post("/api/v1/events", json=SAMPLE_BATCH)
    assert r.status_code == 401


def test_invalid_bearer_token_rejected(client):
    r = client.post("/api/v1/events", json=SAMPLE_BATCH, headers={"Authorization": "Bearer nmt_not_a_real_token"})
    assert r.status_code == 401


def test_valid_token_accepted(client, node_token):
    r = client.post("/api/v1/events", json=SAMPLE_BATCH, headers={"Authorization": f"Bearer {node_token}"})
    assert r.status_code == 200


def test_revoked_token_rejected(client, node_token):
    # node_token is fresh from the fixture, so its node is the most
    # recently created one.
    nodes = client.get("/api/v1/nodes", headers=admin_headers()).json()
    target = max(nodes, key=lambda n: n["id"])
    client.post(f"/api/v1/nodes/{target['id']}/revoke", headers=admin_headers())

    r = client.post("/api/v1/events", json=SAMPLE_BATCH, headers={"Authorization": f"Bearer {node_token}"})
    assert r.status_code == 403


def test_heartbeat_requires_node_auth(client):
    r = client.post("/api/v1/nodes/heartbeat", json={"version": "1.0.0"})
    assert r.status_code == 401


def test_sending_disabled_returns_503(client):
    r = client.post("/api/v1/nodes", json={}, headers=admin_headers())
    token = r.json()["token"]

    from app import fleet_control
    from app.main import app

    fleet_control.set_sending_enabled(app.state.db, False)
    try:
        r2 = client.post("/api/v1/events", json=SAMPLE_BATCH, headers={"Authorization": f"Bearer {token}"})
        assert r2.status_code == 503
    finally:
        fleet_control.set_sending_enabled(app.state.db, True)


def test_rate_limit_returns_429_with_retry_after(client):
    r = client.post("/api/v1/nodes", json={}, headers=admin_headers())
    token = r.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    from app.main import app

    # One real request creates this node's rate-limit bucket; drain it
    # directly afterward instead of firing dozens of requests just to
    # exhaust the default (generous) capacity.
    client.post("/api/v1/events", json=SAMPLE_BATCH, headers=headers)
    node_id = max(n["id"] for n in client.get("/api/v1/nodes", headers=admin_headers()).json())
    app.state.event_rate_limiter._buckets[node_id].tokens = 0

    r2 = client.post("/api/v1/events", json=SAMPLE_BATCH, headers=headers)
    assert r2.status_code == 429
    assert "Retry-After" in r2.headers
