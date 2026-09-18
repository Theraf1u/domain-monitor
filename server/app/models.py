"""Plain dataclasses for rows read out of the database. Kept free of any
DB/HTTP-framework imports so they can be reused by the API layer, the
(future) Telegram bot and tests without pulling in extra dependencies."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Node:
    id: int
    name: str
    token_hash: str
    status: str  # "active" | "revoked"
    version: str | None
    ip: str | None
    hostname: str | None
    created_at: datetime
    last_seen_at: datetime | None
    last_heartbeat_at: datetime | None
    monitoring_enabled: bool
    notifications_enabled: bool
    sending_enabled: bool
    notify_destination: str  # "dm" | "group" | "both"
    notify_group_chat_id: int | None
    notify_group_topic_id: int | None
    agent_buffer_size: int | None

    def is_online(self, offline_after_seconds: int, now: datetime) -> bool:
        if self.status != "active" or self.last_heartbeat_at is None:
            return False
        return (now - self.last_heartbeat_at).total_seconds() <= offline_after_seconds


@dataclass(frozen=True)
class Domain:
    id: int
    domain: str
    first_seen: datetime
    last_seen: datetime
    hits: int
    node_id: int
    ignored: bool
    notification_sent: bool


@dataclass(frozen=True)
class Event:
    id: int
    node_id: int
    domain: str
    source: str
    occurred_at: datetime
    received_at: datetime


@dataclass(frozen=True)
class FilterRule:
    id: int
    list_type: str  # "ignore" | "allow" | "watch"
    pattern_type: str  # "exact" | "suffix" | "wildcard"
    pattern: str
    created_at: datetime
