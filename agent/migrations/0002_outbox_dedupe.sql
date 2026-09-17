-- Collapses the outbox into one row per (domain, source): a domain hit
-- repeatedly while the server is unreachable used to pile up as one row
-- per occurrence, which is pure waste - the server only needs to know
-- how many times and how recently, not a timestamped row for each hit.
ALTER TABLE outbox ADD COLUMN hits INTEGER NOT NULL DEFAULT 1;
ALTER TABLE outbox ADD COLUMN last_occurred_at TEXT;
UPDATE outbox SET last_occurred_at = occurred_at WHERE last_occurred_at IS NULL;

-- Collapse whatever duplicates already exist (from before this migration)
-- down to their most recent row before the unique index below - it would
-- otherwise fail to create on a busy node's outbox, which is exactly the
-- case this migration exists to fix. Not worth reconstructing their true
-- combined hit count: this is a one-time cleanup of already-degraded
-- pre-dedupe data, not the steady-state behavior going forward.
DELETE FROM outbox
WHERE id NOT IN (SELECT MAX(id) FROM outbox GROUP BY domain, source);

CREATE UNIQUE INDEX IF NOT EXISTS idx_outbox_domain_source ON outbox(domain, source);
CREATE INDEX IF NOT EXISTS idx_outbox_last_occurred_at ON outbox(last_occurred_at);
