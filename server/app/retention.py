"""Background task that periodically purges events older than
EVENT_RETENTION_DAYS. The aggregated `domains` table (first/last seen,
hit count) is never purged by this - only the detailed per-sighting
`events` history, which is what actually grows without bound."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.database import Database

logger = logging.getLogger(__name__)

_CHECK_INTERVAL_SECONDS = 3600


class RetentionTask:
    def __init__(self, db: Database, retention_days: int) -> None:
        self.db = db
        self.retention_days = retention_days
        self._stopped = asyncio.Event()

    async def run(self) -> None:
        if self.retention_days <= 0:
            logger.info("Event retention disabled (EVENT_RETENTION_DAYS<=0), keeping events forever")
            return
        while not self._stopped.is_set():
            try:
                cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
                deleted = await asyncio.to_thread(self.db.purge_events_older_than, cutoff)
                if deleted:
                    logger.info("Retention: purged %d event(s) older than %d days", deleted, self.retention_days)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Retention purge failed")
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=_CHECK_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass

    def stop(self) -> None:
        self._stopped.set()
