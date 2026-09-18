"""The "💾 Бэкапы" section: schedule/keep/destination settings, manual
run, and the backup list.
"""
from __future__ import annotations

from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import backup_settings
from app.backup_task import BackupTask
from app.database import Database
from app.live_view import LiveViewManager
from app.telegram import keyboards as kb
from app.telegram.states import Inputs

router = Router()


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
