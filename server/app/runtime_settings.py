"""Config values the admin can change at runtime from the Telegram
Настройки menu, without editing .env and restarting the container. Each
one is backed by the same settings table as app.notifier/app.fleet_control
- unset means "use the .env value", so a fresh install with nothing ever
touched in the menu behaves exactly as it did before this existed.
"""
from __future__ import annotations

import time

from app.config import Config
from app.database import Database

SETTING_EVENT_RETENTION_DAYS = "event_retention_days"
SETTING_NODE_OFFLINE_AFTER_SECONDS = "node_offline_after_seconds"
SETTING_TIMEZONE_OFFSET_MINUTES = "timezone_offset_minutes"


def _host_timezone_offset_minutes() -> int:
    """Best-effort guess at the host's local timezone, used only as the
    default before anyone has set one explicitly via the bot (spec 7.2:
    "по умолчанию попытаться определить timezone хоста, иначе UTC").
    time.timezone/altzone are already host-local by definition - no
    external tzdata lookup needed, so this can't fail."""
    offset_seconds = -(time.altzone if time.daylight and time.localtime().tm_isdst else time.timezone)
    return offset_seconds // 60


def get_timezone_offset_minutes(db: Database, config: Config) -> int:
    raw = db.get_setting(SETTING_TIMEZONE_OFFSET_MINUTES)
    if raw is not None:
        return int(raw)
    return _host_timezone_offset_minutes()


def set_timezone_offset_minutes(db: Database, minutes: int) -> None:
    db.set_setting(SETTING_TIMEZONE_OFFSET_MINUTES, str(minutes))


SETTING_BUFFER_WARNING_PCT = "buffer_warning_pct"
SETTING_BUFFER_CRITICAL_PCT = "buffer_critical_pct"
_DEFAULT_BUFFER_WARNING_PCT = 70
_DEFAULT_BUFFER_CRITICAL_PCT = 90


def get_buffer_warning_pct(db: Database) -> int:
    raw = db.get_setting(SETTING_BUFFER_WARNING_PCT)
    return int(raw) if raw is not None else _DEFAULT_BUFFER_WARNING_PCT


def get_buffer_critical_pct(db: Database) -> int:
    raw = db.get_setting(SETTING_BUFFER_CRITICAL_PCT)
    return int(raw) if raw is not None else _DEFAULT_BUFFER_CRITICAL_PCT


def set_buffer_thresholds(db: Database, warning_pct: int, critical_pct: int) -> None:
    db.set_setting(SETTING_BUFFER_WARNING_PCT, str(warning_pct))
    db.set_setting(SETTING_BUFFER_CRITICAL_PCT, str(critical_pct))


def get_event_retention_days(db: Database, config: Config) -> int:
    raw = db.get_setting(SETTING_EVENT_RETENTION_DAYS)
    return int(raw) if raw is not None else config.event_retention_days


def set_event_retention_days(db: Database, days: int) -> None:
    db.set_setting(SETTING_EVENT_RETENTION_DAYS, str(days))


def get_node_offline_after_seconds(db: Database, config: Config) -> int:
    raw = db.get_setting(SETTING_NODE_OFFLINE_AFTER_SECONDS)
    return int(raw) if raw is not None else config.node_offline_after_seconds


def set_node_offline_after_seconds(db: Database, seconds: int) -> None:
    db.set_setting(SETTING_NODE_OFFLINE_AFTER_SECONDS, str(seconds))
