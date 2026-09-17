-- Last-reported size of the agent's own local outbox, from its
-- heartbeat. Purely informational (lets the bot show "collecting
-- locally, N events not sent yet" instead of the domain list just
-- looking frozen while sending is paused) - never written to by
-- anything except touch_heartbeat().
ALTER TABLE nodes ADD COLUMN agent_buffer_size INTEGER;
