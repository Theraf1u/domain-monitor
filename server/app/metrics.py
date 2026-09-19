"""Prometheus metrics. A handful of counters/gauges updated inline at the
call sites that already know the relevant fact, rather than a separate
polling job - cheaper and can't drift out of sync with reality.

Spec 2.0 Part 2, section 12 is explicit that labels must never carry raw
domains or secrets - every label used here is either a small fixed enum
(kind="db"/"full", status="failed"/"success") or a bare numeric node id,
never a domain, filename, or token."""
from __future__ import annotations

from prometheus_client import Counter, Gauge

EVENTS_TOTAL = Counter("events_total", "Total domain-sighting events ingested")
NEW_DOMAINS_TOTAL = Counter("new_domains_total", "Total newly-seen unique domains")
WATCHLIST_HITS_TOTAL = Counter("watchlist_hits_total", "Total events matching a watch-list rule")
IGNORE_HITS_TOTAL = Counter("ignore_hits_total", "Total events matching an ignore-list rule")
TELEGRAM_ERRORS_TOTAL = Counter("telegram_errors_total", "Total errors sending a Telegram message")
NODES_ONLINE = Gauge("nodes_online", "Nodes currently considered online")
NODES_OFFLINE = Gauge("nodes_offline", "Nodes currently considered offline")
NODES_TOTAL = Gauge("nodes_total", "Total registered nodes")
DOMAINS_TOTAL = Gauge("domains_total", "Total unique domains ever seen")
DATABASE_SIZE_BYTES = Gauge("database_size_bytes", "Size of the server's own SQLite database file")
NODE_BUFFER_BYTES = Gauge("node_buffer_bytes", "Agent-reported local outbox size", ["node_id"])
EVENTS_RATE_LIMITED_TOTAL = Counter(
    "events_rate_limited_total", "Total /api/v1/events requests rejected by the per-node rate limiter",
)
BACKUP_RESULT_TOTAL = Counter(
    "backup_result_total", "Total backup attempts by outcome", ["kind", "result"],
)
MIGRATION_ACTIVE = Gauge(
    "migration_active", "1 if a migration job is in progress (any non-terminal status), else 0",
)
