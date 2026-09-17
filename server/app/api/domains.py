"""Read/search access to the aggregated domains table. Full filtering
(ignore/allow/watch lists, bulk actions, export) lands in a later stage;
this is the minimum needed to verify ingestion is actually working."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_db, require_admin
from app.api.schemas import DomainResponse
from app.database import Database

router = APIRouter(prefix="/api/v1/domains", tags=["domains"], dependencies=[Depends(require_admin)])


@router.get("", response_model=list[DomainResponse])
def list_domains(
    db: Database = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    search: str | None = None,
    order_by: Literal["last_seen", "first_seen", "hits", "domain"] = "last_seen",
    node_id: int | None = None,
) -> list[DomainResponse]:
    domains = db.list_domains(limit=limit, offset=offset, search=search, order_by=order_by, node_id=node_id)
    return [DomainResponse.from_domain(d) for d in domains]
