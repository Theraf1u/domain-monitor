"""FastAPI application entrypoint. Also starts the (optional) Telegram bot
and the notification batching loop as background asyncio tasks alongside
uvicorn's own event loop - one process, one lifespan."""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.api import domains, events, nodes, stats
from app.config import load_config
from app.database import Database
from app.logging_config import setup_logging
from app.metrics import DOMAINS_TOTAL, NODES_ONLINE, NODES_TOTAL
from app.notifier import Notifier
from app.retention import RetentionTask
from app.webadmin.broadcaster import EventBroadcaster
from app.webadmin.deps import RequiresLogin
from app.webadmin import routes_auth, routes_pages, ws as webadmin_ws

logger = logging.getLogger(__name__)

_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webadmin", "static")


async def _session_gc_loop(db: Database, stopped: asyncio.Event) -> None:
    while not stopped.is_set():
        try:
            await asyncio.to_thread(db.purge_expired_sessions)
        except Exception:
            logger.exception("Session GC failed")
        try:
            await asyncio.wait_for(stopped.wait(), timeout=3600)
        except asyncio.TimeoutError:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    setup_logging(config.log_level, config.log_dir)
    logger.info("Starting Domain Monitor Server")

    db = Database(config.database_path)
    db.migrate()

    notifier = Notifier(db, config.admin_id or 0)

    app.state.config = config
    app.state.db = db
    app.state.notifier = notifier
    app.state.broadcaster = EventBroadcaster()

    stop_gc = asyncio.Event()
    background_tasks = [
        asyncio.create_task(RetentionTask(db, config.event_retention_days).run()),
        asyncio.create_task(_session_gc_loop(db, stop_gc)),
    ]

    bot = None
    dp = None
    notifier_task = asyncio.create_task(notifier.run())
    background_tasks.append(notifier_task)

    if config.bot_token and config.admin_id is not None:
        from app.telegram.bot import build_bot_and_dispatcher

        bot, dp = build_bot_and_dispatcher(config, db, notifier)
        notifier.set_bot(bot)
        background_tasks.append(asyncio.create_task(dp.start_polling(bot)))
        logger.info("Telegram bot enabled (admin_id=%s)", config.admin_id)
    else:
        logger.info("Telegram bot disabled (BOT_TOKEN/ADMIN_ID not set) - running API-only")

    logger.info("Domain Monitor Server started on %s:%s", config.host, config.port)
    try:
        yield
    finally:
        logger.info("Shutting down")
        notifier.stop()
        stop_gc.set()
        for task in background_tasks:
            task.cancel()
        await asyncio.gather(*background_tasks, return_exceptions=True)
        if bot is not None:
            await bot.session.close()
        db.close()


app = FastAPI(title="Domain Monitor Server", version="0.1.0", lifespan=lifespan)

app.include_router(nodes.router)
app.include_router(events.router)
app.include_router(domains.router)
app.include_router(stats.router)

app.include_router(routes_auth.router)
app.include_router(routes_pages.router)
app.include_router(webadmin_ws.router)
app.mount("/admin/static", StaticFiles(directory=_STATIC_DIR), name="webadmin-static")


@app.exception_handler(RequiresLogin)
async def _requires_login_handler(request: Request, exc: RequiresLogin) -> RedirectResponse:
    return RedirectResponse(f"/admin/login?next={request.url.path}", status_code=303)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/metrics")
def metrics(request: Request) -> Response:
    db: Database = request.app.state.db
    config = request.app.state.config
    now = datetime.now(timezone.utc)
    nodes_list = db.list_nodes()
    NODES_TOTAL.set(len(nodes_list))
    NODES_ONLINE.set(sum(1 for n in nodes_list if n.is_online(config.node_offline_after_seconds, now)))
    DOMAINS_TOTAL.set(db.count_domains())
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
