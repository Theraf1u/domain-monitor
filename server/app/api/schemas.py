"""Pydantic request/response models for the REST API."""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator

from app.filters import normalize_domain
from app.models import Domain, Node

_VALID_SOURCES = {"tls_sni", "dns", "http_host", "quic", "xray_log"}


class NodeCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def _clean_name(cls, v: str) -> str:
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
        )


class NodeSettingsUpdateRequest(BaseModel):
    monitoring_enabled: bool | None = None
    notifications_enabled: bool | None = None


class HeartbeatRequest(BaseModel):
    version: str | None = None
    ip: str | None = None
    hostname: str | None = None


class EventIn(BaseModel):
    domain: str
    occurred_at: datetime | None = None
    source: str = "tls_sni"

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
