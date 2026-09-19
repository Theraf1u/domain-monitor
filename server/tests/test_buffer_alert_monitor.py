"""BufferAlertMonitor (spec 12): configurable warning/critical
thresholds, alert only on a threshold crossing (never every tick), a
recovery notice when it drops back down, and never guessing a
percentage when buffer_limit_bytes is unknown."""
from __future__ import annotations

import asyncio

import pytest

from app.buffer_alert_monitor import BufferAlertMonitor, _level_for
from app.notifier import Notifier


@pytest.mark.parametrize(
    "buffer_bytes,limit,warning_pct,critical_pct,expected",
    [
        (None, 1000, 70, 90, None),
        (500, None, 70, 90, None),
        (500, 0, 70, 90, None),
        (100, 1000, 70, 90, "ok"),
        (700, 1000, 70, 90, "warning"),
        (900, 1000, 70, 90, "critical"),
        (699, 1000, 70, 90, "ok"),
    ],
)
def test_level_for(buffer_bytes, limit, warning_pct, critical_pct, expected):
    assert _level_for(buffer_bytes, limit, warning_pct, critical_pct) == expected


@pytest.fixture
def notifier(db, config):
    return Notifier(db, config.admin_ids, config)


@pytest.fixture
def monitor(db, notifier):
    return BufferAlertMonitor(db, notifier)


@pytest.fixture
def spy_deliver(notifier, monkeypatch):
    calls = []

    async def fake_deliver(node, text, parse_mode="HTML"):
        calls.append((node.id, text))

    monkeypatch.setattr(notifier, "deliver_for_node", fake_deliver)
    return calls


def test_crossing_warning_threshold_alerts_once(db, monitor, spy_deliver):
    node = db.create_node_auto("tok")
    db.touch_heartbeat(node.id, version='1.0.0', buffer_bytes=750, buffer_limit_bytes=1000)  # 75% -> warning
    asyncio.run(monitor._check_once())
    assert len(spy_deliver) == 1
    assert "заполняется" in spy_deliver[0][1]

    # Staying in the same band on the next tick must not re-alert.
    asyncio.run(monitor._check_once())
    assert len(spy_deliver) == 1


def test_crossing_critical_threshold_alerts(db, monitor, spy_deliver):
    node = db.create_node_auto("tok")
    db.touch_heartbeat(node.id, version='1.0.0', buffer_bytes=950, buffer_limit_bytes=1000)  # 95% -> critical
    asyncio.run(monitor._check_once())
    assert len(spy_deliver) == 1
    assert "критически" in spy_deliver[0][1]


def test_recovery_after_critical_fires_recovery_notice(db, monitor, spy_deliver):
    node = db.create_node_auto("tok")
    db.touch_heartbeat(node.id, version='1.0.0', buffer_bytes=950, buffer_limit_bytes=1000)
    asyncio.run(monitor._check_once())
    assert len(spy_deliver) == 1

    db.touch_heartbeat(node.id, version='1.0.0', buffer_bytes=100, buffer_limit_bytes=1000)  # back to 10% - ok
    asyncio.run(monitor._check_once())
    assert len(spy_deliver) == 2
    assert "снова в норме" in spy_deliver[1][1]

    # Staying "ok" must not re-fire the recovery notice.
    asyncio.run(monitor._check_once())
    assert len(spy_deliver) == 2


def test_unknown_buffer_limit_never_alerts(db, monitor, spy_deliver):
    node = db.create_node_auto("tok")
    db.touch_heartbeat(node.id, version='1.0.0', buffer_bytes=999999)  # no buffer_limit_bytes - old agent
    asyncio.run(monitor._check_once())
    assert spy_deliver == []


def test_thresholds_are_configurable(db, monitor, spy_deliver):
    from app import runtime_settings

    runtime_settings.set_buffer_thresholds(db, 50, 60)
    node = db.create_node_auto("tok")
    db.touch_heartbeat(node.id, version='1.0.0', buffer_bytes=550, buffer_limit_bytes=1000)  # 55% - warning at the lowered threshold
    asyncio.run(monitor._check_once())
    assert len(spy_deliver) == 1
    assert "заполняется" in spy_deliver[0][1]
