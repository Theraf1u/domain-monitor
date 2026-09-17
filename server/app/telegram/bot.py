"""Builds the aiogram Bot/Dispatcher and wires shared objects (db, config,
notifier) into every handler via workflow data, the same pattern as the
single-node MVP."""
from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, MenuButtonCommands

from app.config import Config
from app.database import Database
from app.notifier import Notifier
from app.telegram import handlers
from app.telegram.middleware import AdminOnlyMiddleware


async def configure_bot_commands(bot: Bot) -> None:
    """Registers the /start command hint and the persistent "Меню" button
    via the Bot API, so a fresh BotFather bot works out of the box - no
    manual /setcommands or /setmenubutton step in BotFather needed. Runs
    on every startup; idempotent (Telegram just overwrites with the same
    values if they're already set)."""
    await bot.set_my_commands([BotCommand(command="start", description="Запустить бота")])
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())


def build_bot_and_dispatcher(config: Config, db: Database, notifier: Notifier) -> tuple[Bot, Dispatcher]:
    session = AiohttpSession(proxy=config.telegram_proxy) if config.telegram_proxy else None
    bot = Bot(token=config.bot_token, session=session)
    dp = Dispatcher(storage=MemoryStorage())

    admin_only = AdminOnlyMiddleware(config.admin_id)
    dp.message.middleware(admin_only)
    dp.callback_query.middleware(admin_only)

    dp.include_router(handlers.router)

    dp["db"] = db
    dp["config"] = config
    dp["notifier"] = notifier

    return bot, dp
