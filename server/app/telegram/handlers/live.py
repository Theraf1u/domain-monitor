"""Auto-refresh for read-only info screens (recent/top domains, stats,
backup list). Lives on its own since it needs to call into domains.py,
stats.py, and backups.py to re-render whichever screen kind is live.
"""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery

from app.backup_task import BackupTask
from app.config import Config
from app.database import Database
from app.live_view import LiveViewManager
from app.telegram import keyboards as kb
from app.telegram.handlers.backups import _backup_list_text
from app.telegram.handlers.domains import _domains_recent_text, _domains_top_text
from app.telegram.handlers.stats import _stats_text

router = Router()


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
