-- Agents now collapse repeated hits on the same domain into one buffered
-- row with a counter (see agent/app/buffer.py) instead of one row per
-- occurrence, so a single received event can represent more than one
-- real hit. This column lets domains.hits and the events-per-hour stats
-- stay accurate despite that collapsing.
ALTER TABLE events ADD COLUMN hits INTEGER NOT NULL DEFAULT 1;
