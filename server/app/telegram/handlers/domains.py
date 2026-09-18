"""The "🌐 Домены" section: recent/top quick views, TXT period export, and
the full search/filter/list/card/history/CSV-JSON-export/data-management
flow from spec 2.0 Part 1 section 6.
"""
from __future__ import annotations

import csv
import html
import io
import json
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from app import runtime_settings
from app.config import Config
from app.database import Database
from app.filters import classify_domain
from app.live_view import LiveViewManager
from app.telegram import keyboards as kb
from app.telegram.formatters import format_datetime, format_relative_time
from app.telegram.handlers.common import _parse_date_range, _period_range
from app.telegram.states import Inputs

router = Router()


@router.callback_query(F.data == "domains")
async def cb_domains(call: CallbackQuery) -> None:
    await call.message.edit_text("🌐 <b>Домены</b>", parse_mode="HTML", reply_markup=kb.domains_menu())
    await call.answer()


def _domains_recent_text(db: Database) -> str:
    domains = db.list_domains(limit=20, order_by="last_seen")
    if not domains:
        return "Пока нет ни одного домена."
    lines = "\n".join(f"• <code>{d.domain}</code> ({d.hits})" for d in domains)
    return f"🕐 <b>Последние домены</b>\n\n{lines}"


def _domains_top_text(db: Database) -> str:
    domains = db.top_domains(limit=20)
    if not domains:
        return "Пока нет ни одного домена."
    lines = "\n".join(f"• <code>{d.domain}</code> — {d.hits}" for d in domains)
    return f"🔝 <b>Топ доменов по обращениям</b>\n\n{lines}"


@router.callback_query(F.data == "domains_recent")
async def cb_domains_recent(call: CallbackQuery, db: Database, live_view: LiveViewManager) -> None:
    live_active = live_view.is_active(call.message.chat.id, call.message.message_id)
    await call.message.edit_text(
        _domains_recent_text(db), parse_mode="HTML", reply_markup=kb.domains_recent_menu(live_active),
    )
    await call.answer()


@router.callback_query(F.data == "domains_top")
async def cb_domains_top(call: CallbackQuery, db: Database, live_view: LiveViewManager) -> None:
    live_active = live_view.is_active(call.message.chat.id, call.message.message_id)
    await call.message.edit_text(
        _domains_top_text(db), parse_mode="HTML", reply_markup=kb.domains_top_menu(live_active),
    )
    await call.answer()


@router.callback_query(F.data == "domains_export")
async def cb_domains_export_menu(call: CallbackQuery) -> None:
    await call.message.edit_text(
        "📤 <b>Экспорт доменов</b>\n\nЗа какой период выгрузить .txt?",
        parse_mode="HTML", reply_markup=kb.export_period_menu(),
    )
    await call.answer()


@router.callback_query(F.data.startswith("domains_export_period:"))
async def cb_domains_export_period(call: CallbackQuery, db: Database, config: Config) -> None:
    period = call.data.split(":", 1)[1]
    now = datetime.now(timezone.utc)
    tz_offset = runtime_settings.get_timezone_offset_minutes(db, config)
    since, until = _period_range(period, now, tz_offset)
    label = dict(kb.EXPORT_PERIODS).get(period, period)
    domains = db.list_domains(limit=1_000_000, order_by="domain", since=since, until=until)
    if not domains:
        await call.answer(f"За период «{label}» доменов нет", show_alert=True)
        return
    content = "\n".join(d.domain for d in domains) + "\n"
    file = BufferedInputFile(content.encode("utf-8"), filename="domains.txt")
    await call.message.answer_document(file, caption=f"Экспорт ({label}): {len(domains)} домен(ов)")
    await call.answer()


@router.callback_query(F.data == "domains_export_custom")
async def cb_domains_export_custom(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Inputs.waiting_for_export_range)
    await call.message.edit_text(
        "Введите период в формате <code>ГГГГ-ММ-ДД</code> (один день) или "
        "<code>ГГГГ-ММ-ДД ГГГГ-ММ-ДД</code> (с — по, включительно):",
        parse_mode="HTML", reply_markup=kb.cancel_input("domains_export"),
    )
    await call.answer()


@router.message(Inputs.waiting_for_export_range)
async def on_export_range_input(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    text = (message.text or "").strip()
    parsed = _parse_date_range(text)
    if parsed is None:
        await message.answer(
            "Не понял формат. Пример: <code>2026-09-01</code> или <code>2026-09-01 2026-09-15</code>.",
            parse_mode="HTML", reply_markup=kb.back_button("domains_export"),
        )
        return
    since, until = parsed

    domains = db.list_domains(limit=1_000_000, order_by="domain", since=since, until=until)
    if not domains:
        await message.answer("За этот диапазон доменов нет.", reply_markup=kb.back_button("domains"))
        return
    content = "\n".join(d.domain for d in domains) + "\n"
    file = BufferedInputFile(content.encode("utf-8"), filename="domains.txt")
    await message.answer_document(file, caption=f"Экспорт ({text}): {len(domains)} домен(ов)")
    await message.answer("Готово.", reply_markup=kb.back_button("domains"))


# ------------------------------------------------------------------
# Domains — search / filter / list / card / history (spec 2.0 Part 1, section 6)
# ------------------------------------------------------------------

DOMAIN_MINHITS_VALUES = {"any": None, "10": 10, "100": 100, "1000": 1000}
_DOMAIN_SOURCE_LABELS = {"tls_sni": "TLS SNI", "dns": "DNS"}
DOMAINS_EXPORT_LIMIT = 20_000


def _domain_filters_defaults() -> dict:
    return {
        "node_id": None, "node_label": None, "period": "all", "source": "all",
        "status": "all", "min_hits": "any", "new_only": False,
    }


async def _get_domain_filters(state: FSMContext) -> dict:
    data = await state.get_data()
    filters = _domain_filters_defaults()
    filters.update(data.get("domains_filter") or {})
    return filters


async def _get_domain_search(state: FSMContext) -> str | None:
    data = await state.get_data()
    return data.get("domains_search")


def _domains_query_kwargs(search: str | None, filters: dict) -> dict:
    since, until = _period_range(filters["period"], datetime.now(timezone.utc))
    return dict(
        search=search, node_id=filters["node_id"], since=since, until=until,
        since_field="first_seen" if filters["new_only"] else "last_seen",
        source=None if filters["source"] == "all" else filters["source"],
        min_hits=DOMAIN_MINHITS_VALUES.get(filters["min_hits"]),
        list_status=None if filters["status"] == "all" else filters["status"],
    )


def _domain_filters_summary(search: str | None, filters: dict) -> list[str]:
    parts = []
    if search:
        parts.append(f"поиск «{html.escape(search)}»")
    if filters["node_label"]:
        parts.append(f"нода {filters['node_label']}")
    if filters["period"] != "all":
        parts.append(dict(kb.DOMAIN_PERIODS).get(filters["period"], filters["period"]))
    if filters["source"] != "all":
        parts.append(kb.DOMAIN_SOURCE_LABELS.get(filters["source"], filters["source"]))
    if filters["status"] != "all":
        parts.append(kb.DOMAIN_STATUS_LABELS.get(filters["status"], filters["status"]))
    if filters["min_hits"] != "any":
        parts.append(kb.DOMAIN_MINHITS_LABELS.get(filters["min_hits"], filters["min_hits"]))
    if filters["new_only"]:
        parts.append("только новые")
    return parts


async def _build_domains_list_screen(db: Database, state: FSMContext, page: int) -> tuple[str, list, int, bool, bool]:
    search = await _get_domain_search(state)
    filters = await _get_domain_filters(state)
    kwargs = _domains_query_kwargs(search, filters)
    total = db.count_domains(**kwargs)
    domains = db.list_domains(
        limit=kb.DOMAINS_PAGE_SIZE, offset=page * kb.DOMAINS_PAGE_SIZE, order_by="last_seen", **kwargs,
    )

    lines = ["🌐 <b>Домены</b>"]
    summary = _domain_filters_summary(search, filters)
    if summary:
        lines.append("Фильтр: " + ", ".join(summary))
    lines.append(f"Найдено: {total}")
    if not domains:
        lines.append("\nНичего не найдено.")
    text = "\n".join(lines)

    has_filters = any([
        filters["node_id"] is not None, filters["period"] != "all", filters["source"] != "all",
        filters["status"] != "all", filters["min_hits"] != "any", filters["new_only"],
    ])
    return text, domains, total, bool(search), has_filters


async def _render_domains_list(target, db: Database, state: FSMContext, page: int) -> None:
    text, domains, total, has_search, has_filters = await _build_domains_list_screen(db, state, page)
    await target.edit_text(
        text, parse_mode="HTML", reply_markup=kb.domains_list_menu(domains, page, total, has_search, has_filters),
    )


@router.callback_query(F.data == "domains_list")
async def cb_domains_list(call: CallbackQuery, state: FSMContext, db: Database) -> None:
    await _render_domains_list(call.message, db, state, 0)
    await call.answer()


@router.callback_query(F.data.startswith("domains_list_page:"))
async def cb_domains_list_page(call: CallbackQuery, state: FSMContext, db: Database) -> None:
    page = int(call.data.split(":", 1)[1])
    await _render_domains_list(call.message, db, state, page)
    await call.answer()


@router.callback_query(F.data == "domains_search")
async def cb_domains_search_prompt(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Inputs.waiting_for_domain_search)
    await call.message.edit_text(
        "Введите часть домена для поиска (например <code>google</code> или <code>example.com</code>):",
        parse_mode="HTML", reply_markup=kb.cancel_input("domains_list"),
    )
    await call.answer()


@router.message(Inputs.waiting_for_domain_search)
async def on_domain_search_input(message: Message, state: FSMContext, db: Database) -> None:
    await state.set_state(None)
    query = (message.text or "").strip()
    await state.update_data(domains_search=query or None)
    text, domains, total, has_search, has_filters = await _build_domains_list_screen(db, state, 0)
    await message.answer(
        text, parse_mode="HTML", reply_markup=kb.domains_list_menu(domains, 0, total, has_search, has_filters),
    )


@router.callback_query(F.data == "domains_filter_menu")
async def cb_domains_filter_menu(call: CallbackQuery, state: FSMContext) -> None:
    filters = await _get_domain_filters(state)
    await call.message.edit_text(
        "🎛 <b>Фильтры доменов</b>", parse_mode="HTML", reply_markup=kb.domains_filter_menu(filters),
    )
    await call.answer()


async def _render_domains_filter_menu(call: CallbackQuery, state: FSMContext) -> None:
    filters = await _get_domain_filters(state)
    await call.message.edit_text(
        "🎛 <b>Фильтры доменов</b>", parse_mode="HTML", reply_markup=kb.domains_filter_menu(filters),
    )
    await call.answer()


@router.callback_query(F.data == "domains_filter_node")
async def cb_domains_filter_node(call: CallbackQuery, state: FSMContext, db: Database) -> None:
    filters = await _get_domain_filters(state)
    await call.message.edit_text(
        "Фильтр по ноде:", reply_markup=kb.domains_filter_node_menu(db.list_nodes(), filters["node_id"]),
    )
    await call.answer()


@router.callback_query(F.data.startswith("domains_filter_node_set:"))
async def cb_domains_filter_node_set(call: CallbackQuery, state: FSMContext, db: Database) -> None:
    raw = call.data.split(":", 1)[1]
    filters = await _get_domain_filters(state)
    if raw == "all":
        filters["node_id"], filters["node_label"] = None, None
    else:
        node = db.get_node(int(raw))
        filters["node_id"], filters["node_label"] = int(raw), (node.name if node else "?")
    await state.update_data(domains_filter=filters)
    await _render_domains_filter_menu(call, state)


@router.callback_query(F.data == "domains_filter_period")
async def cb_domains_filter_period(call: CallbackQuery, state: FSMContext) -> None:
    filters = await _get_domain_filters(state)
    await call.message.edit_text("Период:", reply_markup=kb.domains_filter_period_menu(filters["period"]))
    await call.answer()


@router.callback_query(F.data.startswith("domains_filter_period_set:"))
async def cb_domains_filter_period_set(call: CallbackQuery, state: FSMContext) -> None:
    filters = await _get_domain_filters(state)
    filters["period"] = call.data.split(":", 1)[1]
    await state.update_data(domains_filter=filters)
    await _render_domains_filter_menu(call, state)


@router.callback_query(F.data == "domains_filter_source")
async def cb_domains_filter_source(call: CallbackQuery, state: FSMContext) -> None:
    filters = await _get_domain_filters(state)
    await call.message.edit_text("Источник:", reply_markup=kb.domains_filter_source_menu(filters["source"]))
    await call.answer()


@router.callback_query(F.data.startswith("domains_filter_source_set:"))
async def cb_domains_filter_source_set(call: CallbackQuery, state: FSMContext) -> None:
    filters = await _get_domain_filters(state)
    filters["source"] = call.data.split(":", 1)[1]
    await state.update_data(domains_filter=filters)
    await _render_domains_filter_menu(call, state)


@router.callback_query(F.data == "domains_filter_status")
async def cb_domains_filter_status(call: CallbackQuery, state: FSMContext) -> None:
    filters = await _get_domain_filters(state)
    await call.message.edit_text("Статус:", reply_markup=kb.domains_filter_status_menu(filters["status"]))
    await call.answer()


@router.callback_query(F.data.startswith("domains_filter_status_set:"))
async def cb_domains_filter_status_set(call: CallbackQuery, state: FSMContext) -> None:
    filters = await _get_domain_filters(state)
    filters["status"] = call.data.split(":", 1)[1]
    await state.update_data(domains_filter=filters)
    await _render_domains_filter_menu(call, state)


@router.callback_query(F.data == "domains_filter_minhits")
async def cb_domains_filter_minhits(call: CallbackQuery, state: FSMContext) -> None:
    filters = await _get_domain_filters(state)
    await call.message.edit_text("Минимум хитов:", reply_markup=kb.domains_filter_minhits_menu(filters["min_hits"]))
    await call.answer()


@router.callback_query(F.data.startswith("domains_filter_minhits_set:"))
async def cb_domains_filter_minhits_set(call: CallbackQuery, state: FSMContext) -> None:
    filters = await _get_domain_filters(state)
    filters["min_hits"] = call.data.split(":", 1)[1]
    await state.update_data(domains_filter=filters)
    await _render_domains_filter_menu(call, state)


@router.callback_query(F.data == "domains_filter_new_only")
async def cb_domains_filter_new_only(call: CallbackQuery, state: FSMContext) -> None:
    filters = await _get_domain_filters(state)
    filters["new_only"] = not filters["new_only"]
    await state.update_data(domains_filter=filters)
    await _render_domains_filter_menu(call, state)


@router.callback_query(F.data == "domains_filter_reset")
async def cb_domains_filter_reset(call: CallbackQuery, state: FSMContext, db: Database) -> None:
    await state.update_data(domains_filter=None, domains_search=None)
    await _render_domains_list(call.message, db, state, 0)
    await call.answer("Фильтры сброшены")


def _domain_filter_status(all_rules: list, domain_str: str) -> tuple[str, str]:
    verdict = classify_domain(domain_str, all_rules)
    if verdict.is_watched:
        return "watch", "👁 Watch"
    if verdict.is_ignored:
        return "ignore", "🚫 Ignore"
    if verdict.is_allowed:
        return "allow", "✅ Allow"
    return "none", "⚪ Без статуса"


def _build_domain_card_text(db: Database, domain) -> str:
    _, status_label = _domain_filter_status(db.list_filter_rules(), domain.domain)
    node_dist = db.domain_node_distribution(domain.domain)
    source_dist = db.domain_source_distribution(domain.domain)

    lines = [
        f"🌐 <code>{html.escape(domain.domain)}</code>",
        f"Статус: {status_label}",
        f"Хитов: {domain.hits}",
        f"Впервые: {format_datetime(domain.first_seen)}",
        f"Последний раз: {format_datetime(domain.last_seen)} ({format_relative_time(domain.last_seen)})",
    ]
    if node_dist:
        lines.append("")
        lines.append("По нодам:")
        lines.extend(f"  • {name}: {count}" for name, count in node_dist)
    if source_dist:
        lines.append("")
        lines.append("По источникам:")
        lines.extend(f"  • {_DOMAIN_SOURCE_LABELS.get(src, src)}: {count}" for src, count in source_dist)
    return "\n".join(lines)


@router.callback_query(F.data.startswith("domain_card:"))
async def cb_domain_card(call: CallbackQuery, db: Database) -> None:
    _, domain_id, page = call.data.split(":")
    domain = db.get_domain(int(domain_id))
    if domain is None:
        await call.answer("Домен не найден (возможно, данные были очищены)", show_alert=True)
        return
    await call.message.edit_text(
        _build_domain_card_text(db, domain), parse_mode="HTML", reply_markup=kb.domain_card_kb(domain.id, int(page)),
    )
    await call.answer()


@router.callback_query(F.data.startswith("domain_filter:"))
async def cb_domain_filter_prompt(call: CallbackQuery) -> None:
    _, domain_id, list_type, page = call.data.split(":")
    await call.message.edit_text(
        "Тип паттерна:", reply_markup=kb.domain_filter_pattern_type(int(domain_id), list_type, int(page)),
    )
    await call.answer()


@router.callback_query(F.data.startswith("domain_filter_add:"))
async def cb_domain_filter_add(call: CallbackQuery, db: Database) -> None:
    _, domain_id, list_type, pattern_type, page = call.data.split(":")
    domain = db.get_domain(int(domain_id))
    if domain is None:
        await call.answer("Домен не найден", show_alert=True)
        return
    rule = db.add_filter_rule(list_type, pattern_type, domain.domain)
    await call.answer("Такое правило уже существует" if rule is None else f"Добавлено в {list_type}")
    await call.message.edit_text(
        _build_domain_card_text(db, domain), parse_mode="HTML", reply_markup=kb.domain_card_kb(domain.id, int(page)),
    )


@router.callback_query(F.data.startswith("domain_history:"))
async def cb_domain_history(call: CallbackQuery, db: Database) -> None:
    _, domain_id, page, card_page = call.data.split(":")
    domain = db.get_domain(int(domain_id))
    if domain is None:
        await call.answer("Домен не найден", show_alert=True)
        return
    page_i = int(page)
    total = db.count_domain_events(domain.domain)
    lines = [f"📊 <b>История:</b> <code>{html.escape(domain.domain)}</code>"]
    if total == 0:
        lines.append(
            "\nСобытий не найдено - либо их ещё не было, либо история уже удалена по retention "
            "(агрегированные хиты в карточке домена при этом не затрагиваются)."
        )
    else:
        lines.append(f"Всего записей: {total}\n")
        for e in db.list_domain_events(domain.domain, limit=20, offset=page_i * 20):
            node = db.get_node(e.node_id)
            node_name = node.name if node else f"#{e.node_id}"
            source_label = _DOMAIN_SOURCE_LABELS.get(e.source, e.source)
            lines.append(f"{format_datetime(e.occurred_at)} — {node_name} ({source_label}), хитов: {e.hits}")
    await call.message.edit_text(
        "\n".join(lines), parse_mode="HTML",
        reply_markup=kb.domain_history_kb(domain.id, page_i, int(card_page), total),
    )
    await call.answer()


@router.callback_query(F.data == "domains_list_export")
async def cb_domains_list_export(call: CallbackQuery, state: FSMContext, db: Database) -> None:
    search = await _get_domain_search(state)
    filters = await _get_domain_filters(state)
    total = db.count_domains(**_domains_query_kwargs(search, filters))
    if total == 0:
        await call.answer("Нечего экспортировать - список пуст", show_alert=True)
        return
    note = f" ({total} домен(ов))" if total <= DOMAINS_EXPORT_LIMIT else (
        f" ({total} домен(ов), будет выгружено первые {DOMAINS_EXPORT_LIMIT} по последней активности)"
    )
    await call.message.edit_text(
        f"📤 Экспорт текущего списка{note}.\nФормат:", reply_markup=kb.domains_list_export_format(),
    )
    await call.answer()


@router.callback_query(F.data.startswith("domains_list_export_do:"))
async def cb_domains_list_export_do(call: CallbackQuery, state: FSMContext, db: Database) -> None:
    fmt = call.data.split(":", 1)[1]
    search = await _get_domain_search(state)
    filters = await _get_domain_filters(state)
    kwargs = _domains_query_kwargs(search, filters)
    domains = db.list_domains(limit=DOMAINS_EXPORT_LIMIT, order_by="last_seen", **kwargs)
    if not domains:
        await call.answer("Нечего экспортировать", show_alert=True)
        return

    all_rules = db.list_filter_rules()
    rows = []
    for d in domains:
        node = db.get_node(d.node_id)
        status_key, _ = _domain_filter_status(all_rules, d.domain)
        rows.append({
            "domain": d.domain, "hits": d.hits,
            "first_seen": d.first_seen.isoformat(), "last_seen": d.last_seen.isoformat(),
            "node": node.name if node else "", "filter_status": status_key,
        })

    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=["domain", "hits", "first_seen", "last_seen", "node", "filter_status"])
        writer.writeheader()
        writer.writerows(rows)
        content = buf.getvalue()
    else:
        content = json.dumps(rows, ensure_ascii=False, indent=2)

    file = BufferedInputFile(content.encode("utf-8"), filename=f"domains.{fmt}")
    await call.message.answer_document(file, caption=f"Экспорт: {len(rows)} домен(ов)")
    await call.answer()


@router.callback_query(F.data == "domains_data")
async def cb_domains_data(call: CallbackQuery) -> None:
    await call.message.edit_text("🗑 <b>Управление данными</b>", parse_mode="HTML", reply_markup=kb.domains_data_menu())
    await call.answer()


@router.callback_query(F.data == "domains_purge_age")
async def cb_domains_purge_age(call: CallbackQuery) -> None:
    await call.message.edit_text("Удалить события старше:", reply_markup=kb.domains_purge_age_menu())
    await call.answer()


@router.callback_query(F.data.startswith("domains_purge_age_confirm:"))
async def cb_domains_purge_age_confirm(call: CallbackQuery) -> None:
    days = int(call.data.split(":", 1)[1])
    await call.message.edit_text(
        f"Удалить все события старше {days} дней? Сами домены и их суммарные хиты не изменятся, "
        f"удаляется только детальная история по времени.",
        reply_markup=kb.confirm_purge_age(days),
    )
    await call.answer()


@router.callback_query(F.data.startswith("domains_purge_age_do:"))
async def cb_domains_purge_age_do(call: CallbackQuery, db: Database) -> None:
    days = int(call.data.split(":", 1)[1])
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    count = db.purge_events_older_than(cutoff)
    await call.answer(f"Удалено событий: {count}", show_alert=True)
    await call.message.edit_text("🗑 <b>Управление данными</b>", parse_mode="HTML", reply_markup=kb.domains_data_menu())


@router.callback_query(F.data == "domains_purge_events")
async def cb_domains_purge_events(call: CallbackQuery) -> None:
    await call.message.edit_text(
        "Очистить ВСЮ историю событий (детальные записи по времени)? Сами домены и их суммарные хиты останутся - "
        "удаляется только возможность посмотреть историю визитов.",
        reply_markup=kb.confirm_purge_events(),
    )
    await call.answer()


@router.callback_query(F.data == "domains_purge_events_do")
async def cb_domains_purge_events_do(call: CallbackQuery, db: Database) -> None:
    count = db.delete_events_only()
    await call.answer(f"Удалено событий: {count}", show_alert=True)
    await call.message.edit_text("🗑 <b>Управление данными</b>", parse_mode="HTML", reply_markup=kb.domains_data_menu())


@router.callback_query(F.data.startswith("data_reset:"))
async def cb_data_reset_prompt(call: CallbackQuery) -> None:
    back_target = call.data.split(":", 1)[1]
    await call.message.edit_text(
        "🗑 <b>Сброс данных</b>\n\n"
        "Это удалит ВСЕ собранные домены и историю событий на сервере.\n"
        "Ноды, токены, фильтры и настройки не затрагиваются.\n\n"
        "Действие необратимо.",
        parse_mode="HTML", reply_markup=kb.confirm_reset_data(back_target),
    )
    await call.answer()


@router.callback_query(F.data.startswith("data_reset_confirm:"))
async def cb_data_reset_confirm(call: CallbackQuery, db: Database, config: Config) -> None:
    from app.telegram.handlers.stats import _stats_text  # local import: stats.py doesn't import domains.py

    back_target = call.data.split(":", 1)[1]
    domains_count, events_count = db.reset_domains_and_events()
    await call.answer(f"Удалено: {domains_count} доменов, {events_count} событий", show_alert=True)
    if back_target == "stats":
        await call.message.edit_text(
            _stats_text(db, config, "today"), parse_mode="HTML", reply_markup=kb.stats_menu("today"),
        )
    else:
        await cb_domains(call)


# ------------------------------------------------------------------
# Per-domain notification buttons
# ------------------------------------------------------------------

@router.callback_query(F.data.startswith("domain_ignore:"))
async def cb_domain_ignore(call: CallbackQuery, db: Database) -> None:
    domain_id = int(call.data.split(":")[1])
    db.set_ignored(domain_id, True)
    domain = db.get_domain(domain_id)
    if domain:
        db.add_filter_rule("ignore", "suffix", domain.domain)
    await call.answer("Добавлено в игнор (и в Ignore List на будущее)")
    await call.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data.startswith("domain_copy:"))
async def cb_domain_copy(call: CallbackQuery, db: Database) -> None:
    domain_id = int(call.data.split(":")[1])
    domain = db.get_domain(domain_id)
    if domain:
        await call.message.answer(f"<code>{domain.domain}</code>", parse_mode="HTML")
    await call.answer()
