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
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from app.models import Domain, Event, FilterRule, Node

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations")

# Placeholder name auto-assigned by create_node_auto(), replaced by
# touch_heartbeat() with the node's real IP on its first heartbeat -
# matched here so that a user-chosen name is never clobbered.
_AUTO_NAME_RE = re.compile(r"^нода-\d+$")


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

    def create_node_auto(self, token_hash: str) -> Node:
        """Creates a node without requiring a name up front - used by the
        one-tap "Добавить" flow (bot + CLI). Inserts with a temporary
        unique placeholder (the insert's own rowid can't be known before
        the insert happens), then renames it to `нода-{id}` so the node
        is immediately identifiable in lists while it awaits its first
        heartbeat, at which point touch_heartbeat() renames it again to
        the node's real IP."""
        with self._lock:
            placeholder = f"__pending__{_now()}"
            self._conn.execute(
                "INSERT INTO nodes (name, token_hash, status, created_at, monitoring_enabled, "
                "notifications_enabled) VALUES (?, ?, 'active', ?, 1, 1)",
                (placeholder, token_hash, _now()),
            )
            node_id = self._conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            final_name = f"нода-{node_id}"
            self._conn.execute("UPDATE nodes SET name = ? WHERE id = ?", (final_name, node_id))
            self._conn.commit()
            cur = self._conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,))
            return self._row_to_node(cur.fetchone())

    def rename_node(self, node_id: int, new_name: str) -> bool:
        """Returns False (no change made) if the name is already taken by
        a different node."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT 1 FROM nodes WHERE name = ? AND id != ?", (new_name, node_id)
            )
            if cur.fetchone() is not None:
                return False
            self._conn.execute("UPDATE nodes SET name = ? WHERE id = ?", (new_name, node_id))
            self._conn.commit()
            return True

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

    def set_node_sending(self, node_id: int, enabled: bool) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE nodes SET sending_enabled = ? WHERE id = ?", (int(enabled), node_id)
            )
            self._conn.commit()

    def set_node_notify_destination(self, node_id: int, destination: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE nodes SET notify_destination = ? WHERE id = ?", (destination, node_id)
            )
            self._conn.commit()

    def set_node_notify_group(self, node_id: int, chat_id: int, topic_id: int | None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE nodes SET notify_group_chat_id = ?, notify_group_topic_id = ? WHERE id = ?",
                (chat_id, topic_id, node_id),
            )
            self._conn.commit()

    def touch_heartbeat(
        self, node_id: int, version: str | None = None, ip: str | None = None,
        hostname: str | None = None, buffer_size: int | None = None,
    ) -> None:
        with self._lock:
            now = _now()
            if version is not None or ip is not None or hostname is not None or buffer_size is not None:
                self._conn.execute(
                    "UPDATE nodes SET last_heartbeat_at = ?, last_seen_at = ?, "
                    "version = COALESCE(?, version), ip = COALESCE(?, ip), "
                    "hostname = COALESCE(?, hostname), "
                    "agent_buffer_size = COALESCE(?, agent_buffer_size) WHERE id = ?",
                    (now, now, version, ip, hostname, buffer_size, node_id),
                )
            else:
                self._conn.execute(
                    "UPDATE nodes SET last_heartbeat_at = ?, last_seen_at = ? WHERE id = ?",
                    (now, now, node_id),
                )
            self._conn.commit()

            if ip:
                row = self._conn.execute("SELECT name FROM nodes WHERE id = ?", (node_id,)).fetchone()
                if row is not None and _AUTO_NAME_RE.match(row["name"]):
                    taken = self._conn.execute(
                        "SELECT 1 FROM nodes WHERE name = ? AND id != ?", (ip, node_id)
                    ).fetchone()
                    if taken is None:
                        self._conn.execute("UPDATE nodes SET name = ? WHERE id = ?", (ip, node_id))
                        self._conn.commit()
                    # else: ip already taken by another node - keep the placeholder name.

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
            sending_enabled=bool(row["sending_enabled"]),
            notify_destination=row["notify_destination"],
            notify_group_chat_id=row["notify_group_chat_id"],
            notify_group_topic_id=row["notify_group_topic_id"],
            agent_buffer_size=row["agent_buffer_size"],
        )

    # ------------------------------------------------------------------
    # Domains + events
    # ------------------------------------------------------------------

    def record_event(
        self, node_id: int, domain: str, source: str, occurred_at: datetime, hits: int = 1,
    ) -> tuple[Domain, bool]:
        """Insert an event row and upsert the aggregate `domains` row.
        `hits` lets one received event represent more than one real
        occurrence - the agent collapses repeats of the same domain into
        a single buffered row with a counter instead of storing (and
        later sending) a near-identical row per hit. Returns
        (domain_row, is_new)."""
        with self._lock:
            now = _now()
            occurred_str = _fmt_ts(occurred_at)

            self._conn.execute(
                "INSERT INTO events (node_id, domain, source, occurred_at, received_at, hits) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (node_id, domain, source, occurred_str, now, hits),
            )

            cur = self._conn.execute("SELECT * FROM domains WHERE domain = ?", (domain,))
            row = cur.fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO domains "
                    "(domain, first_seen, last_seen, hits, node_id, ignored, notification_sent) "
                    "VALUES (?, ?, ?, ?, ?, 0, 0)",
                    (domain, occurred_str, occurred_str, hits, node_id),
                )
                self._conn.commit()
                cur = self._conn.execute("SELECT * FROM domains WHERE domain = ?", (domain,))
                return self._row_to_domain(cur.fetchone()), True

            self._conn.execute(
                "UPDATE domains SET last_seen = ?, hits = hits + ?, node_id = ? WHERE id = ?",
                (occurred_str, hits, node_id, row["id"]),
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
        since: datetime | None = None, until: datetime | None = None,
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
        if since is not None:
            clauses.append("first_seen >= ?")
            params.append(_fmt_ts(since))
        if until is not None:
            clauses.append("first_seen < ?")
            params.append(_fmt_ts(until))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        with self._lock:
            cur = self._conn.execute(
                f"SELECT * FROM domains {where} ORDER BY {order_col} LIMIT ? OFFSET ?",
                (*params, limit, offset),
            )
            return [self._row_to_domain(r) for r in cur.fetchall()]

    def count_domains(self, since: datetime | None = None, until: datetime | None = None) -> int:
        clauses: list[str] = []
        params: list[object] = []
        if since is not None:
            clauses.append("first_seen >= ?")
            params.append(_fmt_ts(since))
        if until is not None:
            clauses.append("first_seen < ?")
            params.append(_fmt_ts(until))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            cur = self._conn.execute(f"SELECT COUNT(*) FROM domains {where}", params)
            return cur.fetchone()[0]

    def count_events_since(self, since: datetime, until: datetime | None = None) -> int:
        clauses = ["occurred_at >= ?"]
        params: list[object] = [_fmt_ts(since)]
        if until is not None:
            clauses.append("occurred_at < ?")
            params.append(_fmt_ts(until))
        with self._lock:
            cur = self._conn.execute(
                f"SELECT COALESCE(SUM(hits), 0) FROM events WHERE {' AND '.join(clauses)}", params
            )
            return cur.fetchone()[0]

    def top_domains(self, limit: int = 10) -> list[Domain]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM domains ORDER BY hits DESC LIMIT ?", (limit,))
            return [self._row_to_domain(r) for r in cur.fetchall()]

    def top_active_nodes_since(self, since: datetime, limit: int = 3) -> list[tuple[str, int]]:
        """(node name, event count) for the most active nodes in a window -
        a quick read on which node is generating the noise, for the stats
        screen. Nodes that have since been deleted are excluded (the JOIN
        drops their events) rather than shown with a blank name."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT n.name, SUM(e.hits) as c FROM events e JOIN nodes n ON n.id = e.node_id "
                "WHERE e.occurred_at >= ? GROUP BY e.node_id ORDER BY c DESC LIMIT ?",
                (_fmt_ts(since), limit),
            )
            return [(r["name"], r["c"]) for r in cur.fetchall()]

    def purge_events_older_than(self, cutoff: datetime) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM events WHERE occurred_at < ?", (_fmt_ts(cutoff),))
            self._conn.commit()
            return cur.rowcount

    def reset_domains_and_events(self) -> tuple[int, int]:
        """Wipes collected domains and their event history - nodes, filter
        rules and settings are untouched. Used to start a fresh collection
        pass (e.g. after switching a node's routing template, see the
        README note on split-routing hiding domains from the sniffer)."""
        with self._lock:
            events_cur = self._conn.execute("SELECT COUNT(*) FROM events")
            events_count = events_cur.fetchone()[0]
            domains_cur = self._conn.execute("SELECT COUNT(*) FROM domains")
            domains_count = domains_cur.fetchone()[0]
            self._conn.execute("DELETE FROM events")
            self._conn.execute("DELETE FROM domains")
            self._conn.commit()
            return domains_count, events_count

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

    def set_filter_rule_enabled(self, rule_id: int, enabled: bool) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE filter_rules SET enabled = ? WHERE id = ?", (int(enabled), rule_id)
            )
            self._conn.commit()

    def set_filter_rule_comment(self, rule_id: int, comment: str | None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE filter_rules SET comment = ? WHERE id = ?", (comment, rule_id)
            )
            self._conn.commit()

    def record_filter_hits(self, rule_ids: list[int], when: datetime) -> None:
        """Bumps hits_count/last_hit_at for every rule that matched an
        incoming domain - called once per ingested event with whichever
        rule ids classify_domain() found, not one call per rule."""
        if not rule_ids:
            return
        with self._lock:
            when_str = _fmt_ts(when)
            placeholders = ",".join("?" for _ in rule_ids)
            self._conn.execute(
                f"UPDATE filter_rules SET hits_count = hits_count + 1, last_hit_at = ? "
                f"WHERE id IN ({placeholders})",
                (when_str, *rule_ids),
            )
            self._conn.commit()

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
            enabled=bool(row["enabled"]), comment=row["comment"],
            hits_count=row["hits_count"], last_hit_at=_parse_ts(row["last_hit_at"]),
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()
