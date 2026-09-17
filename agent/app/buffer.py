"""Durable, bounded local outbox.

Captured domains are written here synchronously (from the sniffer) and
drained asynchronously by the uplink task. Bounding the table means a
prolonged server outage degrades gracefully - the oldest, least useful
events are dropped - instead of filling the disk.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class OutboxEntry:
    __slots__ = ("id", "domain", "source", "occurred_at")

    def __init__(self, id: int, domain: str, source: str, occurred_at: str) -> None:
        self.id = id
        self.domain = domain
        self.source = source
        self.occurred_at = occurred_at


class Buffer:
    def __init__(self, path: str, max_size: int) -> None:
        self.path = path
        self.max_size = max_size
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        # Tracked in memory instead of a COUNT(*) on every enqueue() - under
        # heavy capture load (hundreds of events/sec on a busy VPN node)
        # that query alone was a measurable chunk of the agent's CPU use.
        # Refreshed from the real table once at startup (migrate()); after
        # that, insert/delete keep it in sync without ever re-scanning.
        self._row_count = 0

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
            self._row_count = self._conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]

    def enqueue(self, domain: str, source: str, occurred_at: datetime) -> None:
        with self._lock:
            occurred_str = occurred_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            self._conn.execute(
                "INSERT INTO outbox (domain, source, occurred_at, created_at) VALUES (?, ?, ?, ?)",
                (domain, source, occurred_str, _now()),
            )
            self._row_count += 1
            if self._row_count > self.max_size:
                overflow = self._row_count - self.max_size
                self._conn.execute(
                    "DELETE FROM outbox WHERE id IN "
                    "(SELECT id FROM outbox ORDER BY id ASC LIMIT ?)",
                    (overflow,),
                )
                self._row_count -= overflow
                logger.warning(
                    "Outbox full (%d entries), dropped %d oldest event(s) - "
                    "server appears to be unreachable for a while",
                    self._row_count + overflow, overflow,
                )
            self._conn.commit()

    def peek_batch(self, limit: int) -> list[OutboxEntry]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT id, domain, source, occurred_at FROM outbox ORDER BY id ASC LIMIT ?",
                (limit,),
            )
            return [OutboxEntry(r["id"], r["domain"], r["source"], r["occurred_at"]) for r in cur.fetchall()]

    def delete_ids(self, ids: list[int]) -> None:
        if not ids:
            return
        with self._lock:
            placeholders = ",".join("?" for _ in ids)
            self._conn.execute(f"DELETE FROM outbox WHERE id IN ({placeholders})", ids)
            self._row_count = max(0, self._row_count - len(ids))
            self._conn.commit()

    def size(self) -> int:
        with self._lock:
            return self._row_count

    def close(self) -> None:
        with self._lock:
            self._conn.close()
