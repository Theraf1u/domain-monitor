"""Config values the admin can change at runtime from the Telegram
Настройки menu, without editing .env and restarting the container. Each
one is backed by the same settings table as app.notifier/app.fleet_control
- unset means "use the .env value", so a fresh install with nothing ever
touched in the menu behaves exactly as it did before this existed.
"""
from __future__ import annotations

from app.config import Config
from app.database import Database

SETTING_EVENT_RETENTION_DAYS = "event_retention_days"
SETTING_NODE_OFFLINE_AFTER_SECONDS = "node_offline_after_seconds"


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
