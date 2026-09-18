-- Explicit capability reporting (spec section 10): which sources this
-- agent BUILD can capture at all (sources_supported) vs which ones this
-- particular run was actually started with (sources_enabled) - stored as
-- JSON arrays since SQLite has no native array type. Both nullable: an
-- agent older than this feature simply never sends them, and the UI must
-- fall back to the existing capture_tls_running/capture_dns_running
-- display for it rather than showing empty capability lists.
ALTER TABLE nodes ADD COLUMN sources_supported TEXT;
ALTER TABLE nodes ADD COLUMN sources_enabled TEXT;
