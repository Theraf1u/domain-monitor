"""Node registry: create/list/inspect/revoke/regenerate, plus the agent's
own heartbeat endpoint. Registry management requires admin auth; the
heartbeat endpoint requires node auth (an agent can only touch itself)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app import fleet_control, runtime_settings
from app.api.deps import get_config, get_db, require_admin, require_node
from app.api.schemas import (
    HeartbeatRequest,
    HeartbeatResponse,
    NodeCreateRequest,
    NodeCreateResponse,
    NodeResponse,
    NodeSettingsUpdateRequest,
)
from app.config import Config
from app.database import Database
from app.models import Node
from app.security import generate_node_token, hash_token

router = APIRouter(prefix="/api/v1/nodes", tags=["nodes"])


@router.post("", response_model=NodeCreateResponse, dependencies=[Depends(require_admin)])
def create_node(body: NodeCreateRequest, db: Database = Depends(get_db)) -> NodeCreateResponse:
    if db.name_exists(body.name):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A node with this name already exists")
    token = generate_node_token()
    node = db.create_node(body.name, hash_token(token))
    return NodeCreateResponse(id=node.id, name=node.name, token=token)


@router.get("", response_model=list[NodeResponse], dependencies=[Depends(require_admin)])
def list_nodes(db: Database = Depends(get_db), config: Config = Depends(get_config)) -> list[NodeResponse]:
    offline_after = runtime_settings.get_node_offline_after_seconds(db, config)
    return [NodeResponse.from_node(n, offline_after) for n in db.list_nodes()]


@router.get("/{node_id}", response_model=NodeResponse, dependencies=[Depends(require_admin)])
def get_node(node_id: int, db: Database = Depends(get_db), config: Config = Depends(get_config)) -> NodeResponse:
    node = db.get_node(node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")
    return NodeResponse.from_node(node, runtime_settings.get_node_offline_after_seconds(db, config))


@router.patch("/{node_id}", response_model=NodeResponse, dependencies=[Depends(require_admin)])
def update_node_settings(
    node_id: int, body: NodeSettingsUpdateRequest, db: Database = Depends(get_db),
    config: Config = Depends(get_config),
) -> NodeResponse:
    node = db.get_node(node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")
    if body.monitoring_enabled is not None:
        db.set_node_monitoring(node_id, body.monitoring_enabled)
    if body.notifications_enabled is not None:
        db.set_node_notifications(node_id, body.notifications_enabled)
    return NodeResponse.from_node(db.get_node(node_id), runtime_settings.get_node_offline_after_seconds(db, config))


@router.post("/{node_id}/regenerate-token", response_model=NodeCreateResponse, dependencies=[Depends(require_admin)])
def regenerate_token(node_id: int, db: Database = Depends(get_db)) -> NodeCreateResponse:
    node = db.get_node(node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")
    token = generate_node_token()
    db.regenerate_token(node_id, hash_token(token))
    return NodeCreateResponse(id=node.id, name=node.name, token=token)


@router.post(
    "/{node_id}/revoke", status_code=status.HTTP_204_NO_CONTENT, response_model=None,
    dependencies=[Depends(require_admin)],
)
def revoke_node(node_id: int, db: Database = Depends(get_db)) -> None:
    node = db.get_node(node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")
    db.set_node_status(node_id, "revoked")


@router.delete(
    "/{node_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None,
    dependencies=[Depends(require_admin)],
)
def delete_node(node_id: int, db: Database = Depends(get_db)) -> None:
    node = db.get_node(node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")
    db.delete_node(node_id)


@router.post("/heartbeat", response_model=HeartbeatResponse)
def heartbeat(
    body: HeartbeatRequest, node: Node = Depends(require_node), db: Database = Depends(get_db),
) -> HeartbeatResponse:
    db.touch_heartbeat(node.id, version=body.version, ip=body.ip, hostname=body.hostname)
    return HeartbeatResponse(
        monitoring_enabled=node.monitoring_enabled and fleet_control.is_monitoring_enabled(db),
        sending_enabled=fleet_control.is_sending_enabled(db),
    )
