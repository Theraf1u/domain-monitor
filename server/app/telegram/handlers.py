"""All Telegram interaction. A single Router; every action except /start is
an inline button (callback_query), per the "no commands, no reply
keyboards" rule carried over from the single-node MVP.
"""
from __future__ import annotations

import io
import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from app.config import Config
from app.database import Database
from app.notifier import Notifier
from app.security import generate_node_token, hash_token
from app.telegram import keyboards as kb

logger = logging.getLogger(__name__)

router = Router()


class Inputs(StatesGroup):
    waiting_for_node_name = State()
    waiting_for_filter_pattern = State()


def _online_ids(db: Database, config: Config) -> set[int]:
    now = datetime.now(timezone.utc)
    return {n.id for n in db.list_nodes() if n.is_online(config.node_offline_after_seconds, now)}


# ------------------------------------------------------------------
# Entry point / navigation
# ------------------------------------------------------------------

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "🖥 <b>Domain Monitor</b>\n\nЦентральная панель управления нодами и доменами.",
        parse_mode="HTML", reply_markup=kb.main_menu(),
    )


@router.callback_query(F.data == "main")
async def cb_main(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.edit_text(
        "🖥 <b>Domain Monitor</b>\n\nЦентральная панель управления нодами и доменами.",
        parse_mode="HTML", reply_markup=kb.main_menu(),
    )
    await call.answer()


# ------------------------------------------------------------------
# Nodes
# ------------------------------------------------------------------

@router.callback_query(F.data == "nodes")
async def cb_nodes(call: CallbackQuery, db: Database, config: Config) -> None:
    nodes = db.list_nodes()
    if not nodes:
        text = "📡 <b>Ноды</b>\n\nПока не добавлено ни одной ноды."
    else:
        text = "📡 <b>Ноды</b>\n\nВыберите ноду для управления:"
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb.nodes_list(nodes, _online_ids(db, config)))
    await call.answer()


@router.callback_query(F.data.startswith("node:"))
async def cb_node_card(call: CallbackQuery, db: Database, config: Config) -> None:
    node_id = int(call.data.split(":")[1])
    node = db.get_node(node_id)
    if node is None:
        await call.answer("Нода не найдена", show_alert=True)
        return

    now = datetime.now(timezone.utc)
    online = node.is_online(config.node_offline_after_seconds, now)
    status_line = "🟢 Online" if online else ("⛔ Отозвана" if node.status == "revoked" else "🔴 Offline")
    hb_line = "никогда"
    if node.last_heartbeat_at:
        delta = int((now - node.last_heartbeat_at).total_seconds())
        hb_line = f"{delta} сек назад"

    events_today = db.count_events_since(now.replace(hour=0, minute=0, second=0, microsecond=0))
    domains = db.list_domains(limit=1000, node_id=node.id)

    text = (
        f"🖥 <b>{node.name}</b>\n\n"
        f"Статус: {status_line}\n"
        f"Последний heartbeat: {hb_line}\n"
        f"Версия агента: {node.version or '—'}\n"
        f"IP: {node.ip or '—'}\n"
        f"Hostname: {node.hostname or '—'}\n\n"
        f"Доменов с этой ноды: {len(domains)}"
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
async def cb_node_add(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Inputs.waiting_for_node_name)
    await call.message.edit_text(
        "Введите имя новой ноды (например: <code>Germany-1</code>):",
        parse_mode="HTML", reply_markup=kb.cancel_input(),
    )
    await call.answer()


@router.message(Inputs.waiting_for_node_name)
async def on_node_name_input(message: Message, state: FSMContext, db: Database, config: Config) -> None:
    name = (message.text or "").strip()
    await state.clear()
    if not name or len(name) > 100:
        await message.answer("Некорректное имя. Открой меню нод и попробуй снова.", reply_markup=kb.back_button("nodes"))
        return
    if db.name_exists(name):
        await message.answer(f"Нода с именем «{name}» уже существует.", reply_markup=kb.back_button("nodes"))
        return

    token = generate_node_token()
    db.create_node(name, hash_token(token))
    await message.answer(
        f"✅ Нода <b>{name}</b> создана.\n\n"
        f"Токен (сохраните, показывается один раз):\n<code>{token}</code>\n\n"
        f"На новом сервере выполните установщик агента (см. README проекта), указав в мастере:\n"
        f"Server URL: <code>{config.public_url}</code>\n"
        f"Node Token: <code>{token}</code>",
        parse_mode="HTML", reply_markup=kb.back_button("nodes"),
    )


# ------------------------------------------------------------------
# Domains
# ------------------------------------------------------------------

@router.callback_query(F.data == "domains")
async def cb_domains(call: CallbackQuery) -> None:
    await call.message.edit_text("🌐 <b>Домены</b>", parse_mode="HTML", reply_markup=kb.domains_menu())
    await call.answer()


@router.callback_query(F.data == "domains_recent")
async def cb_domains_recent(call: CallbackQuery, db: Database) -> None:
    domains = db.list_domains(limit=20, order_by="last_seen")
    if not domains:
        text = "Пока нет ни одного домена."
    else:
        lines = "\n".join(f"• <code>{d.domain}</code> ({d.hits})" for d in domains)
        text = f"🕐 <b>Последние домены</b>\n\n{lines}"
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb.back_button("domains"))
    await call.answer()


@router.callback_query(F.data == "domains_top")
async def cb_domains_top(call: CallbackQuery, db: Database) -> None:
    domains = db.top_domains(limit=20)
    if not domains:
        text = "Пока нет ни одного домена."
    else:
        lines = "\n".join(f"• <code>{d.domain}</code> — {d.hits}" for d in domains)
        text = f"🔝 <b>Топ доменов по обращениям</b>\n\n{lines}"
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb.back_button("domains"))
    await call.answer()


@router.callback_query(F.data == "domains_export")
async def cb_domains_export(call: CallbackQuery, db: Database) -> None:
    domains = db.list_domains(limit=100000, order_by="domain")
    content = "\n".join(d.domain for d in domains) + ("\n" if domains else "")
    file = BufferedInputFile(content.encode("utf-8"), filename="domains.txt")
    await call.message.answer_document(file, caption=f"Экспорт: {len(domains)} домен(ов)")
    await call.answer()


# ------------------------------------------------------------------
# Stats
# ------------------------------------------------------------------

@router.callback_query(F.data == "stats")
async def cb_stats(call: CallbackQuery, db: Database, config: Config) -> None:
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    nodes = db.list_nodes()
    online = sum(1 for n in nodes if n.is_online(config.node_offline_after_seconds, now))

    text = (
        "📊 <b>Статистика</b>\n\n"
        f"Ноды онлайн: {online}/{len(nodes)}\n"
        f"Уникальных доменов: {db.count_domains()}\n"
        f"Событий сегодня: {db.count_events_since(today_start)}\n"
        f"Новых доменов сегодня: {db.count_domains(since=today_start)}"
    )
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb.back_button("main"))
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
    list_type = call.data.split(":", 1)[1]
    rules = db.list_filter_rules(list_type)
    title = {"watch": "🚨 Watch List", "ignore": "🚫 Ignore List", "allow": "✅ Allow List"}[list_type]
    text = title if rules else f"{title}\n\nПусто"
    await call.message.edit_text(text, reply_markup=kb.filters_list(list_type, rules))
    await call.answer()


@router.callback_query(F.data.startswith("filter_remove:"))
async def cb_filter_remove(call: CallbackQuery, db: Database) -> None:
    rule_id = int(call.data.split(":")[1])
    db.remove_filter_rule(rule_id)
    await call.answer("Удалено")
    await cb_filters(call, db)


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
    await call.message.edit_text(f"Введите паттерн, например: {hint}", reply_markup=kb.cancel_input())
    await call.answer()


@router.message(Inputs.waiting_for_filter_pattern)
async def on_filter_pattern_input(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    await state.clear()
    pattern = (message.text or "").strip().lower()
    if not pattern:
        await message.answer("Пустой паттерн, попробуйте снова из меню фильтров.", reply_markup=kb.back_button("filters"))
        return
    rule = db.add_filter_rule(data["list_type"], data["pattern_type"], pattern)
    if rule is None:
        await message.answer("Такое правило уже существует.", reply_markup=kb.back_button("filters"))
    else:
        await message.answer(f"✅ Добавлено в {data['list_type']}: <code>{pattern}</code>", parse_mode="HTML", reply_markup=kb.back_button("filters"))


# ------------------------------------------------------------------
# Settings (minimal for now - extended in a later stage)
# ------------------------------------------------------------------

@router.callback_query(F.data == "settings")
async def cb_settings(call: CallbackQuery, config: Config) -> None:
    text = (
        "⚙️ <b>Настройки</b>\n\n"
        f"Хранение событий: {config.event_retention_days} дн.\n"
        f"Нода считается offline после: {config.node_offline_after_seconds} сек без heartbeat\n\n"
        "Расширенные настройки (ignore/watch-листы, источники, роли) доступны в Web Admin."
    )
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb.settings_menu())
    await call.answer()


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
# Fallback: any stray text/command outside an active FSM state just goes
# back to the main menu (per the "no commands other than /start" rule).
# ------------------------------------------------------------------

@router.message()
async def fallback(message: Message, state: FSMContext) -> None:
    if await state.get_state() is not None:
        return  # an FSM handler above should have matched; do nothing extra
    await message.answer("Используйте меню ниже:", reply_markup=kb.main_menu())
