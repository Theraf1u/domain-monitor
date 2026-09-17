"""Shared FastAPI dependencies: database access and the two auth schemes.

Two independent auth schemes exist side by side:
  - Admin auth (`X-Admin-Key` header) - for managing nodes/settings via the
    REST API directly (scripting/CLI use). Day-to-day management is via
    the Telegram bot, which talks straight to the database, not this API.
  - Node auth (`Authorization: Bearer <token>`) - for an agent pushing its
    own events/heartbeat. Scoped to that one node; a compromised node
    token can never read or modify another node's data.
"""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request, status

from app.config import Config
from app.database import Database
from app.models import Node
from app.notifier import Notifier
from app.security import constant_time_eq, hash_token


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_config(request: Request) -> Config:
    return request.app.state.config


def get_notifier(request: Request) -> Notifier:
    return request.app.state.notifier


def require_admin(
    x_admin_key: str = Header(default=""),
    config: Config = Depends(get_config),
) -> None:
    if not x_admin_key or not constant_time_eq(x_admin_key, config.admin_api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing X-Admin-Key")


def require_node(
    authorization: str = Header(default=""),
    db: Database = Depends(get_db),
) -> Node:
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Empty bearer token")

    node = db.get_node_by_token_hash(hash_token(token))
    if node is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown node token")
    if node.status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Node token has been revoked")
    return node
