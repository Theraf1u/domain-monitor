"""Talks to the Domain Monitor Server: drains the local outbox in batches
and sends periodic heartbeats. Both loops retry with exponential backoff
and never raise out of `run()` - a server outage should degrade the agent
(events pile up in the bounded local buffer), never crash it.
"""
from __future__ import annotations

import asyncio
import logging
import socket

import httpx

from app.buffer import Buffer
from app.config import Config
from app.remote_control import RemoteControl

logger = logging.getLogger(__name__)

AGENT_VERSION = "1.0.0"

_MAX_BACKOFF_SECONDS = 120


class UplinkTask:
    def __init__(self, config: Config, buffer: Buffer, remote_control: RemoteControl) -> None:
        self.config = config
        self.buffer = buffer
        self.remote_control = remote_control
        self._stopped = asyncio.Event()
        self._client = httpx.AsyncClient(
            base_url=config.server_url,
            headers={"Authorization": f"Bearer {config.node_token}"},
            timeout=config.http_timeout_seconds,
        )

    async def run(self) -> None:
        backoff = self.config.batch_interval_seconds
        while not self._stopped.is_set():
            if not self.remote_control.sending_enabled:
                # Paused from Telegram - leave the outbox alone (it keeps
                # accumulating, bounded by max_buffer_size) and just wait
                # for the next heartbeat to possibly clear the flag.
                sent = 0
            else:
                sent = await self._send_pending_batch()
            if sent is None:
                backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)
            else:
                backoff = self.config.batch_interval_seconds
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass

    async def run_heartbeat(self) -> None:
        backoff = self.config.heartbeat_interval_seconds
        while not self._stopped.is_set():
            ok = await self._send_heartbeat()
            backoff = self.config.heartbeat_interval_seconds if ok else min(backoff * 2, _MAX_BACKOFF_SECONDS)
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass

    async def stop(self) -> None:
        self._stopped.set()
        await self._client.aclose()

    async def _send_pending_batch(self) -> int | None:
        """Returns the number of events sent, or None on failure (batch
        stays in the outbox and will be retried)."""
        batch = self.buffer.peek_batch(self.config.batch_max_size)
        if not batch:
            return 0

        payload = {
            "events": [
                {"domain": e.domain, "source": e.source, "occurred_at": e.occurred_at}
                for e in batch
            ]
        }
        try:
            resp = await self._client.post("/api/v1/events", json=payload)
            if resp.status_code == 403:
                logger.error("Node token was revoked by the server - agent cannot send events anymore")
                return None
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Failed to send %d event(s) to server: %s", len(batch), exc)
            return None

        self.buffer.delete_ids([e.id for e in batch])
        logger.debug("Sent %d event(s) to server", len(batch))
        return len(batch)

    async def _send_heartbeat(self) -> bool:
        payload = {
            "version": AGENT_VERSION,
            "hostname": socket.gethostname(),
            "ip": _best_effort_local_ip(),
        }
        try:
            resp = await self._client.post("/api/v1/nodes/heartbeat", json=payload)
            resp.raise_for_status()
            self._apply_remote_control(resp)
            return True
        except httpx.HTTPError as exc:
            logger.debug("Heartbeat failed: %s", exc)
            return False

    def _apply_remote_control(self, resp: httpx.Response) -> None:
        try:
            body = resp.json()
        except ValueError:
            return  # older server without a heartbeat body - leave flags as they are
        if "monitoring_enabled" in body:
            self.remote_control.monitoring_enabled = bool(body["monitoring_enabled"])
        if "sending_enabled" in body:
            self.remote_control.sending_enabled = bool(body["sending_enabled"])


def _best_effort_local_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return None
