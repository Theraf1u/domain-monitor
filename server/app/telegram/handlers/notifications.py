"""The "🔔 Уведомления" Notification Center: batch mode, event-type
toggles, recipients, quiet hours, and the test-notification button.
"""
from __future__ import annotations

import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.notifier import NOTIFY_TYPES, Notifier
from app.telegram import keyboards as kb
from app.telegram.states import Inputs

router = Router()


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
