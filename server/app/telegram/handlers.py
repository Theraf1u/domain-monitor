"""All Telegram interaction. A single Router; every action except /start is
an inline button (callback_query), per the "no commands, no reply
keyboards" rule carried over from the single-node MVP.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from app import backup_settings, fleet_control, runtime_settings
from app.backup_task import BackupTask
from app.config import Config
from app.database import Database
from app.filters import classify_domain, normalize_domain
from app.live_view import LiveViewManager
from app.notifier import Notifier
from app.security import generate_node_token, hash_token
from app.telegram import keyboards as kb
from app.topic_binding import TopicBindingManager

logger = logging.getLogger(__name__)

router = Router()

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class Inputs(StatesGroup):
    waiting_for_node_rename = State()
    waiting_for_filter_pattern = State()
    waiting_for_filter_check = State()
    waiting_for_export_range = State()
    waiting_for_custom_batch_seconds = State()
    waiting_for_retention_days = State()
    waiting_for_offline_seconds = State()
    waiting_for_backup_interval = State()
    waiting_for_backup_keep = State()
    waiting_for_backup_group_chat_id = State()
    waiting_for_backup_group_topic_id = State()


def _online_ids(db: Database, config: Config) -> set[int]:
    now = datetime.now(timezone.utc)
    offline_after = runtime_settings.get_node_offline_after_seconds(db, config)
    return {n.id for n in db.list_nodes() if n.is_online(offline_after, now)}


def _period_range(period: str, now: datetime) -> tuple[datetime | None, datetime | None]:
    """Maps a period key (shared by the export and stats menus) to a
    [since, until) window. None on either side means "no bound in that
    direction" - "all" is (None, None)."""
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
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


# ------------------------------------------------------------------
# Entry point / navigation
# ------------------------------------------------------------------

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    await message.answer(
        "🖥 <b>Domain Monitor</b>\n\nЦентральная панель управления нодами и доменами.",
        parse_mode="HTML",
        reply_markup=kb.main_menu(fleet_control.is_monitoring_enabled(db), fleet_control.is_sending_enabled(db)),
    )


@router.callback_query(F.data == "main")
async def cb_main(call: CallbackQuery, state: FSMContext, db: Database) -> None:
    await state.clear()
    await call.message.edit_text(
        "🖥 <b>Domain Monitor</b>\n\nЦентральная панель управления нодами и доменами.",
        parse_mode="HTML",
        reply_markup=kb.main_menu(fleet_control.is_monitoring_enabled(db), fleet_control.is_sending_enabled(db)),
    )
    await call.answer()


@router.callback_query(F.data == "fleet_toggle_monitoring")
async def cb_fleet_toggle_monitoring(call: CallbackQuery, state: FSMContext, db: Database) -> None:
    fleet_control.set_monitoring_enabled(db, not fleet_control.is_monitoring_enabled(db))
    await call.answer(
        "⏸ Мониторинг остановлен на всех нодах" if not fleet_control.is_monitoring_enabled(db)
        else "▶ Мониторинг возобновлён на всех нодах"
    )
    await cb_main(call, state, db)


@router.callback_query(F.data == "fleet_toggle_sending")
async def cb_fleet_toggle_sending(call: CallbackQuery, state: FSMContext, db: Database) -> None:
    fleet_control.set_sending_enabled(db, not fleet_control.is_sending_enabled(db))
    await call.answer(
        "⏸ Отправка доменов остановлена на всех нодах" if not fleet_control.is_sending_enabled(db)
        else "▶ Отправка доменов возобновлена на всех нодах"
    )
    await cb_main(call, state, db)


# ------------------------------------------------------------------
# Nodes
# ------------------------------------------------------------------

_NODES_LEGEND = "🟢 работает   🔴 отозвана/не отвечает   🔵 на паузе"


@router.callback_query(F.data == "nodes")
async def cb_nodes(call: CallbackQuery, db: Database, config: Config) -> None:
    nodes = db.list_nodes()
    fleet_mon = fleet_control.is_monitoring_enabled(db)
    if not nodes:
        text = "📡 <b>Ноды</b>\n\nПока не добавлено ни одной ноды."
    else:
        text = f"📡 <b>Ноды</b>\n{_NODES_LEGEND}\n\nВыберите ноду для управления:"
    await call.message.edit_text(
        text, parse_mode="HTML", reply_markup=kb.nodes_list(nodes, _online_ids(db, config), fleet_mon),
    )
    await call.answer()


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
    hb_line = "никогда"
    if node.last_heartbeat_at:
        delta = int((now - node.last_heartbeat_at).total_seconds())
        hb_line = f"{delta} сек назад"

    domains = db.list_domains(limit=1000, node_id=node.id)

    buffer_line = ""
    if node.agent_buffer_size:
        buffer_line = (
            f"\n📦 В локальном буфере агента: {node.agent_buffer_size} "
            f"(накоплено, ждёт отправки на сервер)"
        )

    dest_line = kb.NOTIFY_DEST_LABELS.get(node.notify_destination, node.notify_destination)
    if node.notify_destination in ("group", "both"):
        where = (
            f"chat_id {node.notify_group_chat_id}"
            + (f", топик {node.notify_group_topic_id}" if node.notify_group_topic_id else "")
        ) if node.notify_group_chat_id else "не привязано"
        dest_line += f" ({where})"

    text = (
        f"🖥 <b>{node.name}</b>\n\n"
        f"Статус: {status_line}\n"
        f"Последний heartbeat: {hb_line}\n"
        f"Версия агента: {node.version or '—'}\n"
        f"IP: {node.ip or '—'}\n"
        f"Hostname: {node.hostname or '—'}\n\n"
        f"Доменов с этой ноды: {len(domains)}"
        f"{buffer_line}\n"
        f"Уведомления идут: {dest_line}"
    )
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb.node_card(node))
    await call.answer()


@router.callback_query(F.data.startswith("node_toggle_mon:"))
async def cb_node_toggle_mon(call: CallbackQuery, db: Database, config: Config) -> None:
    node_id = int(call.data.split(":")[1])
    node = db.get_node(node_id)
    if node:
        db.set_node_monitoring(node_id, not node.monitoring_enabled)
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
async def cb_node_delete(call: CallbackQuery, db: Database, config: Config) -> None:
    node_id = int(call.data.split(":")[1])
    db.delete_node(node_id)
    await call.answer("Нода удалена")
    await cb_nodes(call, db, config)


@router.callback_query(F.data == "node_add")
async def cb_node_add(call: CallbackQuery, db: Database, config: Config) -> None:
    """One tap, zero typing: creates the node with an auto-assigned
    placeholder name (renamed to its real IP on first heartbeat, see
    Database.touch_heartbeat) and hands back a ready-to-paste token."""
    token = generate_node_token()
    node = db.create_node_auto(hash_token(token))
    await call.message.edit_text(
        f"✅ Нода <b>{node.name}</b> создана.\n\n"
        f"Токен (сохраните, показывается один раз):\n<code>{token}</code>\n\n"
        f"На новом сервере выполните установщик агента (см. README проекта), указав в мастере:\n"
        f"Server URL: <code>{config.public_url}</code>\n"
        f"Node Token: <code>{token}</code>\n\n"
        f"После первого подключения нода автоматически переименуется в свой IP. "
        f"Своё имя можно задать в любой момент через карточку ноды («✏️ Переименовать»).",
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
async def cb_domains_export_period(call: CallbackQuery, db: Database) -> None:
    period = call.data.split(":", 1)[1]
    now = datetime.now(timezone.utc)
    since, until = _period_range(period, now)
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
    parts = text.split()
    try:
        if len(parts) == 1:
            day = datetime.strptime(parts[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            since, until = day, day + timedelta(days=1)
        elif len(parts) == 2:
            start = datetime.strptime(parts[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            end = datetime.strptime(parts[1], "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
            since, until = start, end
        else:
            raise ValueError
    except ValueError:
        await message.answer(
            "Не понял формат. Пример: <code>2026-09-01</code> или <code>2026-09-01 2026-09-15</code>.",
            parse_mode="HTML", reply_markup=kb.back_button("domains_export"),
        )
        return

    domains = db.list_domains(limit=1_000_000, order_by="domain", since=since, until=until)
    if not domains:
        await message.answer("За этот диапазон доменов нет.", reply_markup=kb.back_button("domains"))
        return
    content = "\n".join(d.domain for d in domains) + "\n"
    file = BufferedInputFile(content.encode("utf-8"), filename="domains.txt")
    await message.answer_document(file, caption=f"Экспорт ({text}): {len(domains)} домен(ов)")
    await message.answer("Готово.", reply_markup=kb.back_button("domains"))


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

def _format_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if value < 1024 or unit == "ГБ":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} ГБ"


def _database_size_bytes(config: Config) -> int:
    base = config.database_path
    return sum(
        os.path.getsize(candidate)
        for candidate in (base, base + "-wal", base + "-shm")
        if os.path.isfile(candidate)
    )


def _stats_text(db: Database, config: Config, period: str) -> str:
    now = datetime.now(timezone.utc)
    since, until = _period_range(period, now)
    offline_after = runtime_settings.get_node_offline_after_seconds(db, config)
    nodes = db.list_nodes()
    online = sum(1 for n in nodes if n.is_online(offline_after, now))

    events_count = db.count_events_since(since or _EPOCH, until)
    new_domains = db.count_domains(since=since, until=until)
    total_domains = db.count_domains()
    top_nodes = db.top_active_nodes_since(since or _EPOCH, limit=3)

    period_label = dict(kb.STATS_PERIODS).get(period, period)
    rate_line = ""
    if since is not None:
        hours = max((now - since).total_seconds() / 3600, 1 / 60)
        rate_line = f"Скорость: ~{events_count / hours:.1f} событий/час\n"

    top_lines = "\n".join(f"  {i + 1}. {name} — {count}" for i, (name, count) in enumerate(top_nodes)) or "  —"

    buffered_total = sum(n.agent_buffer_size or 0 for n in nodes)
    buffered_line = (
        f"В буферах агентов (собрано, не отправлено): {buffered_total}\n" if buffered_total else ""
    )
    db_size_line = f"Размер базы доменов: {_format_size(_database_size_bytes(config))}\n"

    return (
        f"📊 <b>Статистика</b> ({period_label})\n\n"
        f"Ноды онлайн: {online}/{len(nodes)}\n"
        f"Уникальных доменов (всего): {total_domains}\n"
        f"Новых доменов за период: {new_domains}\n"
        f"Событий за период: {events_count}\n"
        f"{rate_line}"
        f"{buffered_line}"
        f"{db_size_line}"
        f"\nАктивные ноды за период:\n{top_lines}"
    )


@router.callback_query(F.data == "stats")
async def cb_stats(call: CallbackQuery, db: Database, config: Config, live_view: LiveViewManager) -> None:
    live_active = live_view.is_active(call.message.chat.id, call.message.message_id)
    await call.message.edit_text(
        _stats_text(db, config, "today"), parse_mode="HTML", reply_markup=kb.stats_menu("today", live_active),
    )
    await call.answer()


@router.callback_query(F.data.startswith("stats_period:"))
async def cb_stats_period(call: CallbackQuery, db: Database, config: Config, live_view: LiveViewManager) -> None:
    period = call.data.split(":", 1)[1]
    live_active = live_view.is_active(call.message.chat.id, call.message.message_id)
    await call.message.edit_text(
        _stats_text(db, config, period), parse_mode="HTML", reply_markup=kb.stats_menu(period, live_active),
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
    domain = normalize_domain((message.text or "").strip())
    if domain is None:
        await message.answer("Не похоже на домен. Попробуйте снова из меню фильтров.", reply_markup=kb.back_button("filters"))
        return
    verdict = classify_domain(domain, db.all_filter_rules_cached())
    if verdict.is_watched:
        result = "🚨 Watch — сработает мгновенное уведомление, даже если попадает под Ignore/Allow"
    elif verdict.suppresses_notification:
        result = "🔕 Подавлен (Ignore/Allow) — уведомления не будет"
    else:
        result = "🔔 Обычный домен — уведомление придёт по текущему режиму группировки"
    await message.answer(f"<code>{domain}</code>\n\n{result}", parse_mode="HTML", reply_markup=kb.back_button("filters"))


# ------------------------------------------------------------------
# Settings
# ------------------------------------------------------------------

@router.callback_query(F.data == "settings")
async def cb_settings(call: CallbackQuery, db: Database, config: Config, notifier: Notifier) -> None:
    retention_days = runtime_settings.get_event_retention_days(db, config)
    offline_seconds = runtime_settings.get_node_offline_after_seconds(db, config)
    text = (
        "⚙️ <b>Настройки</b>\n\n"
        f"Хранение событий: {retention_days} дн. (0 — хранить всегда; не влияет на список доменов, "
        f"только на детальную историю)\n"
        f"Нода считается offline после: {offline_seconds} сек без heartbeat\n"
        f"Watch-уведомления: {'включены' if notifier.is_watchlist_enabled() else 'выключены'}\n"
        f"Админы: {', '.join(str(a) for a in config.admin_ids)}"
    )
    await call.message.edit_text(
        text, parse_mode="HTML",
        reply_markup=kb.settings_menu(retention_days, offline_seconds, notifier.is_watchlist_enabled()),
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
# Fallback: any stray text/command outside an active FSM state just goes
# back to the main menu (per the "no commands other than /start" rule).
# ------------------------------------------------------------------

@router.message()
async def fallback(message: Message, state: FSMContext, db: Database) -> None:
    if await state.get_state() is not None:
        return  # an FSM handler above should have matched; do nothing extra
    await message.answer(
        "Используйте меню ниже:",
        reply_markup=kb.main_menu(fleet_control.is_monitoring_enabled(db), fleet_control.is_sending_enabled(db)),
    )
