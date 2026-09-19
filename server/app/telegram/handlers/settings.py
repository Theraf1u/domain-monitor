"""The "⚙️ Настройки" section (spec 2.0 Part 2, section 1): a hub screen
plus Сервер/Ноды по умолчанию/Часовой пояс/Хранение данных/Администраторы/
Безопасность/Диагностика/Обновления/Миграция/О системе.

Docker Engine status is shown via a read-only socket mount (see
docker-compose.yml and app/docker_info.py) - version and container
count only, never anything that could act on a container. Compose
version and UFW status stay genuinely out of reach even with that
socket (Compose is a client-side CLI plugin the daemon knows nothing
about; UFW isn't a Docker concept at all) - those screens say so and
point at `doctor.sh` on the host instead of guessing.
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

    docker_line = await _docker_status_line()

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
        f"Docker: {docker_line}\n"
        f"Compose / UFW: недоступно изнутри контейнера даже с Docker-сокетом "
        f"(Compose - клиентский плагин, UFW вообще не про Docker) - запусти "
        f"<code>domain-monitor-server doctor</code> на самом сервере."
    )
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb.settings_server_menu())
    await call.answer()


async def _docker_status_line() -> str:
    from app import docker_info

    version = await docker_info.engine_version()
    if version is None:
        return "🔴 недоступен (нет сокета /var/run/docker.sock в контейнере - обнови docker-compose.yml и пересоздай контейнер)"
    summary = await docker_info.container_summary()
    if summary is None:
        return f"🟢 Engine {version}"
    running, total = summary
    return f"🟢 Engine {version}, контейнеров: {running}/{total} запущено"


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

    docker_line = await _docker_status_line()
    lines.append(("✓" if docker_line.startswith("🟢") else "✗") + f" Docker: {docker_line}")

    lines.append(
        "\nCompose / NETWORK / SECURITY (firewall) - недоступно для проверки изнутри "
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


# ------------------------------------------------------------------
# spec 2.0 Part 2, section 6 - 🔄 Обновления
#
# Checking for an update is a plain outbound HTTPS call (GitHub's public
# API, no auth needed) - the bot container can do that fine. Actually
# TRIGGERING an update (git pull + rebuild + restart this very container)
# is the same "can't act on itself from inside itself" limitation as
# Миграция above, so this screen only informs and points at the CLI.
# ------------------------------------------------------------------

_GITHUB_LATEST_COMMIT_URL = "https://api.github.com/repos/Theraf1u/domain-monitor/commits/main"


async def _updates_text(config: Config) -> str:
    current_rev = git_revision()
    lines = [
        "🔄 <b>Обновления</b>\n",
        f"Текущая версия: {SERVER_VERSION}",
        f"Текущая ревизия: <code>{current_rev}</code>",
    ]
    if current_rev == "unknown":
        lines.append(
            "\n⚠️ Ревизия неизвестна (сервер собран без GIT_REV - переустанови "
            "через актуальный install.sh/update.sh, там это уже исправлено) - "
            "сравнить с последней версией на GitHub не могу."
        )
        return "\n".join(lines)

    import aiohttp

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                _GITHUB_LATEST_COMMIT_URL, timeout=aiohttp.ClientTimeout(total=8),
                headers={"Accept": "application/vnd.github+json"},
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"GitHub API вернул {resp.status}")
                data = await resp.json()
    except Exception as exc:
        lines.append(f"\n⚠️ Не удалось проверить обновления на GitHub: {type(exc).__name__}")
        return "\n".join(lines)

    latest_sha = data.get("sha", "")[:7]
    latest_msg = (data.get("commit", {}).get("message") or "").splitlines()[0] if data.get("commit") else ""
    if not latest_sha:
        lines.append("\n⚠️ GitHub API вернул неожиданный ответ - сравнить не удалось.")
    elif latest_sha == current_rev or latest_sha.startswith(current_rev) or current_rev.startswith(latest_sha):
        lines.append("\n✅ Установлена последняя версия.")
    else:
        lines.append(
            f"\n🆕 Доступно обновление: <code>{latest_sha}</code> - {latest_msg}\n\n"
            "Обновить (заберёт код, пересоберёт, перезапустит):\n"
            "<code>domain-monitor-server update</code>"
        )
    lines.append(
        "\nАвтообновление по расписанию включается/выключается с самого "
        "сервера (там же лог) - изнутри контейнера это не видно:\n"
        "<code>dm</code> → 6) Управление скриптом"
    )
    return "\n".join(lines)


@router.callback_query(F.data == "settings_updates")
async def cb_settings_updates(call: CallbackQuery, config: Config) -> None:
    await call.answer("Проверяю...")
    text = await _updates_text(config)
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb.settings_updates_menu())


@router.callback_query(F.data == "settings_updates_check")
async def cb_settings_updates_check(call: CallbackQuery, config: Config) -> None:
    await cb_settings_updates(call, config)


@router.callback_query(F.data == "settings_updates_how_to_update")
async def cb_settings_updates_how_to_update(call: CallbackQuery) -> None:
    # Same self-acting limitation as Миграция (see cb_migration_finish_help
    # above): pulling new code, rebuilding the image, and restarting THIS
    # SAME container from inside itself isn't something this process can
    # safely do to itself - and doing it via the read-only Docker socket
    # would mean using it for exactly the create/rebuild actions
    # docker_info.py deliberately never issues (see that module's
    # docstring). Stays a printed command, not a fake button.
    await call.message.edit_text(
        "⬆️ <b>Обновить Server</b>\n\n"
        "Обновление (git pull + пересборка + перезапуск) нельзя запустить "
        "из самого бота - пришлось бы пересобирать и перезапускать тот же "
        "контейнер, в котором бот и работает. Выполни на сервере:\n\n"
        "<code>domain-monitor-server update</code>\n\n"
        "Заберёт актуальный код, пересоберёт образ, перезапустит контейнер. "
        "Бот станет недоступен на несколько секунд во время перезапуска.",
        parse_mode="HTML", reply_markup=kb.settings_updates_info_menu(),
    )
    await call.answer()


@router.callback_query(F.data == "settings_updates_autoupdate")
async def cb_settings_updates_autoupdate(call: CallbackQuery) -> None:
    # Autoupdate is a host crontab entry (see install.sh's
    # enable_auto_update()/CRON_MARKER) - the container has no access to
    # the host's crontab at all (no mount, no shared PID namespace), so
    # there is genuinely nothing here to read or toggle from inside it.
    await call.message.edit_text(
        "⏰ <b>Автообновление</b>\n\n"
        "Статус автообновления (crontab на хосте) не виден изнутри "
        "контейнера - управляется только на самом сервере:\n\n"
        "<code>dm</code> → 6) Управление скриптом → Автообновление\n\n"
        "Там же лог последнего запуска: <code>auto-update.log</code> "
        "в папке проекта.",
        parse_mode="HTML", reply_markup=kb.settings_updates_info_menu(),
    )
    await call.answer()


# ------------------------------------------------------------------
# 1.6 / spec 3.6 Миграция
#
# The bot runs inside the SAME container as the API, so it can read/write
# migration_jobs directly via `db` (no HTTP round-trip needed for that
# part) and it CAN make an outbound HTTP call to a migration target's own
# admin API to check node status - that's just a network request, no
# filesystem/SSH access required. What it genuinely cannot do from in
# here: start a migration (`migrate-to` needs host-level SSH to a brand
# new server) or finish one (flipping ITS OWN Telegram polling off means
# restarting this very container with a changed .env, which the bot has
# no access to edit - see runtime_config.py's docstring in the agent for
# the same container-boundary reasoning). Both are pointed at the CLI
# instead of faked.
# ------------------------------------------------------------------

async def _fetch_old_and_new_nodes(job: dict, config: Config) -> tuple[list, list] | None:
    """None means the comparison itself failed (network error reaching
    one side) - callers must show that honestly rather than rendering an
    empty/misleading node list."""
    import aiohttp

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"http://127.0.0.1:{config.port}/api/v1/nodes",
                headers={"X-Admin-Key": config.admin_api_key}, timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                old_nodes = await resp.json() if resp.status == 200 else []
            async with session.get(
                f"{job['target_url']}/api/v1/nodes",
                headers={"X-Admin-Key": config.admin_api_key}, timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                new_nodes = await resp.json() if resp.status == 200 else []
    except Exception:
        return None
    return old_nodes, new_nodes


async def _migration_status_text(job: dict, config: Config) -> str:
    lines = [
        "🚚 <b>Миграция</b>\n",
        f"Задача #{job['id']}, статус: <b>{job['status']}</b>",
        f"Цель: <code>{job['target_url']}</code>",
        f"Создана: {job['created_at']}",
    ]
    if job.get("error"):
        lines.append(f"Ошибка: {job['error']}")
    lines.append("")

    fetched = await _fetch_old_and_new_nodes(job, config)
    if fetched is None:
        lines.append("⚠️ Не удалось получить список нод (у себя или у цели) - показан только статус задачи.")
        return "\n".join(lines)
    old_nodes, new_nodes = fetched

    new_by_id = {n["id"]: n for n in new_nodes}
    migrated = waiting = offline = 0
    for n in old_nodes:
        target_copy = new_by_id.get(n["id"])
        if target_copy and target_copy.get("online"):
            lines.append(f"  ✅ {n['name']} - переключилась")
            migrated += 1
        elif n.get("online"):
            lines.append(f"  🔄 {n['name']} - ещё на старом сервере")
            waiting += 1
        else:
            lines.append(f"  🔴 {n['name']} - офлайн (не видна нигде)")
            offline += 1
    lines.append(f"\nИтого: {migrated} переключились, {waiting} ждут, {offline} офлайн (из {len(old_nodes)})")
    return "\n".join(lines)


@router.callback_query(F.data == "settings_migration")
async def cb_settings_migration(call: CallbackQuery, db: Database, config: Config) -> None:
    job = db.active_migration_job()
    if job is None:
        await call.message.edit_text(
            "🚚 <b>Миграция</b>\n\n"
            "Сейчас нет активной задачи миграции.\n\n"
            "Начать перенос на другой сервер можно только с самого сервера "
            "(нужен SSH-доступ к новому хосту, недоступный изнутри контейнера):\n"
            "<code>domain-monitor-server migrate-to root@NEW_HOST</code>",
            parse_mode="HTML", reply_markup=kb.settings_migration_none_menu(),
        )
        await call.answer()
        return
    text = await _migration_status_text(job, config)
    await call.message.edit_text(
        text, parse_mode="HTML", reply_markup=kb.settings_migration_active_menu(job["id"], job["status"]),
    )
    await call.answer()


@router.callback_query(F.data.startswith("mig_refresh:"))
async def cb_migration_refresh(call: CallbackQuery, db: Database, config: Config) -> None:
    await call.answer("Обновляю...")
    await cb_settings_migration(call, db, config)


@router.callback_query(F.data.startswith("mig_cutover_confirm:"))
async def cb_migration_cutover_confirm(call: CallbackQuery) -> None:
    job_id = call.data.split(":", 1)[1]
    await call.message.edit_text(
        "⚠️ <b>Начать переключение агентов?</b>\n\n"
        "Каждая нода при своём следующем heartbeat сама проверит новый сервер "
        "и переключится - без ручных действий на нодах. Этот сервер продолжит "
        "работать как обычно, пока ты не выполнишь завершение (Telegram-бот "
        "переключается отдельным шагом, командой на сервере).",
        parse_mode="HTML",
        reply_markup=kb.confirm_keyboard(
            "✅ Да, начать переключение", f"mig_cutover:{job_id}", f"mig_refresh:{job_id}",
        ),
    )
    await call.answer()


@router.callback_query(F.data.startswith("mig_cutover:"))
async def cb_migration_cutover(call: CallbackQuery, db: Database, config: Config) -> None:
    job_id = int(call.data.split(":", 1)[1])
    job = db.get_migration_job(job_id)
    if job is None or job["status"] != "standby":
        await call.answer("Задача не в статусе 'standby' - обнови экран.", show_alert=True)
        return
    db.set_migration_job_status(job_id, "cutover")
    await call.answer("Переключение начато")
    await cb_settings_migration(call, db, config)


@router.callback_query(F.data.startswith("mig_stragglers:"))
async def cb_migration_stragglers(call: CallbackQuery, db: Database, config: Config) -> None:
    """spec 3.5's [📋 Команды для оставшихся]: a node whose agent predates
    Migration 2.0 (or is just offline right now) will never auto-switch -
    it needs the manual `set-server` fallback, same as before this
    feature existed. Prints one ready command per node that hasn't
    already switched."""
    job_id = call.data.split(":", 1)[1]
    job = db.get_migration_job(int(job_id))
    if job is None:
        await call.answer("Задача не найдена.", show_alert=True)
        return

    fetched = await _fetch_old_and_new_nodes(job, config)
    if fetched is None:
        await call.answer("Не удалось получить список нод - попробуй обновить и повтори.", show_alert=True)
        return
    old_nodes, new_nodes = fetched
    new_by_id = {n["id"]: n for n in new_nodes}

    remaining = [n for n in old_nodes if not (new_by_id.get(n["id"]) and new_by_id[n["id"]].get("online"))]
    if not remaining:
        text = "📋 <b>Команды для оставшихся</b>\n\nВсе ноды уже переключились - выполнять вручную нечего."
    else:
        lines = [
            "📋 <b>Команды для оставшихся</b>\n",
            "Эти ноды ещё не переключились сами (офлайн сейчас или Agent "
            "слишком старый и не умеет авто-переключение). Выполни на каждой "
            "ноде - токен не меняется:\n",
        ]
        for n in remaining:
            lines.append(f"  # {n['name']}")
            lines.append(f"  <code>domain-monitor-agent set-server {job['target_url']}</code>")
        text = "\n".join(lines)

    await call.message.edit_text(
        text, parse_mode="HTML", reply_markup=kb.settings_migration_active_menu(int(job_id), job["status"]),
    )
    await call.answer()


@router.callback_query(F.data.startswith("mig_finish_help:"))
async def cb_migration_finish_help(call: CallbackQuery) -> None:
    job_id = call.data.split(":", 1)[1]
    await call.message.edit_text(
        "📋 <b>Как завершить миграцию</b>\n\n"
        "Переключение Telegram-бота нельзя сделать из самого бота (пришлось бы "
        "перезапустить этот же контейнер, а .env ему изнутри не изменить).\n\n"
        "Проверь, что все ноды переключились (🔄 Обновить), затем выполни на "
        "сервере:\n"
        f"<code>domain-monitor-server migrate-finish {job_id}</code>\n\n"
        "Это выключит Telegram-опрос здесь и включит на новом сервере - "
        "переписка с ботом продолжится уже там.",
        parse_mode="HTML", reply_markup=kb.settings_migration_active_menu(int(job_id), "cutover"),
    )
    await call.answer()


@router.callback_query(F.data.startswith("mig_cancel_confirm:"))
async def cb_migration_cancel_confirm(call: CallbackQuery) -> None:
    job_id = call.data.split(":", 1)[1]
    await call.message.edit_text(
        "⚠️ <b>Отменить миграцию?</b>\n\n"
        "Ноды, которые уже успели переключиться на новый сервер, там и "
        "останутся - отмена не двигает их обратно, только останавливает "
        "переключение оставшихся.",
        parse_mode="HTML",
        reply_markup=kb.confirm_keyboard(
            "❌ Да, отменить", f"mig_cancel:{job_id}", f"mig_refresh:{job_id}",
        ),
    )
    await call.answer()


@router.callback_query(F.data.startswith("mig_cancel:"))
async def cb_migration_cancel(call: CallbackQuery, db: Database, config: Config) -> None:
    job_id = int(call.data.split(":", 1)[1])
    job = db.get_migration_job(job_id)
    if job is None or job["status"] in ("completed", "cancelled", "failed"):
        await call.answer("Задача уже в конечном статусе.", show_alert=True)
        await cb_settings_migration(call, db, config)
        return
    db.set_migration_job_status(job_id, "cancelled")
    await call.answer("Миграция отменена")
    await cb_settings_migration(call, db, config)