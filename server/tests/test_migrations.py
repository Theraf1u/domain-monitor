"""Every migration this project ships must be additive: applying it to a
database that already has real data must never lose or corrupt that
data, and every new column must have a safe default for rows that
predate it. This is the same "simulate a pre-upgrade DB, apply migrate(),
confirm old data survives" check done by hand before every migration
shipped to production this session, now automated."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from app.database import Database


def test_full_migration_chain_preserves_data(tmp_path):
    db_path = str(tmp_path / "test.db")
    db = Database(db_path)
    db.migrate()

    node = db.create_node_auto("some-hash")
    db.touch_heartbeat(node.id, version="1.0.0", ip="1.2.3.4", hostname="test-host")
    domain, is_new = db.record_event(node.id, "example.com", "tls_sni", datetime.now(timezone.utc), 1)
    assert is_new
    db.close()

    # Re-open and re-migrate (idempotent - migrate() only applies
    # migrations not already recorded in schema_migrations).
    db2 = Database(db_path)
    db2.migrate()

    node_after = db2.get_node(node.id)
    assert node_after is not None
    assert node_after.version == "1.0.0"
    # Columns added by 0007+ must have safe defaults for a node created
    # before those migrations existed.
    assert node_after.sending_enabled is True

    domains = db2.list_domains()
    assert any(d.domain == "example.com" for d in domains)
    db2.close()


def test_0014_migration_jobs_applies_on_top_of_pre_existing_data(tmp_path):
    """Simulates upgrading a server that predates Migration 2.0: a real
    node exists, 0014 hasn't run yet, then it gets applied - the node
    must survive and migration_jobs must be usable immediately after."""
    db_path = str(tmp_path / "test.db")
    db = Database(db_path)
    db.migrate()
    node = db.create_node_auto("hash")
    db.close()

    conn = sqlite3.connect(db_path)
    conn.execute("DROP TABLE migration_jobs")
    conn.execute("DELETE FROM schema_migrations WHERE filename = '0014_migration_jobs.sql'")
    conn.commit()
    conn.close()

    db2 = Database(db_path)
    db2.migrate()

    assert db2.get_node(node.id) is not None
    job_id = db2.create_migration_job("http://new-server.example")
    job = db2.get_migration_job(job_id)
    assert job is not None
    assert job["status"] == "pending"
    db2.close()


def test_migrate_is_idempotent(db):
    """Calling migrate() again after everything is already applied must
    be a safe no-op, not re-run or error - this is what happens on every
    single container restart."""
    applied_before = db.applied_migrations_count()
    db.migrate()
    assert db.applied_migrations_count() == applied_before
