"""Builds the aiogram Bot/Dispatcher and wires shared objects (db, config,
notifier) into every handler via workflow data, the same pattern as the
single-node MVP."""
from __future__ import annotations

import os

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, FSInputFile, InputProfilePhotoStatic, MenuButtonCommands

from app.backup_task import BackupTask
from app.config import Config
from app.database import Database
from app.live_view import LiveViewManager
from app.notifier import Notifier
from app.telegram import handlers
from app.telegram.middleware import AdminOnlyMiddleware
from app.topic_binding import TopicBindingManager

# Optional profile photo - set via BotFather or dropped in by hand at this
# path. Not committed by default (no logo is invented for you), so this is
# skipped silently if the file isn't there.
_AVATAR_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "bot_avatar.png"
)

BOT_DESCRIPTION = (
    "🖥 Domain Monitor — центральная панель управления нодами и мониторингом доменов для "
    "VPN-инфраструктуры\n\n"
    "Собирает домены с VPN-нод (TLS SNI/DNS) без MITM и расшифровки трафика, показывает "
    "статистику и помогает строить роутинг\n\n"
    "Управление полностью здесь — нажми /start\n\n"
    "Исходники: github.com/Theraf1u/domain-monitor\n"
    "Автор: @Theraflu"
)
BOT_SHORT_DESCRIPTION = "🖥 Мониторинг доменов для VPN-нод · github.com/Theraf1u/domain-monitor · @Theraflu"


async def configure_bot_profile(bot: Bot) -> None:
    """Registers the /start command hint, the persistent "Меню" button,
    the bot's description/short description and (if the asset file is
    present) its profile photo - all via the Bot API, so a fresh
    BotFather bot works out of the box with no manual /setcommands,
    /setdescription, /setabouttext or /setuserpic steps. Runs on every
    startup; idempotent (Telegram just overwrites with the same values
    if they're already set)."""
    await bot.set_my_commands([BotCommand(command="start", description="Запустить бота")])
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    await bot.set_my_description(description=BOT_DESCRIPTION)
    await bot.set_my_short_description(short_description=BOT_SHORT_DESCRIPTION)
    if os.path.isfile(_AVATAR_PATH):
        await bot.set_my_profile_photo(photo=InputProfilePhotoStatic(photo=FSInputFile(_AVATAR_PATH)))


def build_bot_and_dispatcher(
    config: Config, db: Database, notifier: Notifier, backup_task: BackupTask,
    topic_binding: TopicBindingManager, live_view: LiveViewManager,
) -> tuple[Bot, Dispatcher]:
    session = AiohttpSession(proxy=config.telegram_proxy) if config.telegram_proxy else None
    bot = Bot(token=config.bot_token, session=session)
    dp = Dispatcher(storage=MemoryStorage())

    admin_only = AdminOnlyMiddleware(config.admin_ids)
    dp.message.middleware(admin_only)
    dp.callback_query.middleware(admin_only)

    dp.include_router(handlers.router)

    dp["db"] = db
    dp["config"] = config
    dp["notifier"] = notifier
    dp["backup_task"] = backup_task
    dp["topic_binding"] = topic_binding
    dp["live_view"] = live_view

    return bot, dp
