"""Dashboard, Nodes, Domains, Settings, Users pages. Reads go through
plain dependency-injected DB calls; every state-changing POST requires
`verify_csrf` and, where relevant, a minimum role via `require_role`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import Config
from app.database import Database
from app.security import generate_node_token, hash_token
from app.webadmin.auth import hash_password
from app.webadmin.deps import csrf_token_for, get_config, get_db, require_role, require_user, verify_csrf
from app.webadmin.templating import templates

router = APIRouter(prefix="/admin", tags=["webadmin-pages"])


def _ctx(request: Request, user, config: Config, **extra) -> dict:
    session_id = request.cookies.get("session_id", "")
    return {"request": request, "user": user, "csrf_token": csrf_token_for(config, session_id), **extra}


# ------------------------------------------------------------------
# Dashboard
# ------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user=Depends(require_user), db: Database = Depends(get_db), config: Config = Depends(get_config)):
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    minute_ago = now - timedelta(minutes=1)

    nodes = db.list_nodes()
    online = sum(1 for n in nodes if n.is_online(config.node_offline_after_seconds, now))

    stats = {
        "nodes_online": online,
        "nodes_total": len(nodes),
        "unique_domains": db.count_domains(),
        "events_today": db.count_events_since(today_start),
        "new_domains_today": db.count_domains(since=today_start),
        "events_per_minute": db.count_events_since(minute_ago),
    }
    top_domains = db.top_domains(limit=10)
    return templates.TemplateResponse(
        "dashboard.html", _ctx(request, user, config, active="dashboard", stats=stats, top_domains=top_domains),
    )


# ------------------------------------------------------------------
# Nodes
# ------------------------------------------------------------------

@router.get("/nodes", response_class=HTMLResponse)
def nodes_page(request: Request, user=Depends(require_user), db: Database = Depends(get_db), config: Config = Depends(get_config)):
    now = datetime.now(timezone.utc)
    nodes = db.list_nodes()
    return templates.TemplateResponse(
        "nodes.html",
        _ctx(request, user, config, active="nodes", nodes=nodes, now=now,
             offline_after=config.node_offline_after_seconds),
    )


@router.post("/nodes/create")
def nodes_create(
    request: Request, name: str = Form(...), db: Database = Depends(get_db),
    user=Depends(require_role("admin")), _csrf=Depends(verify_csrf),
):
    name = name.strip()
    if not name or db.name_exists(name):
        return RedirectResponse("/admin/nodes", status_code=303)
    token = generate_node_token()
    db.create_node(name, hash_token(token))
    resp = RedirectResponse(f"/admin/nodes?new_token={token}&new_name={name}", status_code=303)
    return resp


@router.post("/nodes/{node_id}/toggle-monitoring")
def nodes_toggle_monitoring(
    node_id: int, db: Database = Depends(get_db), user=Depends(require_role("admin")), _csrf=Depends(verify_csrf),
):
    node = db.get_node(node_id)
    if node:
        db.set_node_monitoring(node_id, not node.monitoring_enabled)
    return RedirectResponse("/admin/nodes", status_code=303)


@router.post("/nodes/{node_id}/toggle-notifications")
def nodes_toggle_notifications(
    node_id: int, db: Database = Depends(get_db), user=Depends(require_role("admin")), _csrf=Depends(verify_csrf),
):
    node = db.get_node(node_id)
    if node:
        db.set_node_notifications(node_id, not node.notifications_enabled)
    return RedirectResponse("/admin/nodes", status_code=303)


@router.post("/nodes/{node_id}/revoke")
def nodes_revoke(
    node_id: int, db: Database = Depends(get_db), user=Depends(require_role("admin")), _csrf=Depends(verify_csrf),
):
    db.set_node_status(node_id, "revoked")
    return RedirectResponse("/admin/nodes", status_code=303)


@router.post("/nodes/{node_id}/delete")
def nodes_delete(
    node_id: int, db: Database = Depends(get_db), user=Depends(require_role("admin")), _csrf=Depends(verify_csrf),
):
    db.delete_node(node_id)
    return RedirectResponse("/admin/nodes", status_code=303)


# ------------------------------------------------------------------
# Domains
# ------------------------------------------------------------------

@router.get("/domains", response_class=HTMLResponse)
def domains_page(
    request: Request, user=Depends(require_user), db: Database = Depends(get_db), config: Config = Depends(get_config),
    search: str | None = None, order_by: str = "last_seen", page: int = 1,
):
    page = max(1, page)
    page_size = 50
    domains = db.list_domains(limit=page_size, offset=(page - 1) * page_size, search=search, order_by=order_by)
    return templates.TemplateResponse(
        "domains.html",
        _ctx(request, user, config, active="domains", domains=domains, search=search or "",
             order_by=order_by, page=page, page_size=page_size),
    )


@router.post("/domains/{domain_id}/ignore")
def domains_ignore(
    domain_id: int, db: Database = Depends(get_db), user=Depends(require_role("admin")), _csrf=Depends(verify_csrf),
):
    db.set_ignored(domain_id, True)
    return RedirectResponse("/admin/domains", status_code=303)


@router.post("/domains/{domain_id}/unignore")
def domains_unignore(
    domain_id: int, db: Database = Depends(get_db), user=Depends(require_role("admin")), _csrf=Depends(verify_csrf),
):
    db.set_ignored(domain_id, False)
    return RedirectResponse("/admin/domains", status_code=303)


@router.get("/domains/export.txt")
def domains_export(user=Depends(require_user), db: Database = Depends(get_db)):
    from fastapi.responses import PlainTextResponse

    domains = db.list_domains(limit=100000, order_by="domain")
    content = "\n".join(d.domain for d in domains) + ("\n" if domains else "")
    return PlainTextResponse(content, headers={"Content-Disposition": "attachment; filename=domains.txt"})


# ------------------------------------------------------------------
# Live events page (WebSocket wiring lives in app/webadmin/ws.py)
# ------------------------------------------------------------------

@router.get("/events", response_class=HTMLResponse)
def events_page(request: Request, user=Depends(require_user), config: Config = Depends(get_config)):
    return templates.TemplateResponse("events.html", _ctx(request, user, config, active="events"))


# ------------------------------------------------------------------
# Filters (ignore / allow / watch)
# ------------------------------------------------------------------

@router.get("/filters", response_class=HTMLResponse)
def filters_page(request: Request, user=Depends(require_user), db: Database = Depends(get_db), config: Config = Depends(get_config)):
    rules = {
        "ignore": db.list_filter_rules("ignore"),
        "allow": db.list_filter_rules("allow"),
        "watch": db.list_filter_rules("watch"),
    }
    return templates.TemplateResponse("filters.html", _ctx(request, user, config, active="filters", rules=rules))


@router.post("/filters/create")
def filters_create(
    list_type: str = Form(...), pattern_type: str = Form(...), pattern: str = Form(...),
    db: Database = Depends(get_db), user=Depends(require_role("admin")), _csrf=Depends(verify_csrf),
):
    pattern = pattern.strip().lower()
    if list_type not in ("ignore", "allow", "watch") or pattern_type not in ("exact", "suffix", "wildcard") or not pattern:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid input")
    db.add_filter_rule(list_type, pattern_type, pattern)
    return RedirectResponse("/admin/filters", status_code=303)


@router.post("/filters/{rule_id}/delete")
def filters_delete(
    rule_id: int, db: Database = Depends(get_db), user=Depends(require_role("admin")), _csrf=Depends(verify_csrf),
):
    db.remove_filter_rule(rule_id)
    return RedirectResponse("/admin/filters", status_code=303)


# ------------------------------------------------------------------
# Settings
# ------------------------------------------------------------------

@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, user=Depends(require_user), config: Config = Depends(get_config)):
    return templates.TemplateResponse("settings.html", _ctx(request, user, config, active="settings"))


# ------------------------------------------------------------------
# Users (owner only)
# ------------------------------------------------------------------

@router.get("/users", response_class=HTMLResponse)
def users_page(request: Request, user=Depends(require_role("owner")), db: Database = Depends(get_db), config: Config = Depends(get_config)):
    users = db.list_users()
    return templates.TemplateResponse("users.html", _ctx(request, user, config, active="users", users=users))


@router.post("/users/create")
def users_create(
    username: str = Form(...), password: str = Form(...), role: str = Form(...),
    db: Database = Depends(get_db), user=Depends(require_role("owner")), _csrf=Depends(verify_csrf),
):
    username = username.strip()
    if len(username) < 3 or len(password) < 8 or role not in ("owner", "admin", "viewer"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid input")
    if db.get_user_by_username(username) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username taken")
    pwd_hash, salt = hash_password(password)
    db.create_user(username, pwd_hash, salt, role)
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/users/{user_id}/delete")
def users_delete(
    user_id: int, db: Database = Depends(get_db), user=Depends(require_role("owner")), _csrf=Depends(verify_csrf),
):
    if user_id == user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete yourself")
    db.delete_user(user_id)
    db.delete_sessions_for_user(user_id)
    return RedirectResponse("/admin/users", status_code=303)
