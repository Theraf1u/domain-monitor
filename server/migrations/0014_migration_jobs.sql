-- Migration 2.0 (spec 2.0 Part 2, section 3): tracks the state of a
-- server-to-server migration started via `migrate-to`. Only one row is
-- ever "active" at a time (status not in 'completed'/'cancelled'/'failed')
-- - enforced in application code, not a DB constraint, since SQLite has
-- no easy partial-unique-index-with-app-level-retry story worth adding
-- for a table this low-traffic. target_url is only ever handed to agents
-- once status='cutover' (see heartbeat endpoint) - during 'pending' and
-- 'standby' the new server is still being prepared and no agent should
-- switch there yet.
CREATE TABLE IF NOT EXISTS migration_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    error TEXT
);
