"""UplinkTask._try_migrate() - the agent's half of Migration 2.0's
switchover protocol (spec 3.3): never switch immediately on seeing
migration_target_url, verify the target's /healthz, verify it accepts
this node's own token via an authenticated heartbeat, and only then
persist + switch. Any failure must leave the agent exactly on its
current server with the buffer untouched.

Runs against real local HTTP servers (http.server in a background
thread), not mocks - the same discipline used to verify this before it
was ever deployed.

KNOWN FLAKE (Windows dev sandbox only): running the full file back to
back, several http.server instances get created/torn down across ~20s
in one process, and on Windows that occasionally produces a spurious
connection failure on an otherwise-correct request (empty exception
message, not a real 403/timeout) - reproduces on any one of these
tests, never in isolation, at roughly 30-40% of full-file runs. Tried
and ruled out: switching to WindowsSelectorEventLoopPolicy, explicit
server_close() after shutdown(), and blocking start_server() until a
raw socket connect succeeds (see below) - none eliminated it, which
points at OS-level TCP stack timing under Windows' ephemeral-port
churn rather than anything in this test's own logic. The underlying
agent code this exercises has been independently, repeatedly verified
against real Linux production servers this session (not just this
suite) - this flake is specific to running pytest on Windows, not a
sign the switchover logic itself is unreliable. If this starts failing
in CI (which would run on Linux), that's a different, real signal and
should be investigated fresh rather than assumed to be this same issue."""
from __future__ import annotations

import json
import os
import socket
import threading
import time
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
        # The listening socket is already bound (HTTPServer.__init__ does
        # that synchronously), but the accept loop only starts once the
        # thread above is actually scheduled - on Windows, under load
        # from several of these servers created/torn down in the same
        # process, that can lag enough for the very first real connection
        # attempt to land before anything is accepting, which httpx
        # surfaces as a connection error with no useful message. Block
        # here until a raw connect actually succeeds, so every test gets
        # a URL that's truly ready.
        for _ in range(50):
            try:
                with socket.create_connection(("127.0.0.1", srv.server_port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.02)
        return f"http://127.0.0.1:{srv.server_port}"

    yield _start
    for s in servers:
        s.shutdown()
        s.server_close()  # shutdown() only stops serve_forever() - the listening socket itself stays open until this


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
