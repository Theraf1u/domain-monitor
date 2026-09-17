"""Background task that periodically purges events older than
EVENT_RETENTION_DAYS. The aggregated `domains` table (first/last seen,
hit count) is never purged by this - only the detailed per-sighting
`events` history, which is what actually grows without bound."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app import runtime_settings
from app.config import Config
from app.database import Database

logger = logging.getLogger(__name__)

_CHECK_INTERVAL_SECONDS = 3600


class RetentionTask:
    def __init__(self, db: Database, config: Config) -> None:
        self.db = db
        self.config = config
        self._stopped = asyncio.Event()

    async def run(self) -> None:
        while not self._stopped.is_set():
            try:
                retention_days = runtime_settings.get_event_retention_days(self.db, self.config)
                if retention_days <= 0:
                    logger.debug("Event retention disabled (0 or less), keeping events forever")
                else:
                    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
                    deleted = await asyncio.to_thread(self.db.purge_events_older_than, cutoff)
                    if deleted:
                        logger.info("Retention: purged %d event(s) older than %d days", deleted, retention_days)
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
