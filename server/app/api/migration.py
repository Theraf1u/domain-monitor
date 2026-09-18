"""Migration 2.0 (spec 2.0 Part 2, section 3) admin API: lets the
`migrate-to`/`migrate-cutover`/`migrate-finish` CLI scripts (and the
Telegram migration-status screen) drive a migration_jobs row the same
way every other host-side script talks to the server - over the local
REST API with X-Admin-Key, never by reaching into the SQLite file
directly. Only one non-terminal job is meaningful at a time (enforced
here, not by a DB constraint - see migrations/0014_migration_jobs.sql)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_db, require_admin
from app.api.schemas import (
    MigrationJobCreateRequest,
    MigrationJobResponse,
    MigrationJobStatusUpdateRequest,
)
from app.database import Database

router = APIRouter(prefix="/api/v1/migration", tags=["migration"], dependencies=[Depends(require_admin)])

_VALID_STATUSES = {"pending", "standby", "cutover", "completed", "cancelled", "failed"}


@router.post("/jobs", response_model=MigrationJobResponse)
def create_job(body: MigrationJobCreateRequest, db: Database = Depends(get_db)) -> MigrationJobResponse:
    existing = db.active_migration_job()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A migration job (id={existing['id']}, status={existing['status']}) is already in progress",
        )
    job_id = db.create_migration_job(body.target_url)
    return MigrationJobResponse(**db.get_migration_job(job_id))


@router.get("/jobs/active", response_model=MigrationJobResponse | None)
def get_active_job(db: Database = Depends(get_db)) -> MigrationJobResponse | None:
    job = db.active_migration_job()
    return MigrationJobResponse(**job) if job else None


@router.get("/jobs/{job_id}", response_model=MigrationJobResponse)
def get_job(job_id: int, db: Database = Depends(get_db)) -> MigrationJobResponse:
    job = db.get_migration_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Migration job not found")
    return MigrationJobResponse(**job)


@router.patch("/jobs/{job_id}", response_model=MigrationJobResponse)
def update_job_status(
    job_id: int, body: MigrationJobStatusUpdateRequest, db: Database = Depends(get_db),
) -> MigrationJobResponse:
    if db.get_migration_job(job_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Migration job not found")
    if body.status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"status must be one of {sorted(_VALID_STATUSES)}",
        )
    db.set_migration_job_status(job_id, body.status, body.error)
    return MigrationJobResponse(**db.get_migration_job(job_id))
