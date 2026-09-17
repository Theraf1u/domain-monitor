"""Session auth + CSRF verification for the Web Admin. Kept independent of
app/api/deps.py: the API uses X-Admin-Key / node bearer tokens, the Web
Admin uses browser sessions - two different trust boundaries that should
never accidentally accept each other's credentials.
"""
from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from app.config import Config
from app.database import Database
from app.models import User
from app.webadmin.auth import CSRF_HEADER, SESSION_COOKIE_NAME


class RequiresLogin(Exception):
    """Raised by page routes instead of returning a response directly, so
    a single exception handler can turn it into a redirect-to-login."""


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_config(request: Request) -> Config:
    return request.app.state.config


def csrf_token_for(config: Config, session_id: str) -> str:
    return hmac.new(config.admin_api_key.encode(), session_id.encode(), hashlib.sha256).hexdigest()


def current_user(request: Request, db: Database = Depends(get_db)) -> User | None:
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        return None
    session = db.get_session(session_id)
    if session is None or session.expires_at < datetime.now(timezone.utc):
        return None
    return db.get_user(session.user_id)


def require_user(request: Request, user: User | None = Depends(current_user)) -> User:
    if user is None:
        raise RequiresLogin()
    return user


def require_role(min_role: str):
    def _dep(user: User = Depends(require_user)) -> User:
        if not user.has_at_least(min_role):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return user
    return _dep


async def verify_csrf(
    request: Request, config: Config = Depends(get_config), user: User = Depends(require_user),
) -> None:
    session_id = request.cookies.get(SESSION_COOKIE_NAME, "")
    expected = csrf_token_for(config, session_id)
    sent = request.headers.get(CSRF_HEADER, "")
    if not sent:
        form = await request.form()
        sent = str(form.get("csrf_token", ""))
    if not sent or not hmac.compare_digest(sent, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")
