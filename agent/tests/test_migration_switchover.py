"""UplinkTask._try_migrate() - the agent's half of Migration 2.0's
switchover protocol (spec 3.3): never switch immediately on seeing
migration_target_url, verify the target's /healthz, verify it accepts
this node's own token via an authenticated heartbeat, and only then
persist + switch. Any failure must leave the agent exactly on its
current server with the buffer untouched.

Runs against real local HTTP servers (http.server in a background
thread), not mocks - the same discipline used to verify this before it
was ever deployed."""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.remote_control import RemoteControl
from app.uplink import UplinkTask


class _GoodTarget(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/api/v1/nodes/heartbeat" and self.headers.get("Authorization") == "Bearer nmt_test":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")
        else:
            self.send_response(403)
            self.end_headers()

    def log_message(self, *a):
        pass


class _HealthzFails(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(500)
        self.end_headers()

    def do_POST(self):
        self.send_response(500)
        self.end_headers()

    def log_message(self, *a):
        pass


class _RejectsToken(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def do_POST(self):
        self.send_response(403)
        self.end_headers()

    def log_message(self, *a):
        pass


@pytest.fixture
def start_server():
    servers = []

    def _start(handler_cls):
        srv = HTTPServer(("127.0.0.1", 0), handler_cls)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_port}"

    yield _start
    for s in servers:
        s.shutdown()


@pytest.fixture
def task(make_config, make_buffer):
    cfg = make_config()
    buf = make_buffer(cfg)
    t = UplinkTask(cfg, buf, RemoteControl())
    yield t, cfg
    import asyncio

    asyncio.run(t._client.aclose())


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_successful_migration_switches_and_persists(task, start_server):
    t, cfg = task
    url = start_server(_GoodTarget)

    _run(t._try_migrate(url))

    assert str(t._client.base_url).rstrip("/") == url
    override_path = os.path.join(cfg.data_dir, "runtime.json")
    assert os.path.exists(override_path)
    with open(override_path) as fh:
        assert json.load(fh)["server_url_override"] == url


def test_healthz_failure_stays_on_old_server(task, start_server):
    t, cfg = task
    url = start_server(_HealthzFails)

    _run(t._try_migrate(url))

    assert str(t._client.base_url).rstrip("/") == cfg.server_url
    assert not os.path.exists(os.path.join(cfg.data_dir, "runtime.json"))


def test_token_rejection_stays_on_old_server(task, start_server):
    t, cfg = task
    url = start_server(_RejectsToken)

    _run(t._try_migrate(url))

    assert str(t._client.base_url).rstrip("/") == cfg.server_url
    assert not os.path.exists(os.path.join(cfg.data_dir, "runtime.json"))


def test_agent_restart_resumes_on_migrated_server(make_config, make_buffer, start_server):
    """spec 3.4: the override must survive a restart - a fresh UplinkTask
    built with the OLD server_url in Config must still pick up the
    persisted override from a previous run."""
    import asyncio

    cfg = make_config()
    buf = make_buffer(cfg)
    t1 = UplinkTask(cfg, buf, RemoteControl())
    url = start_server(_GoodTarget)
    asyncio.run(t1._try_migrate(url))
    asyncio.run(t1._client.aclose())

    buf2 = make_buffer(cfg)
    t2 = UplinkTask(cfg, buf2, RemoteControl())
    assert str(t2._client.base_url).rstrip("/") == url
    asyncio.run(t2._client.aclose())


def test_manual_set_server_clears_a_stale_override(make_config, make_buffer, start_server):
    """agent/scripts/set_server.sh deletes data/runtime.json before
    rewriting .env - simulated here by calling the same primitive
    (runtime_config.set_override(None)) an admin's manual override
    should always win over an old automated migration."""
    from app import runtime_config

    cfg = make_config()
    buf = make_buffer(cfg)
    t = UplinkTask(cfg, buf, RemoteControl())
    url = start_server(_GoodTarget)
    _run(t._try_migrate(url))
    assert os.path.exists(os.path.join(cfg.data_dir, "runtime.json"))

    runtime_config.set_override(cfg.data_dir, None)
    assert runtime_config.effective_server_url(cfg.server_url, cfg.data_dir) == cfg.server_url
    _run(t._client.aclose())
