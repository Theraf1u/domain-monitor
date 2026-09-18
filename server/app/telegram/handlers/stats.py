"""The "📊 Статистика" section: unified periods (incl. custom range),
period-over-period comparison, and the per-node stats screen.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import runtime_settings
from app.config import Config
from app.database import Database
from app.live_view import LiveViewManager
from app.telegram import keyboards as kb
from app.telegram.formatters import format_bytes, format_relative_time
from app.telegram.handlers.common import _parse_date_range, _period_range
from app.telegram.states import Inputs

router = Router()

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _database_size_bytes(config: Config) -> int:
    base = config.database_path
    return sum(
        os.path.getsize(candidate)
        for candidate in (base, base + "-wal", base + "-shm")
        if os.path.isfile(candidate)
    )


def _format_delta(current: int, previous: int) -> str:
    """"было 40, стало 52" -> "🔺 +30%". previous == 0 can't express a
    percentage (division by zero), so it's called out as "новое" (some
    activity where there was none) rather than showing a fake 0%/∞."""
    if previous == 0:
        return "🔺 новое" if current > 0 else "▪️ 0"
    pct = (current - previous) / previous * 100
    arrow = "🔺" if pct > 0 else ("🔻" if pct < 0 else "▪️")
    return f"{arrow} {pct:+.0f}%"


_SOURCE_LABELS = {"tls_sni": "TLS SNI", "dns": "DNS"}


def _stats_text(
    db: Database, config: Config, period: str, custom_range: tuple[datetime, datetime] | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    tz_offset = runtime_settings.get_timezone_offset_minutes(db, config)
    if period == "custom" and custom_range is not None:
        since, until = custom_range
        period_label = f"{since.date()} — {(until - timedelta(seconds=1)).date()}"
    else:
        since, until = _period_range(period, now, tz_offset)
        period_label = dict(kb.DOMAIN_PERIODS).get(period, period)

    offline_after = runtime_settings.get_node_offline_after_seconds(db, config)
    nodes = db.list_nodes()
    online = sum(1 for n in nodes if n.is_online(offline_after, now))

    events_count = db.count_events_since(since or _EPOCH, until)
    new_domains = db.count_domains(since=since, until=until)
    total_domains = db.count_domains()
    top_nodes = db.top_active_nodes_since(since or _EPOCH, limit=3)
    top_domains = (
        db.top_domains_since(since, until, limit=5) if since is not None
        else [(d.domain, d.hits) for d in db.top_domains(limit=5)]
    )
    source_dist = db.source_distribution_since(since or _EPOCH, until)
    offline_incidents = db.count_offline_incidents(since, until)

    watch_hits = sum(r.hits_count for r in db.list_filter_rules("watch"))
    ignore_hits = sum(r.hits_count for r in db.list_filter_rules("ignore"))
    allow_hits = sum(r.hits_count for r in db.list_filter_rules("allow"))

    rate_line = ""
    if since is not None:
        hours = max(((until or now) - since).total_seconds() / 3600, 1 / 60)
        rate_line = f"Скорость: ~{events_count / hours:.1f} событий/час\n"

    comparison_block = ""
    if since is not None:
        duration = (until or now) - since
        prev_since, prev_until = since - duration, since
        prev_events = db.count_events_since(prev_since, prev_until)
        prev_new_domains = db.count_domains(since=prev_since, until=prev_until)
        comparison_block = (
            f"\nПо сравнению с предыдущим периодом такой же длины:\n"
            f"  События: {_format_delta(events_count, prev_events)}\n"
            f"  Новые домены: {_format_delta(new_domains, prev_new_domains)}\n"
        )

    top_lines = "\n".join(f"  {i + 1}. {name} — {count}" for i, (name, count) in enumerate(top_nodes)) or "  —"
    top_domains_lines = "\n".join(f"  {i + 1}. {d} — {c}" for i, (d, c) in enumerate(top_domains)) or "  —"
    source_lines = "\n".join(
        f"  {_SOURCE_LABELS.get(src, src)}: {count}" for src, count in source_dist
    ) or "  —"

    buffered_total = sum(n.agent_buffer_size or 0 for n in nodes)
    buffered_line = (
        f"В буферах агентов (собрано, не отправлено): {buffered_total}\n" if buffered_total else ""
    )
    db_size_line = f"Размер базы доменов: {format_bytes(_database_size_bytes(config))}\n"

    return (
        f"📊 <b>Статистика</b> ({period_label})\n\n"
        f"Ноды онлайн: {online}/{len(nodes)}\n"
        f"Уникальных доменов (всего): {total_domains}\n"
        f"Новых доменов за период: {new_domains}\n"
        f"Событий за период: {events_count}\n"
        f"{rate_line}"
        f"Офлайн-инцидентов за период: {offline_incidents}\n"
        f"{buffered_line}"
        f"{db_size_line}"
        f"{comparison_block}"
        f"\nПо источникам за период:\n{source_lines}\n"
        f"\nWatch/Ignore/Allow хитов (всего, не по периоду): {watch_hits}/{ignore_hits}/{allow_hits}\n"
        f"\nАктивные ноды за период:\n{top_lines}\n"
        f"\nТоп доменов за период:\n{top_domains_lines}"
    )


async def _get_stats_custom_range(state: FSMContext) -> tuple[datetime, datetime] | None:
    data = await state.get_data()
    since_iso, until_iso = data.get("stats_custom_since"), data.get("stats_custom_until")
    if since_iso is None or until_iso is None:
        return None
    return datetime.fromisoformat(since_iso), datetime.fromisoformat(until_iso)


@router.callback_query(F.data == "stats")
async def cb_stats(call: CallbackQuery, db: Database, config: Config, live_view: LiveViewManager) -> None:
    live_active = live_view.is_active(call.message.chat.id, call.message.message_id)
    await call.message.edit_text(
        _stats_text(db, config, "today"), parse_mode="HTML", reply_markup=kb.stats_menu("today", live_active),
    )
    await call.answer()


@router.callback_query(F.data.startswith("stats_period:"))
async def cb_stats_period(
    call: CallbackQuery, state: FSMContext, db: Database, config: Config, live_view: LiveViewManager,
) -> None:
    period = call.data.split(":", 1)[1]
    custom_range = await _get_stats_custom_range(state) if period == "custom" else None
    live_active = live_view.is_active(call.message.chat.id, call.message.message_id)
    await call.message.edit_text(
        _stats_text(db, config, period, custom_range), parse_mode="HTML",
        reply_markup=kb.stats_menu(period, live_active),
    )
    await call.answer()


@router.callback_query(F.data == "stats_custom")
async def cb_stats_custom_prompt(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Inputs.waiting_for_stats_custom_range)
    await call.message.edit_text(
        "Введите период в формате <code>ГГГГ-ММ-ДД</code> (один день) или "
        "<code>ГГГГ-ММ-ДД ГГГГ-ММ-ДД</code> (с — по, включительно):",
        parse_mode="HTML", reply_markup=kb.cancel_input("stats_period:today"),
    )
    await call.answer()


@router.message(Inputs.waiting_for_stats_custom_range)
async def on_stats_custom_range_input(message: Message, state: FSMContext, db: Database, config: Config) -> None:
    await state.set_state(None)
    parsed = _parse_date_range((message.text or "").strip())
    if parsed is None:
        await message.answer(
            "Не понял формат. Пример: <code>2026-09-01</code> или <code>2026-09-01 2026-09-15</code>.",
            parse_mode="HTML", reply_markup=kb.back_button("stats_period:today"),
        )
        return
    since, until = parsed
    await state.update_data(stats_custom_since=since.isoformat(), stats_custom_until=until.isoformat())
    await message.answer(
        _stats_text(db, config, "custom", (since, until)), parse_mode="HTML",
        reply_markup=kb.stats_menu("custom"),
    )


@router.callback_query(F.data == "stats_node_menu")
async def cb_stats_node_menu(call: CallbackQuery, db: Database) -> None:
    nodes = db.list_nodes()
    if not nodes:
        await call.answer("Пока нет ни одной ноды", show_alert=True)
        return
    await call.message.edit_text("📡 <b>Статистика по ноде</b>\n\nВыберите ноду:", parse_mode="HTML", reply_markup=kb.stats_node_select_menu(nodes))
    await call.answer()


def _node_stats_text(db: Database, config: Config, node, period: str) -> str:
    now = datetime.now(timezone.utc)
    tz_offset = runtime_settings.get_timezone_offset_minutes(db, config)
    since, until = _period_range(period, now, tz_offset)
    period_label = dict(kb.DOMAIN_PERIODS).get(period, period)

    events_count = db.count_events_since(since or _EPOCH, until, node_id=node.id)
    unique_domains = db.count_domains(node_id=node.id)
    new_domains = db.count_domains(node_id=node.id, since=since, until=until)
    top_domains = db.list_domains(node_id=node.id, order_by="hits", limit=5)
    source_dist = db.source_distribution_since(since or _EPOCH, until, node_id=node.id)
    offline_incidents = db.count_offline_incidents(since, until, node_id=node.id)

    rate_line = ""
    if since is not None:
        hours = max(((until or now) - since).total_seconds() / 3600, 1 / 60)
        rate_line = f"Скорость: ~{events_count / hours:.1f} событий/час\n"

    if node.buffer_bytes is not None and node.buffer_limit_bytes:
        buffer_line = (
            f"Буфер: {node.agent_buffer_size or 0} событий, "
            f"{format_bytes(node.buffer_bytes)} / {format_bytes(node.buffer_limit_bytes)}\n"
        )
    elif node.agent_buffer_size:
        buffer_line = f"Буфер: {node.agent_buffer_size} событий\n"
    else:
        buffer_line = ""

    top_lines = "\n".join(f"  {i + 1}. {d.domain} — {d.hits}" for i, d in enumerate(top_domains)) or "  —"
    source_lines = "\n".join(f"  {_SOURCE_LABELS.get(s, s)}: {c}" for s, c in source_dist) or "  —"

    return (
        f"📊 <b>Статистика:</b> {node.name} ({period_label})\n\n"
        f"Последний heartbeat: {format_relative_time(node.last_heartbeat_at)}\n"
        f"Событий за период: {events_count}\n"
        f"{rate_line}"
        f"Уникальных доменов (всего с этой ноды): {unique_domains}\n"
        f"Новых доменов за период: {new_domains}\n"
        f"Офлайн-инцидентов за период: {offline_incidents}\n"
        f"{buffer_line}"
        f"\nПо источникам за период:\n{source_lines}\n"
        f"\nТоп доменов (всего с этой ноды):\n{top_lines}"
    )


@router.callback_query(F.data.startswith("stats_node:"))
async def cb_stats_node(call: CallbackQuery, db: Database, config: Config) -> None:
    _, node_id, period = call.data.split(":")
    node = db.get_node(int(node_id))
    if node is None:
        await call.answer("Нода не найдена (возможно, удалена)", show_alert=True)
        return
    await call.message.edit_text(
        _node_stats_text(db, config, node, period), parse_mode="HTML",
        reply_markup=kb.stats_node_menu(node.id, period),
    )
    await call.answer()
