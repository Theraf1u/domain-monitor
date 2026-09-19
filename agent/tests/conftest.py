from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


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
