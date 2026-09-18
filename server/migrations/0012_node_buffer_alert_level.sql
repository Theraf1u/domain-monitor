-- Tracks the last buffer-fill alert level fired for a node, the same way
-- last_known_online tracks health state (migration 0009) - lets
-- BufferAlertMonitor fire exactly one notification per threshold
-- crossing (none -> warning -> critical, or back down to none/"recovered")
-- instead of repeating on every check while a node stays over threshold.
-- NULL = no alert currently active (either never crossed, or already
-- recovered) - distinct from the empty string, and matches existing data
-- with zero risk of a false "was at warning" read on upgrade.
ALTER TABLE nodes ADD COLUMN buffer_alert_level TEXT;
