"""Batches newly-seen domains and delivers them to the admin as Telegram
messages. Ingestion (app/api/events.py) calls `schedule(node, domain)` for
every genuinely new domain; this module owns the accumulation window so a
burst of app traffic doesn't turn into a burst of Telegram messages.

The batch window is a DB-backed setting ("notify_batch_mode": "instant" or
a number of seconds), so the admin can change it live from Telegram without
a restart. "instant" is approximated as a 1s polling granularity rather
than true push - simple, and indistinguishable from instant to a human.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from app.database import Database
from app.metrics import TELEGRAM_ERRORS_TOTAL
from app.models import Node

logger = logging.getLogger(__name__)

SETTING_NOTIFICATIONS_ENABLED = "notifications_enabled"
SETTING_WATCHLIST_NOTIFICATIONS_ENABLED = "watchlist_notifications_enabled"
SETTING_BATCH_MODE = "notify_batch_mode"
_DEFAULT_BATCH_MODE = "instant"


class Notifier:
    def __init__(self, db: Database, admin_id: int) -> None:
        self.db = db
        self.admin_id = admin_id
        self.bot: Bot | None = None
        self._pending: list[tuple[Node, str]] = []
        self._lock = asyncio.Lock()
        self._stopped = asyncio.Event()

    def set_bot(self, bot: Bot) -> None:
        self.bot = bot

    def is_globally_enabled(self) -> bool:
        return self.db.get_setting(SETTING_NOTIFICATIONS_ENABLED, "1") == "1"

    def set_globally_enabled(self, enabled: bool) -> None:
        self.db.set_setting(SETTING_NOTIFICATIONS_ENABLED, "1" if enabled else "0")

    def batch_mode(self) -> str:
        return self.db.get_setting(SETTING_BATCH_MODE, _DEFAULT_BATCH_MODE) or _DEFAULT_BATCH_MODE

    def set_batch_mode(self, mode: str) -> None:
        self.db.set_setting(SETTING_BATCH_MODE, mode)

    def is_watchlist_enabled(self) -> bool:
        return self.db.get_setting(SETTING_WATCHLIST_NOTIFICATIONS_ENABLED, "1") == "1"

    def set_watchlist_enabled(self, enabled: bool) -> None:
        self.db.set_setting(SETTING_WATCHLIST_NOTIFICATIONS_ENABLED, "1" if enabled else "0")

    async def schedule(self, node: Node, domain: str) -> None:
        if self.bot is None or not self.is_globally_enabled() or not node.notifications_enabled:
            return  # no bot configured (API-only mode) - nothing to accumulate for
        async with self._lock:
            self._pending.append((node, domain))

    async def schedule_watchlist(self, node: Node, domain: str) -> None:
        """Watch-list hits bypass batching entirely - an operator who put a
        domain on the watch list wants to know the moment it appears, not
        folded into the next batch window."""
        if self.bot is None or not self.is_watchlist_enabled() or not node.notifications_enabled:
            return
        text = f"🚨 <b>WATCHLIST DOMAIN</b>\n\n<code>{domain}</code>\n\nНода: {node.name}"
        try:
            await self.bot.send_message(self.admin_id, text, parse_mode="HTML")
        except Exception:
            TELEGRAM_ERRORS_TOTAL.inc()
            logger.exception("Failed to deliver watchlist notification to admin")

    async def run(self) -> None:
        while not self._stopped.is_set():
            mode = self.batch_mode()
            interval = 1 if mode == "instant" else max(1, int(mode))
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
            await self._flush()

    def stop(self) -> None:
        self._stopped.set()

    async def _flush(self) -> None:
        async with self._lock:
            if not self._pending:
                return
            batch, self._pending = self._pending, []

        if self.bot is None:
            return

        by_node: dict[str, list[str]] = {}
        for node, domain in batch:
            by_node.setdefault(node.name, []).append(domain)

        for node_name, domains in by_node.items():
            if len(domains) == 1:
                text = f"🌐 Новый домен\n\n<code>{domains[0]}</code>\n\nНода: {node_name}"
            else:
                lines = "\n".join(f"• <code>{d}</code>" for d in domains[:30])
                more = f"\n… и ещё {len(domains) - 30}" if len(domains) > 30 else ""
                text = f"🌐 Обнаружено {len(domains)} новых доменов\n\nНода: {node_name}\n\n{lines}{more}"
            try:
                await self.bot.send_message(self.admin_id, text, parse_mode="HTML")
            except Exception:
                TELEGRAM_ERRORS_TOTAL.inc()
                logger.exception("Failed to deliver notification to admin")
