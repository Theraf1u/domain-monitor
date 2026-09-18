-- Tracks each node's last-observed online/offline state so a background
-- health check can fire exactly one notification per state transition
-- (going offline, coming back) instead of node status being purely
-- computed-on-read with no memory of what it was a moment ago. NULL
-- means "never checked yet" - deliberately distinct from 0/1, so the
-- first check after this migration (or after a node is created) can
-- record a silent baseline instead of firing a false "recovered" alert.
ALTER TABLE nodes ADD COLUMN last_known_online INTEGER;
