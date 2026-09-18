"""Entry point / navigation: /start, the main menu, and the two fleet-wide
toggles shown on it.
"""
from __future__ import annotations

from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import fleet_control, runtime_settings
from app.config import Config
from app.database import Database
from app.notifier import Notifier
from app.telegram import keyboards as kb
from app.telegram.handlers.common import _period_range

router = Router()


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
