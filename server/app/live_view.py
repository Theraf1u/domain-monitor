"""Optional auto-refresh for read-only info screens (recent/top domains,
stats, backup list). A viewer can turn on "🔁 Автообновление" instead of
tapping "🔄 Обновить" repeatedly - a background task re-renders the same
message on an interval until turned off, replaced, or a safety timeout
expires.

Anti-loop safeguards (this is a scheduled task editing a live message on
its own, so the same care applies as to the backup scheduler):
- One task per (chat_id, message_id) - starting a new one always cancels
  any previous one for that exact message first, so toggling on/off
  repeatedly can never pile up tasks for the same screen.
- A hard MAX_DURATION_SECONDS means a forgotten "on" toggle can't run
  forever burning Bot API calls - it just turns itself off.
- A capped MAX_CONCURRENT_VIEWS protects against many screens being left
  open (now more plausible with multiple admins each browsing the bot).
- "message is not modified" from Telegram (the normal case when nothing
  actually changed between refreshes) is swallowed and the loop keeps
  going; any OTHER failure (message deleted, chat blocked the bot, etc)
  stops that one view instead of retrying in a hot loop against
  something that's clearly broken.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from aiogram.exceptions import TelegramBadRequest

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 20
MAX_DURATION_SECONDS = 30 * 60
MAX_CONCURRENT_VIEWS = 25

RenderFn = Callable[[], Awaitable[None]]


class LiveViewManager:
    def __init__(self) -> None:
        self._tasks: dict[tuple[int, int], asyncio.Task] = {}

    def is_active(self, chat_id: int, message_id: int) -> bool:
        return (chat_id, message_id) in self._tasks

    def stop(self, chat_id: int, message_id: int) -> None:
        task = self._tasks.pop((chat_id, message_id), None)
        if task is not None:
            task.cancel()

    def start(self, chat_id: int, message_id: int, render: RenderFn) -> bool:
        """Returns False (and starts nothing) if the concurrent-view cap
        is already reached - the caller should tell the admin to close
        another live view first rather than silently doing nothing."""
        self.stop(chat_id, message_id)
        if len(self._tasks) >= MAX_CONCURRENT_VIEWS:
            return False
        self._tasks[(chat_id, message_id)] = asyncio.create_task(self._run(chat_id, message_id, render))
        return True

    async def _run(self, chat_id: int, message_id: int, render: RenderFn) -> None:
        elapsed = 0
        try:
            while elapsed < MAX_DURATION_SECONDS:
                await asyncio.sleep(INTERVAL_SECONDS)
                elapsed += INTERVAL_SECONDS
                try:
                    await render()
                except TelegramBadRequest as exc:
                    if "message is not modified" not in str(exc).lower():
                        logger.debug("Live view render failed, stopping: %s", exc)
                        return
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.debug("Live view render failed, stopping", exc_info=True)
                    return
        finally:
            self._tasks.pop((chat_id, message_id), None)

    def stop_all(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        self._tasks.clear()
