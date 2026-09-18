"""Unified error handling for the Telegram bot (spec 2.0 Part 1, section 4).

Without this, an unhandled exception inside a handler leaves the tap that
triggered it with no visible reaction (aiogram logs it and moves on) - the
admin sees nothing happen and has no idea whether to retry or that
anything went wrong at all. Registered as the dispatcher's global error
handler (see bot.py), so it catches anything any handler didn't already
deal with itself, without every individual handler needing its own
try/except.
"""
from __future__ import annotations

import logging
import re
import traceback

from aiogram.types import ErrorEvent

from app.config import Config

logger = logging.getLogger(__name__)

# Catches a bot-token-shaped string wherever it ends up (an exception
# message that happened to include a URL with the token in it, say) even
# if it isn't literally config.bot_token - e.g. a stale/wrong token being
# tested.
_BOT_TOKEN_SHAPE_RE = re.compile(r"\d{6,}:[A-Za-z0-9_-]{30,}")


def scrub_secrets(text: str, *known_secrets: str | None) -> str:
    """Replaces known secret values and anything merely shaped like a bot
    token with a placeholder - applied to both what's logged and whatever
    might reach the user, so a secret accidentally interpolated into an
    exception's own message never round-trips back out either way."""
    scrubbed = text
    for secret in known_secrets:
        if secret:
            scrubbed = scrubbed.replace(secret, "***")
    return _BOT_TOKEN_SHAPE_RE.sub("***:***", scrubbed)


def _format_scrubbed_traceback(exc: BaseException, known_secrets: list[str]) -> str:
    tb_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return scrub_secrets(tb_text, *known_secrets)


async def on_dispatcher_error(event: ErrorEvent, config: Config) -> bool:
    """Registered via `@dp.errors()` in bot.py. Returning True tells
    aiogram the error was handled - it stops there instead of also
    logging its own generic "Cause exception while process update" line
    for the same exception."""
    secrets = [config.bot_token, config.admin_api_key]
    logger.error(
        "Unhandled error in Telegram handler:\n%s", _format_scrubbed_traceback(event.exception, secrets),
    )

    message = event.update.message
    if message is None and event.update.callback_query is not None:
        message = event.update.callback_query.message
    if message is None:
        return True  # no chat to reply into (e.g. an edited_message update) - logged, nothing more to do

    try:
        await message.answer(
            "⚠️ Что-то пошло не так при обработке этого действия.\n\n"
            "Попробуйте повторить его ещё раз, или откройте меню заново командой /start. "
            "Если ошибка повторяется - подробности есть в логах сервера.",
        )
    except Exception:
        logger.exception("Failed to notify the admin about the error above")
    return True
