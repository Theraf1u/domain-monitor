"""Lets an admin bind a node's notifications to a specific group (and,
inside a forum-mode group, a specific topic) without typing a raw
chat_id/topic_id by hand: tap "bind" in the bot's DM, then send /bind in
the destination group/topic, and the bot captures chat_id +
message_thread_id straight off that message.

Why not aiogram's own FSM for this: aiogram's default state storage key
includes the chat, so a state set while chatting with the bot in DM is
invisible once the admin sends something in a *different* chat (the
group). This tracks the pending request per admin user_id instead, with
a short TTL so a forgotten "bind" doesn't stay armed forever - another
instance of the same anti-loop/anti-dangling-state care applied
elsewhere in this project (backups, auto-update, live views).
"""
from __future__ import annotations

import time

_TTL_SECONDS = 300


class TopicBindingManager:
    def __init__(self) -> None:
        self._pending: dict[int, tuple[int, float]] = {}  # user_id -> (node_id, expires_at)

    def start(self, user_id: int, node_id: int) -> None:
        self._pending[user_id] = (node_id, time.monotonic() + _TTL_SECONDS)

    def cancel(self, user_id: int) -> None:
        self._pending.pop(user_id, None)

    def has_pending(self, user_id: int) -> bool:
        entry = self._pending.get(user_id)
        if entry is None:
            return False
        if time.monotonic() > entry[1]:
            self._pending.pop(user_id, None)
            return False
        return True

    def pop(self, user_id: int) -> int | None:
        entry = self._pending.pop(user_id, None)
        if entry is None:
            return None
        node_id, expires_at = entry
        if time.monotonic() > expires_at:
            return None
        return node_id
