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
from datetime import datetime, time as dt_time, timedelta, timezone

from aiogram import Bot

from app import runtime_settings
from app.config import Config
from app.database import Database
from app.metrics import TELEGRAM_ERRORS_TOTAL
from app.models import Node

logger = logging.getLogger(__name__)

SETTING_NOTIFICATIONS_ENABLED = "notifications_enabled"
SETTING_WATCHLIST_NOTIFICATIONS_ENABLED = "watchlist_notifications_enabled"
SETTING_BATCH_MODE = "notify_batch_mode"
_DEFAULT_BATCH_MODE = "instant"

# Notification Center (spec 2.0 Part 1, section 8): every event type the bot
# can notify about, whether it fires now or is wired up by a later feature
# (buffer alerts/outdated-agent checks/a disk-space check/unified error
# handling are separate roadmap items - their toggles exist here already so
# Notification Center is one screen for all of them, but a toggle for a
# monitor that doesn't exist yet is simply inert until that monitor ships).
# `ignore_quiet_hours_default` matches spec 8.2's "watch/offline/recovery/
# backup-failure всегда мимо батча" - the same set defaults to also bypass
# quiet hours, since those are exactly the alerts an operator wants to see
# regardless of the hour; the default is overridable per type.
NOTIFY_TYPES: dict[str, dict] = {
    "new_domain": {"label": "🌐 Новые домены", "ignore_quiet_hours_default": False},
    "watch_hit": {"label": "🚨 Watch-хиты", "ignore_quiet_hours_default": True},
    "node_offline": {"label": "🔴 Нода offline", "ignore_quiet_hours_default": True},
    "node_recovered": {"label": "🟢 Нода восстановлена", "ignore_quiet_hours_default": True},
    "agent_buffer_warning": {"label": "📦 Буфер: предупреждение", "ignore_quiet_hours_default": False},
    "agent_buffer_critical": {"label": "📦 Буфер: критично", "ignore_quiet_hours_default": True},
    "agent_outdated": {"label": "🟡 Устаревший агент", "ignore_quiet_hours_default": False},
    "backup_failed": {"label": "💾 Ошибка бэкапа", "ignore_quiet_hours_default": True},
    "backup_success": {"label": "💾 Бэкап успешен", "ignore_quiet_hours_default": False, "enabled_default": False},
    "disk_space_warning": {"label": "💽 Мало места на диске", "ignore_quiet_hours_default": False},
    "server_error": {"label": "⚠️ Ошибка сервера", "ignore_quiet_hours_default": True},
}

_SETTING_TYPE_ENABLED_PREFIX = "notify_type_enabled:"
_SETTING_TYPE_IQH_PREFIX = "notify_type_iqh:"
SETTING_QUIET_HOURS_ENABLED = "quiet_hours_enabled"
SETTING_QUIET_HOURS_START = "quiet_hours_start"
SETTING_QUIET_HOURS_END = "quiet_hours_end"
_DEFAULT_QUIET_START = "23:00"
_DEFAULT_QUIET_END = "08:00"

SETTING_GLOBAL_DESTINATION = "notify_global_destination"
_DEFAULT_GLOBAL_DESTINATION = "dm"


def _parse_hhmm(value: str) -> dt_time:
    hours, _, minutes = value.partition(":")
    return dt_time(hour=int(hours), minute=int(minutes or 0))


class Notifier:
    def __init__(self, db: Database, admin_ids: list[int], config: Config | None = None) -> None:
        self.db = db
        self.admin_ids = admin_ids
        self.config = config
        self.bot: Bot | None = None
        self._pending: list[tuple[Node, str]] = []
        self._lock = asyncio.Lock()
        self._stopped = asyncio.Event()

    def set_bot(self, bot: Bot) -> None:
        self.bot = bot

    async def broadcast(self, text: str, parse_mode: str | None = "HTML") -> None:
        """Sends to every configured admin's DM, independently - one
        admin having blocked the bot (or a stale/invalid id) never stops
        the others from being notified. Used for admin-facing messages
        that aren't tied to any one node (backup status, etc); per-node
        alerts go through deliver_for_node() instead, which respects
        that node's own routing."""
        if self.bot is None:
            return
        for admin_id in self.admin_ids:
            try:
                await self.bot.send_message(admin_id, text, parse_mode=parse_mode)
            except Exception:
                TELEGRAM_ERRORS_TOTAL.inc()
                logger.exception("Failed to deliver message to admin %s", admin_id)

    async def deliver_for_node(self, node: Node, text: str, parse_mode: str | None = "HTML") -> None:
        """Routes a node-scoped alert (new domain, watchlist hit) to
        wherever THAT node is configured to send them: every admin's DM,
        a specific group (optionally one forum topic in it), or both.
        Falls back to DM if "group" is selected but never actually got a
        chat_id bound yet, so a half-finished setup doesn't just eat
        notifications silently."""
        if self.bot is None:
            return
        dest = self.global_destination() if node.notify_destination == "inherit" else node.notify_destination
        if dest in ("dm", "both") or (dest == "group" and node.notify_group_chat_id is None):
            await self.broadcast(text, parse_mode)
        if dest in ("group", "both") and node.notify_group_chat_id is not None:
            try:
                await self.bot.send_message(
                    node.notify_group_chat_id, text, parse_mode=parse_mode,
                    message_thread_id=node.notify_group_topic_id,
                )
            except Exception:
                TELEGRAM_ERRORS_TOTAL.inc()
                logger.exception(
                    "Failed to deliver notification for node %s to group %s (topic %s)",
                    node.id, node.notify_group_chat_id, node.notify_group_topic_id,
                )

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

    # ------------------------------------------------------------------
    # Notification Center (spec section 8)
    # ------------------------------------------------------------------

    def is_type_enabled(self, event_type: str) -> bool:
        default = "1" if NOTIFY_TYPES.get(event_type, {}).get("enabled_default", True) else "0"
        return self.db.get_setting(_SETTING_TYPE_ENABLED_PREFIX + event_type, default) == "1"

    def set_type_enabled(self, event_type: str, enabled: bool) -> None:
        self.db.set_setting(_SETTING_TYPE_ENABLED_PREFIX + event_type, "1" if enabled else "0")

    def ignores_quiet_hours(self, event_type: str) -> bool:
        raw = self.db.get_setting(_SETTING_TYPE_IQH_PREFIX + event_type)
        if raw is not None:
            return raw == "1"
        return NOTIFY_TYPES.get(event_type, {}).get("ignore_quiet_hours_default", False)

    def set_ignores_quiet_hours(self, event_type: str, value: bool) -> None:
        self.db.set_setting(_SETTING_TYPE_IQH_PREFIX + event_type, "1" if value else "0")

    def quiet_hours(self) -> tuple[bool, str, str]:
        enabled = self.db.get_setting(SETTING_QUIET_HOURS_ENABLED, "0") == "1"
        start = self.db.get_setting(SETTING_QUIET_HOURS_START, _DEFAULT_QUIET_START) or _DEFAULT_QUIET_START
        end = self.db.get_setting(SETTING_QUIET_HOURS_END, _DEFAULT_QUIET_END) or _DEFAULT_QUIET_END
        return enabled, start, end

    def set_quiet_hours(self, enabled: bool, start: str, end: str) -> None:
        self.db.set_setting(SETTING_QUIET_HOURS_ENABLED, "1" if enabled else "0")
        self.db.set_setting(SETTING_QUIET_HOURS_START, start)
        self.db.set_setting(SETTING_QUIET_HOURS_END, end)

    def is_quiet_hours_active(self, now: datetime | None = None) -> bool:
        enabled, start, end = self.quiet_hours()
        if not enabled:
            return False
        now = now or datetime.now(timezone.utc)
        if self.config is not None:
            offset = runtime_settings.get_timezone_offset_minutes(self.db, self.config)
            now = now.astimezone(timezone.utc) + timedelta(minutes=offset)
        current = now.time()
        start_t, end_t = _parse_hhmm(start), _parse_hhmm(end)
        if start_t <= end_t:
            return start_t <= current < end_t
        return current >= start_t or current < end_t  # window wraps past midnight

    def should_deliver(self, event_type: str) -> bool:
        if not self.is_globally_enabled() or not self.is_type_enabled(event_type):
            return False
        if self.is_quiet_hours_active() and not self.ignores_quiet_hours(event_type):
            return False
        return True

    def global_destination(self) -> str:
        return self.db.get_setting(SETTING_GLOBAL_DESTINATION, _DEFAULT_GLOBAL_DESTINATION) or _DEFAULT_GLOBAL_DESTINATION

    def set_global_destination(self, destination: str) -> None:
        self.db.set_setting(SETTING_GLOBAL_DESTINATION, destination)

    async def send_test(self, node: Node | None = None) -> dict[str, str]:
        """Actually sends a test message and reports per-destination
        success/failure (spec 8.4) - unlike every other notify_* method,
        this doesn't swallow exceptions into a log line, because the
        whole point is to show the admin whether delivery really works."""
        if self.bot is None:
            return {"bot": "❌ Бот не инициализирован"}
        results: dict[str, str] = {}
        text = "🧪 Тестовое уведомление Domain Monitor"
        for admin_id in self.admin_ids:
            try:
                await self.bot.send_message(admin_id, text)
                results[f"DM {admin_id}"] = "✅ Доставлено"
            except Exception as exc:
                results[f"DM {admin_id}"] = f"❌ {exc}"
        if node is not None:
            dest = self.global_destination() if node.notify_destination == "inherit" else node.notify_destination
            if dest in ("group", "both") and node.notify_group_chat_id is not None:
                try:
                    await self.bot.send_message(
                        node.notify_group_chat_id, text, message_thread_id=node.notify_group_topic_id,
                    )
                    results[f"Группа ({node.name})"] = "✅ Доставлено"
                except Exception as exc:
                    results[f"Группа ({node.name})"] = f"❌ {exc}"
        return results

    async def schedule(self, node: Node, domain: str) -> None:
        if self.bot is None or not self.is_globally_enabled() or not self.is_type_enabled("new_domain") \
                or not node.notifications_enabled:
            return  # no bot configured (API-only mode) - nothing to accumulate for
        async with self._lock:
            self._pending.append((node, domain))

    async def schedule_watchlist(self, node: Node, domain: str) -> None:
        """Watch-list hits bypass batching entirely - an operator who put a
        domain on the watch list wants to know the moment it appears, not
        folded into the next batch window."""
        if self.bot is None or not self.is_watchlist_enabled() or not self.should_deliver("watch_hit") \
                or not node.notifications_enabled:
            return
        text = f"🚨 <b>WATCHLIST DOMAIN</b>\n\n<code>{domain}</code>\n\nНода: {node.name}"
        await self.deliver_for_node(node, text)

    async def notify_offline(self, node: Node) -> None:
        """Fired once per online->offline transition by NodeHealthMonitor
        - never on a timer, so a node that stays offline for a week
        doesn't produce a week of repeated pings."""
        if self.bot is None or not node.notifications_enabled or not self.should_deliver("node_offline"):
            return
        text = f"🔴 Нода <b>{node.name}</b> недоступна (нет heartbeat)"
        await self.deliver_for_node(node, text)

    async def notify_recovered(self, node: Node) -> None:
        """Mirror of notify_offline() for the offline->online transition."""
        if self.bot is None or not node.notifications_enabled or not self.should_deliver("node_recovered"):
            return
        text = f"🟢 Нода <b>{node.name}</b> снова на связи"
        await self.deliver_for_node(node, text)

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
            if not (self.is_globally_enabled() and self.is_type_enabled("new_domain")):
                self._pending = []  # type turned off since these were scheduled - drop, don't deliver stale items
                return
            if self.is_quiet_hours_active() and not self.ignores_quiet_hours("new_domain"):
                return  # hold the batch - retried next tick, delivered once quiet hours end
            batch, self._pending = self._pending, []

        if self.bot is None:
            return

        by_node: dict[int, tuple[Node, list[str]]] = {}
        for node, domain in batch:
            entry = by_node.setdefault(node.id, (node, []))
            entry[1].append(domain)

        for node, domains in by_node.values():
            if len(domains) == 1:
                text = f"🌐 Новый домен\n\n<code>{domains[0]}</code>\n\nНода: {node.name}"
            else:
                lines = "\n".join(f"• <code>{d}</code>" for d in domains[:30])
                more = f"\n… и ещё {len(domains) - 30}" if len(domains) > 30 else ""
                text = f"🌐 Обнаружено {len(domains)} новых доменов\n\nНода: {node.name}\n\n{lines}{more}"
            await self.deliver_for_node(node, text)
