"""NodeHealthMonitor: exactly one notification per online<->offline
transition, never a stream of repeats while a node stays down, and a
silent baseline on first observation (spec 5.5 / Part 1)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.health_monitor import NodeHealthMonitor
from app.notifier import Notifier


class SpyNotifier:
    """Records calls instead of touching Telegram - offline_count/
    recovered_count let tests assert exactly-once, not just "at least
    once"."""

    def __init__(self) -> None:
        self.offline_calls: list[int] = []
        self.recovered_calls: list[int] = []

    async def notify_offline(self, node) -> None:
        self.offline_calls.append(node.id)

    async def notify_recovered(self, node) -> None:
        self.recovered_calls.append(node.id)


@pytest.fixture
def spy_notifier():
    return SpyNotifier()


@pytest.fixture
def monitor(db, config, spy_notifier):
    return NodeHealthMonitor(db, config, spy_notifier)


def test_first_observation_is_a_silent_baseline(db, monitor, spy_notifier):
    node = db.create_node_auto("tok")
    db.touch_heartbeat(node.id)  # online right now
    asyncio.run(monitor._check_once())
    assert spy_notifier.offline_calls == []
    assert spy_notifier.recovered_calls == []
    assert db.get_node(node.id).last_known_online is True


def test_online_to_offline_fires_exactly_once(db, monitor, spy_notifier):
    node = db.create_node_auto("tok")
    db.touch_heartbeat(node.id)
    asyncio.run(monitor._check_once())  # baseline: online

    # Backdate the heartbeat past the offline threshold without a new one.
    stale = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    db._conn.execute("UPDATE nodes SET last_heartbeat_at = ? WHERE id = ?", (stale, node.id))
    db._conn.commit()

    asyncio.run(monitor._check_once())
    assert spy_notifier.offline_calls == [node.id]

    # Staying offline across further checks must not spam more alerts.
    asyncio.run(monitor._check_once())
    asyncio.run(monitor._check_once())
    assert spy_notifier.offline_calls == [node.id]
    assert spy_notifier.recovered_calls == []


def test_offline_to_online_fires_recovery_exactly_once(db, monitor, spy_notifier):
    node = db.create_node_auto("tok")
    stale = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    db._conn.execute("UPDATE nodes SET last_heartbeat_at = ? WHERE id = ?", (stale, node.id))
    db._conn.commit()
    asyncio.run(monitor._check_once())  # baseline: offline
    assert spy_notifier.offline_calls == []

    db.touch_heartbeat(node.id)  # heartbeat arrives - back online
    asyncio.run(monitor._check_once())
    assert spy_notifier.recovered_calls == [node.id]

    asyncio.run(monitor._check_once())
    assert spy_notifier.recovered_calls == [node.id]  # still exactly once


def test_revoked_node_never_alerts(db, monitor, spy_notifier):
    node = db.create_node_auto("tok")
    db.touch_heartbeat(node.id)
    asyncio.run(monitor._check_once())
    db.set_node_status(node.id, "revoked")

    stale = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    db._conn.execute("UPDATE nodes SET last_heartbeat_at = ? WHERE id = ?", (stale, node.id))
    db._conn.commit()
    asyncio.run(monitor._check_once())
    assert spy_notifier.offline_calls == []
