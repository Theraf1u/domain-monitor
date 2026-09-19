"""Notification Center (spec Part 1 section 8): per-type toggles, quiet
hours (including the midnight-wraparound case), and should_deliver()'s
combined gating logic."""
from __future__ import annotations

from datetime import datetime, timezone

from app import runtime_settings
from app.notifier import Notifier


def make_notifier(db, config):
    # is_quiet_hours_active() converts "now" using the configured
    # timezone offset, which defaults to the HOST machine's own local
    # timezone when unset - pin it to UTC so these tests are
    # deterministic regardless of what machine/CI runs them.
    runtime_settings.set_timezone_offset_minutes(db, 0)
    return Notifier(db, config.admin_ids, config)


def test_type_enabled_defaults_true_and_can_be_toggled(db, config):
    n = make_notifier(db, config)
    assert n.is_type_enabled("new_domain") is True
    n.set_type_enabled("new_domain", False)
    assert n.is_type_enabled("new_domain") is False
    n.set_type_enabled("new_domain", True)
    assert n.is_type_enabled("new_domain") is True


def test_quiet_hours_disabled_by_default(db, config):
    n = make_notifier(db, config)
    enabled, start, end = n.quiet_hours()
    assert enabled is False
    assert n.is_quiet_hours_active() is False


def test_quiet_hours_simple_window(db, config):
    n = make_notifier(db, config)
    n.set_quiet_hours(True, "23:00", "08:00")
    # window wraps past midnight: active at 23:30 and at 02:00, not at 12:00
    assert n.is_quiet_hours_active(datetime(2026, 1, 1, 23, 30, tzinfo=timezone.utc)) is True
    assert n.is_quiet_hours_active(datetime(2026, 1, 1, 2, 0, tzinfo=timezone.utc)) is True
    assert n.is_quiet_hours_active(datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)) is False


def test_quiet_hours_non_wrapping_window(db, config):
    n = make_notifier(db, config)
    n.set_quiet_hours(True, "13:00", "14:00")
    assert n.is_quiet_hours_active(datetime(2026, 1, 1, 13, 30, tzinfo=timezone.utc)) is True
    assert n.is_quiet_hours_active(datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)) is False
    assert n.is_quiet_hours_active(datetime(2026, 1, 1, 15, 0, tzinfo=timezone.utc)) is False


def test_should_deliver_respects_type_toggle(db, config):
    n = make_notifier(db, config)
    assert n.should_deliver("new_domain") is True
    n.set_type_enabled("new_domain", False)
    assert n.should_deliver("new_domain") is False


def test_should_deliver_respects_global_toggle(db, config):
    n = make_notifier(db, config)
    assert n.should_deliver("new_domain") is True
    n.set_globally_enabled(False)
    assert n.should_deliver("new_domain") is False
    n.set_globally_enabled(True)
    assert n.should_deliver("new_domain") is True


def test_should_deliver_blocked_during_quiet_hours_unless_type_ignores_it(db, config):
    n = make_notifier(db, config)
    n.set_quiet_hours(True, "00:00", "23:59")  # active almost all day
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    # A normal type respects quiet hours.
    n.is_quiet_hours_active = lambda now=None: True  # pin quiet hours "on" regardless of wall clock
    assert n.should_deliver("new_domain") is False

    # node_offline is critical by default (ignore_quiet_hours_default) - must still fire.
    assert n.should_deliver("node_offline") is True


def test_ignores_quiet_hours_can_be_overridden_per_type(db, config):
    n = make_notifier(db, config)
    assert n.ignores_quiet_hours("node_offline") is True  # critical by default
    n.set_ignores_quiet_hours("node_offline", False)
    assert n.ignores_quiet_hours("node_offline") is False
    assert n.ignores_quiet_hours("new_domain") is False  # non-critical default
    n.set_ignores_quiet_hours("new_domain", True)
    assert n.ignores_quiet_hours("new_domain") is True
