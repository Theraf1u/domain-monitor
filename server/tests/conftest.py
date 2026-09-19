"""Shared fixtures. Every test runs against a real, throwaway SQLite
database (not mocks) - the same discipline used to verify every feature
in this codebase before it ever touched production."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

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
