-- Per-node notification routing: where new-domain/watchlist alerts for
-- THIS node go, independent of every other node's setting. "dm" (the
-- original behaviour - every admin's private chat), "group" (a specific
-- supergroup, optionally a forum topic inside it), or "both".
ALTER TABLE nodes ADD COLUMN notify_destination TEXT NOT NULL DEFAULT 'dm';
ALTER TABLE nodes ADD COLUMN notify_group_chat_id INTEGER;
ALTER TABLE nodes ADD COLUMN notify_group_topic_id INTEGER;
