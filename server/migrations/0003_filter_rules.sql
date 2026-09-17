-- Ignore / Allow / Watch lists, kept separate from the existing per-domain
-- `domains.ignored` manual toggle: a filter_rule is a *pattern* checked at
-- ingestion time against every incoming domain, not a one-off flag on an
-- already-recorded row.
CREATE TABLE IF NOT EXISTS filter_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    list_type TEXT NOT NULL CHECK (list_type IN ('ignore', 'allow', 'watch')),
    pattern_type TEXT NOT NULL CHECK (pattern_type IN ('exact', 'suffix', 'wildcard')),
    pattern TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_filter_rules_unique ON filter_rules(list_type, pattern_type, pattern);
CREATE INDEX IF NOT EXISTS idx_filter_rules_list_type ON filter_rules(list_type);
