"""Migration 2.0's /api/v1/migration endpoints - the REST layer that
migrate_to.sh/migrate_cutover.sh/etc. and the Telegram screen both drive
instead of touching migration_jobs directly."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    # module-scoped: aiogram's Router instances are module-level
    # singletons (see app/telegram/handlers/*.py) that can only ever be
    # attached to one Dispatcher - a fresh `TestClient(app)` per test
    # would re-run the app's lifespan and try to re-attach them,
    # raising "Router is already attached". One shared app per test
    # module (real production only ever starts lifespan once too) sums
    # to the same thing minus that artifact; each test below cleans up
    # its own migration_jobs state instead of relying on DB isolation.
    import os

    os.environ["DATA_DIR"] = str(tmp_path_factory.mktemp("data"))
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clear_active_job(client):
    """Every test starts from "no active migration job", regardless of
    what an earlier test in this module left behind."""
    yield
    active = client.get("/api/v1/migration/jobs/active", headers=auth_headers()).json()
    if active is not None:
        client.patch(
            f"/api/v1/migration/jobs/{active['id']}", json={"status": "cancelled"}, headers=auth_headers(),
        )


def auth_headers():
    import os

    return {"X-Admin-Key": os.environ["ADMIN_API_KEY"]}


def test_no_active_job_returns_null(client):
    r = client.get("/api/v1/migration/jobs/active", headers=auth_headers())
    assert r.status_code == 200
    assert r.json() is None


def test_create_job_then_it_becomes_active(client):
    r = client.post("/api/v1/migration/jobs", json={"target_url": "http://target.example"}, headers=auth_headers())
    assert r.status_code == 200
    job = r.json()
    assert job["status"] == "pending"
    assert job["target_url"] == "http://target.example"

    active = client.get("/api/v1/migration/jobs/active", headers=auth_headers())
    assert active.json()["id"] == job["id"]


def test_second_create_conflicts_while_one_is_active(client):
    client.post("/api/v1/migration/jobs", json={"target_url": "http://a.example"}, headers=auth_headers())
    r2 = client.post("/api/v1/migration/jobs", json={"target_url": "http://b.example"}, headers=auth_headers())
    assert r2.status_code == 409


def test_status_transitions_and_terminal_clears_active(client):
    created = client.post(
        "/api/v1/migration/jobs", json={"target_url": "http://target.example"}, headers=auth_headers(),
    ).json()
    job_id = created["id"]

    for status in ("standby", "cutover", "completed"):
        r = client.patch(f"/api/v1/migration/jobs/{job_id}", json={"status": status}, headers=auth_headers())
        assert r.status_code == 200
        assert r.json()["status"] == status

    active = client.get("/api/v1/migration/jobs/active", headers=auth_headers())
    assert active.json() is None

    # A new job can now be created since the previous one reached a terminal state.
    r = client.post("/api/v1/migration/jobs", json={"target_url": "http://second.example"}, headers=auth_headers())
    assert r.status_code == 200


def test_invalid_status_rejected(client):
    created = client.post(
        "/api/v1/migration/jobs", json={"target_url": "http://target.example"}, headers=auth_headers(),
    ).json()
    r = client.patch(
        f"/api/v1/migration/jobs/{created['id']}", json={"status": "not-a-real-status"}, headers=auth_headers(),
    )
    assert r.status_code == 422


def test_unknown_job_id_404s(client):
    r = client.get("/api/v1/migration/jobs/999999", headers=auth_headers())
    assert r.status_code == 404


def test_requires_admin_key(client):
    r = client.get("/api/v1/migration/jobs/active")
    assert r.status_code == 401


def test_create_refuses_while_backup_task_busy(client, monkeypatch):
    """spec 10: the other half of the concurrency guard - starting a
    migration must be refused while a backup/restore holds
    BackupTask's lock, even though nothing about backups is exposed
    over this API. is_busy() is a thin wrapper over the real
    asyncio.Lock (see test_backup.py for that side); here it's enough
    to prove the migration endpoint actually checks it."""
    from app.main import app

    monkeypatch.setattr(app.state.backup_task, "is_busy", lambda: True)
    r = client.post("/api/v1/migration/jobs", json={"target_url": "http://x.example"}, headers=auth_headers())
    assert r.status_code == 409
    assert "backup" in r.json()["detail"].lower()
