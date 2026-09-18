-- Per-node sending toggle, mirroring the existing per-node
-- monitoring_enabled: a node's effective "may this agent send events"
-- state is node.sending_enabled AND the fleet-wide sending switch,
-- same fold pattern already used for monitoring_enabled in
-- app/api/nodes.py's heartbeat(). Defaults to enabled so every
-- existing node keeps behaving exactly as before this migration.
ALTER TABLE nodes ADD COLUMN sending_enabled INTEGER NOT NULL DEFAULT 1;
