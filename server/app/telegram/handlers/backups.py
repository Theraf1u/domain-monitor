"""The "💾 Бэкапы" section: Backup Manager 2.0 (spec 2.0 Part 2, section
2) - schedule/keep/destination/kind settings, manual run, list with
per-backup download/verify/restore/delete, and the restore flow.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

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
        f"Тип: {kb.BACKUP_KIND_LABELS.get(backup_settings.kind(db), backup_settings.kind(db))}\n"
        f"Последний запуск: {last_line}"
    )


@router.callback_query(F.data == "backups")
async def cb_backups(call: CallbackQuery, db: Database) -> None:
    await call.message.edit_text(
        _backups_text(db), parse_mode="HTML",
        reply_markup=kb.backups_menu(
            backup_settings.is_enabled(db), backup_settings.interval_hours(db),
            backup_settings.keep_count(db), backup_settings.destination(db), backup_settings.kind(db),
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


@router.callback_query(F.data == "backup_set_kind")
async def cb_backup_set_kind(call: CallbackQuery, db: Database) -> None:
    await call.message.edit_text(
        "Что сохранять при автобэкапе?\n\n"
        "🗄 База данных - SQLite + WAL/SHM (ноды, фильтры, настройки - всё уже в БД).\n"
        "📦 Полный - то же самое плюс снимок переменных окружения (.env), для disaster recovery/миграции.",
        reply_markup=kb.backup_kind_menu(backup_settings.kind(db)),
    )
    await call.answer()


@router.callback_query(F.data.startswith("backup_kind_set:"))
async def cb_backup_kind_set(call: CallbackQuery, db: Database) -> None:
    value = call.data.split(":", 1)[1]
    backup_settings.set_kind(db, value)
    await call.answer("Тип автобэкапа обновлён")
    await cb_backups(call, db)


@router.callback_query(F.data == "backup_now")
async def cb_backup_now(call: CallbackQuery) -> None:
    await call.message.edit_text("Какой бэкап создать?", reply_markup=kb.backup_now_kind_menu())
    await call.answer()


@router.callback_query(F.data.startswith("backup_now_do:"))
async def cb_backup_now_do(call: CallbackQuery, backup_task: BackupTask) -> None:
    kind = call.data.split(":", 1)[1]
    await call.answer("Запускаю бэкап...")
    result = await backup_task.run_backup_now(kind)
    await call.message.edit_text(result, parse_mode="HTML", reply_markup=kb.back_button("backups"))


def _fmt_size(num_bytes: int) -> str:
    return f"{num_bytes // 1024} КБ" if num_bytes < 1024 * 1024 else f"{num_bytes / (1024 * 1024):.1f} МБ"


def _backup_list_text(backup_task: BackupTask) -> str:
    entries = backup_task.list_backups()
    if not entries:
        return "📋 <b>Список бэкапов</b>\n\nПока ни одного бэкапа не сделано."
    lines = []
    for e in entries[:20]:
        when = datetime.fromtimestamp(e["mtime"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        kind_label = kb.BACKUP_KIND_LABELS.get(e["type"], e["type"])
        verified = e["verified"]
        v_icon = "✅" if verified else ("❌" if verified is False else "❔")
        lines.append(f"• <code>{e['filename']}</code>\n  {kind_label}, {_fmt_size(e['size'])}, {when} UTC, проверка: {v_icon}")
    more = f"\n… и ещё {len(entries) - 20}" if len(entries) > 20 else ""
    return "📋 <b>Список бэкапов</b>\n\n" + "\n".join(lines) + more


@router.callback_query(F.data == "backup_list")
async def cb_backup_list(call: CallbackQuery, backup_task: BackupTask, live_view: LiveViewManager) -> None:
    live_active = live_view.is_active(call.message.chat.id, call.message.message_id)
    filenames = [e["filename"] for e in backup_task.list_backups()[:20]]
    await call.message.edit_text(
        _backup_list_text(backup_task), parse_mode="HTML", reply_markup=kb.backup_list_menu(filenames, live_active),
    )
    await call.answer()


def _find_entry(backup_task: BackupTask, filename: str) -> dict | None:
    return next((e for e in backup_task.list_backups() if e["filename"] == filename), None)


def _backup_item_text(entry: dict) -> str:
    when = datetime.fromtimestamp(entry["mtime"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    kind_label = kb.BACKUP_KIND_LABELS.get(entry["type"], entry["type"])
    checksum = entry["checksum"] or "—"
    checksum_short = checksum if checksum == "—" else f"{checksum[:16]}…"
    verified = entry["verified"]
    v_text = "✅ пройдена" if verified else ("❌ ПРОВАЛЕНА" if verified is False else "❔ не проверялась")
    return (
        f"💾 <code>{entry['filename']}</code>\n\n"
        f"Тип: {kind_label}\n"
        f"Создан: {when}\n"
        f"Размер: {_fmt_size(entry['size'])}\n"
        f"Checksum (sha256): <code>{checksum_short}</code>\n"
        f"Проверка целостности: {v_text}"
    )


@router.callback_query(F.data.startswith("bk_item:"))
async def cb_backup_item(call: CallbackQuery, backup_task: BackupTask) -> None:
    filename = call.data.split(":", 1)[1]
    entry = _find_entry(backup_task, filename)
    if entry is None:
        await call.answer("Бэкап не найден (возможно, уже удалён)", show_alert=True)
        return
    await call.message.edit_text(
        _backup_item_text(entry), parse_mode="HTML", reply_markup=kb.backup_item_menu(filename),
    )
    await call.answer()


@router.callback_query(F.data.startswith("bk_dl:"))
async def cb_backup_download(call: CallbackQuery, backup_task: BackupTask) -> None:
    filename = call.data.split(":", 1)[1]
    full = os.path.join(backup_task.backup_dir, filename)
    if not os.path.isfile(full):
        await call.answer("Файл не найден", show_alert=True)
        return
    await call.answer("Отправляю...")
    with open(full, "rb") as f:
        content = f.read()
    await call.message.answer_document(BufferedInputFile(content, filename=filename), caption=f"💾 {filename}")


@router.callback_query(F.data.startswith("bk_verify:"))
async def cb_backup_verify(call: CallbackQuery, backup_task: BackupTask) -> None:
    filename = call.data.split(":", 1)[1]
    full = os.path.join(backup_task.backup_dir, filename)
    if not os.path.isfile(full):
        await call.answer("Файл не найден", show_alert=True)
        return
    await call.answer("Проверяю...")
    ok, detail = backup_task.reverify(filename)
    entry = _find_entry(backup_task, filename)
    await call.message.edit_text(
        _backup_item_text(entry) + f"\n\nРезультат: {'✅ ok' if ok else f'❌ {detail}'}",
        parse_mode="HTML", reply_markup=kb.backup_item_menu(filename),
    )


@router.callback_query(F.data.startswith("bk_del_confirm:"))
async def cb_backup_delete_confirm(call: CallbackQuery, backup_task: BackupTask) -> None:
    filename = call.data.split(":", 1)[1]
    entry = _find_entry(backup_task, filename)
    if entry is None:
        await call.answer("Бэкап не найден", show_alert=True)
        return
    await call.message.edit_text(
        f"Удалить бэкап <code>{filename}</code>? Действие необратимо.",
        parse_mode="HTML", reply_markup=kb.confirm_backup_delete(filename),
    )
    await call.answer()


@router.callback_query(F.data.startswith("bk_del:"))
async def cb_backup_delete(call: CallbackQuery, backup_task: BackupTask, live_view: LiveViewManager) -> None:
    filename = call.data.split(":", 1)[1]
    full = os.path.join(backup_task.backup_dir, filename)
    removed = False
    if os.path.isfile(full):
        os.remove(full)
        removed = True
    meta = full + ".meta.json"
    if os.path.isfile(meta):
        os.remove(meta)
    await call.answer("Удалено" if removed else "Файл уже отсутствовал")
    await cb_backup_list(call, backup_task, live_view)


@router.callback_query(F.data.startswith("bk_restore_confirm:"))
async def cb_backup_restore_confirm(call: CallbackQuery, backup_task: BackupTask) -> None:
    filename = call.data.split(":", 1)[1]
    entry = _find_entry(backup_task, filename)
    if entry is None:
        await call.answer("Бэкап не найден", show_alert=True)
        return
    await call.message.edit_text(
        f"♻️ Восстановить из <code>{filename}</code>?\n\n"
        f"Перед восстановлением автоматически будет создан снимок текущего состояния БД - "
        f"если что-то пойдёт не так, произойдёт автоматический откат к нему.\n\n"
        f"Ноды/фильтры/настройки/домены будут заменены содержимым бэкапа.",
        parse_mode="HTML", reply_markup=kb.confirm_backup_restore(filename),
    )
    await call.answer()


@router.callback_query(F.data.startswith("bk_restore:"))
async def cb_backup_restore(call: CallbackQuery, backup_task: BackupTask) -> None:
    filename = call.data.split(":", 1)[1]
    await call.answer("Восстанавливаю...")
    await call.message.edit_text("♻️ Восстановление выполняется...")
    result = await backup_task.restore_from_backup(filename)
    await call.message.edit_text(result, parse_mode="HTML", reply_markup=kb.back_button("backups"))


@router.callback_query(F.data == "backup_restore_menu")
async def cb_backup_restore_menu(call: CallbackQuery, backup_task: BackupTask) -> None:
    filenames = [e["filename"] for e in backup_task.list_backups()[:20]]
    text = (
        "♻️ <b>Восстановление</b>\n\nВыберите бэкап для восстановления:"
        if filenames else "♻️ <b>Восстановление</b>\n\nПока нет ни одного бэкапа."
    )
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb.backup_restore_menu(filenames))
    await call.answer()
