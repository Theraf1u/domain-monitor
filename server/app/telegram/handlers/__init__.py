"""Combines every handlers/*.py module's router into the single `router`
that bot.py includes - external code (bot.py, error_handler.py) keeps
importing `app.telegram.handlers` and using `.router` exactly as when
this was one file; only the internal layout changed.

Order matters for exactly one thing: `common.fallback_router` (the
catch-all "this screen is stale" + "use the menu" handlers) must be
included last, so it only ever catches what no other module's specific
filter already matched. Every other module's callback_data/state filters
are mutually exclusive by construction (checked against the pre-refactor
route dump - see the refactor commit message), so their relative order
doesn't matter.
"""
from __future__ import annotations

from aiogram import Router

from app.telegram.handlers import (
    backups,
    common,
    domains,
    filters,
    live,
    main,
    nodes,
    notifications,
    settings,
    stats,
)

router = Router()
router.include_router(common.router)
router.include_router(main.router)
router.include_router(nodes.router)
router.include_router(domains.router)
router.include_router(stats.router)
router.include_router(notifications.router)
router.include_router(filters.router)
router.include_router(settings.router)
router.include_router(backups.router)
router.include_router(live.router)
router.include_router(common.fallback_router)
