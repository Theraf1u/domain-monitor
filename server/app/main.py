"""FastAPI application entrypoint. Serves the agent-facing REST API and
runs the Telegram bot as a background asyncio task alongside uvicorn's own
event loop. Telegram is the only human-facing control surface - there is
no browser UI."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app import runtime_settings
from app.api import domains, events, nodes, stats
from app.backup_task import BackupTask
from app.config import load_config
from app.database import Database
from app.health_monitor import NodeHealthMonitor
from app.live_view import LiveViewManager
from app.logging_config import setup_logging
from app.metrics import DOMAINS_TOTAL, NODES_ONLINE, NODES_TOTAL
from app.notifier import Notifier
from app.rate_limit import NodeRateLimiter
from app.retention import RetentionTask
from app.telegram.bot import build_bot_and_dispatcher, configure_bot_profile
from app.topic_binding import TopicBindingManager

logger = logging.getLogger(__name__)


async def _run_polling_forever(dp, bot, stopped: asyncio.Event) -> None:
    """Wraps dp.start_polling() with a restart-on-crash loop. Long-poll
    connections can get cut by an intermediate proxy/NAT (observed: a
    SOCKS5 proxy silently dropping the connection) - without this, one
    dropped connection would permanently kill the bot until the container
    is manually restarted."""
    backoff = 1
    while not stopped.is_set():
        try:
            # handle_signals=False: this task runs inside uvicorn's own
            # event loop/process - aiogram installing its own SIGTERM/SIGINT
            # handlers here would fight with uvicorn's, since asyncio only
            # keeps the last handler registered for a given signal.
            await dp.start_polling(bot, polling_timeout=20, handle_signals=False)
            backoff = 1  # a clean return (e.g. dp.stop_polling()) resets backoff
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Telegram polling crashed, restarting in %ss", backoff)
        if stopped.is_set():
            return
        try:
            await asyncio.wait_for(stopped.wait(), timeout=backoff)
        except asyncio.TimeoutError:
            pass
        backoff = min(backoff * 2, 30)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    setup_logging(config.log_level, config.log_dir)
    logger.info("Starting Domain Monitor Server")

    db = Database(config.database_path)
    db.migrate()

    notifier = Notifier(db, config.admin_ids)
    backup_task = BackupTask(db, config, notifier)
    topic_binding = TopicBindingManager()
    live_view = LiveViewManager()

    app.state.config = config
    app.state.db = db
    app.state.notifier = notifier
    app.state.backup_task = backup_task
    app.state.event_rate_limiter = NodeRateLimiter(
        config.events_rate_limit_capacity, config.events_rate_limit_per_second,
    )

    stop_polling = asyncio.Event()
    health_monitor = NodeHealthMonitor(db, config, notifier)
    background_tasks = [
        asyncio.create_task(RetentionTask(db, config).run()),
        asyncio.create_task(notifier.run()),
        asyncio.create_task(backup_task.run()),
        asyncio.create_task(health_monitor.run()),
    ]

    bot, dp = build_bot_and_dispatcher(config, db, notifier, backup_task, topic_binding, live_view)
    notifier.set_bot(bot)
    try:
        await configure_bot_profile(bot)
    except Exception:
        # Cosmetic (command hint, menu button, description, photo) - never
        # worth failing server startup over a transient Telegram API hiccup.
        logger.exception("Failed to configure bot profile - continuing anyway")
    background_tasks.append(asyncio.create_task(_run_polling_forever(dp, bot, stop_polling)))
    logger.info("Telegram bot enabled (admin_ids=%s)", config.admin_ids)

    logger.info("Domain Monitor Server started on %s:%s", config.host, config.port)
    try:
        yield
    finally:
        logger.info("Shutting down")
        notifier.stop()
        backup_task.stop()
        health_monitor.stop()
        live_view.stop_all()
        stop_polling.set()
        for task in background_tasks:
            task.cancel()
        await asyncio.gather(*background_tasks, return_exceptions=True)
        await bot.session.close()
        db.close()


app = FastAPI(title="Domain Monitor Server", version="0.1.0", lifespan=lifespan)

app.include_router(nodes.router)
app.include_router(events.router)
app.include_router(domains.router)
app.include_router(stats.router)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/metrics")
def metrics(request: Request) -> Response:
    db: Database = request.app.state.db
    config = request.app.state.config
    now = datetime.now(timezone.utc)
    offline_after_seconds = runtime_settings.get_node_offline_after_seconds(db, config)
    nodes_list = db.list_nodes()
    NODES_TOTAL.set(len(nodes_list))
    NODES_ONLINE.set(sum(1 for n in nodes_list if n.is_online(offline_after_seconds, now)))
    DOMAINS_TOTAL.set(db.count_domains())
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
