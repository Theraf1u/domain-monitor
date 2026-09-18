-- Extends filter_rules with the fields the 2.0 Filters UI needs: a rule
-- can be disabled without deleting it, annotated with a note, and its
-- own match activity tracked (how often it fired, and when last). All
-- additive with safe defaults, so every existing rule keeps matching
-- exactly as before this migration: enabled=1, comment=NULL,
-- hits_count=0, last_hit_at=NULL.
ALTER TABLE filter_rules ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1;
ALTER TABLE filter_rules ADD COLUMN comment TEXT;
ALTER TABLE filter_rules ADD COLUMN hits_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE filter_rules ADD COLUMN last_hit_at TEXT;
