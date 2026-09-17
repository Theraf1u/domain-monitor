-- Durable local buffer: every captured domain lands here first, and is
-- only deleted once the server has acknowledged it. This is what lets the
-- agent survive a network outage or a server restart without losing data
-- collected in the meantime.
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'tls_sni',
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outbox_created_at ON outbox(created_at);
