"""DB-backed settings for the automatic backup task - same settings-table
pattern as app.notifier / app.fleet_control / app.runtime_settings, so
values survive restarts and are editable live from Telegram.

Every setter clamps to a sane range - this is one of the anti-runaway
safeguards for the backup feature: no combination of bot input can push
the interval to ~0 (tight loop hammering disk/Telegram) or keep_count to
something that fills the disk with thousands of copies.
"""
from __future__ import annotations

from app.database import Database

SETTING_ENABLED = "backup_enabled"
SETTING_INTERVAL_HOURS = "backup_interval_hours"
SETTING_KEEP_COUNT = "backup_keep_count"
SETTING_DESTINATION = "backup_destination"  # "server" | "dm" | "group"
SETTING_GROUP_CHAT_ID = "backup_group_chat_id"
SETTING_GROUP_TOPIC_ID = "backup_group_topic_id"
SETTING_LAST_RUN_AT = "backup_last_run_at"

MIN_INTERVAL_HOURS = 1
MAX_INTERVAL_HOURS = 24 * 30
DEFAULT_INTERVAL_HOURS = 24

MIN_KEEP_COUNT = 1
MAX_KEEP_COUNT = 100
DEFAULT_KEEP_COUNT = 7

VALID_DESTINATIONS = ("server", "dm", "group")


def is_enabled(db: Database) -> bool:
    return db.get_setting(SETTING_ENABLED, "0") == "1"


def set_enabled(db: Database, enabled: bool) -> None:
    db.set_setting(SETTING_ENABLED, "1" if enabled else "0")


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def interval_hours(db: Database) -> int:
    raw = db.get_setting(SETTING_INTERVAL_HOURS, str(DEFAULT_INTERVAL_HOURS))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = DEFAULT_INTERVAL_HOURS
    return _clamp(value, MIN_INTERVAL_HOURS, MAX_INTERVAL_HOURS)


def set_interval_hours(db: Database, hours: int) -> None:
    db.set_setting(SETTING_INTERVAL_HOURS, str(_clamp(hours, MIN_INTERVAL_HOURS, MAX_INTERVAL_HOURS)))


def keep_count(db: Database) -> int:
    raw = db.get_setting(SETTING_KEEP_COUNT, str(DEFAULT_KEEP_COUNT))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = DEFAULT_KEEP_COUNT
    return _clamp(value, MIN_KEEP_COUNT, MAX_KEEP_COUNT)


def set_keep_count(db: Database, count: int) -> None:
    db.set_setting(SETTING_KEEP_COUNT, str(_clamp(count, MIN_KEEP_COUNT, MAX_KEEP_COUNT)))


def destination(db: Database) -> str:
    value = db.get_setting(SETTING_DESTINATION, "server") or "server"
    return value if value in VALID_DESTINATIONS else "server"


def set_destination(db: Database, dest: str) -> None:
    if dest not in VALID_DESTINATIONS:
        raise ValueError(f"unknown backup destination: {dest!r}")
    db.set_setting(SETTING_DESTINATION, dest)


def group_chat_id(db: Database) -> int | None:
    raw = db.get_setting(SETTING_GROUP_CHAT_ID)
    return int(raw) if raw else None


def set_group_chat_id(db: Database, chat_id: int) -> None:
    db.set_setting(SETTING_GROUP_CHAT_ID, str(chat_id))


def group_topic_id(db: Database) -> int | None:
    raw = db.get_setting(SETTING_GROUP_TOPIC_ID)
    return int(raw) if raw else None


def set_group_topic_id(db: Database, topic_id: int | None) -> None:
    db.set_setting(SETTING_GROUP_TOPIC_ID, str(topic_id) if topic_id is not None else "")


def last_run_at(db: Database) -> str | None:
    return db.get_setting(SETTING_LAST_RUN_AT)


def set_last_run_at(db: Database, iso_ts: str) -> None:
    db.set_setting(SETTING_LAST_RUN_AT, iso_ts)
