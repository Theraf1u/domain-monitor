"""Prometheus metrics. A handful of counters/gauges updated inline at the
call sites that already know the relevant fact, rather than a separate
polling job - cheaper and can't drift out of sync with reality."""
from __future__ import annotations

from prometheus_client import Counter, Gauge

EVENTS_TOTAL = Counter("events_total", "Total domain-sighting events ingested")
NEW_DOMAINS_TOTAL = Counter("new_domains_total", "Total newly-seen unique domains")
WATCHLIST_HITS_TOTAL = Counter("watchlist_hits_total", "Total events matching a watch-list rule")
TELEGRAM_ERRORS_TOTAL = Counter("telegram_errors_total", "Total errors sending a Telegram message")
NODES_ONLINE = Gauge("nodes_online", "Nodes currently considered online")
NODES_TOTAL = Gauge("nodes_total", "Total registered nodes")
DOMAINS_TOTAL = Gauge("domains_total", "Total unique domains ever seen")
