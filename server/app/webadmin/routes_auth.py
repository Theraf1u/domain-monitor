"""Login/logout and first-run setup (create the initial Owner account)."""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import Config
from app.database import Database
from app.webadmin.auth import (
    LoginRateLimiter,
    SESSION_COOKIE_NAME,
    hash_password,
    new_session_id,
    session_expiry,
    verify_password,
)
from app.webadmin.deps import current_user, get_config, get_db
from app.webadmin.templating import templates

router = APIRouter(prefix="/admin", tags=["webadmin-auth"])

_PRE_CSRF_COOKIE = "csrf_pre"
_login_limiter = LoginRateLimiter()


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.get("/setup", response_class=HTMLResponse)
def setup_get(request: Request, db: Database = Depends(get_db)):
    if db.any_user_exists():
        return RedirectResponse("/admin/login", status_code=303)
    token = secrets.token_urlsafe(16)
    resp = templates.TemplateResponse(request, "setup.html", {"csrf_token": token, "error": None})
    resp.set_cookie(_PRE_CSRF_COOKIE, token, httponly=True, samesite="lax")
    return resp


@router.post("/setup", response_class=HTMLResponse)
def setup_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    password2: str = Form(...),
    csrf_token: str = Form(...),
    db: Database = Depends(get_db),
):
    if db.any_user_exists():
        return RedirectResponse("/admin/login", status_code=303)
    cookie_token = request.cookies.get(_PRE_CSRF_COOKIE, "")
    if not cookie_token or cookie_token != csrf_token:
        return templates.TemplateResponse(
            request, "setup.html", {"csrf_token": cookie_token, "error": "Форма устарела, попробуйте снова"},
        )
    username = username.strip()
    if len(username) < 3 or len(password) < 8:
        return templates.TemplateResponse(
            request, "setup.html",
            {"csrf_token": cookie_token, "error": "Логин от 3 символов, пароль от 8 символов"},
        )
    if password != password2:
        return templates.TemplateResponse(
            request, "setup.html", {"csrf_token": cookie_token, "error": "Пароли не совпадают"},
        )

    pwd_hash, salt = hash_password(password)
    user = db.create_user(username, pwd_hash, salt, role="owner")

    session_id = new_session_id()
    db.create_session(session_id, user.id, session_expiry())
    resp = RedirectResponse("/admin/", status_code=303)
    resp.set_cookie(SESSION_COOKIE_NAME, session_id, httponly=True, samesite="lax", max_age=7 * 24 * 3600)
    resp.delete_cookie(_PRE_CSRF_COOKIE)
    return resp


@router.get("/login", response_class=HTMLResponse)
def login_get(request: Request, db: Database = Depends(get_db)):
    if not db.any_user_exists():
        return RedirectResponse("/admin/setup", status_code=303)
    if current_user(request, db) is not None:
        return RedirectResponse("/admin/", status_code=303)
    token = secrets.token_urlsafe(16)
    resp = templates.TemplateResponse(request, "login.html", {"csrf_token": token, "error": None})
    resp.set_cookie(_PRE_CSRF_COOKIE, token, httponly=True, samesite="lax")
    return resp


@router.post("/login", response_class=HTMLResponse)
def login_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
    db: Database = Depends(get_db),
):
    cookie_token = request.cookies.get(_PRE_CSRF_COOKIE, "")
    ip = _client_ip(request)

    if not cookie_token or cookie_token != csrf_token:
        return templates.TemplateResponse(
            request, "login.html", {"csrf_token": cookie_token, "error": "Форма устарела, попробуйте снова"},
        )

    if _login_limiter.is_locked_out(ip):
        return templates.TemplateResponse(
            request, "login.html",
            {"csrf_token": cookie_token, "error": "Слишком много попыток входа. Попробуйте позже."},
        )

    user = db.get_user_by_username(username.strip())
    if user is None or not verify_password(password, user.password_hash, user.password_salt):
        _login_limiter.record_failure(ip)
        return templates.TemplateResponse(
            request, "login.html", {"csrf_token": cookie_token, "error": "Неверный логин или пароль"},
        )

    _login_limiter.record_success(ip)
    session_id = new_session_id()
    db.create_session(session_id, user.id, session_expiry())
    resp = RedirectResponse("/admin/", status_code=303)
    resp.set_cookie(SESSION_COOKIE_NAME, session_id, httponly=True, samesite="lax", max_age=7 * 24 * 3600)
    resp.delete_cookie(_PRE_CSRF_COOKIE)
    return resp


@router.post("/logout")
def logout(request: Request, db: Database = Depends(get_db)):
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id:
        db.delete_session(session_id)
    resp = RedirectResponse("/admin/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE_NAME)
    return resp
