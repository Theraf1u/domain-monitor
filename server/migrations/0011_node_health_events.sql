-- History of node online/offline transitions, so the stats screen can
-- report "offline incidents за период" - NodeHealthMonitor already
-- computes exactly these transitions (see app/health_monitor.py), this
-- just gives it somewhere to record them instead of firing a notification
-- and forgetting.
CREATE TABLE IF NOT EXISTS node_health_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_node_health_events_occurred ON node_health_events(occurred_at);
CREATE INDEX IF NOT EXISTS idx_node_health_events_node ON node_health_events(node_id);
