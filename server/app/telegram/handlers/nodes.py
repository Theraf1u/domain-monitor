"""The "📡 Ноды" section: list (search/filter/pagination), bulk actions,
node card, diagnostics, and the group/topic notification-binding flow.
"""
from __future__ import annotations

import html
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app import fleet_control, runtime_settings
from app.agent_versions import version_badge
from app.config import Config
from app.database import Database
from app.security import generate_node_token, hash_token
from app.telegram import keyboards as kb
from app.telegram.formatters import format_bytes, format_duration, format_relative_time
from app.telegram.states import Inputs
from app.topic_binding import TopicBindingManager

router = Router()


def _online_ids(db: Database, config: Config) -> set[int]:
    now = datetime.now(timezone.utc)
    offline_after = runtime_settings.get_node_offline_after_seconds(db, config)
    return {n.id for n in db.list_nodes() if n.is_online(offline_after, now)}


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
