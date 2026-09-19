# Domain Monitor Agent

Lightweight agent that runs directly on a VPN node: captures domains
locally (via `tshark`, currently TLS SNI and DNS queries) and pushes them
to a central [Domain Monitor Server](../domain-monitor-server) over
HTTPS. This is the **Agent** half of the multi-node project - it has no
Telegram bot, no local admin UI, and no durable domain history of its own
beyond a small retry buffer; all of that lives on the Server.

## Requirements

- A Domain Monitor Server already running and reachable from this node.
- A node token, created on the Server (Telegram: **📡 Ноды → ➕
  Добавить**, or `POST /api/v1/nodes` with `X-Admin-Key`). There is no
  web UI - Telegram is the only management surface.
- Docker + Docker Compose.

## Quick start

> Setting up the server and an agent on the same box? Use the unified
> installer at the repo root instead - it can do both:
> `curl -fsSL https://raw.githubusercontent.com/Theraf1u/domain-monitor/main/install.sh | sudo bash`

```bash
git clone <this-repo> domain-monitor-agent
cd domain-monitor-agent
cp .env.example .env
# edit .env: SERVER_URL and NODE_TOKEN (from the Server)
docker compose up -d --build
```

Or run the interactive installer (wizard for Server URL / Node Token /
Node Name / Interface, then builds and starts):

```bash
curl -fsSL https://.../install-agent.sh | sudo bash
```

The container needs `network_mode: host` plus `NET_ADMIN`/`NET_RAW`
capabilities (already set in `docker-compose.yml`) to run `tshark` without
running fully privileged.

## CLI

Once installed, manage the service with `domain-monitor-agent <command>`:

```
menu       interactive management menu
status     container + health status
start / stop / restart
logs       follow logs
update     pull latest code, rebuild, restart
doctor     run diagnostics (Docker, .env, tshark, capture caps, server reachability, disk)
backup     back up local buffer + config to ./backups
restore <file>  restore from a backup
set-server <url>  point this agent at a different server (keeps its node token)
uninstall  remove container/image/data (with confirmation)
```

`set-server` is the manual way to repoint an agent - it always wins over
anything below. There's also an **automatic** path: if the server this
agent talks to runs a `migrate-to` migration (Migration 2.0) and reaches
the cutover step, this agent picks up the new server's address on its own
next heartbeat, verifies it (checks its `/healthz`, sends an
authenticated heartbeat with its own token), and only then switches -
never blindly. Nothing to do on the node itself for that case. See the
Server's own README for the full Migration 2.0 flow.

## Configuration (`.env`)

| Variable | Required | Description |
|---|---|---|
| `SERVER_URL` | yes | Where the Domain Monitor Server is reachable. |
| `NODE_TOKEN` | yes | Issued by the Server when the node was created. Starts with `nmt_`. |
| `NODE_NAME` | no | Local-only label used in this agent's own logs; the node's real identity/name lives on the Server, tied to the token. |
| `INTERFACE` | no | Capture interface (default `any`). |
| `LOG_LEVEL` | no | Default `INFO`. |
| `BATCH_INTERVAL_SECONDS` / `BATCH_MAX_SIZE` | no | How often / how many events per HTTP push (defaults `5` / `200`). |
| `HEARTBEAT_INTERVAL_SECONDS` | no | Default `30`. |
| `MAX_BUFFER_BYTES` | no | Local outbox cap in bytes (default `1073741824`, 1 GiB), by estimated on-disk size, not event count. The outbox stores one row per domain (repeat hits bump a counter, not a new row), and least-recently-seen domains are dropped first if the server is unreachable long enough to hit the cap. |
| `SOURCES` | no | Comma-separated detection sources to run (default `tls_sni`). Currently implemented: `tls_sni`, `dns`. Each runs its own `tshark` process; an unknown name is ignored with a warning rather than failing startup. |

## How it works

1. `tshark` runs one process per enabled `SOURCES` entry:
   - `tls_sni`: captures `tls.handshake.extensions_server_name` (the SNI
     extension - the plaintext hostname a client sends before encryption
     is established).
   - `dns`: captures `dns.qry.name` from outgoing DNS queries, catching
     hostnames even when the app's own connection never does a TLS
     handshake the agent can see (e.g. traffic that goes through
     something other than this host's own TLS stack).
2. Every normalized hostname is written to a local, durable SQLite outbox
   (`app/buffer.py`) - this is what survives a server outage or an agent
   restart without losing data.
3. A background task drains the outbox in batches, POSTs them to
   `/api/v1/events` with the node token, and retries with exponential
   backoff on failure. Successfully delivered events are only then removed
   from the outbox.
4. A second background task sends a heartbeat (version/IP/hostname, plus
   buffer size, capture status, and more) to the Server periodically, so
   the Telegram bot can show the node
   as online/offline.

If the outbox fills up (server unreachable for a long time), the oldest
entries are dropped first and a warning is logged - graceful degradation
over an unbounded disk-filling queue.

## Updating / uninstalling

```bash
docker compose pull && docker compose up -d --build   # update
docker compose down                                     # stop
docker compose down -v && rm -rf data                   # stop + wipe local buffer
```

## Security / privacy

- Only the hostname from the TLS handshake is captured - never packet
  payloads.
- The node token is scrubbed from log output automatically (see
  `app/logging_config.py`).
- A revoked token (from the Server) makes every further push return 403;
  the agent logs this clearly rather than retrying forever.

## Tests

```bash
cd agent
python -m venv .venv && . .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements-dev.txt
pytest
```

Covers the persistent runtime override (`app/runtime_config.py`) and the
full Migration 2.0 switchover protocol (`UplinkTask._try_migrate()`) -
the latter runs against real local HTTP servers standing in for a
migration target, not mocks, covering the happy path (switch + persist +
survives a restart) and every failure mode (target unreachable, target
rejects the token) that must leave the agent exactly where it was.

## License

MIT.
