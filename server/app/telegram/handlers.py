"""All Telegram interaction. A single Router; every action except /start is
an inline button (callback_query), per the "no commands, no reply
keyboards" rule carried over from the single-node MVP.
"""
from __future__ import annotations

import csv
import html
import io
import json
import logging
import os
import re
import socket
from datetime import datetime, timedelta, timezone

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardMarkup, Message

from app import backup_settings, fleet_control, runtime_settings
from app.agent_versions import version_badge
from app.backup_task import BackupTask
from app.config import Config
from app.database import Database
from app.filters import classify_domain, normalize_domain
from app.live_view import LiveViewManager
from app.notifier import NOTIFY_TYPES, Notifier
from app.security import generate_node_token, hash_token
from app.telegram import keyboards as kb
from app.telegram.formatters import (
    format_bytes, format_datetime, format_datetime_local, format_duration, format_relative_time,
    format_timezone_offset,
)
from app.topic_binding import TopicBindingManager

logger = logging.getLogger(__name__)

router = Router()

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


@router.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery) -> None:
    """The page-counter button in pagination rows (kb.add_pagination_row) -
    not meant to do anything, but every callback still has to answer() or
    the tap just spins forever on the user's end."""
    await call.answer()


class Inputs(StatesGroup):
    waiting_for_node_rename = State()
    waiting_for_node_search = State()
    waiting_for_domain_search = State()
    waiting_for_stats_custom_range = State()
    waiting_for_filter_pattern = State()
    waiting_for_filter_comment = State()
    waiting_for_filter_import = State()
    waiting_for_filter_check = State()
    waiting_for_export_range = State()
    waiting_for_custom_batch_seconds = State()
    waiting_for_quiet_hours_range = State()
    waiting_for_retention_days = State()
    waiting_for_offline_seconds = State()
    waiting_for_timezone_offset = State()
    waiting_for_buffer_thresholds = State()
    waiting_for_backup_interval = State()
    waiting_for_backup_keep = State()
    waiting_for_backup_group_chat_id = State()
    waiting_for_backup_group_topic_id = State()


def _online_ids(db: Database, config: Config) -> set[int]:
    now = datetime.now(timezone.utc)
    offline_after = runtime_settings.get_node_offline_after_seconds(db, config)
    return {n.id for n in db.list_nodes() if n.is_online(offline_after, now)}


def _period_range(
    period: str, now: datetime, tz_offset_minutes: int = 0,
) -> tuple[datetime | None, datetime | None]:
    """Maps a period key (shared by the export and stats menus) to a
    [since, until) window. None on either side means "no bound in that
    direction" - "all" is (None, None). "today"/"yesterday" are calendar
    days in the admin's configured timezone (spec 7.2), not UTC - shift
    into local time to find local midnight, then shift the boundary back
    to UTC for the actual DB comparison (everything is still stored and
    compared in UTC; only the boundary's *position* is timezone-aware)."""
    local_now = now + timedelta(minutes=tz_offset_minutes)
    today_start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_start = today_start_local - timedelta(minutes=tz_offset_minutes)
    if period == "1h":
        return now - timedelta(hours=1), None
    if period == "6h":
        return now - timedelta(hours=6), None
    if period == "today":
        return today_start, None
    if period == "yesterday":
        return today_start - timedelta(days=1), today_start
    if period == "24h":
        return now - timedelta(hours=24), None
    if period in ("7d", "week"):
        return now - timedelta(days=7), None
    if period in ("30d", "month"):
        return now - timedelta(days=30), None
    return None, None  # "all"


def _parse_date_range(text: str) -> tuple[datetime, datetime] | None:
    """"2026-09-01" -> that single day, or "2026-09-01 2026-09-15" -> that
    inclusive range. None on anything that doesn't parse - callers show
    their own format-hint error rather than this raising."""
    parts = text.split()
    try:
        if len(parts) == 1:
            day = datetime.strptime(parts[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            return day, day + timedelta(days=1)
        if len(parts) == 2:
            start = datetime.strptime(parts[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            end = datetime.strptime(parts[1], "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
            return start, end
    except ValueError:
        pass
    return None


# ------------------------------------------------------------------
# Entry point / navigation
# ------------------------------------------------------------------

def _dashboard_text(db: Database, config: Config, notifier: Notifier) -> str:
    """Main menu as an at-a-glance status board, not just a list of
    buttons - an operator opening the bot should see whether anything
    needs attention before tapping into a submenu."""
    now = datetime.now(timezone.utc)
    offline_after = runtime_settings.get_node_offline_after_seconds(db, config)
    nodes = db.list_nodes()
    online = sum(1 for n in nodes if n.is_online(offline_after, now))
    tz_offset = runtime_settings.get_timezone_offset_minutes(db, config)
    today_start, _ = _period_range("today", now, tz_offset)
    new_today = db.count_domains(since=today_start)
    buffered_total = sum(n.agent_buffer_size or 0 for n in nodes)
    watch_state = "включены" if notifier.is_watchlist_enabled() else "выключены"

    return (
        "🖥 <b>Domain Monitor</b>\n\n"
        "🟢 Сервер работает\n"
        f"📡 Ноды: {online} / {len(nodes)} online\n"
        f"🌐 Доменов сегодня: {new_today}\n"
        f"📥 Буфер нод: {buffered_total} событий\n"
        f"🔔 Watch-уведомления: {watch_state}"
    )


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext, db: Database, config: Config, notifier: Notifier) -> None:
    await state.clear()
    await message.answer(
        _dashboard_text(db, config, notifier),
        parse_mode="HTML",
        reply_markup=kb.main_menu(fleet_control.is_monitoring_enabled(db), fleet_control.is_sending_enabled(db)),
    )


@router.callback_query(F.data == "main")
async def cb_main(call: CallbackQuery, state: FSMContext, db: Database, config: Config, notifier: Notifier) -> None:
    await state.clear()
    await call.message.edit_text(
        _dashboard_text(db, config, notifier),
        parse_mode="HTML",
        reply_markup=kb.main_menu(fleet_control.is_monitoring_enabled(db), fleet_control.is_sending_enabled(db)),
    )
    await call.answer()


@router.callback_query(F.data == "fleet_toggle_monitoring")
async def cb_fleet_toggle_monitoring(
    call: CallbackQuery, state: FSMContext, db: Database, config: Config, notifier: Notifier,
) -> None:
    fleet_control.set_monitoring_enabled(db, not fleet_control.is_monitoring_enabled(db))
    await call.answer(
        "⏸ Мониторинг остановлен на всех нодах" if not fleet_control.is_monitoring_enabled(db)
        else "▶ Мониторинг возобновлён на всех нодах"
    )
    await cb_main(call, state, db, config, notifier)


@router.callback_query(F.data == "fleet_toggle_sending")
async def cb_fleet_toggle_sending(
    call: CallbackQuery, state: FSMContext, db: Database, config: Config, notifier: Notifier,
) -> None:
    fleet_control.set_sending_enabled(db, not fleet_control.is_sending_enabled(db))
    await call.answer(
        "⏸ Отправка доменов остановлена на всех нодах" if not fleet_control.is_sending_enabled(db)
        else "▶ Отправка доменов возобновлена на всех нодах"
    )
    await cb_main(call, state, db, config, notifier)


# ------------------------------------------------------------------
# Nodes
# ------------------------------------------------------------------

def _node_category(node, online_ids: set[int], fleet_mon: bool) -> str:
    if node.status == "revoked":
        return "revoked"
    if node.id not in online_ids:
        return "offline"
    if not (node.monitoring_enabled and fleet_mon):
        return "paused"
    return "online"


def _node_matches_filter(node, filter_key: str, online_ids: set[int], fleet_mon: bool) -> bool:
    if filter_key == "all":
        return True
    if filter_key == "full_buffer":
        return (
            node.buffer_bytes is not None and node.buffer_limit_bytes
            and node.buffer_bytes / node.buffer_limit_bytes >= 0.9
        )
    return _node_category(node, online_ids, fleet_mon) == filter_key


def _node_matches_search(node, term: str) -> bool:
    if not term:
        return True
    term = term.lower()
    return (
        term in node.name.lower()
        or term in (node.ip or "").lower()
        or term in (node.hostname or "").lower()
    )


async def _nodes_state(state: FSMContext) -> tuple[str, str]:
    data = await state.get_data()
    return data.get("nodes_filter", "all"), data.get("nodes_search", "")


def _build_nodes_screen(
    db: Database, config: Config, filter_key: str, search: str, page: int,
) -> tuple[str, InlineKeyboardMarkup]:
    nodes = db.list_nodes()
    online_ids = _online_ids(db, config)
    fleet_mon = fleet_control.is_monitoring_enabled(db)
    filtered = [
        n for n in nodes
        if _node_matches_filter(n, filter_key, online_ids, fleet_mon) and _node_matches_search(n, search)
    ]
    counts = {
        cat: sum(1 for n in nodes if _node_category(n, online_ids, fleet_mon) == cat)
        for cat in ("online", "paused", "offline", "revoked")
    }
    page_nodes = filtered[page * kb.NODES_PAGE_SIZE:(page + 1) * kb.NODES_PAGE_SIZE]

    lines = [
        "📡 <b>Ноды</b>",
        f"🟢 Online: {counts['online']}   🔵 На паузе: {counts['paused']}   "
        f"🔴 Offline: {counts['offline']}   ⚫ Отозваны: {counts['revoked']}",
    ]
    if search:
        lines.append(f"🔎 Поиск: «{html.escape(search)}»")
    if filter_key != "all":
        lines.append(f"Фильтр: {kb.NODE_FILTER_LABELS.get(filter_key, filter_key)}")
    lines.append("")
    if not nodes:
        lines.append("Пока не добавлено ни одной ноды.")
    elif not filtered:
        lines.append("Ничего не найдено по текущим поиску/фильтру.")
    else:
        lines.append("Выберите ноду для управления:")
    text = "\n".join(lines)
    markup = kb.nodes_list(page_nodes, online_ids, fleet_mon, filter_key, bool(search), page, len(filtered))
    return text, markup


@router.callback_query(F.data == "nodes")
async def cb_nodes(call: CallbackQuery, state: FSMContext, db: Database, config: Config) -> None:
    await state.set_state(None)
    filter_key, search = await _nodes_state(state)
    text, markup = _build_nodes_screen(db, config, filter_key, search, page=0)
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    await call.answer()


@router.callback_query(F.data.startswith("nodes_page:"))
async def cb_nodes_page(call: CallbackQuery, state: FSMContext, db: Database, config: Config) -> None:
    page = int(call.data.split(":")[1])
    filter_key, search = await _nodes_state(state)
    text, markup = _build_nodes_screen(db, config, filter_key, search, page)
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    await call.answer()


@router.callback_query(F.data == "nodes_filter_menu")
async def cb_nodes_filter_menu(call: CallbackQuery, state: FSMContext) -> None:
    filter_key, _ = await _nodes_state(state)
    await call.message.edit_text(
        "📡 Выберите фильтр по статусу ноды:", reply_markup=kb.nodes_filter_menu(filter_key),
    )
    await call.answer()


@router.callback_query(F.data.startswith("nodes_filter_set:"))
async def cb_nodes_filter_set(call: CallbackQuery, state: FSMContext, db: Database, config: Config) -> None:
    filter_key = call.data.split(":")[1]
    await state.update_data(nodes_filter=filter_key)
    _, search = await _nodes_state(state)
    text, markup = _build_nodes_screen(db, config, filter_key, search, page=0)
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    await call.answer()


@router.callback_query(F.data == "nodes_search")
async def cb_nodes_search_prompt(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Inputs.waiting_for_node_search)
    await call.message.edit_text(
        "🔎 Введите часть имени, IP или hostname ноды для поиска:",
        reply_markup=kb.cancel_input("nodes"),
    )
    await call.answer()


@router.message(Inputs.waiting_for_node_search)
async def on_node_search_input(message: Message, state: FSMContext, db: Database, config: Config) -> None:
    term = (message.text or "").strip()
    await state.set_state(None)
    await state.update_data(nodes_search=term)
    filter_key, _ = await _nodes_state(state)
    text, markup = _build_nodes_screen(db, config, filter_key, term, page=0)
    await message.answer(text, parse_mode="HTML", reply_markup=markup)


@router.callback_query(F.data == "nodes_search_clear")
async def cb_nodes_search_clear(call: CallbackQuery, state: FSMContext, db: Database, config: Config) -> None:
    await state.update_data(nodes_search="")
    filter_key, _ = await _nodes_state(state)
    text, markup = _build_nodes_screen(db, config, filter_key, "", page=0)
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    await call.answer()


# ------------------------------------------------------------------
# Nodes - bulk actions
# ------------------------------------------------------------------

async def _bulk_selected(state: FSMContext) -> set[int]:
    data = await state.get_data()
    return set(data.get("bulk_selected", []))


async def _set_bulk_selected(state: FSMContext, ids: set[int]) -> None:
    await state.update_data(bulk_selected=sorted(ids))


def _is_outdated(node) -> bool:
    badge = version_badge(node.version)
    return bool(badge and badge.startswith("🟡"))


def _is_problem(node, online_ids: set[int]) -> bool:
    return node.id not in online_ids or bool(node.last_send_error)


async def _render_bulk_screen(call: CallbackQuery, state: FSMContext, db: Database, config: Config) -> None:
    nodes = db.list_nodes()
    selected = await _bulk_selected(state)
    selected &= {n.id for n in nodes}  # drop ids of nodes deleted meanwhile
    await _set_bulk_selected(state, selected)
    text = (
        "🧰 <b>Массовые действия с нодами</b>\n\n"
        f"Выбрано: {len(selected)} из {len(nodes)}\n\n"
        "Отметьте ноды ✅/⬜, затем выберите действие ниже - "
        "перед применением будет запрошено подтверждение."
    )
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb.nodes_bulk_menu(nodes, selected))
    await call.answer()


@router.callback_query(F.data == "nodes_bulk")
async def cb_nodes_bulk(call: CallbackQuery, state: FSMContext, db: Database, config: Config) -> None:
    await _render_bulk_screen(call, state, db, config)


@router.callback_query(F.data.startswith("nodes_bulk_toggle:"))
async def cb_nodes_bulk_toggle(call: CallbackQuery, state: FSMContext, db: Database, config: Config) -> None:
    node_id = int(call.data.split(":")[1])
    selected = await _bulk_selected(state)
    selected.symmetric_difference_update({node_id})
    await _set_bulk_selected(state, selected)
    await _render_bulk_screen(call, state, db, config)


@router.callback_query(F.data == "nodes_bulk_select_all")
async def cb_nodes_bulk_select_all(call: CallbackQuery, state: FSMContext, db: Database, config: Config) -> None:
    await _set_bulk_selected(state, {n.id for n in db.list_nodes()})
    await _render_bulk_screen(call, state, db, config)


@router.callback_query(F.data == "nodes_bulk_select_none")
async def cb_nodes_bulk_select_none(call: CallbackQuery, state: FSMContext, db: Database, config: Config) -> None:
    await _set_bulk_selected(state, set())
    await _render_bulk_screen(call, state, db, config)


@router.callback_query(F.data == "nodes_bulk_select_outdated")
async def cb_nodes_bulk_select_outdated(call: CallbackQuery, state: FSMContext, db: Database, config: Config) -> None:
    await _set_bulk_selected(state, {n.id for n in db.list_nodes() if _is_outdated(n)})
    await _render_bulk_screen(call, state, db, config)


@router.callback_query(F.data == "nodes_bulk_select_problem")
async def cb_nodes_bulk_select_problem(call: CallbackQuery, state: FSMContext, db: Database, config: Config) -> None:
    online_ids = _online_ids(db, config)
    await _set_bulk_selected(state, {n.id for n in db.list_nodes() if _is_problem(n, online_ids)})
    await _render_bulk_screen(call, state, db, config)


@router.callback_query(F.data.startswith("nodes_bulk_action:"))
async def cb_nodes_bulk_action_prompt(call: CallbackQuery, state: FSMContext, db: Database) -> None:
    action = call.data.split(":")[1]
    selected = await _bulk_selected(state)
    if not selected:
        await call.answer("Сначала отметьте хотя бы одну ноду.", show_alert=True)
        return
    label = kb.BULK_ACTION_LABELS.get(action, action)
    await call.message.edit_text(
        f"Применить «{label}» к {len(selected)} нод(ам)?",
        reply_markup=kb.confirm_bulk_action(action, len(selected)),
    )
    await call.answer()


@router.callback_query(F.data.startswith("nodes_bulk_confirm:"))
async def cb_nodes_bulk_confirm(call: CallbackQuery, state: FSMContext, db: Database, config: Config) -> None:
    action = call.data.split(":")[1]
    selected = await _bulk_selected(state)
    setter, value = {
        "mon_on": (db.set_node_monitoring, True),
        "mon_off": (db.set_node_monitoring, False),
        "send_on": (db.set_node_sending, True),
        "send_off": (db.set_node_sending, False),
        "notif_on": (db.set_node_notifications, True),
        "notif_off": (db.set_node_notifications, False),
    }.get(action, (None, None))
    if setter is None:
        await call.answer("Неизвестное действие", show_alert=True)
        return
    for node_id in selected:
        setter(node_id, value)
    await _set_bulk_selected(state, set())
    await call.answer(f"Применено к {len(selected)} нод(ам)")
    await _render_bulk_screen(call, state, db, config)


@router.callback_query(F.data.startswith("node:"))
async def cb_node_card(call: CallbackQuery, db: Database, config: Config) -> None:
    node_id = int(call.data.split(":")[1])
    node = db.get_node(node_id)
    if node is None:
        await call.answer("Нода не найдена", show_alert=True)
        return

    now = datetime.now(timezone.utc)
    offline_after = runtime_settings.get_node_offline_after_seconds(db, config)
    online = node.is_online(offline_after, now)
    status_line = "🟢 Online" if online else ("⛔ Отозвана" if node.status == "revoked" else "🔴 Offline")
    hb_line = format_relative_time(node.last_heartbeat_at, now)

    domains = db.list_domains(limit=1000, node_id=node.id)

    uptime_line = ""
    if node.agent_uptime_seconds is not None:
        uptime_line = f"Uptime агента: {format_duration(node.agent_uptime_seconds)}\n"

    buffer_line = ""
    if node.buffer_bytes is not None and node.buffer_limit_bytes:
        buffer_line = (
            f"\n📦 Буфер: {node.agent_buffer_size or 0} событий, "
            f"{format_bytes(node.buffer_bytes)} / {format_bytes(node.buffer_limit_bytes)}"
        )
    elif node.agent_buffer_size:
        # Older agent that reports buffer_size but not the byte-level
        # telemetry yet - show what we actually know, nothing invented.
        buffer_line = (
            f"\n📦 В локальном буфере агента: {node.agent_buffer_size} "
            f"(накоплено, ждёт отправки на сервер)"
        )

    sources_line = ""
    if node.capture_tls_running is not None or node.capture_dns_running is not None:
        # A source this agent was never asked to enable (known via
        # sources_enabled, spec 10) isn't a problem - omit it rather than
        # showing 🔴 for something that was never supposed to be running.
        # Falls back to always showing both when sources_enabled is
        # unknown (pre-upgrade agent), matching the old behavior exactly.
        enabled = set(node.sources_enabled) if node.sources_enabled is not None else None
        parts = []
        if node.capture_tls_running is not None and (enabled is None or "tls_sni" in enabled):
            parts.append(f"TLS SNI {'🟢' if node.capture_tls_running else '🔴'}")
        if node.capture_dns_running is not None and (enabled is None or "dns" in enabled):
            parts.append(f"DNS {'🟢' if node.capture_dns_running else '🔴'}")
        if parts:
            sources_line = f"Источники: {', '.join(parts)}\n"

    error_line = ""
    if node.last_send_error:
        error_line = f"\n⚠️ Ошибка последней отправки: {html.escape(node.last_send_error)}\n"

    dest_line = (
        "⬜ Как по умолчанию" if node.notify_destination == "inherit"
        else kb.NOTIFY_DEST_LABELS.get(node.notify_destination, node.notify_destination)
    )
    if node.notify_destination in ("group", "both"):
        where = (
            f"chat_id {node.notify_group_chat_id}"
            + (f", топик {node.notify_group_topic_id}" if node.notify_group_topic_id else "")
        ) if node.notify_group_chat_id else "не привязано"
        dest_line += f" ({where})"

    version_line = version_badge(node.version) or f"Версия агента: {node.version or '—'}"
    text = (
        f"🖥 <b>{node.name}</b>\n\n"
        f"Статус: {status_line}\n"
        f"Последний heartbeat: {hb_line}\n"
        f"{version_line}\n"
        f"IP: {node.ip or '—'}\n"
        f"Hostname: {node.hostname or '—'}\n"
        f"{uptime_line}"
        f"{sources_line}\n"
        f"Доменов с этой ноды: {len(domains)}"
        f"{buffer_line}"
        f"{error_line}\n"
        f"Уведомления идут: {dest_line}"
    )
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb.node_card(node))
    await call.answer()


def _build_node_check_text(node, db: Database, config: Config) -> str:
    """Everything the "🧪 Проверить" button can honestly say from data
    the server already has - never connects to the node itself
    (architecture stays Agent -> Server, no SSH/remote exec)."""
    now = datetime.now(timezone.utc)
    offline_after = runtime_settings.get_node_offline_after_seconds(db, config)
    online = node.is_online(offline_after, now)

    effective_monitoring = node.monitoring_enabled and fleet_control.is_monitoring_enabled(db)
    effective_sending = node.sending_enabled and fleet_control.is_sending_enabled(db)

    issues: list[str] = []
    lines = [f"🧪 <b>Проверка ноды: {node.name}</b>\n"]

    hb_line = format_relative_time(node.last_heartbeat_at, now)
    lines.append(f"Heartbeat: {hb_line}")
    if node.last_heartbeat_at is not None:
        delay = (now - node.last_heartbeat_at).total_seconds()
        lines.append(f"Задержка heartbeat: {int(delay)} сек (порог offline: {offline_after} сек)")
        if delay > offline_after:
            issues.append("нет свежего heartbeat - нода считается offline")
    else:
        issues.append("от ноды ещё не было ни одного heartbeat")

    lines.append(version_badge(node.version) or f"Версия агента: {node.version or '—'}")
    if not effective_monitoring:
        why = "выключен на ноде" if not node.monitoring_enabled else "выключен на всей флотилии"
        lines.append(f"Мониторинг: на паузе ({why}) - capture ожидаемо не запущен")
    if not effective_sending:
        why = "выключена на ноде" if not node.sending_enabled else "выключена на всей флотилии"
        lines.append(f"Отправка: на паузе ({why}) - 503 ниже ожидаем")

    if node.capture_tls_running is not None or node.capture_dns_running is not None:
        # A source outside sources_enabled was never supposed to be
        # running - report it separately from an actual malfunction
        # (unknown sources_enabled, i.e. a pre-upgrade agent, keeps the
        # old "any source not running is worth flagging" behavior).
        enabled = set(node.sources_enabled) if node.sources_enabled is not None else None
        cap_bits, not_enabled_bits = [], []
        if node.capture_tls_running is not None:
            if enabled is not None and "tls_sni" not in enabled:
                not_enabled_bits.append("TLS SNI")
            else:
                cap_bits.append(f"TLS SNI {'работает' if node.capture_tls_running else 'НЕ работает'}")
                if effective_monitoring and not node.capture_tls_running:
                    issues.append("мониторинг включён, но capture TLS SNI не запущен на агенте")
        if node.capture_dns_running is not None:
            if enabled is not None and "dns" not in enabled:
                not_enabled_bits.append("DNS")
            else:
                cap_bits.append(f"DNS {'работает' if node.capture_dns_running else 'НЕ работает'}")
                if effective_monitoring and not node.capture_dns_running:
                    issues.append("мониторинг включён, но capture DNS не запущен на агенте")
        if cap_bits:
            lines.append("Capture: " + ", ".join(cap_bits))
        if not_enabled_bits:
            lines.append(f"Не включено на этой ноде: {', '.join(not_enabled_bits)}")
    else:
        lines.append("Capture: неизвестно (агент не сообщает - обновите agent)")

    if node.buffer_bytes is not None and node.buffer_limit_bytes:
        pct = 100 * node.buffer_bytes / node.buffer_limit_bytes
        lines.append(
            f"Буфер (backlog): {node.agent_buffer_size or 0} событий, "
            f"{format_bytes(node.buffer_bytes)} / {format_bytes(node.buffer_limit_bytes)} ({pct:.0f}%)"
        )
        if pct >= 90:
            issues.append(f"буфер заполнен на {pct:.0f}% - события могут начать теряться")
    elif node.agent_buffer_size:
        lines.append(f"Буфер (backlog): {node.agent_buffer_size} событий")

    lines.append(f"Последняя успешная отправка на сервер: {format_relative_time(node.last_send_success_at, now)}")
    if node.last_send_error:
        lines.append(f"⚠️ Ошибка последней отправки: {html.escape(node.last_send_error)}")
        if effective_sending:
            issues.append("последняя отправка событий закончилась ошибкой")
        # else: "503 Service Unavailable" is the server's deliberate
        # response while sending is paused - showing the raw text above
        # is honest, but it's not a problem to flag when the pause is
        # intentional (same "не выдумывать проблему" rule as monitoring).
    if node.dropped_events_total:
        lines.append(f"Потеряно событий (с последнего запуска агента): {node.dropped_events_total}")
        issues.append(f"агент уже потерял {node.dropped_events_total} событий из переполненного буфера")

    lines.append("")
    if online and not issues:
        lines.append("✅ Проблем не найдено.")
    else:
        lines.append("Найденные проблемы:")
        lines.extend(f"  • {issue}" for issue in issues)

    return "\n".join(lines)


@router.callback_query(F.data.startswith("node_check:"))
async def cb_node_check(call: CallbackQuery, db: Database, config: Config) -> None:
    node_id = int(call.data.split(":")[1])
    node = db.get_node(node_id)
    if node is None:
        await call.answer("Нода не найдена", show_alert=True)
        return
    text = _build_node_check_text(node, db, config)
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb.back_button(f"node:{node.id}"))
    await call.answer()


@router.callback_query(F.data.startswith("node_toggle_mon:"))
async def cb_node_toggle_mon(call: CallbackQuery, db: Database, config: Config) -> None:
    node_id = int(call.data.split(":")[1])
    node = db.get_node(node_id)
    if node:
        db.set_node_monitoring(node_id, not node.monitoring_enabled)
    await cb_node_card(call, db, config)


@router.callback_query(F.data.startswith("node_toggle_sending:"))
async def cb_node_toggle_sending(call: CallbackQuery, db: Database, config: Config) -> None:
    node_id = int(call.data.split(":")[1])
    node = db.get_node(node_id)
    if node:
        db.set_node_sending(node_id, not node.sending_enabled)
    await cb_node_card(call, db, config)


@router.callback_query(F.data.startswith("node_toggle_notif:"))
async def cb_node_toggle_notif(call: CallbackQuery, db: Database, config: Config) -> None:
    node_id = int(call.data.split(":")[1])
    node = db.get_node(node_id)
    if node:
        db.set_node_notifications(node_id, not node.notifications_enabled)
    await cb_node_card(call, db, config)


@router.callback_query(F.data.startswith("node_notify_dest:"))
async def cb_node_notify_dest(call: CallbackQuery, db: Database) -> None:
    node_id = int(call.data.split(":")[1])
    node = db.get_node(node_id)
    if node is None:
        await call.answer("Нода не найдена", show_alert=True)
        return
    await call.message.edit_text(
        f"📍 <b>{node.name}</b> — куда слать уведомления о новых доменах и Watch-хитах?\n\n"
        f"💬 В ЛС — всем админам в личку (как раньше).\n"
        f"👥 В группу — в конкретный чат, можно с указанием топика (темы) внутри него.\n"
        f"🔀 И то, и то.",
        parse_mode="HTML", reply_markup=kb.node_notify_dest_menu(node),
    )
    await call.answer()


@router.callback_query(F.data.startswith("node_notify_dest_set:"))
async def cb_node_notify_dest_set(call: CallbackQuery, db: Database) -> None:
    _, node_id_raw, dest = call.data.split(":")
    node_id = int(node_id_raw)
    db.set_node_notify_destination(node_id, dest)
    await call.answer("Способ доставки обновлён")
    await cb_node_notify_dest(call, db)


@router.callback_query(F.data.startswith("node_notify_bind:"))
async def cb_node_notify_bind(call: CallbackQuery, db: Database, topic_binding: TopicBindingManager) -> None:
    node_id = int(call.data.split(":")[1])
    node = db.get_node(node_id)
    if node is None:
        await call.answer("Нода не найдена", show_alert=True)
        return
    topic_binding.start(call.from_user.id, node_id)
    await call.message.edit_text(
        f"🔗 Привязка группы/топика для <b>{node.name}</b>\n\n"
        f"1. Добавь бота в нужную супергруппу (обычным участником).\n"
        f"2. Если это форум с темами — открой нужную тему, если нет - просто напиши в общий чат.\n"
        f"3. Отправь туда команду:\n<code>/bind</code>\n\n"
        f"Бот сам подхватит chat_id и топик из этого сообщения. Действует "
        f"{300 // 60} минут, потом привязку нужно начать заново.",
        parse_mode="HTML", reply_markup=kb.cancel_input(f"node_notify_dest:{node_id}"),
    )
    await call.answer()


@router.message(Command("bind"))
async def cmd_bind(message: Message, db: Database, topic_binding: TopicBindingManager) -> None:
    user = message.from_user
    if user is None:
        return
    node_id = topic_binding.pop(user.id)
    if node_id is None:
        # No pending bind request from this admin - a stray /bind (or an
        # expired one). Silent in a group (no reason to spam it), a short
        # hint in DM.
        if message.chat.type == "private":
            await message.reply("Нет активной привязки. Начни из карточки ноды: 📍 Куда слать -> 🔗 Привязать группу/топик.")
        return
    node = db.get_node(node_id)
    if node is None:
        return
    db.set_node_notify_group(node_id, message.chat.id, message.message_thread_id)
    where = f"chat_id <code>{message.chat.id}</code>"
    if message.message_thread_id:
        where += f", топик <code>{message.message_thread_id}</code>"
    await message.reply(
        f"✅ Привязано к ноде <b>{node.name}</b>: {where}.\n"
        f"Не забудь выставить способ доставки «👥 В группу» или «🔀 В ЛС и в группу» в карточке ноды, "
        f"если ещё не сделал.",
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("node_regen:"))
async def cb_node_regen(call: CallbackQuery, db: Database) -> None:
    node_id = int(call.data.split(":")[1])
    node = db.get_node(node_id)
    if node is None:
        await call.answer("Нода не найдена", show_alert=True)
        return
    token = generate_node_token()
    db.regenerate_token(node_id, hash_token(token))
    await call.message.answer(
        f"🔑 Новый токен для <b>{node.name}</b>:\n\n<code>{token}</code>\n\n"
        f"Старый токен больше не работает. Обновите NODE_TOKEN в .env агента и перезапустите его "
        f"(<code>domain-monitor-agent restart</code>).",
        parse_mode="HTML",
    )
    await call.answer("Токен обновлён")


@router.callback_query(F.data.startswith("node_revoke:"))
async def cb_node_revoke(call: CallbackQuery, db: Database, config: Config) -> None:
    node_id = int(call.data.split(":")[1])
    db.set_node_status(node_id, "revoked")
    await call.answer("Нода отозвана")
    await cb_node_card(call, db, config)


@router.callback_query(F.data.startswith("node_delete:"))
async def cb_node_delete_confirm_prompt(call: CallbackQuery) -> None:
    node_id = int(call.data.split(":")[1])
    await call.message.edit_text(
        "🗑 Удалить ноду вместе со всеми её доменами и событиями?\nЭто действие необратимо.",
        reply_markup=kb.confirm_delete_node(node_id),
    )
    await call.answer()


@router.callback_query(F.data.startswith("node_delete_confirm:"))
async def cb_node_delete(call: CallbackQuery, state: FSMContext, db: Database, config: Config) -> None:
    node_id = int(call.data.split(":")[1])
    db.delete_node(node_id)
    await call.answer("Нода удалена")
    await cb_nodes(call, state, db, config)


_INSTALL_ONE_LINER = (
    "curl -fsSL https://raw.githubusercontent.com/Theraf1u/domain-monitor/main/install.sh "
    '| sudo bash -s -- agent "{server_url}" "{token}"'
)


@router.callback_query(F.data == "node_add")
async def cb_node_add(call: CallbackQuery, db: Database, config: Config) -> None:
    """One tap, zero typing: creates the node with an auto-assigned
    placeholder name (renamed to its real IP on first heartbeat, see
    Database.touch_heartbeat) and hands back one ready-to-paste command -
    server URL and token are baked into it, nothing to type on the new
    node, no separate wizard prompts to answer there."""
    token = generate_node_token()
    node = db.create_node_auto(hash_token(token))
    command = _INSTALL_ONE_LINER.format(server_url=config.public_url, token=token)
    await call.message.edit_text(
        f"✅ Нода <b>{node.name}</b> создана.\n\n"
        f"Выполните на новой ноде одну команду (сервер и токен уже внутри):\n"
        f"<pre>{command}</pre>\n"
        f"Токен показывается только сейчас — если команду не скопировать, "
        f"придётся создать ноду заново.\n\n"
        f"После установки нода сама переименуется в свой IP. Своё имя можно "
        f"задать в любой момент через карточку ноды («✏️ Переименовать»).",
        parse_mode="HTML", reply_markup=kb.back_button("nodes"),
    )
    await call.answer()


@router.callback_query(F.data.startswith("node_rename:"))
async def cb_node_rename(call: CallbackQuery, state: FSMContext, db: Database) -> None:
    node_id = int(call.data.split(":")[1])
    node = db.get_node(node_id)
    if node is None:
        await call.answer("Нода не найдена", show_alert=True)
        return
    await state.set_state(Inputs.waiting_for_node_rename)
    await state.update_data(node_id=node_id)
    await call.message.edit_text(
        f"Введите новое имя для ноды <b>{node.name}</b>:",
        parse_mode="HTML", reply_markup=kb.cancel_input(f"node:{node_id}"),
    )
    await call.answer()


@router.message(Inputs.waiting_for_node_rename)
async def on_node_rename_input(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    node_id = data.get("node_id")
    await state.clear()
    name = (message.text or "").strip()
    if not node_id:
        await message.answer("Сессия истекла. Открой меню нод и попробуй снова.", reply_markup=kb.back_button("nodes"))
        return
    if not name or len(name) > 100:
        await message.answer("Некорректное имя. Открой карточку ноды и попробуй снова.", reply_markup=kb.back_button(f"node:{node_id}"))
        return
    if not db.rename_node(node_id, name):
        await message.answer(f"Нода с именем «{name}» уже существует.", reply_markup=kb.back_button(f"node:{node_id}"))
        return
    node = db.get_node(node_id)
    await message.answer(
        f"✅ Нода переименована в <b>{node.name}</b>.",
        parse_mode="HTML", reply_markup=kb.back_button(f"node:{node_id}"),
    )


# ------------------------------------------------------------------
# Domains
# ------------------------------------------------------------------

@router.callback_query(F.data == "domains")
async def cb_domains(call: CallbackQuery) -> None:
    await call.message.edit_text("🌐 <b>Домены</b>", parse_mode="HTML", reply_markup=kb.domains_menu())
    await call.answer()


def _domains_recent_text(db: Database) -> str:
    domains = db.list_domains(limit=20, order_by="last_seen")
    if not domains:
        return "Пока нет ни одного домена."
    lines = "\n".join(f"• <code>{d.domain}</code> ({d.hits})" for d in domains)
    return f"🕐 <b>Последние домены</b>\n\n{lines}"


def _domains_top_text(db: Database) -> str:
    domains = db.top_domains(limit=20)
    if not domains:
        return "Пока нет ни одного домена."
    lines = "\n".join(f"• <code>{d.domain}</code> — {d.hits}" for d in domains)
    return f"🔝 <b>Топ доменов по обращениям</b>\n\n{lines}"


@router.callback_query(F.data == "domains_recent")
async def cb_domains_recent(call: CallbackQuery, db: Database, live_view: LiveViewManager) -> None:
    live_active = live_view.is_active(call.message.chat.id, call.message.message_id)
    await call.message.edit_text(
        _domains_recent_text(db), parse_mode="HTML", reply_markup=kb.domains_recent_menu(live_active),
    )
    await call.answer()


@router.callback_query(F.data == "domains_top")
async def cb_domains_top(call: CallbackQuery, db: Database, live_view: LiveViewManager) -> None:
    live_active = live_view.is_active(call.message.chat.id, call.message.message_id)
    await call.message.edit_text(
        _domains_top_text(db), parse_mode="HTML", reply_markup=kb.domains_top_menu(live_active),
    )
    await call.answer()


@router.callback_query(F.data == "domains_export")
async def cb_domains_export_menu(call: CallbackQuery) -> None:
    await call.message.edit_text(
        "📤 <b>Экспорт доменов</b>\n\nЗа какой период выгрузить .txt?",
        parse_mode="HTML", reply_markup=kb.export_period_menu(),
    )
    await call.answer()


@router.callback_query(F.data.startswith("domains_export_period:"))
async def cb_domains_export_period(call: CallbackQuery, db: Database, config: Config) -> None:
    period = call.data.split(":", 1)[1]
    now = datetime.now(timezone.utc)
    tz_offset = runtime_settings.get_timezone_offset_minutes(db, config)
    since, until = _period_range(period, now, tz_offset)
    label = dict(kb.EXPORT_PERIODS).get(period, period)
    domains = db.list_domains(limit=1_000_000, order_by="domain", since=since, until=until)
    if not domains:
        await call.answer(f"За период «{label}» доменов нет", show_alert=True)
        return
    content = "\n".join(d.domain for d in domains) + "\n"
    file = BufferedInputFile(content.encode("utf-8"), filename="domains.txt")
    await call.message.answer_document(file, caption=f"Экспорт ({label}): {len(domains)} домен(ов)")
    await call.answer()


@router.callback_query(F.data == "domains_export_custom")
async def cb_domains_export_custom(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Inputs.waiting_for_export_range)
    await call.message.edit_text(
        "Введите период в формате <code>ГГГГ-ММ-ДД</code> (один день) или "
        "<code>ГГГГ-ММ-ДД ГГГГ-ММ-ДД</code> (с — по, включительно):",
        parse_mode="HTML", reply_markup=kb.cancel_input("domains_export"),
    )
    await call.answer()


@router.message(Inputs.waiting_for_export_range)
async def on_export_range_input(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    text = (message.text or "").strip()
    parsed = _parse_date_range(text)
    if parsed is None:
        await message.answer(
            "Не понял формат. Пример: <code>2026-09-01</code> или <code>2026-09-01 2026-09-15</code>.",
            parse_mode="HTML", reply_markup=kb.back_button("domains_export"),
        )
        return
    since, until = parsed

    domains = db.list_domains(limit=1_000_000, order_by="domain", since=since, until=until)
    if not domains:
        await message.answer("За этот диапазон доменов нет.", reply_markup=kb.back_button("domains"))
        return
    content = "\n".join(d.domain for d in domains) + "\n"
    file = BufferedInputFile(content.encode("utf-8"), filename="domains.txt")
    await message.answer_document(file, caption=f"Экспорт ({text}): {len(domains)} домен(ов)")
    await message.answer("Готово.", reply_markup=kb.back_button("domains"))


# ------------------------------------------------------------------
# Domains — search / filter / list / card / history (spec 2.0 Part 1, section 6)
# ------------------------------------------------------------------

DOMAIN_MINHITS_VALUES = {"any": None, "10": 10, "100": 100, "1000": 1000}
_DOMAIN_SOURCE_LABELS = {"tls_sni": "TLS SNI", "dns": "DNS"}
DOMAINS_EXPORT_LIMIT = 20_000


def _domain_filters_defaults() -> dict:
    return {
        "node_id": None, "node_label": None, "period": "all", "source": "all",
        "status": "all", "min_hits": "any", "new_only": False,
    }


async def _get_domain_filters(state: FSMContext) -> dict:
    data = await state.get_data()
    filters = _domain_filters_defaults()
    filters.update(data.get("domains_filter") or {})
    return filters


async def _get_domain_search(state: FSMContext) -> str | None:
    data = await state.get_data()
    return data.get("domains_search")


def _domains_query_kwargs(search: str | None, filters: dict) -> dict:
    since, until = _period_range(filters["period"], datetime.now(timezone.utc))
    return dict(
        search=search, node_id=filters["node_id"], since=since, until=until,
        since_field="first_seen" if filters["new_only"] else "last_seen",
        source=None if filters["source"] == "all" else filters["source"],
        min_hits=DOMAIN_MINHITS_VALUES.get(filters["min_hits"]),
        list_status=None if filters["status"] == "all" else filters["status"],
    )


def _domain_filters_summary(search: str | None, filters: dict) -> list[str]:
    parts = []
    if search:
        parts.append(f"поиск «{html.escape(search)}»")
    if filters["node_label"]:
        parts.append(f"нода {filters['node_label']}")
    if filters["period"] != "all":
        parts.append(dict(kb.DOMAIN_PERIODS).get(filters["period"], filters["period"]))
    if filters["source"] != "all":
        parts.append(kb.DOMAIN_SOURCE_LABELS.get(filters["source"], filters["source"]))
    if filters["status"] != "all":
        parts.append(kb.DOMAIN_STATUS_LABELS.get(filters["status"], filters["status"]))
    if filters["min_hits"] != "any":
        parts.append(kb.DOMAIN_MINHITS_LABELS.get(filters["min_hits"], filters["min_hits"]))
    if filters["new_only"]:
        parts.append("только новые")
    return parts


async def _build_domains_list_screen(db: Database, state: FSMContext, page: int) -> tuple[str, list, int, bool, bool]:
    search = await _get_domain_search(state)
    filters = await _get_domain_filters(state)
    kwargs = _domains_query_kwargs(search, filters)
    total = db.count_domains(**kwargs)
    domains = db.list_domains(
        limit=kb.DOMAINS_PAGE_SIZE, offset=page * kb.DOMAINS_PAGE_SIZE, order_by="last_seen", **kwargs,
    )

    lines = ["🌐 <b>Домены</b>"]
    summary = _domain_filters_summary(search, filters)
    if summary:
        lines.append("Фильтр: " + ", ".join(summary))
    lines.append(f"Найдено: {total}")
    if not domains:
        lines.append("\nНичего не найдено.")
    text = "\n".join(lines)

    has_filters = any([
        filters["node_id"] is not None, filters["period"] != "all", filters["source"] != "all",
        filters["status"] != "all", filters["min_hits"] != "any", filters["new_only"],
    ])
    return text, domains, total, bool(search), has_filters


async def _render_domains_list(target, db: Database, state: FSMContext, page: int) -> None:
    text, domains, total, has_search, has_filters = await _build_domains_list_screen(db, state, page)
    await target.edit_text(
        text, parse_mode="HTML", reply_markup=kb.domains_list_menu(domains, page, total, has_search, has_filters),
    )


@router.callback_query(F.data == "domains_list")
async def cb_domains_list(call: CallbackQuery, state: FSMContext, db: Database) -> None:
    await _render_domains_list(call.message, db, state, 0)
    await call.answer()


@router.callback_query(F.data.startswith("domains_list_page:"))
async def cb_domains_list_page(call: CallbackQuery, state: FSMContext, db: Database) -> None:
    page = int(call.data.split(":", 1)[1])
    await _render_domains_list(call.message, db, state, page)
    await call.answer()


@router.callback_query(F.data == "domains_search")
async def cb_domains_search_prompt(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Inputs.waiting_for_domain_search)
    await call.message.edit_text(
        "Введите часть домена для поиска (например <code>google</code> или <code>example.com</code>):",
        parse_mode="HTML", reply_markup=kb.cancel_input("domains_list"),
    )
    await call.answer()


@router.message(Inputs.waiting_for_domain_search)
async def on_domain_search_input(message: Message, state: FSMContext, db: Database) -> None:
    await state.set_state(None)
    query = (message.text or "").strip()
    await state.update_data(domains_search=query or None)
    text, domains, total, has_search, has_filters = await _build_domains_list_screen(db, state, 0)
    await message.answer(
        text, parse_mode="HTML", reply_markup=kb.domains_list_menu(domains, 0, total, has_search, has_filters),
    )


@router.callback_query(F.data == "domains_filter_menu")
async def cb_domains_filter_menu(call: CallbackQuery, state: FSMContext) -> None:
    filters = await _get_domain_filters(state)
    await call.message.edit_text(
        "🎛 <b>Фильтры доменов</b>", parse_mode="HTML", reply_markup=kb.domains_filter_menu(filters),
    )
    await call.answer()


async def _render_domains_filter_menu(call: CallbackQuery, state: FSMContext) -> None:
    filters = await _get_domain_filters(state)
    await call.message.edit_text(
        "🎛 <b>Фильтры доменов</b>", parse_mode="HTML", reply_markup=kb.domains_filter_menu(filters),
    )
    await call.answer()


@router.callback_query(F.data == "domains_filter_node")
async def cb_domains_filter_node(call: CallbackQuery, state: FSMContext, db: Database) -> None:
    filters = await _get_domain_filters(state)
    await call.message.edit_text(
        "Фильтр по ноде:", reply_markup=kb.domains_filter_node_menu(db.list_nodes(), filters["node_id"]),
    )
    await call.answer()


@router.callback_query(F.data.startswith("domains_filter_node_set:"))
async def cb_domains_filter_node_set(call: CallbackQuery, state: FSMContext, db: Database) -> None:
    raw = call.data.split(":", 1)[1]
    filters = await _get_domain_filters(state)
    if raw == "all":
        filters["node_id"], filters["node_label"] = None, None
    else:
        node = db.get_node(int(raw))
        filters["node_id"], filters["node_label"] = int(raw), (node.name if node else "?")
    await state.update_data(domains_filter=filters)
    await _render_domains_filter_menu(call, state)


@router.callback_query(F.data == "domains_filter_period")
async def cb_domains_filter_period(call: CallbackQuery, state: FSMContext) -> None:
    filters = await _get_domain_filters(state)
    await call.message.edit_text("Период:", reply_markup=kb.domains_filter_period_menu(filters["period"]))
    await call.answer()


@router.callback_query(F.data.startswith("domains_filter_period_set:"))
async def cb_domains_filter_period_set(call: CallbackQuery, state: FSMContext) -> None:
    filters = await _get_domain_filters(state)
    filters["period"] = call.data.split(":", 1)[1]
    await state.update_data(domains_filter=filters)
    await _render_domains_filter_menu(call, state)


@router.callback_query(F.data == "domains_filter_source")
async def cb_domains_filter_source(call: CallbackQuery, state: FSMContext) -> None:
    filters = await _get_domain_filters(state)
    await call.message.edit_text("Источник:", reply_markup=kb.domains_filter_source_menu(filters["source"]))
    await call.answer()


@router.callback_query(F.data.startswith("domains_filter_source_set:"))
async def cb_domains_filter_source_set(call: CallbackQuery, state: FSMContext) -> None:
    filters = await _get_domain_filters(state)
    filters["source"] = call.data.split(":", 1)[1]
    await state.update_data(domains_filter=filters)
    await _render_domains_filter_menu(call, state)


@router.callback_query(F.data == "domains_filter_status")
async def cb_domains_filter_status(call: CallbackQuery, state: FSMContext) -> None:
    filters = await _get_domain_filters(state)
    await call.message.edit_text("Статус:", reply_markup=kb.domains_filter_status_menu(filters["status"]))
    await call.answer()


@router.callback_query(F.data.startswith("domains_filter_status_set:"))
async def cb_domains_filter_status_set(call: CallbackQuery, state: FSMContext) -> None:
    filters = await _get_domain_filters(state)
    filters["status"] = call.data.split(":", 1)[1]
    await state.update_data(domains_filter=filters)
    await _render_domains_filter_menu(call, state)


@router.callback_query(F.data == "domains_filter_minhits")
async def cb_domains_filter_minhits(call: CallbackQuery, state: FSMContext) -> None:
    filters = await _get_domain_filters(state)
    await call.message.edit_text("Минимум хитов:", reply_markup=kb.domains_filter_minhits_menu(filters["min_hits"]))
    await call.answer()


@router.callback_query(F.data.startswith("domains_filter_minhits_set:"))
async def cb_domains_filter_minhits_set(call: CallbackQuery, state: FSMContext) -> None:
    filters = await _get_domain_filters(state)
    filters["min_hits"] = call.data.split(":", 1)[1]
    await state.update_data(domains_filter=filters)
    await _render_domains_filter_menu(call, state)


@router.callback_query(F.data == "domains_filter_new_only")
async def cb_domains_filter_new_only(call: CallbackQuery, state: FSMContext) -> None:
    filters = await _get_domain_filters(state)
    filters["new_only"] = not filters["new_only"]
    await state.update_data(domains_filter=filters)
    await _render_domains_filter_menu(call, state)


@router.callback_query(F.data == "domains_filter_reset")
async def cb_domains_filter_reset(call: CallbackQuery, state: FSMContext, db: Database) -> None:
    await state.update_data(domains_filter=None, domains_search=None)
    await _render_domains_list(call.message, db, state, 0)
    await call.answer("Фильтры сброшены")


def _domain_filter_status(all_rules: list, domain_str: str) -> tuple[str, str]:
    verdict = classify_domain(domain_str, all_rules)
    if verdict.is_watched:
        return "watch", "👁 Watch"
    if verdict.is_ignored:
        return "ignore", "🚫 Ignore"
    if verdict.is_allowed:
        return "allow", "✅ Allow"
    return "none", "⚪ Без статуса"


def _build_domain_card_text(db: Database, domain) -> str:
    _, status_label = _domain_filter_status(db.list_filter_rules(), domain.domain)
    node_dist = db.domain_node_distribution(domain.domain)
    source_dist = db.domain_source_distribution(domain.domain)

    lines = [
        f"🌐 <code>{html.escape(domain.domain)}</code>",
        f"Статус: {status_label}",
        f"Хитов: {domain.hits}",
        f"Впервые: {format_datetime(domain.first_seen)}",
        f"Последний раз: {format_datetime(domain.last_seen)} ({format_relative_time(domain.last_seen)})",
    ]
    if node_dist:
        lines.append("")
        lines.append("По нодам:")
        lines.extend(f"  • {name}: {count}" for name, count in node_dist)
    if source_dist:
        lines.append("")
        lines.append("По источникам:")
        lines.extend(f"  • {_DOMAIN_SOURCE_LABELS.get(src, src)}: {count}" for src, count in source_dist)
    return "\n".join(lines)


@router.callback_query(F.data.startswith("domain_card:"))
async def cb_domain_card(call: CallbackQuery, db: Database) -> None:
    _, domain_id, page = call.data.split(":")
    domain = db.get_domain(int(domain_id))
    if domain is None:
        await call.answer("Домен не найден (возможно, данные были очищены)", show_alert=True)
        return
    await call.message.edit_text(
        _build_domain_card_text(db, domain), parse_mode="HTML", reply_markup=kb.domain_card_kb(domain.id, int(page)),
    )
    await call.answer()


@router.callback_query(F.data.startswith("domain_filter:"))
async def cb_domain_filter_prompt(call: CallbackQuery) -> None:
    _, domain_id, list_type, page = call.data.split(":")
    await call.message.edit_text(
        "Тип паттерна:", reply_markup=kb.domain_filter_pattern_type(int(domain_id), list_type, int(page)),
    )
    await call.answer()


@router.callback_query(F.data.startswith("domain_filter_add:"))
async def cb_domain_filter_add(call: CallbackQuery, db: Database) -> None:
    _, domain_id, list_type, pattern_type, page = call.data.split(":")
    domain = db.get_domain(int(domain_id))
    if domain is None:
        await call.answer("Домен не найден", show_alert=True)
        return
    rule = db.add_filter_rule(list_type, pattern_type, domain.domain)
    await call.answer("Такое правило уже существует" if rule is None else f"Добавлено в {list_type}")
    await call.message.edit_text(
        _build_domain_card_text(db, domain), parse_mode="HTML", reply_markup=kb.domain_card_kb(domain.id, int(page)),
    )


@router.callback_query(F.data.startswith("domain_history:"))
async def cb_domain_history(call: CallbackQuery, db: Database) -> None:
    _, domain_id, page, card_page = call.data.split(":")
    domain = db.get_domain(int(domain_id))
    if domain is None:
        await call.answer("Домен не найден", show_alert=True)
        return
    page_i = int(page)
    total = db.count_domain_events(domain.domain)
    lines = [f"📊 <b>История:</b> <code>{html.escape(domain.domain)}</code>"]
    if total == 0:
        lines.append(
            "\nСобытий не найдено - либо их ещё не было, либо история уже удалена по retention "
            "(агрегированные хиты в карточке домена при этом не затрагиваются)."
        )
    else:
        lines.append(f"Всего записей: {total}\n")
        for e in db.list_domain_events(domain.domain, limit=20, offset=page_i * 20):
            node = db.get_node(e.node_id)
            node_name = node.name if node else f"#{e.node_id}"
            source_label = _DOMAIN_SOURCE_LABELS.get(e.source, e.source)
            lines.append(f"{format_datetime(e.occurred_at)} — {node_name} ({source_label}), хитов: {e.hits}")
    await call.message.edit_text(
        "\n".join(lines), parse_mode="HTML",
        reply_markup=kb.domain_history_kb(domain.id, page_i, int(card_page), total),
    )
    await call.answer()


@router.callback_query(F.data == "domains_list_export")
async def cb_domains_list_export(call: CallbackQuery, state: FSMContext, db: Database) -> None:
    search = await _get_domain_search(state)
    filters = await _get_domain_filters(state)
    total = db.count_domains(**_domains_query_kwargs(search, filters))
    if total == 0:
        await call.answer("Нечего экспортировать - список пуст", show_alert=True)
        return
    note = f" ({total} домен(ов))" if total <= DOMAINS_EXPORT_LIMIT else (
        f" ({total} домен(ов), будет выгружено первые {DOMAINS_EXPORT_LIMIT} по последней активности)"
    )
    await call.message.edit_text(
        f"📤 Экспорт текущего списка{note}.\nФормат:", reply_markup=kb.domains_list_export_format(),
    )
    await call.answer()


@router.callback_query(F.data.startswith("domains_list_export_do:"))
async def cb_domains_list_export_do(call: CallbackQuery, state: FSMContext, db: Database) -> None:
    fmt = call.data.split(":", 1)[1]
    search = await _get_domain_search(state)
    filters = await _get_domain_filters(state)
    kwargs = _domains_query_kwargs(search, filters)
    domains = db.list_domains(limit=DOMAINS_EXPORT_LIMIT, order_by="last_seen", **kwargs)
    if not domains:
        await call.answer("Нечего экспортировать", show_alert=True)
        return

    all_rules = db.list_filter_rules()
    rows = []
    for d in domains:
        node = db.get_node(d.node_id)
        status_key, _ = _domain_filter_status(all_rules, d.domain)
        rows.append({
            "domain": d.domain, "hits": d.hits,
            "first_seen": d.first_seen.isoformat(), "last_seen": d.last_seen.isoformat(),
            "node": node.name if node else "", "filter_status": status_key,
        })

    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=["domain", "hits", "first_seen", "last_seen", "node", "filter_status"])
        writer.writeheader()
        writer.writerows(rows)
        content = buf.getvalue()
    else:
        content = json.dumps(rows, ensure_ascii=False, indent=2)

    file = BufferedInputFile(content.encode("utf-8"), filename=f"domains.{fmt}")
    await call.message.answer_document(file, caption=f"Экспорт: {len(rows)} домен(ов)")
    await call.answer()


@router.callback_query(F.data == "domains_data")
async def cb_domains_data(call: CallbackQuery) -> None:
    await call.message.edit_text("🗑 <b>Управление данными</b>", parse_mode="HTML", reply_markup=kb.domains_data_menu())
    await call.answer()


@router.callback_query(F.data == "domains_purge_age")
async def cb_domains_purge_age(call: CallbackQuery) -> None:
    await call.message.edit_text("Удалить события старше:", reply_markup=kb.domains_purge_age_menu())
    await call.answer()


@router.callback_query(F.data.startswith("domains_purge_age_confirm:"))
async def cb_domains_purge_age_confirm(call: CallbackQuery) -> None:
    days = int(call.data.split(":", 1)[1])
    await call.message.edit_text(
        f"Удалить все события старше {days} дней? Сами домены и их суммарные хиты не изменятся, "
        f"удаляется только детальная история по времени.",
        reply_markup=kb.confirm_purge_age(days),
    )
    await call.answer()


@router.callback_query(F.data.startswith("domains_purge_age_do:"))
async def cb_domains_purge_age_do(call: CallbackQuery, db: Database) -> None:
    days = int(call.data.split(":", 1)[1])
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    count = db.purge_events_older_than(cutoff)
    await call.answer(f"Удалено событий: {count}", show_alert=True)
    await call.message.edit_text("🗑 <b>Управление данными</b>", parse_mode="HTML", reply_markup=kb.domains_data_menu())


@router.callback_query(F.data == "domains_purge_events")
async def cb_domains_purge_events(call: CallbackQuery) -> None:
    await call.message.edit_text(
        "Очистить ВСЮ историю событий (детальные записи по времени)? Сами домены и их суммарные хиты останутся - "
        "удаляется только возможность посмотреть историю визитов.",
        reply_markup=kb.confirm_purge_events(),
    )
    await call.answer()


@router.callback_query(F.data == "domains_purge_events_do")
async def cb_domains_purge_events_do(call: CallbackQuery, db: Database) -> None:
    count = db.delete_events_only()
    await call.answer(f"Удалено событий: {count}", show_alert=True)
    await call.message.edit_text("🗑 <b>Управление данными</b>", parse_mode="HTML", reply_markup=kb.domains_data_menu())


@router.callback_query(F.data.startswith("data_reset:"))
async def cb_data_reset_prompt(call: CallbackQuery) -> None:
    back_target = call.data.split(":", 1)[1]
    await call.message.edit_text(
        "🗑 <b>Сброс данных</b>\n\n"
        "Это удалит ВСЕ собранные домены и историю событий на сервере.\n"
        "Ноды, токены, фильтры и настройки не затрагиваются.\n\n"
        "Действие необратимо.",
        parse_mode="HTML", reply_markup=kb.confirm_reset_data(back_target),
    )
    await call.answer()


@router.callback_query(F.data.startswith("data_reset_confirm:"))
async def cb_data_reset_confirm(call: CallbackQuery, db: Database, config: Config) -> None:
    back_target = call.data.split(":", 1)[1]
    domains_count, events_count = db.reset_domains_and_events()
    await call.answer(f"Удалено: {domains_count} доменов, {events_count} событий", show_alert=True)
    if back_target == "stats":
        await call.message.edit_text(
            _stats_text(db, config, "today"), parse_mode="HTML", reply_markup=kb.stats_menu("today"),
        )
    else:
        await cb_domains(call)


# ------------------------------------------------------------------
# Stats
# ------------------------------------------------------------------

def _database_size_bytes(config: Config) -> int:
    base = config.database_path
    return sum(
        os.path.getsize(candidate)
        for candidate in (base, base + "-wal", base + "-shm")
        if os.path.isfile(candidate)
    )


def _format_delta(current: int, previous: int) -> str:
    """"было 40, стало 52" -> "🔺 +30%". previous == 0 can't express a
    percentage (division by zero), so it's called out as "новое" (some
    activity where there was none) rather than showing a fake 0%/∞."""
    if previous == 0:
        return "🔺 новое" if current > 0 else "▪️ 0"
    pct = (current - previous) / previous * 100
    arrow = "🔺" if pct > 0 else ("🔻" if pct < 0 else "▪️")
    return f"{arrow} {pct:+.0f}%"


_SOURCE_LABELS = {"tls_sni": "TLS SNI", "dns": "DNS"}


def _stats_text(
    db: Database, config: Config, period: str, custom_range: tuple[datetime, datetime] | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    tz_offset = runtime_settings.get_timezone_offset_minutes(db, config)
    if period == "custom" and custom_range is not None:
        since, until = custom_range
        period_label = f"{since.date()} — {(until - timedelta(seconds=1)).date()}"
    else:
        since, until = _period_range(period, now, tz_offset)
        period_label = dict(kb.DOMAIN_PERIODS).get(period, period)

    offline_after = runtime_settings.get_node_offline_after_seconds(db, config)
    nodes = db.list_nodes()
    online = sum(1 for n in nodes if n.is_online(offline_after, now))

    events_count = db.count_events_since(since or _EPOCH, until)
    new_domains = db.count_domains(since=since, until=until)
    total_domains = db.count_domains()
    top_nodes = db.top_active_nodes_since(since or _EPOCH, limit=3)
    top_domains = (
        db.top_domains_since(since, until, limit=5) if since is not None
        else [(d.domain, d.hits) for d in db.top_domains(limit=5)]
    )
    source_dist = db.source_distribution_since(since or _EPOCH, until)
    offline_incidents = db.count_offline_incidents(since, until)

    watch_hits = sum(r.hits_count for r in db.list_filter_rules("watch"))
    ignore_hits = sum(r.hits_count for r in db.list_filter_rules("ignore"))
    allow_hits = sum(r.hits_count for r in db.list_filter_rules("allow"))

    rate_line = ""
    if since is not None:
        hours = max(((until or now) - since).total_seconds() / 3600, 1 / 60)
        rate_line = f"Скорость: ~{events_count / hours:.1f} событий/час\n"

    comparison_block = ""
    if since is not None:
        duration = (until or now) - since
        prev_since, prev_until = since - duration, since
        prev_events = db.count_events_since(prev_since, prev_until)
        prev_new_domains = db.count_domains(since=prev_since, until=prev_until)
        comparison_block = (
            f"\nПо сравнению с предыдущим периодом такой же длины:\n"
            f"  События: {_format_delta(events_count, prev_events)}\n"
            f"  Новые домены: {_format_delta(new_domains, prev_new_domains)}\n"
        )

    top_lines = "\n".join(f"  {i + 1}. {name} — {count}" for i, (name, count) in enumerate(top_nodes)) or "  —"
    top_domains_lines = "\n".join(f"  {i + 1}. {d} — {c}" for i, (d, c) in enumerate(top_domains)) or "  —"
    source_lines = "\n".join(
        f"  {_SOURCE_LABELS.get(src, src)}: {count}" for src, count in source_dist
    ) or "  —"

    buffered_total = sum(n.agent_buffer_size or 0 for n in nodes)
    buffered_line = (
        f"В буферах агентов (собрано, не отправлено): {buffered_total}\n" if buffered_total else ""
    )
    db_size_line = f"Размер базы доменов: {format_bytes(_database_size_bytes(config))}\n"

    return (
        f"📊 <b>Статистика</b> ({period_label})\n\n"
        f"Ноды онлайн: {online}/{len(nodes)}\n"
        f"Уникальных доменов (всего): {total_domains}\n"
        f"Новых доменов за период: {new_domains}\n"
        f"Событий за период: {events_count}\n"
        f"{rate_line}"
        f"Офлайн-инцидентов за период: {offline_incidents}\n"
        f"{buffered_line}"
        f"{db_size_line}"
        f"{comparison_block}"
        f"\nПо источникам за период:\n{source_lines}\n"
        f"\nWatch/Ignore/Allow хитов (всего, не по периоду): {watch_hits}/{ignore_hits}/{allow_hits}\n"
        f"\nАктивные ноды за период:\n{top_lines}\n"
        f"\nТоп доменов за период:\n{top_domains_lines}"
    )


async def _get_stats_custom_range(state: FSMContext) -> tuple[datetime, datetime] | None:
    data = await state.get_data()
    since_iso, until_iso = data.get("stats_custom_since"), data.get("stats_custom_until")
    if since_iso is None or until_iso is None:
        return None
    return datetime.fromisoformat(since_iso), datetime.fromisoformat(until_iso)


@router.callback_query(F.data == "stats")
async def cb_stats(call: CallbackQuery, db: Database, config: Config, live_view: LiveViewManager) -> None:
    live_active = live_view.is_active(call.message.chat.id, call.message.message_id)
    await call.message.edit_text(
        _stats_text(db, config, "today"), parse_mode="HTML", reply_markup=kb.stats_menu("today", live_active),
    )
    await call.answer()


@router.callback_query(F.data.startswith("stats_period:"))
async def cb_stats_period(
    call: CallbackQuery, state: FSMContext, db: Database, config: Config, live_view: LiveViewManager,
) -> None:
    period = call.data.split(":", 1)[1]
    custom_range = await _get_stats_custom_range(state) if period == "custom" else None
    live_active = live_view.is_active(call.message.chat.id, call.message.message_id)
    await call.message.edit_text(
        _stats_text(db, config, period, custom_range), parse_mode="HTML",
        reply_markup=kb.stats_menu(period, live_active),
    )
    await call.answer()


@router.callback_query(F.data == "stats_custom")
async def cb_stats_custom_prompt(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Inputs.waiting_for_stats_custom_range)
    await call.message.edit_text(
        "Введите период в формате <code>ГГГГ-ММ-ДД</code> (один день) или "
        "<code>ГГГГ-ММ-ДД ГГГГ-ММ-ДД</code> (с — по, включительно):",
        parse_mode="HTML", reply_markup=kb.cancel_input("stats_period:today"),
    )
    await call.answer()


@router.message(Inputs.waiting_for_stats_custom_range)
async def on_stats_custom_range_input(message: Message, state: FSMContext, db: Database, config: Config) -> None:
    await state.set_state(None)
    parsed = _parse_date_range((message.text or "").strip())
    if parsed is None:
        await message.answer(
            "Не понял формат. Пример: <code>2026-09-01</code> или <code>2026-09-01 2026-09-15</code>.",
            parse_mode="HTML", reply_markup=kb.back_button("stats_period:today"),
        )
        return
    since, until = parsed
    await state.update_data(stats_custom_since=since.isoformat(), stats_custom_until=until.isoformat())
    await message.answer(
        _stats_text(db, config, "custom", (since, until)), parse_mode="HTML",
        reply_markup=kb.stats_menu("custom"),
    )


@router.callback_query(F.data == "stats_node_menu")
async def cb_stats_node_menu(call: CallbackQuery, db: Database) -> None:
    nodes = db.list_nodes()
    if not nodes:
        await call.answer("Пока нет ни одной ноды", show_alert=True)
        return
    await call.message.edit_text("📡 <b>Статистика по ноде</b>\n\nВыберите ноду:", parse_mode="HTML", reply_markup=kb.stats_node_select_menu(nodes))
    await call.answer()


def _node_stats_text(db: Database, config: Config, node, period: str) -> str:
    now = datetime.now(timezone.utc)
    tz_offset = runtime_settings.get_timezone_offset_minutes(db, config)
    since, until = _period_range(period, now, tz_offset)
    period_label = dict(kb.DOMAIN_PERIODS).get(period, period)

    events_count = db.count_events_since(since or _EPOCH, until, node_id=node.id)
    unique_domains = db.count_domains(node_id=node.id)
    new_domains = db.count_domains(node_id=node.id, since=since, until=until)
    top_domains = db.list_domains(node_id=node.id, order_by="hits", limit=5)
    source_dist = db.source_distribution_since(since or _EPOCH, until, node_id=node.id)
    offline_incidents = db.count_offline_incidents(since, until, node_id=node.id)

    rate_line = ""
    if since is not None:
        hours = max(((until or now) - since).total_seconds() / 3600, 1 / 60)
        rate_line = f"Скорость: ~{events_count / hours:.1f} событий/час\n"

    if node.buffer_bytes is not None and node.buffer_limit_bytes:
        buffer_line = (
            f"Буфер: {node.agent_buffer_size or 0} событий, "
            f"{format_bytes(node.buffer_bytes)} / {format_bytes(node.buffer_limit_bytes)}\n"
        )
    elif node.agent_buffer_size:
        buffer_line = f"Буфер: {node.agent_buffer_size} событий\n"
    else:
        buffer_line = ""

    top_lines = "\n".join(f"  {i + 1}. {d.domain} — {d.hits}" for i, d in enumerate(top_domains)) or "  —"
    source_lines = "\n".join(f"  {_SOURCE_LABELS.get(s, s)}: {c}" for s, c in source_dist) or "  —"

    return (
        f"📊 <b>Статистика:</b> {node.name} ({period_label})\n\n"
        f"Последний heartbeat: {format_relative_time(node.last_heartbeat_at)}\n"
        f"Событий за период: {events_count}\n"
        f"{rate_line}"
        f"Уникальных доменов (всего с этой ноды): {unique_domains}\n"
        f"Новых доменов за период: {new_domains}\n"
        f"Офлайн-инцидентов за период: {offline_incidents}\n"
        f"{buffer_line}"
        f"\nПо источникам за период:\n{source_lines}\n"
        f"\nТоп доменов (всего с этой ноды):\n{top_lines}"
    )


@router.callback_query(F.data.startswith("stats_node:"))
async def cb_stats_node(call: CallbackQuery, db: Database, config: Config) -> None:
    _, node_id, period = call.data.split(":")
    node = db.get_node(int(node_id))
    if node is None:
        await call.answer("Нода не найдена (возможно, удалена)", show_alert=True)
        return
    await call.message.edit_text(
        _node_stats_text(db, config, node, period), parse_mode="HTML",
        reply_markup=kb.stats_node_menu(node.id, period),
    )
    await call.answer()


# ------------------------------------------------------------------
# Notifications
# ------------------------------------------------------------------

@router.callback_query(F.data == "notify_menu")
async def cb_notify_menu(call: CallbackQuery, notifier: Notifier) -> None:
    await call.message.edit_text(
        "🔔 <b>Уведомления</b>\n\nРежим группировки новых доменов:",
        parse_mode="HTML",
        reply_markup=kb.notify_menu(notifier.batch_mode(), notifier.is_globally_enabled()),
    )
    await call.answer()


@router.callback_query(F.data == "notify_toggle_global")
async def cb_notify_toggle(call: CallbackQuery, notifier: Notifier) -> None:
    notifier.set_globally_enabled(not notifier.is_globally_enabled())
    await cb_notify_menu(call, notifier)


@router.callback_query(F.data.startswith("notify_mode:"))
async def cb_notify_mode(call: CallbackQuery, notifier: Notifier) -> None:
    mode = call.data.split(":", 1)[1]
    notifier.set_batch_mode(mode)
    await cb_notify_menu(call, notifier)
    await call.answer("Режим обновлён")


@router.callback_query(F.data == "notify_custom")
async def cb_notify_custom(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Inputs.waiting_for_custom_batch_seconds)
    await call.message.edit_text(
        "Введите свой интервал группировки в секундах (1–3600):",
        reply_markup=kb.cancel_input("notify_menu"),
    )
    await call.answer()


@router.message(Inputs.waiting_for_custom_batch_seconds)
async def on_custom_batch_seconds_input(message: Message, state: FSMContext, notifier: Notifier) -> None:
    await state.clear()
    raw = (message.text or "").strip()
    if not raw.isdigit() or not (1 <= int(raw) <= 3600):
        await message.answer(
            "Нужно целое число секунд от 1 до 3600. Попробуйте снова из меню уведомлений.",
            reply_markup=kb.back_button("notify_menu"),
        )
        return
    notifier.set_batch_mode(raw)
    await message.answer(f"✅ Интервал группировки: {raw} сек.", reply_markup=kb.back_button("notify_menu"))


@router.callback_query(F.data == "notify_types")
async def cb_notify_types(call: CallbackQuery, notifier: Notifier) -> None:
    type_states = [(t, meta["label"], notifier.is_type_enabled(t)) for t, meta in NOTIFY_TYPES.items()]
    await call.message.edit_text(
        "📋 <b>Типы событий</b>\n\nКакие уведомления присылать:", parse_mode="HTML",
        reply_markup=kb.notify_types_menu(type_states),
    )
    await call.answer()


@router.callback_query(F.data.startswith("notify_type_toggle:"))
async def cb_notify_type_toggle(call: CallbackQuery, notifier: Notifier) -> None:
    event_type = call.data.split(":", 1)[1]
    notifier.set_type_enabled(event_type, not notifier.is_type_enabled(event_type))
    await cb_notify_types(call, notifier)


@router.callback_query(F.data == "notify_recipients")
async def cb_notify_recipients(call: CallbackQuery, notifier: Notifier) -> None:
    await call.message.edit_text(
        "📍 <b>Получатели по умолчанию</b>\n\n"
        "Куда слать уведомления для нод, у которых не задан свой способ доставки "
        "(«⬜ Как по умолчанию» в карточке ноды):",
        parse_mode="HTML", reply_markup=kb.notify_recipients_menu(notifier.global_destination()),
    )
    await call.answer()


@router.callback_query(F.data.startswith("notify_global_dest_set:"))
async def cb_notify_global_dest_set(call: CallbackQuery, notifier: Notifier) -> None:
    notifier.set_global_destination(call.data.split(":", 1)[1])
    await call.answer("Получатель по умолчанию обновлён")
    await cb_notify_recipients(call, notifier)


@router.callback_query(F.data == "notify_quiet_hours")
async def cb_notify_quiet_hours(call: CallbackQuery, notifier: Notifier) -> None:
    enabled, start, end = notifier.quiet_hours()
    await call.message.edit_text(
        "🌙 <b>Тихие часы</b>\n\nВ это время большинство уведомлений не присылаются "
        "(кроме отмеченных как критичные - см. «📋 Типы событий»).",
        parse_mode="HTML", reply_markup=kb.notify_quiet_hours_menu(enabled, start, end),
    )
    await call.answer()


@router.callback_query(F.data == "notify_quiet_hours_toggle")
async def cb_notify_quiet_hours_toggle(call: CallbackQuery, notifier: Notifier) -> None:
    enabled, start, end = notifier.quiet_hours()
    notifier.set_quiet_hours(not enabled, start, end)
    await cb_notify_quiet_hours(call, notifier)


@router.callback_query(F.data == "notify_quiet_hours_edit")
async def cb_notify_quiet_hours_edit_prompt(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Inputs.waiting_for_quiet_hours_range)
    await call.message.edit_text(
        "Введите время в формате <code>ЧЧ:ММ ЧЧ:ММ</code> (начало — конец), например "
        "<code>23:00 08:00</code>:",
        parse_mode="HTML", reply_markup=kb.cancel_input("notify_quiet_hours"),
    )
    await call.answer()


@router.message(Inputs.waiting_for_quiet_hours_range)
async def on_quiet_hours_range_input(message: Message, state: FSMContext, notifier: Notifier) -> None:
    await state.set_state(None)
    parts = (message.text or "").strip().split()
    if len(parts) != 2 or not all(re.match(r"^\d{1,2}:\d{2}$", p) for p in parts):
        await message.answer(
            "Не понял формат. Пример: <code>23:00 08:00</code>.",
            parse_mode="HTML", reply_markup=kb.back_button("notify_quiet_hours"),
        )
        return
    try:
        for p in parts:
            h, m = p.split(":")
            if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
                raise ValueError
    except ValueError:
        await message.answer(
            "Часы должны быть 0-23, минуты 0-59. Попробуйте снова.", reply_markup=kb.back_button("notify_quiet_hours"),
        )
        return
    enabled, _, _ = notifier.quiet_hours()
    notifier.set_quiet_hours(enabled, parts[0], parts[1])
    await message.answer(
        f"✅ Тихие часы: {parts[0]}–{parts[1]}", reply_markup=kb.back_button("notify_quiet_hours"),
    )


@router.callback_query(F.data == "notify_test")
async def cb_notify_test(call: CallbackQuery, notifier: Notifier) -> None:
    await call.answer("Отправляю...")
    results = await notifier.send_test()
    lines = "\n".join(f"{dest}: {status}" for dest, status in results.items())
    await call.message.edit_text(
        f"🧪 <b>Результат теста</b>\n\n{lines}", parse_mode="HTML", reply_markup=kb.back_button("notify_menu"),
    )


# ------------------------------------------------------------------
# Filters (ignore / allow / watch)
# ------------------------------------------------------------------

@router.callback_query(F.data == "filters")
async def cb_filters(call: CallbackQuery, db: Database) -> None:
    counts = {lt: len(db.list_filter_rules(lt)) for lt in ("watch", "ignore", "allow")}
    await call.message.edit_text(
        "🔍 <b>Фильтры</b>\n\n"
        "Watch — всегда мгновенное уведомление.\n"
        "Ignore/Allow — не уведомлять (домен помечается suppressed).",
        parse_mode="HTML", reply_markup=kb.filters_menu(counts),
    )
    await call.answer()


@router.callback_query(F.data.startswith("filters_list:"))
async def cb_filters_list(call: CallbackQuery, db: Database) -> None:
    _, list_type, page_raw = call.data.split(":")
    page = int(page_raw)
    rules = db.list_filter_rules(list_type)
    title = {"watch": "🚨 Watch List", "ignore": "🚫 Ignore List", "allow": "✅ Allow List"}[list_type]
    text = title if rules else f"{title}\n\nПусто"
    await call.message.edit_text(text, reply_markup=kb.filters_list(list_type, rules, page))
    await call.answer()


def _find_rule(db: Database, rule_id: int):
    return next((r for r in db.list_filter_rules() if r.id == rule_id), None)


def _build_filter_rule_text(rule) -> str:
    tag = kb.PATTERN_TAG.get(rule.pattern_type, rule.pattern_type)
    list_label = {"watch": "🚨 Watch", "ignore": "🚫 Ignore", "allow": "✅ Allow"}.get(rule.list_type, rule.list_type)
    lines = [
        f"{list_label} · <code>{html.escape(rule.pattern)}</code> ({tag})",
        "",
        f"Статус: {'🟢 включено' if rule.enabled else '🔴 выключено'}",
        f"Сработало раз: {rule.hits_count}",
        f"Последнее срабатывание: {format_relative_time(rule.last_hit_at)}",
        f"Комментарий: {html.escape(rule.comment) if rule.comment else '—'}",
    ]
    return "\n".join(lines)


@router.callback_query(F.data.startswith("filter_rule:"))
async def cb_filter_rule(call: CallbackQuery, db: Database) -> None:
    _, rule_id_raw, page_raw = call.data.split(":")
    rule = _find_rule(db, int(rule_id_raw))
    if rule is None:
        await call.answer("Правило не найдено", show_alert=True)
        return
    await call.message.edit_text(
        _build_filter_rule_text(rule), parse_mode="HTML", reply_markup=kb.filter_rule_card(rule, int(page_raw)),
    )
    await call.answer()


@router.callback_query(F.data.startswith("filter_toggle:"))
async def cb_filter_toggle(call: CallbackQuery, db: Database) -> None:
    _, rule_id_raw, page_raw = call.data.split(":")
    rule = _find_rule(db, int(rule_id_raw))
    if rule is None:
        await call.answer("Правило не найдено", show_alert=True)
        return
    db.set_filter_rule_enabled(rule.id, not rule.enabled)
    rule = _find_rule(db, rule.id)
    await call.message.edit_text(
        _build_filter_rule_text(rule), parse_mode="HTML", reply_markup=kb.filter_rule_card(rule, int(page_raw)),
    )
    await call.answer("Включено" if rule.enabled else "Выключено")


@router.callback_query(F.data.startswith("filter_comment:"))
async def cb_filter_comment_prompt(call: CallbackQuery, state: FSMContext, db: Database) -> None:
    _, rule_id_raw, page_raw = call.data.split(":")
    rule = _find_rule(db, int(rule_id_raw))
    if rule is None:
        await call.answer("Правило не найдено", show_alert=True)
        return
    await state.set_state(Inputs.waiting_for_filter_comment)
    await state.update_data(filter_rule_id=rule.id, filter_rule_page=int(page_raw))
    await call.message.edit_text(
        "Введите комментарий к правилу (например, зачем оно нужно). "
        "Отправьте «-», чтобы очистить существующий комментарий.",
        reply_markup=kb.cancel_input(f"filter_rule:{rule.id}:{page_raw}"),
    )
    await call.answer()


@router.message(Inputs.waiting_for_filter_comment)
async def on_filter_comment_input(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    rule_id, page = data.get("filter_rule_id"), data.get("filter_rule_page", 0)
    await state.set_state(None)
    if rule_id is None:
        await message.answer("Сессия истекла. Откройте меню фильтров и попробуйте снова.", reply_markup=kb.back_button("filters"))
        return
    text = (message.text or "").strip()
    comment = None if text == "-" else text
    db.set_filter_rule_comment(rule_id, comment)
    rule = _find_rule(db, rule_id)
    if rule is None:
        await message.answer("Правило больше не существует.", reply_markup=kb.back_button("filters"))
        return
    await message.answer(
        _build_filter_rule_text(rule), parse_mode="HTML", reply_markup=kb.filter_rule_card(rule, page),
    )


@router.callback_query(F.data.startswith("filter_remove_confirm:"))
async def cb_filter_remove_confirm(call: CallbackQuery, db: Database) -> None:
    _, rule_id_raw, page_raw = call.data.split(":")
    rule_id, page = int(rule_id_raw), int(page_raw)
    rule = next((r for r in db.list_filter_rules() if r.id == rule_id), None)
    if rule is None:
        await call.answer("Правило не найдено", show_alert=True)
        return
    tag = kb.PATTERN_TAG.get(rule.pattern_type, rule.pattern_type)
    await call.message.edit_text(
        f"Удалить правило <code>{rule.pattern}</code> ({tag}) из {rule.list_type}?",
        parse_mode="HTML", reply_markup=kb.confirm_remove_filter(rule, page),
    )
    await call.answer()


@router.callback_query(F.data.startswith("filter_remove:"))
async def cb_filter_remove(call: CallbackQuery, db: Database) -> None:
    _, rule_id_raw, list_type, page_raw = call.data.split(":")
    db.remove_filter_rule(int(rule_id_raw))
    await call.answer("Удалено")
    rules = db.list_filter_rules(list_type)
    title = {"watch": "🚨 Watch List", "ignore": "🚫 Ignore List", "allow": "✅ Allow List"}[list_type]
    text = title if rules else f"{title}\n\nПусто"
    await call.message.edit_text(text, reply_markup=kb.filters_list(list_type, rules, int(page_raw)))


@router.callback_query(F.data == "filter_add")
async def cb_filter_add(call: CallbackQuery) -> None:
    await call.message.edit_text("Выберите список:", reply_markup=kb.filter_add_list_type())
    await call.answer()


@router.callback_query(F.data.startswith("filter_add_type:"))
async def cb_filter_add_type(call: CallbackQuery) -> None:
    list_type = call.data.split(":", 1)[1]
    await call.message.edit_text(
        f"Список: {list_type}\nВыберите тип паттерна:", reply_markup=kb.filter_add_pattern_type(list_type),
    )
    await call.answer()


@router.callback_query(F.data.startswith("filter_add_ptype:"))
async def cb_filter_add_ptype(call: CallbackQuery, state: FSMContext) -> None:
    _, list_type, pattern_type = call.data.split(":")
    await state.set_state(Inputs.waiting_for_filter_pattern)
    await state.update_data(list_type=list_type, pattern_type=pattern_type)
    hint = {
        "suffix": "example.com (покроет и все поддомены)",
        "exact": "api.example.com (только этот домен)",
        "wildcard": "*.example.com (шаблон)",
    }[pattern_type]
    await call.message.edit_text(
        f"Введите паттерн, например: {hint}\n\n"
        f"Можно сразу вставить список — по одному паттерну на строку, добавятся все за раз.",
        reply_markup=kb.cancel_input("filters"),
    )
    await call.answer()


@router.message(Inputs.waiting_for_filter_pattern)
async def on_filter_pattern_input(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    await state.clear()
    raw_lines = (message.text or "").splitlines()
    patterns = []
    seen = set()
    for line in raw_lines:
        pattern = line.strip().lower()
        if not pattern or pattern in seen:
            continue
        seen.add(pattern)
        patterns.append(pattern)

    if not patterns:
        await message.answer("Пустой паттерн, попробуйте снова из меню фильтров.", reply_markup=kb.back_button("filters"))
        return

    if len(patterns) == 1:
        rule = db.add_filter_rule(data["list_type"], data["pattern_type"], patterns[0])
        if rule is None:
            await message.answer("Такое правило уже существует.", reply_markup=kb.back_button("filters"))
        else:
            await message.answer(
                f"✅ Добавлено в {data['list_type']}: <code>{patterns[0]}</code>",
                parse_mode="HTML", reply_markup=kb.back_button("filters"),
            )
        return

    added = 0
    duplicates = 0
    for pattern in patterns:
        rule = db.add_filter_rule(data["list_type"], data["pattern_type"], pattern)
        if rule is None:
            duplicates += 1
        else:
            added += 1
    await message.answer(
        f"✅ Массовое добавление в {data['list_type']} завершено.\n"
        f"Добавлено: {added}\n"
        f"Уже было (пропущено): {duplicates}",
        reply_markup=kb.back_button("filters"),
    )


def _extract_patterns_from_text(text: str) -> list[str]:
    seen: set[str] = set()
    patterns: list[str] = []
    for line in text.splitlines():
        pattern = line.strip().lower()
        if not pattern or pattern in seen:
            continue
        seen.add(pattern)
        patterns.append(pattern)
    return patterns


def _extract_patterns_from_csv(text: str) -> list[str]:
    """Takes the first column of every row - a filter-import CSV is just
    a list of patterns, optionally with extra columns nobody asked us to
    interpret. A header row's first cell (e.g. "pattern") is harmless
    here: it'll fail _looks_like_pattern() and land in the invalid
    count, not silently get imported as a real rule."""
    reader = csv.reader(io.StringIO(text))
    seen: set[str] = set()
    patterns: list[str] = []
    for row in reader:
        if not row:
            continue
        pattern = row[0].strip().lower()
        if not pattern or pattern in seen:
            continue
        seen.add(pattern)
        patterns.append(pattern)
    return patterns


def _looks_like_pattern(s: str) -> bool:
    """Deliberately looser than normalize_domain() - wildcard patterns
    like "*.example.com" are valid filter_rule patterns but would fail
    strict hostname validation. Just enough of a sanity check to catch
    obviously-broken lines (empty, whitespace inside, absurdly long)."""
    return bool(s) and " " not in s and "\t" not in s and len(s) <= 253


async def _render_filter_import_preview(
    message: Message, state: FSMContext, db: Database, list_type: str, pattern_type: str, patterns: list[str],
) -> None:
    valid = [p for p in patterns if _looks_like_pattern(p)]
    invalid_count = len(patterns) - len(valid)
    existing_same_list = {r.pattern for r in db.list_filter_rules(list_type) if r.pattern_type == pattern_type}
    new_patterns = [p for p in valid if p not in existing_same_list]
    duplicate_count = len(valid) - len(new_patterns)

    other_lists = [lt for lt in ("watch", "ignore", "allow") if lt != list_type]
    conflicts = []
    for lt in other_lists:
        other_patterns = {r.pattern for r in db.list_filter_rules(lt)}
        conflicts.extend(p for p in new_patterns if p in other_patterns)

    await state.update_data(
        filter_import_list_type=list_type, filter_import_pattern_type=pattern_type, filter_import_patterns=new_patterns,
    )

    lines = [
        "📥 <b>Предпросмотр импорта</b>",
        f"Список: {list_type}, тип паттерна: {kb.PATTERN_TAG.get(pattern_type, pattern_type)}",
        "",
        f"Найдено строк: {len(patterns)}",
        f"Новых: {len(new_patterns)}",
        f"Уже есть в этом списке (пропустятся): {duplicate_count}",
        f"Некорректных (пропустятся): {invalid_count}",
    ]
    if conflicts:
        shown = ", ".join(f"<code>{html.escape(p)}</code>" for p in conflicts[:10])
        more = f" и ещё {len(conflicts) - 10}" if len(conflicts) > 10 else ""
        lines.append(
            f"\n⚠️ Уже есть в другом списке (Watch/Ignore/Allow): {shown}{more}\n"
            f"Будут добавлены и сюда - приоритет Watch над Ignore/Allow не меняется."
        )
    if not new_patterns:
        lines.append("\nНечего импортировать - все строки уже есть или некорректны.")
        await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=kb.back_button("filters"))
        return

    lines.append(f"\nИмпортировать {len(new_patterns)} новых правил?")
    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=kb.confirm_filter_import())


@router.callback_query(F.data == "filter_import")
async def cb_filter_import(call: CallbackQuery) -> None:
    await call.message.edit_text("Выберите список для импорта:", reply_markup=kb.filter_import_list_type())
    await call.answer()


@router.callback_query(F.data.startswith("filter_import_type:"))
async def cb_filter_import_type(call: CallbackQuery) -> None:
    list_type = call.data.split(":", 1)[1]
    await call.message.edit_text(
        f"Список: {list_type}\nВыберите тип паттерна:", reply_markup=kb.filter_import_pattern_type(list_type),
    )
    await call.answer()


@router.callback_query(F.data.startswith("filter_import_ptype:"))
async def cb_filter_import_ptype(call: CallbackQuery, state: FSMContext) -> None:
    _, list_type, pattern_type = call.data.split(":")
    await state.set_state(Inputs.waiting_for_filter_import)
    await state.update_data(filter_import_list_type=list_type, filter_import_pattern_type=pattern_type)
    await call.message.edit_text(
        "Вставьте список паттернов (по одному на строку) текстом, "
        "или пришлите файлом <b>.txt</b> (по строке) или <b>.csv</b> (первая колонка).",
        parse_mode="HTML", reply_markup=kb.cancel_input("filters"),
    )
    await call.answer()


@router.message(Inputs.waiting_for_filter_import, F.document)
async def on_filter_import_document(message: Message, state: FSMContext, db: Database, bot: Bot) -> None:
    data = await state.get_data()
    list_type, pattern_type = data.get("filter_import_list_type"), data.get("filter_import_pattern_type")
    await state.set_state(None)
    if not list_type or not pattern_type:
        await message.answer("Сессия истекла. Откройте меню фильтров и попробуйте снова.", reply_markup=kb.back_button("filters"))
        return
    filename = message.document.file_name or ""
    try:
        buf = await bot.download(message.document)
        text = buf.read().decode("utf-8", errors="replace")
    except Exception:
        logger.exception("Failed to download filter import document")
        await message.answer("Не удалось прочитать файл. Попробуйте ещё раз.", reply_markup=kb.back_button("filters"))
        return
    patterns = _extract_patterns_from_csv(text) if filename.lower().endswith(".csv") else _extract_patterns_from_text(text)
    if not patterns:
        await message.answer("Файл пустой или не удалось разобрать ни одной строки.", reply_markup=kb.back_button("filters"))
        return
    await _render_filter_import_preview(message, state, db, list_type, pattern_type, patterns)


@router.message(Inputs.waiting_for_filter_import)
async def on_filter_import_text(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    list_type, pattern_type = data.get("filter_import_list_type"), data.get("filter_import_pattern_type")
    await state.set_state(None)
    if not list_type or not pattern_type:
        await message.answer("Сессия истекла. Откройте меню фильтров и попробуйте снова.", reply_markup=kb.back_button("filters"))
        return
    patterns = _extract_patterns_from_text(message.text or "")
    if not patterns:
        await message.answer("Пустой ввод, попробуйте снова из меню фильтров.", reply_markup=kb.back_button("filters"))
        return
    await _render_filter_import_preview(message, state, db, list_type, pattern_type, patterns)


@router.callback_query(F.data == "filter_import_confirm")
async def cb_filter_import_confirm(call: CallbackQuery, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    list_type = data.get("filter_import_list_type")
    pattern_type = data.get("filter_import_pattern_type")
    patterns = data.get("filter_import_patterns") or []
    await state.update_data(filter_import_patterns=None)
    if not list_type or not pattern_type or not patterns:
        await call.answer("Нечего импортировать (сессия истекла?)", show_alert=True)
        return
    added = duplicates = 0
    for pattern in patterns:
        rule = db.add_filter_rule(list_type, pattern_type, pattern)
        if rule is None:
            duplicates += 1
        else:
            added += 1
    await call.message.edit_text(
        f"✅ Импорт в {list_type} завершён.\nДобавлено: {added}\nПропущено (гонка/дубликат): {duplicates}",
        reply_markup=kb.back_button("filters"),
    )
    await call.answer()


@router.callback_query(F.data == "filter_export")
async def cb_filter_export(call: CallbackQuery) -> None:
    await call.message.edit_text("Что экспортировать?", reply_markup=kb.filter_export_scope())
    await call.answer()


@router.callback_query(F.data.startswith("filter_export_scope:"))
async def cb_filter_export_scope(call: CallbackQuery) -> None:
    scope = call.data.split(":", 1)[1]
    await call.message.edit_text("В каком формате?", reply_markup=kb.filter_export_format(scope))
    await call.answer()


def _rules_for_export(db: Database, scope: str) -> list:
    if scope == "all":
        return [r for lt in ("watch", "ignore", "allow") for r in db.list_filter_rules(lt)]
    return db.list_filter_rules(scope)


def _render_filter_export_txt(rules: list) -> str:
    return "\n".join(r.pattern for r in rules) + "\n"


def _render_filter_export_csv(rules: list) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["list_type", "pattern_type", "pattern", "enabled", "comment", "hits_count", "last_hit_at"])
    for r in rules:
        writer.writerow([
            r.list_type, r.pattern_type, r.pattern, int(r.enabled), r.comment or "",
            r.hits_count, r.last_hit_at.isoformat() if r.last_hit_at else "",
        ])
    return buf.getvalue()


def _render_filter_export_json(rules: list) -> str:
    return json.dumps(
        [
            {
                "list_type": r.list_type, "pattern_type": r.pattern_type, "pattern": r.pattern,
                "enabled": r.enabled, "comment": r.comment, "hits_count": r.hits_count,
                "last_hit_at": r.last_hit_at.isoformat() if r.last_hit_at else None,
            }
            for r in rules
        ],
        ensure_ascii=False, indent=2,
    )


@router.callback_query(F.data.startswith("filter_export_fmt:"))
async def cb_filter_export_fmt(call: CallbackQuery, db: Database) -> None:
    _, scope, fmt = call.data.split(":")
    rules = _rules_for_export(db, scope)
    if not rules:
        await call.answer("Список пуст - нечего экспортировать", show_alert=True)
        return
    renderers = {"txt": _render_filter_export_txt, "csv": _render_filter_export_csv, "json": _render_filter_export_json}
    content = renderers[fmt](rules)
    file = BufferedInputFile(content.encode("utf-8"), filename=f"filters_{scope}.{fmt}")
    await call.message.answer_document(file, caption=f"Экспорт ({scope}): {len(rules)} правил(о)")
    await call.answer()


@router.callback_query(F.data == "filter_check")
async def cb_filter_check(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Inputs.waiting_for_filter_check)
    await call.message.edit_text(
        "Введите домен, чтобы проверить, как его обработают текущие правила:",
        reply_markup=kb.cancel_input("filters"),
    )
    await call.answer()


@router.message(Inputs.waiting_for_filter_check)
async def on_filter_check_input(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    raw = (message.text or "").strip()
    domain = normalize_domain(raw)
    if domain is None:
        await message.answer(
            f"«{html.escape(raw)}» не похоже на домен. Попробуйте снова из меню фильтров.",
            parse_mode="HTML", reply_markup=kb.back_button("filters"),
        )
        return

    all_rules = db.all_filter_rules_cached()
    verdict = classify_domain(domain, all_rules)
    matched = [r for r in all_rules if r.id in verdict.matched_rule_ids]

    list_icon = {"watch": "🚨", "ignore": "🚫", "allow": "✅"}
    if matched:
        rules_lines = "\n".join(
            f"  {list_icon.get(r.list_type, '•')} {r.list_type}: "
            f"<code>{html.escape(r.pattern)}</code> ({kb.PATTERN_TAG.get(r.pattern_type, r.pattern_type)})"
            for r in matched
        )
        rules_block = f"Совпавшие правила:\n{rules_lines}"
    else:
        rules_block = "Совпавших правил нет."

    if verdict.is_watched:
        result = "🚨 Watch — придёт мгновенное уведомление, даже если домен попадает под Ignore/Allow"
    elif verdict.suppresses_notification:
        result = "🔕 Подавлен (Ignore/Allow) — уведомления не будет"
    else:
        result = "🔔 Обычный домен — уведомление придёт по текущему режиму группировки"

    text = f"<code>{domain}</code>\n\n{rules_block}\n\nИтог: {result}"
    await message.answer(text, parse_mode="HTML", reply_markup=kb.back_button("filters"))


# ------------------------------------------------------------------
# Settings
# ------------------------------------------------------------------

@router.callback_query(F.data == "settings")
async def cb_settings(call: CallbackQuery, db: Database, config: Config, notifier: Notifier) -> None:
    retention_days = runtime_settings.get_event_retention_days(db, config)
    offline_seconds = runtime_settings.get_node_offline_after_seconds(db, config)
    tz_offset = runtime_settings.get_timezone_offset_minutes(db, config)
    warning_pct = runtime_settings.get_buffer_warning_pct(db)
    critical_pct = runtime_settings.get_buffer_critical_pct(db)
    now = datetime.now(timezone.utc)
    text = (
        "⚙️ <b>Настройки</b>\n\n"
        f"🖥 Центр управления (этот бот): <code>{config.public_url}</code>\n"
        f"Хост: <code>{socket.gethostname()}</code>\n\n"
        f"Хранение событий: {retention_days} дн. (0 — хранить всегда; не влияет на список доменов, "
        f"только на детальную историю)\n"
        f"Нода считается offline после: {offline_seconds} сек без heartbeat\n"
        f"Часовой пояс: {format_timezone_offset(tz_offset)} (сейчас там {format_datetime_local(now, tz_offset)})\n"
        f"Пороги буфера: warning {warning_pct}% / critical {critical_pct}%\n"
        f"Watch-уведомления: {'включены' if notifier.is_watchlist_enabled() else 'выключены'}\n"
        f"Админы: {', '.join(str(a) for a in config.admin_ids)}"
    )
    await call.message.edit_text(
        text, parse_mode="HTML",
        reply_markup=kb.settings_menu(
            retention_days, offline_seconds, notifier.is_watchlist_enabled(), format_timezone_offset(tz_offset),
            f"{warning_pct}%/{critical_pct}%",
        ),
    )
    await call.answer()


@router.callback_query(F.data == "settings_retention")
async def cb_settings_retention(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Inputs.waiting_for_retention_days)
    await call.message.edit_text(
        "Сколько дней хранить детальную историю событий? (0 — хранить всегда)",
        reply_markup=kb.cancel_input("settings"),
    )
    await call.answer()


@router.message(Inputs.waiting_for_retention_days)
async def on_retention_days_input(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) > 3650:
        await message.answer(
            "Нужно целое число дней (0–3650). Попробуйте снова из настроек.", reply_markup=kb.back_button("settings"),
        )
        return
    runtime_settings.set_event_retention_days(db, int(raw))
    await message.answer(f"✅ Хранение событий: {raw} дн.", reply_markup=kb.back_button("settings"))


@router.callback_query(F.data == "settings_offline")
async def cb_settings_offline(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Inputs.waiting_for_offline_seconds)
    await call.message.edit_text(
        "Через сколько секунд без heartbeat нода считается offline?",
        reply_markup=kb.cancel_input("settings"),
    )
    await call.answer()


@router.message(Inputs.waiting_for_offline_seconds)
async def on_offline_seconds_input(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    raw = (message.text or "").strip()
    if not raw.isdigit() or not (10 <= int(raw) <= 86400):
        await message.answer(
            "Нужно число секунд от 10 до 86400. Попробуйте снова из настроек.", reply_markup=kb.back_button("settings"),
        )
        return
    runtime_settings.set_node_offline_after_seconds(db, int(raw))
    await message.answer(f"✅ Offline через: {raw} сек.", reply_markup=kb.back_button("settings"))


@router.callback_query(F.data == "settings_timezone")
async def cb_settings_timezone(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Inputs.waiting_for_timezone_offset)
    await call.message.edit_text(
        "Часовой пояс для отображения времени и границ «сегодня»/«вчера».\n"
        "Введите смещение от UTC в часах, например <code>+3</code> (Москва) или <code>-5</code>, "
        "или <code>0</code> для UTC:",
        parse_mode="HTML", reply_markup=kb.cancel_input("settings"),
    )
    await call.answer()


@router.message(Inputs.waiting_for_timezone_offset)
async def on_timezone_offset_input(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    raw = (message.text or "").strip().replace(",", ".")
    try:
        hours = float(raw)
    except ValueError:
        await message.answer(
            "Нужно число часов, например <code>+3</code> или <code>-5</code>. Попробуйте снова из настроек.",
            parse_mode="HTML", reply_markup=kb.back_button("settings"),
        )
        return
    if not -12 <= hours <= 14:
        await message.answer(
            "Смещение должно быть от -12 до +14 часов. Попробуйте снова из настроек.",
            reply_markup=kb.back_button("settings"),
        )
        return
    minutes = round(hours * 60)
    runtime_settings.set_timezone_offset_minutes(db, minutes)
    await message.answer(
        f"✅ Часовой пояс: {format_timezone_offset(minutes)}", reply_markup=kb.back_button("settings"),
    )


@router.callback_query(F.data == "settings_buffer")
async def cb_settings_buffer(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Inputs.waiting_for_buffer_thresholds)
    await call.message.edit_text(
        "Пороги заполнения буфера ноды в процентах, формат <code>warning critical</code>, "
        "например <code>70 90</code>:",
        parse_mode="HTML", reply_markup=kb.cancel_input("settings"),
    )
    await call.answer()


@router.message(Inputs.waiting_for_buffer_thresholds)
async def on_buffer_thresholds_input(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    parts = (message.text or "").strip().split()
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        await message.answer(
            "Нужны два числа: warning и critical, например <code>70 90</code>. Попробуйте снова из настроек.",
            parse_mode="HTML", reply_markup=kb.back_button("settings"),
        )
        return
    warning_pct, critical_pct = int(parts[0]), int(parts[1])
    if not (1 <= warning_pct < critical_pct <= 100):
        await message.answer(
            "Нужно 1 ≤ warning < critical ≤ 100. Попробуйте снова из настроек.", reply_markup=kb.back_button("settings"),
        )
        return
    runtime_settings.set_buffer_thresholds(db, warning_pct, critical_pct)
    await message.answer(
        f"✅ Пороги буфера: warning {warning_pct}% / critical {critical_pct}%", reply_markup=kb.back_button("settings"),
    )


@router.callback_query(F.data == "settings_toggle_watchlist")
async def cb_settings_toggle_watchlist(call: CallbackQuery, db: Database, config: Config, notifier: Notifier) -> None:
    notifier.set_watchlist_enabled(not notifier.is_watchlist_enabled())
    await cb_settings(call, db, config, notifier)


# ------------------------------------------------------------------
# Per-domain notification buttons
# ------------------------------------------------------------------

@router.callback_query(F.data.startswith("domain_ignore:"))
async def cb_domain_ignore(call: CallbackQuery, db: Database) -> None:
    domain_id = int(call.data.split(":")[1])
    db.set_ignored(domain_id, True)
    domain = db.get_domain(domain_id)
    if domain:
        db.add_filter_rule("ignore", "suffix", domain.domain)
    await call.answer("Добавлено в игнор (и в Ignore List на будущее)")
    await call.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data.startswith("domain_copy:"))
async def cb_domain_copy(call: CallbackQuery, db: Database) -> None:
    domain_id = int(call.data.split(":")[1])
    domain = db.get_domain(domain_id)
    if domain:
        await call.message.answer(f"<code>{domain.domain}</code>", parse_mode="HTML")
    await call.answer()


# ------------------------------------------------------------------
# Backups
# ------------------------------------------------------------------

def _backups_text(db: Database) -> str:
    last = backup_settings.last_run_at(db)
    last_line = "ещё не запускался" if not last else last.split(".")[0].replace("T", " ") + " UTC"
    dest = backup_settings.destination(db)
    dest_line = kb.BACKUP_DEST_LABELS.get(dest, dest)
    if dest == "group":
        chat_id = backup_settings.group_chat_id(db)
        topic_id = backup_settings.group_topic_id(db)
        dest_line += f" (chat_id: {chat_id if chat_id is not None else '—'}, топик: {topic_id if topic_id is not None else 'общий'})"
    return (
        "💾 <b>Бэкапы</b>\n\n"
        f"Автобэкап: {'включён' if backup_settings.is_enabled(db) else 'выключен'}\n"
        f"Периодичность: {backup_settings.interval_hours(db)} ч.\n"
        f"Хранить копий: {backup_settings.keep_count(db)}\n"
        f"Доставка: {dest_line}\n"
        f"Последний запуск: {last_line}"
    )


@router.callback_query(F.data == "backups")
async def cb_backups(call: CallbackQuery, db: Database) -> None:
    await call.message.edit_text(
        _backups_text(db), parse_mode="HTML",
        reply_markup=kb.backups_menu(
            backup_settings.is_enabled(db), backup_settings.interval_hours(db),
            backup_settings.keep_count(db), backup_settings.destination(db),
        ),
    )
    await call.answer()


@router.callback_query(F.data == "backup_toggle")
async def cb_backup_toggle(call: CallbackQuery, db: Database) -> None:
    backup_settings.set_enabled(db, not backup_settings.is_enabled(db))
    await cb_backups(call, db)


@router.callback_query(F.data == "backup_set_interval")
async def cb_backup_set_interval(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Inputs.waiting_for_backup_interval)
    await call.message.edit_text(
        f"Как часто делать автобэкап, в часах ({backup_settings.MIN_INTERVAL_HOURS}-"
        f"{backup_settings.MAX_INTERVAL_HOURS})?",
        reply_markup=kb.cancel_input("backups"),
    )
    await call.answer()


@router.message(Inputs.waiting_for_backup_interval)
async def on_backup_interval_input(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Нужно целое число часов. Попробуйте снова из меню бэкапов.", reply_markup=kb.back_button("backups"))
        return
    backup_settings.set_interval_hours(db, int(raw))
    await message.answer(f"✅ Периодичность: {backup_settings.interval_hours(db)} ч.", reply_markup=kb.back_button("backups"))


@router.callback_query(F.data == "backup_set_keep")
async def cb_backup_set_keep(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Inputs.waiting_for_backup_keep)
    await call.message.edit_text(
        f"Сколько последних копий хранить ({backup_settings.MIN_KEEP_COUNT}-{backup_settings.MAX_KEEP_COUNT})?",
        reply_markup=kb.cancel_input("backups"),
    )
    await call.answer()


@router.message(Inputs.waiting_for_backup_keep)
async def on_backup_keep_input(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Нужно целое число. Попробуйте снова из меню бэкапов.", reply_markup=kb.back_button("backups"))
        return
    backup_settings.set_keep_count(db, int(raw))
    await message.answer(f"✅ Хранить копий: {backup_settings.keep_count(db)}", reply_markup=kb.back_button("backups"))


@router.callback_query(F.data == "backup_set_destination")
async def cb_backup_set_destination(call: CallbackQuery, db: Database) -> None:
    await call.message.edit_text(
        "Куда доставлять готовые бэкапы?", reply_markup=kb.backup_destination_menu(backup_settings.destination(db)),
    )
    await call.answer()


@router.callback_query(F.data.startswith("backup_dest:"))
async def cb_backup_dest(call: CallbackQuery, db: Database, state: FSMContext) -> None:
    dest = call.data.split(":", 1)[1]
    if dest == "group":
        backup_settings.set_destination(db, "group")
        await state.set_state(Inputs.waiting_for_backup_group_chat_id)
        await call.message.edit_text(
            "Введите chat_id группы (число, обычно отрицательное - например -1001234567890).\n\n"
            "Как узнать: добавь бота в группу, перешли любое сообщение из неё в @getidsbot - "
            "он покажет chat_id.",
            reply_markup=kb.cancel_input("backups"),
        )
        await call.answer()
        return
    backup_settings.set_destination(db, dest)
    await call.answer("Способ доставки обновлён")
    await cb_backups(call, db)


@router.message(Inputs.waiting_for_backup_group_chat_id)
async def on_backup_group_chat_id_input(message: Message, state: FSMContext, db: Database) -> None:
    raw = (message.text or "").strip()
    if not raw.lstrip("-").isdigit():
        await message.answer("Нужно числовое значение chat_id. Попробуйте снова из меню бэкапов.", reply_markup=kb.back_button("backups"))
        await state.clear()
        return
    backup_settings.set_group_chat_id(db, int(raw))
    await state.set_state(Inputs.waiting_for_backup_group_topic_id)
    await message.answer(
        "Теперь ID топика (темы) внутри группы, если он используется - или пришлите «-», "
        "если бэкапы должны идти в общий чат без топика.",
        reply_markup=kb.cancel_input("backups"),
    )


@router.message(Inputs.waiting_for_backup_group_topic_id)
async def on_backup_group_topic_id_input(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    raw = (message.text or "").strip()
    if raw in ("-", ""):
        backup_settings.set_group_topic_id(db, None)
    elif raw.isdigit():
        backup_settings.set_group_topic_id(db, int(raw))
    else:
        await message.answer(
            "Нужно число (ID топика) или «-» для общего чата. Попробуйте снова из меню бэкапов.",
            reply_markup=kb.back_button("backups"),
        )
        return
    await message.answer("✅ Доставка в группу настроена.", reply_markup=kb.back_button("backups"))


@router.callback_query(F.data == "backup_now")
async def cb_backup_now(call: CallbackQuery, db: Database, backup_task: BackupTask) -> None:
    await call.answer("Запускаю бэкап...")
    result = await backup_task.run_backup_now()
    await call.message.edit_text(result, parse_mode="HTML", reply_markup=kb.back_button("backups"))


def _backup_list_text(backup_task: BackupTask) -> str:
    entries = backup_task.list_backups()
    if not entries:
        return "📋 <b>Список бэкапов</b>\n\nПока ни одного бэкапа не сделано."
    lines = []
    for name, size, mtime in entries[:20]:
        when = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        lines.append(f"• <code>{name}</code> — {size // 1024} КБ, {when} UTC")
    more = f"\n… и ещё {len(entries) - 20}" if len(entries) > 20 else ""
    return "📋 <b>Список бэкапов</b>\n\n" + "\n".join(lines) + more


@router.callback_query(F.data == "backup_list")
async def cb_backup_list(call: CallbackQuery, backup_task: BackupTask, live_view: LiveViewManager) -> None:
    live_active = live_view.is_active(call.message.chat.id, call.message.message_id)
    await call.message.edit_text(
        _backup_list_text(backup_task), parse_mode="HTML", reply_markup=kb.backup_list_menu(live_active),
    )
    await call.answer()


# ------------------------------------------------------------------
# Live views (auto-refresh for read-only info screens)
# ------------------------------------------------------------------

def _render_live_screen(
    kind: str, arg: str | None, db: Database, config: Config, backup_task: BackupTask, live_active: bool,
):
    if kind == "domains_recent":
        return _domains_recent_text(db), kb.domains_recent_menu(live_active)
    if kind == "domains_top":
        return _domains_top_text(db), kb.domains_top_menu(live_active)
    if kind == "stats":
        period = arg or "today"
        return _stats_text(db, config, period), kb.stats_menu(period, live_active)
    if kind == "backup_list":
        return _backup_list_text(backup_task), kb.backup_list_menu(live_active)
    raise ValueError(f"unknown live view kind: {kind!r}")


@router.callback_query(F.data.startswith("live_toggle:"))
async def cb_live_toggle(
    call: CallbackQuery, db: Database, config: Config, backup_task: BackupTask,
    live_view: LiveViewManager, bot: Bot,
) -> None:
    parts = call.data.split(":", 2)
    kind = parts[1]
    arg = parts[2] if len(parts) > 2 else None
    chat_id = call.message.chat.id
    message_id = call.message.message_id

    if live_view.is_active(chat_id, message_id):
        live_view.stop(chat_id, message_id)
        await call.answer("Автообновление выключено")
    else:
        async def render() -> None:
            text, markup = _render_live_screen(kind, arg, db, config, backup_task, True)
            await bot.edit_message_text(
                chat_id=chat_id, message_id=message_id, text=text, parse_mode="HTML", reply_markup=markup,
            )

        started = live_view.start(chat_id, message_id, render)
        if not started:
            await call.answer(
                "Слишком много активных автообновлений сразу - выключи какое-нибудь другое и попробуй снова.",
                show_alert=True,
            )
            return
        minutes = live_view.MAX_DURATION_SECONDS // 60
        await call.answer(f"Автообновление включено (каждые {live_view.INTERVAL_SECONDS} сек, до {minutes} мин)")

    live_active = live_view.is_active(chat_id, message_id)
    text, markup = _render_live_screen(kind, arg, db, config, backup_task, live_active)
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=markup)


# ------------------------------------------------------------------
# Fallbacks - registered last, so they only catch what nothing above did.
# ------------------------------------------------------------------

@router.callback_query()
async def callback_fallback(call: CallbackQuery) -> None:
    """A button from a message sent before some callback_data format
    changed (or a screen that's been removed entirely) must never just
    throw - the tap gets an honest "this screen is stale" instead of the
    eternal Telegram spinner or an unhandled-exception trip through
    error_handler.py."""
    await call.answer("⚠️ Этот экран устарел. Откройте меню заново.", show_alert=True)


@router.message()
async def fallback(message: Message, state: FSMContext, db: Database) -> None:
    if await state.get_state() is not None:
        return  # an FSM handler above should have matched; do nothing extra
    await message.answer(
        "Используйте меню ниже:",
        reply_markup=kb.main_menu(fleet_control.is_monitoring_enabled(db), fleet_control.is_sending_enabled(db)),
    )
