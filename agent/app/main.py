"""Agent entrypoint: capture -> local buffer -> uplink to server."""
from __future__ import annotations

import asyncio
import logging
import signal

from app.buffer import Buffer
from app.config import ConfigError, load_config
from app.logging_config import setup_logging
from app.remote_control import RemoteControl
from app.sniffer import Sniffer
from app.uplink import UplinkTask

logger = logging.getLogger(__name__)


async def _run() -> None:
    config = load_config()
    setup_logging(config.log_level, config.log_dir)
    logger.info(
        "Starting Domain Monitor Agent (label=%s, interface=%s, sources=%s, server=%s)",
        config.node_label, config.interface, ",".join(config.sources), config.server_url,
    )

    buffer = Buffer(config.database_path, config.max_buffer_bytes)
    buffer.migrate()

    remote_control = RemoteControl()
    sniffer = Sniffer(buffer, config.interface, config.tshark_path, config.sources, remote_control)
    uplink = UplinkTask(config, buffer, remote_control, sniffer=sniffer)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass  # Windows dev environments: fine, container runtime supports this.

    logger.info("Domain Monitor Agent started")

    tasks = [
        asyncio.create_task(sniffer.run()),
        asyncio.create_task(uplink.run()),
        asyncio.create_task(uplink.run_heartbeat()),
    ]

    await stop_event.wait()
    logger.info("Shutting down")

    await sniffer.stop()
    await uplink.stop()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    buffer.close()
    logger.info("Shutdown complete")


def main() -> None:
    try:
        asyncio.run(_run())
    except ConfigError as exc:
        print(f"Configuration error: {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
