"""The "🧰 Фильтры" section: rule list/card, add, bulk import (text/.txt/
.csv with preview + conflict detection), export (TXT/CSV/JSON), and the
domain-check tool.
"""
from __future__ import annotations

import csv
import html
import io
import json
import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from app.database import Database
from app.filters import classify_domain, normalize_domain
from app.telegram import keyboards as kb
from app.telegram.formatters import format_relative_time
from app.telegram.states import Inputs

logger = logging.getLogger(__name__)

router = Router()


@router.callback_query(F.data == "filters")
async def cb_filters(call: CallbackQuery, db: Database) -> None:
    counts = {lt: len(db.list_filter_rules(lt)) for lt in ("watch", "ignore", "allow")}
    await call.message.edit_text(
        "🔍 <b>Фильтры</b>\n\n"
        "Watch — всегда мгновенное уведомление.\n"
        "Ignore/Allow — не уведомлять (домен помечается suppressed).",
        parse_mode="HTML", reply_markup=kb.filters_menu(counts),
    )
    await call.answer()


@router.callback_query(F.data.startswith("filters_list:"))
async def cb_filters_list(call: CallbackQuery, db: Database) -> None:
    _, list_type, page_raw = call.data.split(":")
    page = int(page_raw)
    rules = db.list_filter_rules(list_type)
    title = {"watch": "🚨 Watch List", "ignore": "🚫 Ignore List", "allow": "✅ Allow List"}[list_type]
    text = title if rules else f"{title}\n\nПусто"
    await call.message.edit_text(text, reply_markup=kb.filters_list(list_type, rules, page))
    await call.answer()


def _find_rule(db: Database, rule_id: int):
    return next((r for r in db.list_filter_rules() if r.id == rule_id), None)


def _build_filter_rule_text(rule) -> str:
    tag = kb.PATTERN_TAG.get(rule.pattern_type, rule.pattern_type)
    list_label = {"watch": "🚨 Watch", "ignore": "🚫 Ignore", "allow": "✅ Allow"}.get(rule.list_type, rule.list_type)
    lines = [
        f"{list_label} · <code>{html.escape(rule.pattern)}</code> ({tag})",
        "",
        f"Статус: {'🟢 включено' if rule.enabled else '🔴 выключено'}",
        f"Сработало раз: {rule.hits_count}",
        f"Последнее срабатывание: {format_relative_time(rule.last_hit_at)}",
        f"Комментарий: {html.escape(rule.comment) if rule.comment else '—'}",
    ]
    return "\n".join(lines)


@router.callback_query(F.data.startswith("filter_rule:"))
async def cb_filter_rule(call: CallbackQuery, db: Database) -> None:
    _, rule_id_raw, page_raw = call.data.split(":")
    rule = _find_rule(db, int(rule_id_raw))
    if rule is None:
        await call.answer("Правило не найдено", show_alert=True)
        return
    await call.message.edit_text(
        _build_filter_rule_text(rule), parse_mode="HTML", reply_markup=kb.filter_rule_card(rule, int(page_raw)),
    )
    await call.answer()


@router.callback_query(F.data.startswith("filter_toggle:"))
async def cb_filter_toggle(call: CallbackQuery, db: Database) -> None:
    _, rule_id_raw, page_raw = call.data.split(":")
    rule = _find_rule(db, int(rule_id_raw))
    if rule is None:
        await call.answer("Правило не найдено", show_alert=True)
        return
    db.set_filter_rule_enabled(rule.id, not rule.enabled)
    rule = _find_rule(db, rule.id)
    await call.message.edit_text(
        _build_filter_rule_text(rule), parse_mode="HTML", reply_markup=kb.filter_rule_card(rule, int(page_raw)),
    )
    await call.answer("Включено" if rule.enabled else "Выключено")


@router.callback_query(F.data.startswith("filter_comment:"))
async def cb_filter_comment_prompt(call: CallbackQuery, state: FSMContext, db: Database) -> None:
    _, rule_id_raw, page_raw = call.data.split(":")
    rule = _find_rule(db, int(rule_id_raw))
    if rule is None:
        await call.answer("Правило не найдено", show_alert=True)
        return
    await state.set_state(Inputs.waiting_for_filter_comment)
    await state.update_data(filter_rule_id=rule.id, filter_rule_page=int(page_raw))
    await call.message.edit_text(
        "Введите комментарий к правилу (например, зачем оно нужно). "
        "Отправьте «-», чтобы очистить существующий комментарий.",
        reply_markup=kb.cancel_input(f"filter_rule:{rule.id}:{page_raw}"),
    )
    await call.answer()


@router.message(Inputs.waiting_for_filter_comment)
async def on_filter_comment_input(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    rule_id, page = data.get("filter_rule_id"), data.get("filter_rule_page", 0)
    await state.set_state(None)
    if rule_id is None:
        await message.answer("Сессия истекла. Откройте меню фильтров и попробуйте снова.", reply_markup=kb.back_button("filters"))
        return
    text = (message.text or "").strip()
    comment = None if text == "-" else text
    db.set_filter_rule_comment(rule_id, comment)
    rule = _find_rule(db, rule_id)
    if rule is None:
        await message.answer("Правило больше не существует.", reply_markup=kb.back_button("filters"))
        return
    await message.answer(
        _build_filter_rule_text(rule), parse_mode="HTML", reply_markup=kb.filter_rule_card(rule, page),
    )


@router.callback_query(F.data.startswith("filter_remove_confirm:"))
async def cb_filter_remove_confirm(call: CallbackQuery, db: Database) -> None:
    _, rule_id_raw, page_raw = call.data.split(":")
    rule_id, page = int(rule_id_raw), int(page_raw)
    rule = next((r for r in db.list_filter_rules() if r.id == rule_id), None)
    if rule is None:
        await call.answer("Правило не найдено", show_alert=True)
        return
    tag = kb.PATTERN_TAG.get(rule.pattern_type, rule.pattern_type)
    await call.message.edit_text(
        f"Удалить правило <code>{rule.pattern}</code> ({tag}) из {rule.list_type}?",
        parse_mode="HTML", reply_markup=kb.confirm_remove_filter(rule, page),
    )
    await call.answer()


@router.callback_query(F.data.startswith("filter_remove:"))
async def cb_filter_remove(call: CallbackQuery, db: Database) -> None:
    _, rule_id_raw, list_type, page_raw = call.data.split(":")
    db.remove_filter_rule(int(rule_id_raw))
    await call.answer("Удалено")
    rules = db.list_filter_rules(list_type)
    title = {"watch": "🚨 Watch List", "ignore": "🚫 Ignore List", "allow": "✅ Allow List"}[list_type]
    text = title if rules else f"{title}\n\nПусто"
    await call.message.edit_text(text, reply_markup=kb.filters_list(list_type, rules, int(page_raw)))


@router.callback_query(F.data == "filter_add")
async def cb_filter_add(call: CallbackQuery) -> None:
    await call.message.edit_text("Выберите список:", reply_markup=kb.filter_add_list_type())
    await call.answer()


@router.callback_query(F.data.startswith("filter_add_type:"))
async def cb_filter_add_type(call: CallbackQuery) -> None:
    list_type = call.data.split(":", 1)[1]
    await call.message.edit_text(
        f"Список: {list_type}\nВыберите тип паттерна:", reply_markup=kb.filter_add_pattern_type(list_type),
    )
    await call.answer()


@router.callback_query(F.data.startswith("filter_add_ptype:"))
async def cb_filter_add_ptype(call: CallbackQuery, state: FSMContext) -> None:
    _, list_type, pattern_type = call.data.split(":")
    await state.set_state(Inputs.waiting_for_filter_pattern)
    await state.update_data(list_type=list_type, pattern_type=pattern_type)
    hint = {
        "suffix": "example.com (покроет и все поддомены)",
        "exact": "api.example.com (только этот домен)",
        "wildcard": "*.example.com (шаблон)",
    }[pattern_type]
    await call.message.edit_text(
        f"Введите паттерн, например: {hint}\n\n"
        f"Можно сразу вставить список — по одному паттерну на строку, добавятся все за раз.",
        reply_markup=kb.cancel_input("filters"),
    )
    await call.answer()


@router.message(Inputs.waiting_for_filter_pattern)
async def on_filter_pattern_input(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    await state.clear()
    raw_lines = (message.text or "").splitlines()
    patterns = []
    seen = set()
    for line in raw_lines:
        pattern = line.strip().lower()
        if not pattern or pattern in seen:
            continue
        seen.add(pattern)
        patterns.append(pattern)

    if not patterns:
        await message.answer("Пустой паттерн, попробуйте снова из меню фильтров.", reply_markup=kb.back_button("filters"))
        return

    if len(patterns) == 1:
        rule = db.add_filter_rule(data["list_type"], data["pattern_type"], patterns[0])
        if rule is None:
            await message.answer("Такое правило уже существует.", reply_markup=kb.back_button("filters"))
        else:
            await message.answer(
                f"✅ Добавлено в {data['list_type']}: <code>{patterns[0]}</code>",
                parse_mode="HTML", reply_markup=kb.back_button("filters"),
            )
        return

    added = 0
    duplicates = 0
    for pattern in patterns:
        rule = db.add_filter_rule(data["list_type"], data["pattern_type"], pattern)
        if rule is None:
            duplicates += 1
        else:
            added += 1
    await message.answer(
        f"✅ Массовое добавление в {data['list_type']} завершено.\n"
        f"Добавлено: {added}\n"
        f"Уже было (пропущено): {duplicates}",
        reply_markup=kb.back_button("filters"),
    )


def _extract_patterns_from_text(text: str) -> list[str]:
    seen: set[str] = set()
    patterns: list[str] = []
    for line in text.splitlines():
        pattern = line.strip().lower()
        if not pattern or pattern in seen:
            continue
        seen.add(pattern)
        patterns.append(pattern)
    return patterns


def _extract_patterns_from_csv(text: str) -> list[str]:
    """Takes the first column of every row - a filter-import CSV is just
    a list of patterns, optionally with extra columns nobody asked us to
    interpret. A header row's first cell (e.g. "pattern") is harmless
    here: it'll fail _looks_like_pattern() and land in the invalid
    count, not silently get imported as a real rule."""
    reader = csv.reader(io.StringIO(text))
    seen: set[str] = set()
    patterns: list[str] = []
    for row in reader:
        if not row:
            continue
        pattern = row[0].strip().lower()
        if not pattern or pattern in seen:
            continue
        seen.add(pattern)
        patterns.append(pattern)
    return patterns


def _looks_like_pattern(s: str) -> bool:
    """Deliberately looser than normalize_domain() - wildcard patterns
    like "*.example.com" are valid filter_rule patterns but would fail
    strict hostname validation. Just enough of a sanity check to catch
    obviously-broken lines (empty, whitespace inside, absurdly long)."""
    return bool(s) and " " not in s and "\t" not in s and len(s) <= 253


async def _render_filter_import_preview(
    message: Message, state: FSMContext, db: Database, list_type: str, pattern_type: str, patterns: list[str],
) -> None:
    valid = [p for p in patterns if _looks_like_pattern(p)]
    invalid_count = len(patterns) - len(valid)
    existing_same_list = {r.pattern for r in db.list_filter_rules(list_type) if r.pattern_type == pattern_type}
    new_patterns = [p for p in valid if p not in existing_same_list]
    duplicate_count = len(valid) - len(new_patterns)

    other_lists = [lt for lt in ("watch", "ignore", "allow") if lt != list_type]
    conflicts = []
    for lt in other_lists:
        other_patterns = {r.pattern for r in db.list_filter_rules(lt)}
        conflicts.extend(p for p in new_patterns if p in other_patterns)

    await state.update_data(
        filter_import_list_type=list_type, filter_import_pattern_type=pattern_type, filter_import_patterns=new_patterns,
    )

    lines = [
        "📥 <b>Предпросмотр импорта</b>",
        f"Список: {list_type}, тип паттерна: {kb.PATTERN_TAG.get(pattern_type, pattern_type)}",
        "",
        f"Найдено строк: {len(patterns)}",
        f"Новых: {len(new_patterns)}",
        f"Уже есть в этом списке (пропустятся): {duplicate_count}",
        f"Некорректных (пропустятся): {invalid_count}",
    ]
    if conflicts:
        shown = ", ".join(f"<code>{html.escape(p)}</code>" for p in conflicts[:10])
        more = f" и ещё {len(conflicts) - 10}" if len(conflicts) > 10 else ""
        lines.append(
            f"\n⚠️ Уже есть в другом списке (Watch/Ignore/Allow): {shown}{more}\n"
            f"Будут добавлены и сюда - приоритет Watch над Ignore/Allow не меняется."
        )
    if not new_patterns:
        lines.append("\nНечего импортировать - все строки уже есть или некорректны.")
        await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=kb.back_button("filters"))
        return

    lines.append(f"\nИмпортировать {len(new_patterns)} новых правил?")
    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=kb.confirm_filter_import())


@router.callback_query(F.data == "filter_import")
async def cb_filter_import(call: CallbackQuery) -> None:
    await call.message.edit_text("Выберите список для импорта:", reply_markup=kb.filter_import_list_type())
    await call.answer()


@router.callback_query(F.data.startswith("filter_import_type:"))
async def cb_filter_import_type(call: CallbackQuery) -> None:
    list_type = call.data.split(":", 1)[1]
    await call.message.edit_text(
        f"Список: {list_type}\nВыберите тип паттерна:", reply_markup=kb.filter_import_pattern_type(list_type),
    )
    await call.answer()


@router.callback_query(F.data.startswith("filter_import_ptype:"))
async def cb_filter_import_ptype(call: CallbackQuery, state: FSMContext) -> None:
    _, list_type, pattern_type = call.data.split(":")
    await state.set_state(Inputs.waiting_for_filter_import)
    await state.update_data(filter_import_list_type=list_type, filter_import_pattern_type=pattern_type)
    await call.message.edit_text(
        "Вставьте список паттернов (по одному на строку) текстом, "
        "или пришлите файлом <b>.txt</b> (по строке) или <b>.csv</b> (первая колонка).",
        parse_mode="HTML", reply_markup=kb.cancel_input("filters"),
    )
    await call.answer()


@router.message(Inputs.waiting_for_filter_import, F.document)
async def on_filter_import_document(message: Message, state: FSMContext, db: Database, bot: Bot) -> None:
    data = await state.get_data()
    list_type, pattern_type = data.get("filter_import_list_type"), data.get("filter_import_pattern_type")
    await state.set_state(None)
    if not list_type or not pattern_type:
        await message.answer("Сессия истекла. Откройте меню фильтров и попробуйте снова.", reply_markup=kb.back_button("filters"))
        return
    filename = message.document.file_name or ""
    try:
        buf = await bot.download(message.document)
        text = buf.read().decode("utf-8", errors="replace")
    except Exception:
        logger.exception("Failed to download filter import document")
        await message.answer("Не удалось прочитать файл. Попробуйте ещё раз.", reply_markup=kb.back_button("filters"))
        return
    patterns = _extract_patterns_from_csv(text) if filename.lower().endswith(".csv") else _extract_patterns_from_text(text)
    if not patterns:
        await message.answer("Файл пустой или не удалось разобрать ни одной строки.", reply_markup=kb.back_button("filters"))
        return
    await _render_filter_import_preview(message, state, db, list_type, pattern_type, patterns)


@router.message(Inputs.waiting_for_filter_import)
async def on_filter_import_text(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    list_type, pattern_type = data.get("filter_import_list_type"), data.get("filter_import_pattern_type")
    await state.set_state(None)
    if not list_type or not pattern_type:
        await message.answer("Сессия истекла. Откройте меню фильтров и попробуйте снова.", reply_markup=kb.back_button("filters"))
        return
    patterns = _extract_patterns_from_text(message.text or "")
    if not patterns:
        await message.answer("Пустой ввод, попробуйте снова из меню фильтров.", reply_markup=kb.back_button("filters"))
        return
    await _render_filter_import_preview(message, state, db, list_type, pattern_type, patterns)


@router.callback_query(F.data == "filter_import_confirm")
async def cb_filter_import_confirm(call: CallbackQuery, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    list_type = data.get("filter_import_list_type")
    pattern_type = data.get("filter_import_pattern_type")
    patterns = data.get("filter_import_patterns") or []
    await state.update_data(filter_import_patterns=None)
    if not list_type or not pattern_type or not patterns:
        await call.answer("Нечего импортировать (сессия истекла?)", show_alert=True)
        return
    added = duplicates = 0
    for pattern in patterns:
        rule = db.add_filter_rule(list_type, pattern_type, pattern)
        if rule is None:
            duplicates += 1
        else:
            added += 1
    await call.message.edit_text(
        f"✅ Импорт в {list_type} завершён.\nДобавлено: {added}\nПропущено (гонка/дубликат): {duplicates}",
        reply_markup=kb.back_button("filters"),
    )
    await call.answer()


@router.callback_query(F.data == "filter_export")
async def cb_filter_export(call: CallbackQuery) -> None:
    await call.message.edit_text("Что экспортировать?", reply_markup=kb.filter_export_scope())
    await call.answer()


@router.callback_query(F.data.startswith("filter_export_scope:"))
async def cb_filter_export_scope(call: CallbackQuery) -> None:
    scope = call.data.split(":", 1)[1]
    await call.message.edit_text("В каком формате?", reply_markup=kb.filter_export_format(scope))
    await call.answer()


def _rules_for_export(db: Database, scope: str) -> list:
    if scope == "all":
        return [r for lt in ("watch", "ignore", "allow") for r in db.list_filter_rules(lt)]
    return db.list_filter_rules(scope)


def _render_filter_export_txt(rules: list) -> str:
    return "\n".join(r.pattern for r in rules) + "\n"


def _render_filter_export_csv(rules: list) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["list_type", "pattern_type", "pattern", "enabled", "comment", "hits_count", "last_hit_at"])
    for r in rules:
        writer.writerow([
            r.list_type, r.pattern_type, r.pattern, int(r.enabled), r.comment or "",
            r.hits_count, r.last_hit_at.isoformat() if r.last_hit_at else "",
        ])
    return buf.getvalue()


def _render_filter_export_json(rules: list) -> str:
    return json.dumps(
        [
            {
                "list_type": r.list_type, "pattern_type": r.pattern_type, "pattern": r.pattern,
                "enabled": r.enabled, "comment": r.comment, "hits_count": r.hits_count,
                "last_hit_at": r.last_hit_at.isoformat() if r.last_hit_at else None,
            }
            for r in rules
        ],
        ensure_ascii=False, indent=2,
    )


@router.callback_query(F.data.startswith("filter_export_fmt:"))
async def cb_filter_export_fmt(call: CallbackQuery, db: Database) -> None:
    _, scope, fmt = call.data.split(":")
    rules = _rules_for_export(db, scope)
    if not rules:
        await call.answer("Список пуст - нечего экспортировать", show_alert=True)
        return
    renderers = {"txt": _render_filter_export_txt, "csv": _render_filter_export_csv, "json": _render_filter_export_json}
    content = renderers[fmt](rules)
    file = BufferedInputFile(content.encode("utf-8"), filename=f"filters_{scope}.{fmt}")
    await call.message.answer_document(file, caption=f"Экспорт ({scope}): {len(rules)} правил(о)")
    await call.answer()


@router.callback_query(F.data == "filter_check")
async def cb_filter_check(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Inputs.waiting_for_filter_check)
    await call.message.edit_text(
        "Введите домен, чтобы проверить, как его обработают текущие правила:",
        reply_markup=kb.cancel_input("filters"),
    )
    await call.answer()


@router.message(Inputs.waiting_for_filter_check)
async def on_filter_check_input(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    raw = (message.text or "").strip()
    domain = normalize_domain(raw)
    if domain is None:
        await message.answer(
            f"«{html.escape(raw)}» не похоже на домен. Попробуйте снова из меню фильтров.",
            parse_mode="HTML", reply_markup=kb.back_button("filters"),
        )
        return

    all_rules = db.all_filter_rules_cached()
    verdict = classify_domain(domain, all_rules)
    matched = [r for r in all_rules if r.id in verdict.matched_rule_ids]

    list_icon = {"watch": "🚨", "ignore": "🚫", "allow": "✅"}
    if matched:
        rules_lines = "\n".join(
            f"  {list_icon.get(r.list_type, '•')} {r.list_type}: "
            f"<code>{html.escape(r.pattern)}</code> ({kb.PATTERN_TAG.get(r.pattern_type, r.pattern_type)})"
            for r in matched
        )
        rules_block = f"Совпавшие правила:\n{rules_lines}"
    else:
        rules_block = "Совпавших правил нет."

    if verdict.is_watched:
        result = "🚨 Watch — придёт мгновенное уведомление, даже если домен попадает под Ignore/Allow"
    elif verdict.suppresses_notification:
        result = "🔕 Подавлен (Ignore/Allow) — уведомления не будет"
    else:
        result = "🔔 Обычный домен — уведомление придёт по текущему режиму группировки"

    text = f"<code>{domain}</code>\n\n{rules_block}\n\nИтог: {result}"
    await message.answer(text, parse_mode="HTML", reply_markup=kb.back_button("filters"))
