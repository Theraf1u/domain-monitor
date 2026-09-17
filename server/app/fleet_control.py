"""Two global on/off switches for the whole agent fleet, toggled from the
Telegram main menu - distinct from the per-node "Мониторинг" toggle on
each node's own card, which only affects that one node.

- monitoring: whether agents should be capturing traffic at all.
- sending: whether agents should be pushing captured events to the server.

Persisted as ordinary settings rows (the same store app.notifier uses for
its batch-mode setting), so a value survives a server restart and reaches
every agent the next time it reports in via heartbeat - no agent restart
needed.
"""
from __future__ import annotations

from app.database import Database

SETTING_MONITORING_ENABLED = "fleet_monitoring_enabled"
SETTING_SENDING_ENABLED = "fleet_sending_enabled"


def is_monitoring_enabled(db: Database) -> bool:
    return db.get_setting(SETTING_MONITORING_ENABLED, "1") == "1"


def set_monitoring_enabled(db: Database, enabled: bool) -> None:
    db.set_setting(SETTING_MONITORING_ENABLED, "1" if enabled else "0")


def is_sending_enabled(db: Database) -> bool:
    return db.get_setting(SETTING_SENDING_ENABLED, "1") == "1"


def set_sending_enabled(db: Database, enabled: bool) -> None:
    db.set_setting(SETTING_SENDING_ENABLED, "1" if enabled else "0")
