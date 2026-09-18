"""Pydantic request/response models for the REST API."""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator

from app.filters import normalize_domain
from app.models import Domain, Node

_VALID_SOURCES = {"tls_sni", "dns", "http_host", "quic", "xray_log"}


class NodeCreateRequest(BaseModel):
    # Omit name entirely for the one-tap flow: the server auto-assigns a
    # placeholder (`нода-{id}`), later replaced with the node's real IP
    # on its first heartbeat.
    name: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def _clean_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v


class NodeCreateResponse(BaseModel):
    id: int
    name: str
    token: str  # shown once


class NodeResponse(BaseModel):
    id: int
    name: str
    status: str
    online: bool
    version: str | None
    ip: str | None
    hostname: str | None
    created_at: datetime
    last_seen_at: datetime | None
    last_heartbeat_at: datetime | None
    monitoring_enabled: bool
    notifications_enabled: bool
    sending_enabled: bool

    @staticmethod
    def from_node(node: Node, offline_after_seconds: int) -> "NodeResponse":
        now = datetime.now(timezone.utc)
        return NodeResponse(
            id=node.id,
            name=node.name,
            status=node.status,
            online=node.is_online(offline_after_seconds, now),
            version=node.version,
            ip=node.ip,
            hostname=node.hostname,
            created_at=node.created_at,
            last_seen_at=node.last_seen_at,
            last_heartbeat_at=node.last_heartbeat_at,
            monitoring_enabled=node.monitoring_enabled,
            notifications_enabled=node.notifications_enabled,
            sending_enabled=node.sending_enabled,
        )


class NodeSettingsUpdateRequest(BaseModel):
    monitoring_enabled: bool | None = None
    notifications_enabled: bool | None = None
    sending_enabled: bool | None = None


class HeartbeatRequest(BaseModel):
    version: str | None = None
    ip: str | None = None
    hostname: str | None = None
    buffer_size: int | None = None
    # Everything below is optional and additive - an older agent that
    # doesn't send these yet is a perfectly valid heartbeat, and
    # touch_heartbeat()'s COALESCE-based update leaves the stored value
    # untouched when a field is absent, same as the original four.
    agent_uptime_seconds: int | None = None
    buffer_bytes: int | None = None
    buffer_limit_bytes: int | None = None
    dropped_events_total: int | None = None
    capture_tls_running: bool | None = None
    capture_dns_running: bool | None = None
    last_send_error: str | None = None
    last_send_success_at: datetime | None = None
    sources_supported: list[str] | None = None
    sources_enabled: list[str] | None = None


class HeartbeatResponse(BaseModel):
    # Effective state the agent should apply immediately - monitoring
    # already folds in both the per-node and fleet-wide switches, so the
    # agent doesn't need to know that distinction exists at all.
    monitoring_enabled: bool
    sending_enabled: bool


class EventIn(BaseModel):
    domain: str
    occurred_at: datetime | None = None
    source: str = "tls_sni"
    # How many real occurrences this one event represents - the agent
    # collapses repeats of the same domain in its local buffer into one
    # row with a counter instead of storing/sending one row per hit.
    # Bounded well above anything a real outage could legitimately
    # produce, as a sanity ceiling against a malformed or compromised
    # agent trying to inflate a domain's hit count.
    hits: int = Field(default=1, ge=1, le=1_000_000)

    @field_validator("domain")
    @classmethod
    def _validate_domain(cls, v: str) -> str:
        normalized = normalize_domain(v)
        if normalized is None:
            raise ValueError(f"not a valid hostname: {v!r}")
        return normalized

    @field_validator("source")
    @classmethod
    def _validate_source(cls, v: str) -> str:
        if v not in _VALID_SOURCES:
            raise ValueError(f"unknown source: {v!r}")
        return v


class EventBatchRequest(BaseModel):
    events: list[EventIn] = Field(min_length=1, max_length=1000)


class EventBatchResponse(BaseModel):
    accepted: int
    new_domains: list[str]


class DomainResponse(BaseModel):
    id: int
    domain: str
    first_seen: datetime
    last_seen: datetime
    hits: int
    node_id: int
    ignored: bool

    @staticmethod
    def from_domain(d: Domain) -> "DomainResponse":
        return DomainResponse(
            id=d.id, domain=d.domain, first_seen=d.first_seen, last_seen=d.last_seen,
            hits=d.hits, node_id=d.node_id, ignored=d.ignored,
        )


class StatsResponse(BaseModel):
    nodes_online: int
    nodes_total: int
    unique_domains: int
    events_today: int
    new_domains_today: int
