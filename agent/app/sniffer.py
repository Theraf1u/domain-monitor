"""Live domain capture via `tshark`. Plugin-style: each enabled source runs
its own tshark subprocess with its own capture filter, and just tags every
hostname it sees with its own `source` value before writing to the local
outbox (app/buffer.py) - the sniffer itself knows nothing about the
server, HTTP, or retries; that's the uplink task's job.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from app.buffer import Buffer
from app.filters import normalize_domain

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _SourceSpec:
    name: str
    tshark_filter: str
    tshark_field: str


# Only sources with a real, working implementation are listed here - per
# the "no stubs/pseudocode" rule, a source that isn't actually implemented
# yet is documented as a roadmap item, not offered as a toggle that
# silently does nothing.
_AVAILABLE_SOURCES: dict[str, _SourceSpec] = {
    "tls_sni": _SourceSpec("tls_sni", "tls.handshake.extensions_server_name", "tls.handshake.extensions_server_name"),
    "dns": _SourceSpec("dns", "dns.flags.response == 0 && dns.qry.name", "dns.qry.name"),
}


def resolve_sources(raw: str) -> list[_SourceSpec]:
    """Parses SOURCES env value (comma-separated) into known source specs,
    ignoring/warning on unknown names rather than failing startup."""
    names = [s.strip() for s in raw.split(",") if s.strip()]
    specs = []
    for name in names:
        spec = _AVAILABLE_SOURCES.get(name)
        if spec is None:
            logger.warning("Unknown sniffer source %r ignored (available: %s)", name, ", ".join(_AVAILABLE_SOURCES))
            continue
        specs.append(spec)
    return specs or [_AVAILABLE_SOURCES["tls_sni"]]


class _SourceWorker:
    """Runs one tshark subprocess for one source, restarting it on crash."""

    def __init__(self, spec: _SourceSpec, buffer: Buffer, interface: str, tshark_path: str) -> None:
        self.spec = spec
        self.buffer = buffer
        self.interface = interface
        self.tshark_path = tshark_path
        self._process: asyncio.subprocess.Process | None = None
        self._stopped = asyncio.Event()

    async def run(self) -> None:
        while not self._stopped.is_set():
            try:
                await self._run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Sniffer source %s crashed, restarting in 5s", self.spec.name)
            if not self._stopped.is_set():
                await asyncio.sleep(5)

    async def stop(self) -> None:
        self._stopped.set()
        if self._process and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()

    async def _run_once(self) -> None:
        cmd = [
            self.tshark_path,
            "-i", self.interface,
            "-Y", self.spec.tshark_filter,
            "-T", "fields",
            "-e", self.spec.tshark_field,
            "-l",
        ]
        logger.info("Starting tshark [%s]: %s", self.spec.name, " ".join(cmd))
        self._process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        assert self._process.stdout is not None
        stderr_task = asyncio.create_task(self._drain_stderr(self._process.stderr))

        try:
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    break
                self._handle_line(line.decode(errors="replace"))
        finally:
            stderr_task.cancel()

        returncode = await self._process.wait()
        logger.warning("tshark [%s] exited with code %s", self.spec.name, returncode)

    async def _drain_stderr(self, stream: asyncio.StreamReader) -> None:
        while True:
            line = await stream.readline()
            if not line:
                return
            text = line.decode(errors="replace").strip()
            if text and "Capturing on" not in text:
                logger.debug("tshark [%s] stderr: %s", self.spec.name, text)

    def _handle_line(self, raw_line: str) -> None:
        line = raw_line.strip()
        if not line:
            return
        for token in line.replace(",", " ").split():
            domain = normalize_domain(token)
            if domain is None:
                continue
            self.buffer.enqueue(domain, self.spec.name, datetime.now(timezone.utc))


class Sniffer:
    """Owns one _SourceWorker per enabled source."""

    def __init__(self, buffer: Buffer, interface: str, tshark_path: str, sources: list[str]) -> None:
        specs = resolve_sources(",".join(sources))
        self._workers = [_SourceWorker(spec, buffer, interface, tshark_path) for spec in specs]
        self._tasks: list[asyncio.Task] = []

    async def run(self) -> None:
        self._tasks = [asyncio.create_task(w.run()) for w in self._workers]
        await asyncio.gather(*self._tasks)

    async def stop(self) -> None:
        await asyncio.gather(*(w.stop() for w in self._workers))
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
