"""Event ingestion: the endpoint agents actually call, in a loop, for the
entire lifetime of the deployment. Accepts a batch (the agent buffers and
retries locally, so this is always "here's what I've got since last time",
not a single event) and applies each one under the pushing node's own
identity - an agent can never write events under another node's name."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request

from app.api.deps import get_db, get_notifier, require_node
from app.api.schemas import EventBatchRequest, EventBatchResponse
from app.database import Database
from app.filters import classify_domain
from app.metrics import EVENTS_TOTAL, NEW_DOMAINS_TOTAL, WATCHLIST_HITS_TOTAL
from app.models import Node
from app.notifier import Notifier

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/events", tags=["events"])


@router.post("", response_model=EventBatchResponse)
async def ingest_events(
    request: Request, body: EventBatchRequest, node: Node = Depends(require_node), db: Database = Depends(get_db),
    notifier: Notifier = Depends(get_notifier),
) -> EventBatchResponse:
    broadcaster = getattr(request.app.state, "broadcaster", None)
    rules = await asyncio.to_thread(db.all_filter_rules_cached)
    new_domains: list[str] = []
    for event in body.events:
        occurred_at = event.occurred_at or datetime.now(timezone.utc)
        domain_row, is_new = await asyncio.to_thread(
            db.record_event, node.id, event.domain, event.source, occurred_at
        )
        EVENTS_TOTAL.inc()
        if is_new:
            new_domains.append(domain_row.domain)
            NEW_DOMAINS_TOTAL.inc()
            verdict = classify_domain(domain_row.domain, rules)
            if verdict.is_watched:
                WATCHLIST_HITS_TOTAL.inc()
                await notifier.schedule_watchlist(node, domain_row.domain)
            elif verdict.suppresses_notification:
                await asyncio.to_thread(db.set_ignored, domain_row.id, True)
            else:
                await notifier.schedule(node, domain_row.domain)
        if broadcaster is not None:
            await broadcaster.broadcast({
                "domain": event.domain, "node": node.name, "source": event.source,
                "occurred_at": occurred_at.isoformat(), "is_new": is_new,
            })

    await asyncio.to_thread(db.touch_last_seen, node.id)
    if new_domains:
        logger.info("Node %s (%s) reported %d new domain(s)", node.id, node.name, len(new_domains))

    return EventBatchResponse(accepted=len(body.events), new_domains=new_domains)
