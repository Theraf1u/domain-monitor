-- Extended, fully-optional heartbeat telemetry (2.0 spec section 11).
-- Every column is nullable with no default, matching the request
-- schema's Optional[...] fields: an old agent that never sends these
-- just never populates them, same as version/ip/hostname already did.
ALTER TABLE nodes ADD COLUMN agent_uptime_seconds INTEGER;
ALTER TABLE nodes ADD COLUMN buffer_bytes INTEGER;
ALTER TABLE nodes ADD COLUMN buffer_limit_bytes INTEGER;
ALTER TABLE nodes ADD COLUMN dropped_events_total INTEGER;
ALTER TABLE nodes ADD COLUMN capture_tls_running INTEGER;
ALTER TABLE nodes ADD COLUMN capture_dns_running INTEGER;
ALTER TABLE nodes ADD COLUMN last_send_error TEXT;
ALTER TABLE nodes ADD COLUMN last_send_success_at TEXT;
