"""The "⚙️ Настройки" section: retention, offline timeout, timezone,
buffer-alert thresholds, and the watchlist toggle.
"""
from __future__ import annotations

import socket
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import runtime_settings
from app.config import Config
from app.database import Database
from app.notifier import Notifier
from app.telegram import keyboards as kb
from app.telegram.formatters import format_datetime_local, format_timezone_offset
from app.telegram.states import Inputs

router = Router()


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
