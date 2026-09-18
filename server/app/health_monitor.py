"""Background task that watches each node's online/offline state and
fires exactly one notification per state transition, never a stream of
repeated pings while a node stays down. Structurally modeled on
RetentionTask: a periodic tick, graceful stop, exceptions logged but
never fatal to the loop.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app import runtime_settings
from app.config import Config
from app.database import Database
from app.notifier import Notifier

logger = logging.getLogger(__name__)

_CHECK_INTERVAL_SECONDS = 60


class NodeHealthMonitor:
    def __init__(self, db: Database, config: Config, notifier: Notifier) -> None:
        self.db = db
        self.config = config
        self.notifier = notifier
        self._stopped = asyncio.Event()

    async def run(self) -> None:
        while not self._stopped.is_set():
            try:
                await self._check_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Node health check failed")
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=_CHECK_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def _check_once(self) -> None:
        now = datetime.now(timezone.utc)
        offline_after = await asyncio.to_thread(
            runtime_settings.get_node_offline_after_seconds, self.db, self.config,
        )
        nodes = await asyncio.to_thread(self.db.list_nodes)
        for node in nodes:
            if node.status != "active":
                continue  # a revoked node isn't "offline", it's gone - no alert to fire
            online = node.is_online(offline_after, now)
            if node.last_known_online is None:
                # First observation for this node (just created, or this
                # task's first pass since a restart) - record the
                # baseline silently. Firing "recovered" here would be
                # wrong for a node that was simply online the whole time.
                await asyncio.to_thread(self.db.set_node_last_known_online, node.id, online)
                continue
            if online == node.last_known_online:
                continue
            await asyncio.to_thread(self.db.set_node_last_known_online, node.id, online)
            await asyncio.to_thread(
                self.db.record_node_health_event, node.id, "recovered" if online else "offline", now,
            )
            if online:
                await self.notifier.notify_recovered(node)
            else:
                await self.notifier.notify_offline(node)

    def stop(self) -> None:
        self._stopped.set()
