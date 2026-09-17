"""Dashboard summary numbers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends

from app.api.deps import get_config, get_db, require_admin
from app.api.schemas import StatsResponse
from app.config import Config
from app.database import Database

router = APIRouter(prefix="/api/v1/stats", tags=["stats"], dependencies=[Depends(require_admin)])


@router.get("", response_model=StatsResponse)
def get_stats(db: Database = Depends(get_db), config: Config = Depends(get_config)) -> StatsResponse:
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    nodes = db.list_nodes()
    nodes_online = sum(1 for n in nodes if n.is_online(config.node_offline_after_seconds, now))

    return StatsResponse(
        nodes_online=nodes_online,
        nodes_total=len(nodes),
        unique_domains=db.count_domains(),
        events_today=db.count_events_since(today_start),
        new_domains_today=db.count_domains(since=today_start),
    )
