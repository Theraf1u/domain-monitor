"""Builds the aiogram Bot/Dispatcher and wires shared objects (db, config,
notifier) into every handler via workflow data, the same pattern as the
single-node MVP."""
from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from app.config import Config
from app.database import Database
from app.notifier import Notifier
from app.telegram import handlers
from app.telegram.middleware import AdminOnlyMiddleware


def build_bot_and_dispatcher(config: Config, db: Database, notifier: Notifier) -> tuple[Bot, Dispatcher]:
    assert config.bot_token and config.admin_id is not None

    bot = Bot(token=config.bot_token)
    dp = Dispatcher(storage=MemoryStorage())

    admin_only = AdminOnlyMiddleware(config.admin_id)
    dp.message.middleware(admin_only)
    dp.callback_query.middleware(admin_only)

    dp.include_router(handlers.router)

    dp["db"] = db
    dp["config"] = config
    dp["notifier"] = notifier

    return bot, dp
