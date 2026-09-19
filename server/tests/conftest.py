"""Shared fixtures. Every test runs against a real, throwaway SQLite
database (not mocks) - the same discipline used to verify every feature
in this codebase before it ever touched production."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("ADMIN_API_KEY", "test-admin-key-" + "x" * 20)
os.environ.setdefault("BOT_TOKEN", "123456:" + "a" * 35)
os.environ.setdefault("ADMIN_ID", "111111111")


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app.config import load_config

    return load_config()


@pytest.fixture
def db(config):
    # Same database_path a real Config resolves to (DATA_DIR/domain_monitor.db)
    # - tests that need both `db` and `config` (e.g. backup_task, which
    # reads config.database_path to find the file to archive) must see
    # the exact same file, not two independently-chosen temp paths.
    from app.database import Database

    database = Database(config.database_path)
    database.migrate()
    yield database
    database.close()


@pytest.fixture(scope="session")
def client(tmp_path_factory):
    """A FastAPI TestClient backed by a full app.main.app lifespan -
    shared across every test FILE in this session, not just within one
    module. aiogram's handler Routers are true module-level singletons
    (see app/telegram/handlers/*.py) that raise "Router is already
    attached" if a second Dispatcher ever tries to attach them - since
    FastAPI's lifespan (which builds the Dispatcher) would otherwise run
    once per module-scoped fixture, any second test file using its own
    client fixture collides with the first. One shared instance for the
    whole test session sidesteps that entirely (real production only
    ever starts lifespan once too, so this isn't testing anything
    fictional). Tests that need isolated state clean up after
    themselves instead of relying on a fresh DB per test."""
    import os

    os.environ["DATA_DIR"] = str(tmp_path_factory.mktemp("data"))
    from app.main import app

    with TestClient(app) as c:
        yield c


def admin_headers() -> dict:
    return {"X-Admin-Key": os.environ["ADMIN_API_KEY"]}
