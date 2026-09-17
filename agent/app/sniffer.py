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

# A live tshark process accumulates per-TCP-stream bookkeeping (endpoint/
# conversation tracking) for as long as it runs, and on a node with a
# high rate of new connections (hundreds of concurrent users) that state
# grows steadily even with reassembly/sequence-analysis disabled above -
# there's no live-capture equivalent of the "free everything at EOF" that
# happens when tshark finishes reading a finite pcap file. Restarting the
# process periodically resets that state before it can become a problem,
# independent of whatever the exact accumulating structure turns out to
# be. Losing ~1-2s of capture during the swap is a fine trade for a
# process that can no longer grow without bound.
_MAX_SESSION_SECONDS = 600


@dataclass(frozen=True)
class _SourceSpec:
    name: str
    tshark_filter: str
    tshark_field: str
    capture_filter: str  # BPF, applied by the kernel before tshark ever
                          # sees the packet - the actual performance lever.
                          # tshark_filter above is a *display* filter, applied
                          # in userspace after full protocol dissection, so it
                          # alone does nothing to cut CPU spent dissecting
                          # traffic that could never match anyway.


# Only sources with a real, working implementation are listed here - per
# the "no stubs/pseudocode" rule, a source that isn't actually implemented
# yet is documented as a roadmap item, not offered as a toggle that
# silently does nothing.
_AVAILABLE_SOURCES: dict[str, _SourceSpec] = {
    "tls_sni": _SourceSpec(
        "tls_sni", "tls.handshake.extensions_server_name", "tls.handshake.extensions_server_name",
        capture_filter="tcp",
    ),
    "dns": _SourceSpec(
        "dns", "dns.flags.response == 0 && dns.qry.name", "dns.qry.name",
        capture_filter="udp port 53",
    ),
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
                await asyncio.wait_for(self._run_once(), timeout=_MAX_SESSION_SECONDS)
            except asyncio.TimeoutError:
                logger.info(
                    "Sniffer source %s: periodic restart after %ss (bounds long-capture state growth)",
                    self.spec.name, _MAX_SESSION_SECONDS,
                )
                await self._terminate_process()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Sniffer source %s crashed, restarting in 5s", self.spec.name)
                await asyncio.sleep(5)

    async def stop(self) -> None:
        self._stopped.set()
        await self._terminate_process()

    async def _terminate_process(self) -> None:
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
            "-f", self.spec.capture_filter,  # kernel-level BPF filter - the
                                              # actual CPU saver, see _SourceSpec
            "-n",  # disable all name resolution (MAC/network/transport) -
                    # this alone is a well-known tshark CPU sink under load
            "-Q",  # quiet: skip the periodic packet-count status output
            # TCP stream reassembly keeps per-connection state alive for the
            # life of the capture process. On a node with hundreds of
            # concurrently churning connections, that state grows without
            # bound over a long-running live capture (unlike analyzing a
            # finite pcap file, where it gets freed at EOF) - this is what
            # actually balloons RAM/CPU over time, not raw packet volume.
            # We don't need reassembly anyway: a TLS ClientHello carrying
            # SNI is essentially always a single packet.
            "-o", "tcp.desegment_tcp_streams:false",
            "-o", "tls.desegment_ssl_records:false",
            # TCP sequence-number analysis (retransmission/RTT/out-of-order
            # detection) is on by default and keeps *its own* per-stream
            # state table alive for the life of the process, same issue as
            # reassembly above and, on a churn-heavy node, the bigger of
            # the two. We only read one field off a ClientHello - none of
            # this analysis is used for anything here.
            "-o", "tcp.analyze_sequence_numbers:false",
            "-o", "tcp.calculate_timestamps:false",
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
