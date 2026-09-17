"""Data access layer for the server.

Same rationale as the agent's database.py: hand-written sqlite3 wrapper,
one method per operation, module-level lock for write serialization, WAL
journal mode. FastAPI route handlers are plain `def` (not `async def`), so
the framework runs them in its own thread pool automatically - a slow disk
write never blocks the event loop, and this lock only has to protect
against genuinely concurrent requests, not a single-threaded event loop.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from app.models import Domain, Event, FilterRule, Node

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _fmt_ts(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")

    # ------------------------------------------------------------------
    # Schema / migrations
    # ------------------------------------------------------------------

    def migrate(self) -> None:
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "  filename TEXT PRIMARY KEY,"
                "  applied_at TEXT NOT NULL"
                ")"
            )
            applied = {
                row[0] for row in self._conn.execute("SELECT filename FROM schema_migrations")
            }
            pending = sorted(
                f for f in os.listdir(_MIGRATIONS_DIR) if f.endswith(".sql") and f not in applied
            )
            for filename in pending:
                path = os.path.join(_MIGRATIONS_DIR, filename)
                with open(path, "r", encoding="utf-8") as fh:
                    sql = fh.read()
                logger.info("Applying migration %s", filename)
                self._conn.executescript(sql)
                self._conn.execute(
                    "INSERT INTO schema_migrations (filename, applied_at) VALUES (?, ?)",
                    (filename, _now()),
                )
                self._conn.commit()

    # ------------------------------------------------------------------
    # Nodes
    # ------------------------------------------------------------------

    def create_node(self, name: str, token_hash: str) -> Node:
        with self._lock:
            self._conn.execute(
                "INSERT INTO nodes (name, token_hash, status, created_at, monitoring_enabled, "
                "notifications_enabled) VALUES (?, ?, 'active', ?, 1, 1)",
                (name, token_hash, _now()),
            )
            self._conn.commit()
            cur = self._conn.execute("SELECT * FROM nodes WHERE name = ?", (name,))
            return self._row_to_node(cur.fetchone())

    def name_exists(self, name: str) -> bool:
        with self._lock:
            cur = self._conn.execute("SELECT 1 FROM nodes WHERE name = ?", (name,))
            return cur.fetchone() is not None

    def get_node(self, node_id: int) -> Node | None:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,))
            row = cur.fetchone()
            return self._row_to_node(row) if row else None

    def get_node_by_token_hash(self, token_hash: str) -> Node | None:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM nodes WHERE token_hash = ?", (token_hash,))
            row = cur.fetchone()
            return self._row_to_node(row) if row else None

    def list_nodes(self) -> list[Node]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM nodes ORDER BY name ASC")
            return [self._row_to_node(r) for r in cur.fetchall()]

    def regenerate_token(self, node_id: int, token_hash: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE nodes SET token_hash = ?, status = 'active' WHERE id = ?",
                (token_hash, node_id),
            )
            self._conn.commit()

    def set_node_status(self, node_id: int, status: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE nodes SET status = ? WHERE id = ?", (status, node_id))
            self._conn.commit()

    def delete_node(self, node_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
            self._conn.commit()

    def set_node_monitoring(self, node_id: int, enabled: bool) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE nodes SET monitoring_enabled = ? WHERE id = ?", (int(enabled), node_id)
            )
            self._conn.commit()

    def set_node_notifications(self, node_id: int, enabled: bool) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE nodes SET notifications_enabled = ? WHERE id = ?", (int(enabled), node_id)
            )
            self._conn.commit()

    def touch_heartbeat(
        self, node_id: int, version: str | None = None, ip: str | None = None,
        hostname: str | None = None,
    ) -> None:
        with self._lock:
            now = _now()
            if version is not None or ip is not None or hostname is not None:
                self._conn.execute(
                    "UPDATE nodes SET last_heartbeat_at = ?, last_seen_at = ?, "
                    "version = COALESCE(?, version), ip = COALESCE(?, ip), "
                    "hostname = COALESCE(?, hostname) WHERE id = ?",
                    (now, now, version, ip, hostname, node_id),
                )
            else:
                self._conn.execute(
                    "UPDATE nodes SET last_heartbeat_at = ?, last_seen_at = ? WHERE id = ?",
                    (now, now, node_id),
                )
            self._conn.commit()

    def touch_last_seen(self, node_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE nodes SET last_seen_at = ? WHERE id = ?", (_now(), node_id)
            )
            self._conn.commit()

    @staticmethod
    def _row_to_node(row: sqlite3.Row) -> Node:
        return Node(
            id=row["id"],
            name=row["name"],
            token_hash=row["token_hash"],
            status=row["status"],
            version=row["version"],
            ip=row["ip"],
            hostname=row["hostname"],
            created_at=_parse_ts(row["created_at"]),
            last_seen_at=_parse_ts(row["last_seen_at"]),
            last_heartbeat_at=_parse_ts(row["last_heartbeat_at"]),
            monitoring_enabled=bool(row["monitoring_enabled"]),
            notifications_enabled=bool(row["notifications_enabled"]),
        )

    # ------------------------------------------------------------------
    # Domains + events
    # ------------------------------------------------------------------

    def record_event(self, node_id: int, domain: str, source: str, occurred_at: datetime) -> tuple[Domain, bool]:
        """Insert an event row and upsert the aggregate `domains` row.
        Returns (domain_row, is_new)."""
        with self._lock:
            now = _now()
            occurred_str = _fmt_ts(occurred_at)

            self._conn.execute(
                "INSERT INTO events (node_id, domain, source, occurred_at, received_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (node_id, domain, source, occurred_str, now),
            )

            cur = self._conn.execute("SELECT * FROM domains WHERE domain = ?", (domain,))
            row = cur.fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO domains "
                    "(domain, first_seen, last_seen, hits, node_id, ignored, notification_sent) "
                    "VALUES (?, ?, ?, 1, ?, 0, 0)",
                    (domain, occurred_str, occurred_str, node_id),
                )
                self._conn.commit()
                cur = self._conn.execute("SELECT * FROM domains WHERE domain = ?", (domain,))
                return self._row_to_domain(cur.fetchone()), True

            self._conn.execute(
                "UPDATE domains SET last_seen = ?, hits = hits + 1, node_id = ? WHERE id = ?",
                (occurred_str, node_id, row["id"]),
            )
            self._conn.commit()
            cur = self._conn.execute("SELECT * FROM domains WHERE id = ?", (row["id"],))
            return self._row_to_domain(cur.fetchone()), False

    def get_domain(self, domain_id: int) -> Domain | None:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM domains WHERE id = ?", (domain_id,))
            row = cur.fetchone()
            return self._row_to_domain(row) if row else None

    def mark_notified(self, domain_id: int) -> None:
        with self._lock:
            self._conn.execute("UPDATE domains SET notification_sent = 1 WHERE id = ?", (domain_id,))
            self._conn.commit()

    def pending_notifications(self, limit: int = 50) -> list[Domain]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM domains WHERE notification_sent = 0 AND ignored = 0 "
                "ORDER BY first_seen ASC LIMIT ?",
                (limit,),
            )
            return [self._row_to_domain(r) for r in cur.fetchall()]

    def set_ignored(self, domain_id: int, ignored: bool) -> None:
        with self._lock:
            self._conn.execute("UPDATE domains SET ignored = ? WHERE id = ?", (int(ignored), domain_id))
            self._conn.commit()

    def list_domains(
        self, limit: int = 50, offset: int = 0, search: str | None = None,
        order_by: str = "last_seen", node_id: int | None = None,
    ) -> list[Domain]:
        order_col = {
            "last_seen": "last_seen DESC",
            "first_seen": "first_seen DESC",
            "hits": "hits DESC",
            "domain": "domain ASC",
        }.get(order_by, "last_seen DESC")

        clauses: list[str] = []
        params: list[object] = []
        if search:
            clauses.append("domain LIKE ?")
            params.append(f"%{search}%")
        if node_id is not None:
            clauses.append("node_id = ?")
            params.append(node_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        with self._lock:
            cur = self._conn.execute(
                f"SELECT * FROM domains {where} ORDER BY {order_col} LIMIT ? OFFSET ?",
                (*params, limit, offset),
            )
            return [self._row_to_domain(r) for r in cur.fetchall()]

    def count_domains(self, since: datetime | None = None) -> int:
        with self._lock:
            if since is None:
                cur = self._conn.execute("SELECT COUNT(*) FROM domains")
            else:
                cur = self._conn.execute(
                    "SELECT COUNT(*) FROM domains WHERE first_seen >= ?", (_fmt_ts(since),)
                )
            return cur.fetchone()[0]

    def count_events_since(self, since: datetime) -> int:
        with self._lock:
            cur = self._conn.execute(
                "SELECT COUNT(*) FROM events WHERE occurred_at >= ?", (_fmt_ts(since),)
            )
            return cur.fetchone()[0]

    def top_domains(self, limit: int = 10) -> list[Domain]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM domains ORDER BY hits DESC LIMIT ?", (limit,))
            return [self._row_to_domain(r) for r in cur.fetchall()]

    def purge_events_older_than(self, cutoff: datetime) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM events WHERE occurred_at < ?", (_fmt_ts(cutoff),))
            self._conn.commit()
            return cur.rowcount

    @staticmethod
    def _row_to_domain(row: sqlite3.Row) -> Domain:
        return Domain(
            id=row["id"],
            domain=row["domain"],
            first_seen=_parse_ts(row["first_seen"]),
            last_seen=_parse_ts(row["last_seen"]),
            hits=row["hits"],
            node_id=row["node_id"],
            ignored=bool(row["ignored"]),
            notification_sent=bool(row["notification_sent"]),
        )

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            cur = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = cur.fetchone()
            return row[0] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # Filter rules (ignore / allow / watch)
    # ------------------------------------------------------------------

    def add_filter_rule(self, list_type: str, pattern_type: str, pattern: str) -> FilterRule | None:
        """Returns the new rule, or None if an identical one already exists."""
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO filter_rules (list_type, pattern_type, pattern, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (list_type, pattern_type, pattern, _now()),
                )
                self._conn.commit()
            except sqlite3.IntegrityError:
                return None
            cur = self._conn.execute(
                "SELECT * FROM filter_rules WHERE list_type = ? AND pattern_type = ? AND pattern = ?",
                (list_type, pattern_type, pattern),
            )
            return self._row_to_filter_rule(cur.fetchone())

    def remove_filter_rule(self, rule_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM filter_rules WHERE id = ?", (rule_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def list_filter_rules(self, list_type: str | None = None) -> list[FilterRule]:
        with self._lock:
            if list_type:
                cur = self._conn.execute(
                    "SELECT * FROM filter_rules WHERE list_type = ? ORDER BY created_at DESC", (list_type,)
                )
            else:
                cur = self._conn.execute("SELECT * FROM filter_rules ORDER BY list_type, created_at DESC")
            return [self._row_to_filter_rule(r) for r in cur.fetchall()]

    def all_filter_rules_cached(self) -> list[FilterRule]:
        """Same as list_filter_rules() with no arguments - named distinctly
        at call sites that check every incoming event against the full
        rule set, to make that hot path obvious when reading events.py."""
        return self.list_filter_rules()

    @staticmethod
    def _row_to_filter_rule(row: sqlite3.Row) -> FilterRule:
        return FilterRule(
            id=row["id"], list_type=row["list_type"], pattern_type=row["pattern_type"],
            pattern=row["pattern"], created_at=_parse_ts(row["created_at"]),
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()
