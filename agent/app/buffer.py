"""Durable, bounded local outbox.

Captured domains are written here synchronously (from the sniffer) and
drained asynchronously by the uplink task. One row per (domain, source):
a domain hit repeatedly while unsent bumps that row's `hits` counter
instead of adding another near-identical row - no point storing
thousands of copies of the same domain while the server is unreachable.

Bounded by estimated on-disk size (not row count) so a prolonged outage
degrades gracefully - the oldest, least-recently-seen domains are
dropped - instead of filling the node's disk. The estimate is a cheap
running total kept in memory (domain/source string length + a fixed
per-row overhead), not a real stat() of the file: exact byte-for-byte
accuracy isn't the point, staying in the right ballpark cheaply is -
the same reasoning that keeps row-count tracking in memory instead of
a `COUNT(*)` on every single insert.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations")

# Fixed per-row overhead in the size estimate: id, hits, three timestamp
# columns, index entries. Not exact - just enough that the estimate
# tracks reality closely enough for a soft cap.
_ROW_OVERHEAD_BYTES = 96

# How many enqueue() calls between size-cap checks - checking after every
# single insert would mean an extra comparison on every packet under
# heavy capture load for no practical benefit, since a handful of rows
# either way never matters against a gigabyte budget.
_SIZE_CHECK_EVERY = 50


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _row_bytes(domain: str, source: str) -> int:
    return len(domain.encode("utf-8")) + len(source.encode("utf-8")) + _ROW_OVERHEAD_BYTES


class OutboxEntry:
    __slots__ = ("id", "domain", "source", "occurred_at", "hits")

    def __init__(self, id: int, domain: str, source: str, occurred_at: str, hits: int) -> None:
        self.id = id
        self.domain = domain
        self.source = source
        self.occurred_at = occurred_at
        self.hits = hits


class Buffer:
    def __init__(self, path: str, max_bytes: int) -> None:
        self.path = path
        self.max_bytes = max_bytes
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        # All three kept in memory and updated on every insert/update/
        # delete, never re-derived with a table scan except once here at
        # startup - see the module docstring.
        self._row_count = 0
        self._pending_hits = 0
        self._estimated_bytes = 0
        self._since_last_check = 0

    def migrate(self) -> None:
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "  filename TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied = {r[0] for r in self._conn.execute("SELECT filename FROM schema_migrations")}
            pending = sorted(
                f for f in os.listdir(_MIGRATIONS_DIR) if f.endswith(".sql") and f not in applied
            )
            for filename in pending:
                with open(os.path.join(_MIGRATIONS_DIR, filename), "r", encoding="utf-8") as fh:
                    sql = fh.read()
                logger.info("Applying migration %s", filename)
                self._conn.executescript(sql)
                self._conn.execute(
                    "INSERT INTO schema_migrations (filename, applied_at) VALUES (?, ?)",
                    (filename, _now()),
                )
                self._conn.commit()

            cur = self._conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(hits), 0), "
                "COALESCE(SUM(LENGTH(domain) + LENGTH(source)), 0) FROM outbox"
            )
            row_count, pending_hits, string_bytes = cur.fetchone()
            self._row_count = row_count
            self._pending_hits = pending_hits
            self._estimated_bytes = string_bytes + row_count * _ROW_OVERHEAD_BYTES

    def enqueue(self, domain: str, source: str, occurred_at: datetime) -> None:
        with self._lock:
            occurred_str = occurred_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            # UPDATE first, INSERT only if nothing matched - two simple
            # statements instead of one ON CONFLICT, but rowcount then
            # tells us unambiguously whether this was a new domain (so
            # the in-memory counters below can be adjusted exactly,
            # instead of drifting from reality and needing periodic
            # re-scans to correct).
            cur = self._conn.execute(
                "UPDATE outbox SET hits = hits + 1, last_occurred_at = ? "
                "WHERE domain = ? AND source = ?",
                (occurred_str, domain, source),
            )
            if cur.rowcount == 0:
                self._conn.execute(
                    "INSERT INTO outbox (domain, source, occurred_at, last_occurred_at, created_at, hits) "
                    "VALUES (?, ?, ?, ?, ?, 1)",
                    (domain, source, occurred_str, occurred_str, _now()),
                )
                self._row_count += 1
                self._estimated_bytes += _row_bytes(domain, source)
            self._pending_hits += 1

            self._since_last_check += 1
            if self._since_last_check >= _SIZE_CHECK_EVERY:
                self._since_last_check = 0
                self._enforce_size_cap()
            self._conn.commit()

    def _enforce_size_cap(self) -> None:
        if self._estimated_bytes <= self.max_bytes:
            return
        # Evict oldest-by-least-recently-seen in batches until back under
        # budget, instead of one row at a time - under a sustained outage
        # this check runs every _SIZE_CHECK_EVERY enqueues, so trimming a
        # single row each time would mean doing this work constantly.
        to_drop = max(1, self._row_count // 20)  # ~5% of the table at a time
        cur = self._conn.execute(
            "SELECT id, LENGTH(domain) + LENGTH(source) AS sz, hits FROM outbox "
            "ORDER BY last_occurred_at ASC LIMIT ?",
            (to_drop,),
        )
        rows = cur.fetchall()
        if not rows:
            return
        ids = [r["id"] for r in rows]
        freed_bytes = sum(r["sz"] + _ROW_OVERHEAD_BYTES for r in rows)
        freed_hits = sum(r["hits"] for r in rows)
        placeholders = ",".join("?" for _ in ids)
        self._conn.execute(f"DELETE FROM outbox WHERE id IN ({placeholders})", ids)
        self._row_count -= len(ids)
        self._pending_hits = max(0, self._pending_hits - freed_hits)
        self._estimated_bytes = max(0, self._estimated_bytes - freed_bytes)
        logger.warning(
            "Outbox over its %d MB budget, dropped %d least-recently-seen domain(s) "
            "(%d buffered hits) - server appears to be unreachable for a while",
            self.max_bytes // (1024 * 1024), len(ids), freed_hits,
        )
        if self._estimated_bytes > self.max_bytes:
            self._enforce_size_cap()

    def peek_batch(self, limit: int) -> list[OutboxEntry]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT id, domain, source, last_occurred_at, hits FROM outbox "
                "ORDER BY last_occurred_at ASC LIMIT ?",
                (limit,),
            )
            return [
                OutboxEntry(r["id"], r["domain"], r["source"], r["last_occurred_at"], r["hits"])
                for r in cur.fetchall()
            ]

    def delete_ids(self, ids: list[int]) -> None:
        if not ids:
            return
        with self._lock:
            placeholders = ",".join("?" for _ in ids)
            cur = self._conn.execute(
                f"SELECT COALESCE(SUM(hits), 0), COALESCE(SUM(LENGTH(domain) + LENGTH(source)), 0) "
                f"FROM outbox WHERE id IN ({placeholders})", ids,
            )
            freed_hits, freed_string_bytes = cur.fetchone()
            self._conn.execute(f"DELETE FROM outbox WHERE id IN ({placeholders})", ids)
            self._row_count = max(0, self._row_count - len(ids))
            self._pending_hits = max(0, self._pending_hits - freed_hits)
            self._estimated_bytes = max(0, self._estimated_bytes - freed_string_bytes - len(ids) * _ROW_OVERHEAD_BYTES)
            self._conn.commit()

    def size(self) -> int:
        """Total buffered-but-unsent hits (not distinct domains) - what
        gets reported in the heartbeat and shown to the admin, matching
        what "buffered, not sent yet" meant before dedupe existed."""
        with self._lock:
            return self._pending_hits

    def close(self) -> None:
        with self._lock:
            self._conn.close()
