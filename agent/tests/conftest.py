from __future__ import annotations

import asyncio
import os
import platform
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

if platform.system() == "Windows":
    # test_migration_switchover.py runs many short-lived asyncio.run()
    # calls against real local http.server sockets across the test
    # session - the default WindowsProactorEventLoopPolicy has known
    # socket-teardown timing quirks under exactly that pattern (rare,
    # order-dependent connection-refused flakes only reproducible with
    # the full suite, never in isolation). The Selector policy doesn't
    # have this issue and is what these tests actually need (no IOCP
    # features used anywhere here). Production code is unaffected - this
    # only changes the policy inside pytest's own process.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture
def make_config(tmp_path):
    def _make(server_url="http://old-server.invalid"):
        from app.config import Config

        data_dir = str(tmp_path)
        return Config(
            server_url=server_url, node_token="nmt_test", node_label="test", interface="any",
            tshark_path="tshark", sources=["tls_sni"], data_dir=data_dir,
            database_path=os.path.join(data_dir, "buf.db"), log_level="INFO",
            log_dir=os.path.join(data_dir, "logs"), batch_interval_seconds=5, batch_max_size=200,
            heartbeat_interval_seconds=30, max_buffer_bytes=1024 * 1024, http_timeout_seconds=3,
        )

    return _make


@pytest.fixture
def make_buffer(tmp_path):
    buffers = []

    def _make(cfg):
        from app.buffer import Buffer

        buf = Buffer(cfg.database_path, cfg.max_buffer_bytes)
        buffers.append(buf)
        return buf

    yield _make
    for b in buffers:
        try:
            b._conn.close()
        except Exception:
            pass
