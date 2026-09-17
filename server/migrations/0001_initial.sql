-- Nodes: one row per registered agent. token_hash is SHA-256 of the token
-- the agent presents as `Authorization: Bearer <token>` - the plaintext
-- token is shown to the operator once, at creation/regeneration time, and
-- never stored.
CREATE TABLE IF NOT EXISTS nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    version TEXT,
    ip TEXT,
    hostname TEXT,
    created_at TEXT NOT NULL,
    last_seen_at TEXT,
    last_heartbeat_at TEXT,
    monitoring_enabled INTEGER NOT NULL DEFAULT 1,
    notifications_enabled INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
CREATE UNIQUE INDEX IF NOT EXISTS idx_nodes_token_hash ON nodes(token_hash);

-- Domains: one row per globally-unique domain, attributed to whichever node
-- most recently reported it. Full per-sighting history lives in `events`.
CREATE TABLE IF NOT EXISTS domains (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    hits INTEGER NOT NULL DEFAULT 0,
    node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    ignored INTEGER NOT NULL DEFAULT 0,
    notification_sent INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_domains_domain ON domains(domain);
CREATE INDEX IF NOT EXISTS idx_domains_last_seen ON domains(last_seen);
CREATE INDEX IF NOT EXISTS idx_domains_first_seen ON domains(first_seen);
CREATE INDEX IF NOT EXISTS idx_domains_hits ON domains(hits);
CREATE INDEX IF NOT EXISTS idx_domains_node ON domains(node_id);

-- Events: full per-sighting history, one row per domain observation
-- reported by an agent. Subject to retention cleanup (EVENT_RETENTION_DAYS).
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    domain TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'tls_sni',
    occurred_at TEXT NOT NULL,
    received_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_occurred_at ON events(occurred_at);
CREATE INDEX IF NOT EXISTS idx_events_node ON events(node_id);
CREATE INDEX IF NOT EXISTS idx_events_domain ON events(domain);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
