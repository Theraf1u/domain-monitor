"""Talks to the Domain Monitor Server: drains the local outbox in batches
and sends periodic heartbeats. Both loops retry with exponential backoff
and never raise out of `run()` - a server outage should degrade the agent
(events pile up in the bounded local buffer), never crash it.
"""
from __future__ import annotations

import asyncio
import logging
import socket
import time
from datetime import datetime, timezone

import httpx

from app import runtime_config
from app.buffer import Buffer
from app.config import Config
from app.remote_control import RemoteControl
from app.sniffer import Sniffer, sources_supported

logger = logging.getLogger(__name__)

AGENT_VERSION = "1.0.0"

_MAX_BACKOFF_SECONDS = 120

# How long an error message can get before it's truncated for the
# heartbeat - just enough for an operator to recognize what's wrong
# (timeout vs. DNS failure vs. 5xx) without shipping a full traceback
# off the node on every single heartbeat.
_MAX_ERROR_LENGTH = 200


class UplinkTask:
    def __init__(
        self, config: Config, buffer: Buffer, remote_control: RemoteControl, sniffer: Sniffer | None = None,
    ) -> None:
        self.config = config
        self.buffer = buffer
        self.remote_control = remote_control
        self.sniffer = sniffer
        self._stopped = asyncio.Event()
        self._start_time = time.monotonic()
        self._last_send_error: str | None = None
        self._last_send_success_at: str | None = None
        # Picks up a persisted migration switchover from a previous run
        # (spec 3.4) so a restarted agent resumes on the server it was
        # last confirmed-migrated to, not the original .env SERVER_URL.
        effective_url = runtime_config.effective_server_url(config.server_url, config.data_dir)
        self._client = httpx.AsyncClient(
            base_url=effective_url,
            headers={"Authorization": f"Bearer {config.node_token}"},
            timeout=config.http_timeout_seconds,
        )

    async def run(self) -> None:
        backoff = self.config.batch_interval_seconds
        while not self._stopped.is_set():
            if not self.remote_control.sending_enabled:
                # Paused from Telegram - leave the outbox alone (it keeps
                # accumulating, bounded by max_buffer_bytes) and just wait
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
                {"domain": e.domain, "source": e.source, "occurred_at": e.occurred_at, "hits": e.hits}
                for e in batch
            ]
        }
        try:
            resp = await self._client.post("/api/v1/events", json=payload)
            if resp.status_code == 403:
                logger.error("Node token was revoked by the server - agent cannot send events anymore")
                self._last_send_error = "403: node token revoked"
                return None
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Failed to send %d event(s) to server: %s", len(batch), exc)
            self._last_send_error = str(exc)[:_MAX_ERROR_LENGTH]
            return None

        self.buffer.delete_ids([e.id for e in batch])
        self._last_send_error = None
        self._last_send_success_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        logger.debug("Sent %d event(s) to server", len(batch))
        return len(batch)

    async def _send_heartbeat(self) -> bool:
        payload = {
            "version": AGENT_VERSION,
            "hostname": socket.gethostname(),
            "ip": _best_effort_local_ip(),
            # Lets the bot show "buffered, not sent yet" instead of the
            # domain list just looking frozen while sending is paused -
            # the agent keeps capturing locally the whole time, this is
            # the only way the server can see that it's happening.
            "buffer_size": self.buffer.size(),
            # Everything below is optional/additive on the server side -
            # an older server that doesn't know these fields yet just
            # ignores them (FastAPI/Pydantic drops unknown keys by
            # default), so this never breaks talking to an old server.
            "agent_uptime_seconds": int(time.monotonic() - self._start_time),
            "buffer_bytes": self.buffer.size_bytes(),
            "buffer_limit_bytes": self.config.max_buffer_bytes,
            "dropped_events_total": self.buffer.dropped_total(),
            "last_send_error": self._last_send_error,
            "last_send_success_at": self._last_send_success_at,
        }
        if self.sniffer is not None:
            status = self.sniffer.capture_status()
            if "tls_sni" in status:
                payload["capture_tls_running"] = status["tls_sni"]
            if "dns" in status:
                payload["capture_dns_running"] = status["dns"]
            payload["sources_supported"] = sources_supported()
            payload["sources_enabled"] = self.sniffer.sources_enabled()
        try:
            resp = await self._client.post("/api/v1/nodes/heartbeat", json=payload)
            resp.raise_for_status()
            migration_target = self._apply_remote_control(resp)
            if migration_target:
                # Deliberately awaited here, not fired-and-forgotten: the
                # heartbeat loop's own backoff/retry naturally paces retries
                # of a failed migration attempt, and only one migration
                # attempt is ever in flight at a time.
                await self._try_migrate(migration_target)
            return True
        except httpx.HTTPError as exc:
            logger.debug("Heartbeat failed: %s", exc)
            return False

    def _apply_remote_control(self, resp: httpx.Response) -> str | None:
        """Returns a migration target URL to attempt switching to, or None.
        Spec 3.3 step 1: receiving the field must NOT switch immediately -
        it only returns the candidate for _try_migrate() to verify."""
        try:
            body = resp.json()
        except ValueError:
            return None  # older server without a heartbeat body - leave flags as they are
        if "monitoring_enabled" in body:
            self.remote_control.monitoring_enabled = bool(body["monitoring_enabled"])
        if "sending_enabled" in body:
            self.remote_control.sending_enabled = bool(body["sending_enabled"])
        target = body.get("migration_target_url")
        if not target or not isinstance(target, str):
            return None
        target = target.rstrip("/")
        current = str(self._client.base_url).rstrip("/")
        return target if target and target != current else None

    async def _try_migrate(self, target_url: str) -> None:
        """Spec 3.3's exact switchover sequence: verify the target is alive,
        verify it accepts THIS node's own token, only then persist+switch.
        Any failure anywhere leaves the agent exactly on the current server
        (base_url and the persisted override are both left untouched) -
        the buffer/outbox is never touched by this method at all, so
        events already captured are never at risk regardless of outcome."""
        try:
            async with httpx.AsyncClient(timeout=self.config.http_timeout_seconds) as probe:
                health_resp = await probe.get(f"{target_url}/healthz")
                health_resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Migration target %s failed /healthz check (%s) - staying on current server", target_url, exc)
            return

        try:
            async with httpx.AsyncClient(
                base_url=target_url,
                headers={"Authorization": f"Bearer {self.config.node_token}"},
                timeout=self.config.http_timeout_seconds,
            ) as probe:
                confirm_resp = await probe.post(
                    "/api/v1/nodes/heartbeat",
                    json={"version": AGENT_VERSION, "hostname": socket.gethostname(), "buffer_size": self.buffer.size()},
                )
                confirm_resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Migration target %s rejected this node's token (%s) - staying on current server", target_url, exc)
            return

        # Target is alive and accepted the token - persist the switchover
        # (survives a restart) and switch the live client immediately.
        runtime_config.set_override(self.config.data_dir, target_url)
        self._client.base_url = target_url
        logger.info("Migrated uplink to %s (confirmed via authenticated heartbeat)", target_url)


def _best_effort_local_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return None
