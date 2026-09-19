"""The heartbeat endpoint must only advertise migration_target_url while
a migration job is in 'cutover' - never before (the point of standby),
and never after (a completed/cancelled/failed job must stop offering
it to any node that hasn't already switched)."""
from __future__ import annotations

from app.api.nodes import heartbeat
from app.api.schemas import HeartbeatRequest


def test_migration_target_url_only_during_cutover(db, config):
    node = db.create_node_auto("tok")
    req = HeartbeatRequest(version="1.0.0")

    resp = heartbeat(req, node, db)
    assert resp.migration_target_url is None

    job_id = db.create_migration_job("http://target.example")
    resp = heartbeat(req, node, db)
    assert resp.migration_target_url is None, "pending must not advertise the target yet"

    db.set_migration_job_status(job_id, "standby")
    resp = heartbeat(req, node, db)
    assert resp.migration_target_url is None, "standby must not advertise the target yet"

    db.set_migration_job_status(job_id, "cutover")
    resp = heartbeat(req, node, db)
    assert resp.migration_target_url == "http://target.example"

    db.set_migration_job_status(job_id, "completed")
    resp = heartbeat(req, node, db)
    assert resp.migration_target_url is None, "a finished job must stop being advertised"


def test_heartbeat_still_reports_monitoring_and_sending_flags(db, config):
    node = db.create_node_auto("tok")
    resp = heartbeat(HeartbeatRequest(version="1.0.0"), node, db)
    assert resp.monitoring_enabled is True
    assert resp.sending_enabled is True
