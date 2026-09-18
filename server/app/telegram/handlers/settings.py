"""The "⚙️ Настройки" section (spec 2.0 Part 2, section 1): a hub screen
plus Сервер/Ноды по умолчанию/Часовой пояс/Хранение данных/Администраторы/
Безопасность/Диагностика/О системе.

Docker/UFW status, system updates, and migration aren't reachable from
here yet - those are later stages in the spec's own execution order
(6/7), and showing a status/action screen for something the container
genuinely cannot check (no Docker socket mounted, by design - see
docker-compose.yml) would mean fabricating data. Where a check is
honestly outside what a container can see, the screen says so and points
at `doctor.sh` on the host instead of guessing.
"""
from __future__ import annotations

import shutil
import socket
import sys
from datetime import datetime, timezone

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import runtime_settings
from app.config import Config
from app.database import Database
from app.notifier import Notifier
from app.telegram import keyboards as kb
from app.telegram.formatters import format_bytes, format_datetime_local, format_duration, format_timezone_offset
from app.telegram.handlers.stats import _database_size_bytes
from app.telegram.states import Inputs
from app.version import SERVER_VERSION, git_revision, uptime_seconds

router = Router()


@router.callback_query(F.data == "settings")
async def cb_settings(call: CallbackQuery, db: Database, config: Config, notifier: Notifier) -> None:
    await call.message.edit_text(
        "⚙️ <b>Настройки</b>", parse_mode="HTML",
        reply_markup=kb.settings_menu(notifier.is_watchlist_enabled()),
    )
    await call.answer()


@router.callback_query(F.data == "settings_toggle_watchlist")
async def cb_settings_toggle_watchlist(call: CallbackQuery, db: Database, config: Config, notifier: Notifier) -> None:
    notifier.set_watchlist_enabled(not notifier.is_watchlist_enabled())
    await cb_settings(call, db, config, notifier)


# ------------------------------------------------------------------
# 1.1 Сервер
# ------------------------------------------------------------------

@router.callback_query(F.data == "settings_server")
async def cb_settings_server(call: CallbackQuery, db: Database, config: Config, bot: Bot) -> None:
    free_bytes = shutil.disk_usage(config.data_dir).free
    db_size = _database_size_bytes(config)
    telegram_line = "🟢 бот на связи (это сообщение и есть проверка)"
    try:
        me = await bot.get_me()
        telegram_line = f"🟢 подключён как @{me.username}"
    except Exception:
        telegram_line = "🔴 не удалось получить данные бота от Telegram API"

    text = (
        "🖥 <b>Сервер</b>\n\n"
        f"Версия: {SERVER_VERSION} (git {git_revision()})\n"
        f"Uptime процесса: {format_duration(uptime_seconds())}\n"
        f"PUBLIC_URL: <code>{config.public_url}</code>\n"
        f"API порт: {config.port}\n"
        f"Путь к БД: <code>{config.database_path}</code>\n"
        f"Размер БД: {format_bytes(db_size)}\n"
        f"Свободно на диске: {format_bytes(free_bytes)}\n"
        f"Telegram: {telegram_line}\n"
        f"Docker / Compose / UFW: недоступно для проверки изнутри контейнера "
        f"(нет доступа к Docker-сокету по архитектуре) - запусти "
        f"<code>domain-monitor-server doctor</code> на самом сервере."
    )
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb.settings_server_menu())
    await call.answer()


# ------------------------------------------------------------------
# 1.1 continued / 1.3 Ноды по умолчанию (offline timeout + buffer thresholds)
# ------------------------------------------------------------------

@router.callback_query(F.data == "settings_node_defaults")
async def cb_settings_node_defaults(call: CallbackQuery, db: Database, config: Config) -> None:
    offline_seconds = runtime_settings.get_node_offline_after_seconds(db, config)
    warning_pct = runtime_settings.get_buffer_warning_pct(db)
    critical_pct = runtime_settings.get_buffer_critical_pct(db)
    await call.message.edit_text(
        "📡 <b>Ноды по умолчанию</b>\n\nЭти значения применяются ко всем нодам, если не переопределены.",
        parse_mode="HTML",
        reply_markup=kb.settings_node_defaults_menu(offline_seconds, f"{warning_pct}%/{critical_pct}%"),
    )
    await call.answer()


@router.callback_query(F.data == "settings_offline")
async def cb_settings_offline(call: CallbackQuery, db: Database, config: Config) -> None:
    offline_seconds = runtime_settings.get_node_offline_after_seconds(db, config)
    await call.message.edit_text(
        "Через сколько секунд без heartbeat нода считается offline?",
        reply_markup=kb.settings_offline_menu(offline_seconds),
    )
    await call.answer()


@router.callback_query(F.data.startswith("settings_offline_set:"))
async def cb_settings_offline_set(call: CallbackQuery, db: Database, config: Config) -> None:
    seconds = int(call.data.split(":", 1)[1])
    runtime_settings.set_node_offline_after_seconds(db, seconds)
    await call.answer(f"Offline через: {seconds} сек")
    await cb_settings_offline(call, db, config)


@router.callback_query(F.data == "settings_offline_custom")
async def cb_settings_offline_custom_prompt(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Inputs.waiting_for_offline_seconds)
    await call.message.edit_text(
        "Через сколько секунд без heartbeat нода считается offline? (10-86400)",
        reply_markup=kb.cancel_input("settings_offline"),
    )
    await call.answer()


@router.message(Inputs.waiting_for_offline_seconds)
async def on_offline_seconds_input(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    raw = (message.text or "").strip()
    if not raw.isdigit() or not (10 <= int(raw) <= 86400):
        await message.answer(
            "Нужно число секунд от 10 до 86400. Попробуйте снова.", reply_markup=kb.back_button("settings_offline"),
        )
        return
    runtime_settings.set_node_offline_after_seconds(db, int(raw))
    await message.answer(f"✅ Offline через: {raw} сек.", reply_markup=kb.back_button("settings_node_defaults"))


@router.callback_query(F.data == "settings_buffer")
async def cb_settings_buffer(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Inputs.waiting_for_buffer_thresholds)
    await call.message.edit_text(
        "Пороги заполнения буфера ноды в процентах, формат <code>warning critical</code>, "
        "например <code>70 90</code>:",
        parse_mode="HTML", reply_markup=kb.cancel_input("settings_node_defaults"),
    )
    await call.answer()


@router.message(Inputs.waiting_for_buffer_thresholds)
async def on_buffer_thresholds_input(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    parts = (message.text or "").strip().split()
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        await message.answer(
            "Нужны два числа: warning и critical, например <code>70 90</code>. Попробуйте снова.",
            parse_mode="HTML", reply_markup=kb.back_button("settings_node_defaults"),
        )
        return
    warning_pct, critical_pct = int(parts[0]), int(parts[1])
    if not (1 <= warning_pct < critical_pct <= 100):
        await message.answer(
            "Нужно 1 ≤ warning < critical ≤ 100. Попробуйте снова.", reply_markup=kb.back_button("settings_node_defaults"),
        )
        return
    runtime_settings.set_buffer_thresholds(db, warning_pct, critical_pct)
    await message.answer(
        f"✅ Пороги буфера: warning {warning_pct}% / critical {critical_pct}%",
        reply_markup=kb.back_button("settings_node_defaults"),
    )


# ------------------------------------------------------------------
# 1.2 Хранение данных
# ------------------------------------------------------------------

@router.callback_query(F.data == "settings_retention_menu")
async def cb_settings_retention_menu(call: CallbackQuery, db: Database, config: Config) -> None:
    retention_days = runtime_settings.get_event_retention_days(db, config)
    await call.message.edit_text(
        "🗄 <b>Хранение данных</b>\n\nСколько дней хранить детальную историю событий "
        "(0 — хранить всегда; не влияет на список доменов, только на детальную историю):",
        parse_mode="HTML", reply_markup=kb.settings_retention_menu(retention_days),
    )
    await call.answer()


@router.callback_query(F.data.startswith("settings_retention_set:"))
async def cb_settings_retention_set(call: CallbackQuery, db: Database, config: Config) -> None:
    days = int(call.data.split(":", 1)[1])
    runtime_settings.set_event_retention_days(db, days)
    await call.answer("Хранение обновлено" if days else "Хранить всегда")
    await cb_settings_retention_menu(call, db, config)


@router.callback_query(F.data == "settings_retention_custom")
async def cb_settings_retention_custom_prompt(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Inputs.waiting_for_retention_days)
    await call.message.edit_text(
        "Сколько дней хранить детальную историю событий? (0–3650, 0 — хранить всегда)",
        reply_markup=kb.cancel_input("settings_retention_menu"),
    )
    await call.answer()


@router.message(Inputs.waiting_for_retention_days)
async def on_retention_days_input(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) > 3650:
        await message.answer(
            "Нужно целое число дней (0–3650). Попробуйте снова.", reply_markup=kb.back_button("settings_retention_menu"),
        )
        return
    runtime_settings.set_event_retention_days(db, int(raw))
    await message.answer(f"✅ Хранение событий: {raw} дн.", reply_markup=kb.back_button("settings_retention_menu"))


# ------------------------------------------------------------------
# 1.3 Часовой пояс (unchanged from Part 1, section 7.2)
# ------------------------------------------------------------------

@router.callback_query(F.data == "settings_timezone")
async def cb_settings_timezone(call: CallbackQuery, state: FSMContext, db: Database, config: Config) -> None:
    tz_offset = runtime_settings.get_timezone_offset_minutes(db, config)
    now = datetime.now(timezone.utc)
    await state.set_state(Inputs.waiting_for_timezone_offset)
    await call.message.edit_text(
        f"🌍 <b>Часовой пояс</b>\n\n"
        f"Текущий: {format_timezone_offset(tz_offset)} (сейчас там {format_datetime_local(now, tz_offset)})\n\n"
        f"Введите смещение от UTC в часах, например <code>+3</code> (Москва) или <code>-5</code>, "
        f"или <code>0</code> для UTC:",
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


# ------------------------------------------------------------------
# 1.4? Администраторы (read-only - admins come from ADMIN_ID in .env, not
# the DB, and changing them from inside the container would mean editing
# the host's .env from the bot - the exact pattern the spec's migration
# section explicitly forbids for the agent's runtime config, and the same
# reasoning applies here: who has admin access is not something to change
# via a self-modifying container process).
# ------------------------------------------------------------------

@router.callback_query(F.data == "settings_admins")
async def cb_settings_admins(call: CallbackQuery, config: Config) -> None:
    admins = "\n".join(f"  • <code>{a}</code>" for a in config.admin_ids)
    text = (
        "👥 <b>Администраторы</b>\n\n"
        f"{admins}\n\n"
        "Бот отвечает только этим Telegram ID - все остальные молча игнорируются.\n\n"
        "Список задаётся переменной <code>ADMIN_ID</code> в <code>.env</code> на сервере "
        "(через запятую для нескольких админов) и требует перезапуска контейнера - "
        "изменить его из самого бота нельзя (кто имеет доступ к управлению - не то, "
        "что процесс должен уметь менять сам себе)."
    )
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb.settings_admins_menu())
    await call.answer()


# ------------------------------------------------------------------
# 1.? Безопасность - informational: the guarantees already in place
# (spec section 9), not togglable settings.
# ------------------------------------------------------------------

@router.callback_query(F.data == "settings_security")
async def cb_settings_security(call: CallbackQuery) -> None:
    text = (
        "🔐 <b>Безопасность</b>\n\n"
        "• Node token хранится в БД только как SHA-256 hash - plaintext токен "
        "показывается один раз при создании/перевыпуске и не сохраняется.\n"
        "• BOT_TOKEN, NODE_TOKEN, ADMIN_API_KEY никогда не пишутся в лог сервера.\n"
        "• Необработанные ошибки бота логируются со scrub секретов "
        "(см. app/telegram/error_handler.py) - traceback в Telegram-сообщении "
        "пользователю не показывается, только общий текст.\n"
        "• Архитектура Agent → Server - только authenticated HTTPS; нет SSH-сервера "
        "в Agent, нет произвольного удалённого выполнения команд через heartbeat.\n"
        "• Бот отвечает только ID из ADMIN_ID."
    )
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb.settings_security_menu())
    await call.answer()


# ------------------------------------------------------------------
# 1.4 Диагностика (spec section 1.4) - only checks actually possible from
# inside the container (no Docker socket mounted); everything else points
# at `domain-monitor-server doctor` on the host, which already covers
# Docker/Compose/UFW/firewall.
# ------------------------------------------------------------------

@router.callback_query(F.data == "settings_diagnostics")
async def cb_settings_diagnostics(call: CallbackQuery) -> None:
    await call.message.edit_text(
        "🩺 <b>Диагностика</b>\n\n"
        "Проверяет то, что видно изнутри контейнера: БД, свободное место, Telegram API.\n"
        "Docker/Compose/UFW/сеть - запусти <code>domain-monitor-server doctor</code> на самом сервере.",
        parse_mode="HTML", reply_markup=kb.settings_diagnostics_menu(),
    )
    await call.answer()


@router.callback_query(F.data == "settings_diagnostics_run")
async def cb_settings_diagnostics_run(call: CallbackQuery, db: Database, config: Config, bot: Bot) -> None:
    await call.answer("Проверяю...")
    lines = ["🩺 <b>Результат диагностики</b>\n"]

    lines.append(f"Python: {sys.version.split()[0]}")

    free_bytes = shutil.disk_usage(config.data_dir).free
    free_mb = free_bytes // (1024 * 1024)
    disk_ok = "✓" if free_mb >= 500 else "✗"
    lines.append(f"{disk_ok} Место на диске: {format_bytes(free_bytes)}" + ("" if free_mb >= 500 else " (мало!)"))

    integrity_ok = db.integrity_check()
    lines.append(("✓" if integrity_ok else "✗") + f" Целостность БД: {'ok' if integrity_ok else 'ПРОВАЛЕНА'}")
    lines.append(f"  Применено миграций: {db.applied_migrations_count()}")

    try:
        me = await bot.get_me()
        lines.append(f"✓ Telegram API: доступен (@{me.username})")
    except Exception as exc:
        lines.append(f"✗ Telegram API: недоступен ({type(exc).__name__})")

    if not config.public_url or "localhost" in config.public_url:
        lines.append("✗ PUBLIC_URL похож на localhost - новые ноды не смогут достучаться извне")
    else:
        lines.append(f"✓ PUBLIC_URL задан: {config.public_url}")

    lines.append(
        "\nDOCKER / NETWORK / SECURITY (firewall) - недоступно для проверки изнутри "
        "контейнера. Запусти <code>domain-monitor-server doctor</code> на сервере."
    )

    await call.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=kb.settings_diagnostics_menu())


# ------------------------------------------------------------------
# 1.5 О системе
# ------------------------------------------------------------------

@router.callback_query(F.data == "settings_about")
async def cb_settings_about(call: CallbackQuery, db: Database, config: Config) -> None:
    node_count = len(db.list_nodes())
    db_size = _database_size_bytes(config)
    text = (
        "ℹ️ <b>О системе</b>\n\n"
        f"Domain Monitor: {SERVER_VERSION}\n"
        f"Git revision: {git_revision()}\n"
        f"DB schema (применено миграций): {db.applied_migrations_count()}\n"
        f"Python: {sys.version.split()[0]}\n"
        f"Нод зарегистрировано: {node_count}\n"
        f"Размер БД: {format_bytes(db_size)}\n"
        f"Uptime процесса: {format_duration(uptime_seconds())}\n"
        f"Hostname: <code>{socket.gethostname()}</code>"
    )
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb.settings_about_menu())
    await call.answer()