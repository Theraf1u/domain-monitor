# Domain Monitor Server

Central backend for multi-node domain monitoring: agents installed on VPN
nodes push newly-seen domains here over HTTPS; you manage everything -
nodes, domains, filters, notifications - through a Telegram bot. There is
no web UI; the REST API exists purely for agents (and optional scripting),
not for browsing.

```
                     ┌─ Agent Germany
                     │
Clients → VPN ───────┼─ Agent Netherlands
                     │
                     ├─ Agent Finland
                     │
                     └─ Agent Kazakhstan
                              │
                              ▼
                    Domain Monitor Server
                              │
                          Telegram
```

This is the **Server** half of the project. Install `domain-monitor-agent`
separately on each VPN node you want to monitor - see that project's own
README for the agent installer.

## Features

- **Multi-node**: any number of agents, each authenticated with its own
  revocable token.
- **REST API** (`/api/v1/*`): nodes, events, domains, stats - what agents
  talk to, and what you can script against with `X-Admin-Key`.
- **Telegram bot - the only control surface**: node cards, domain
  browsing, batched new-domain notifications, Ignore/Allow/Watch list
  management, all inline-button driven. Only one Telegram account
  (`ADMIN_ID`) can use it; everyone else is silently ignored.
- **Event retention**: detailed per-sighting history is purged after
  `EVENT_RETENTION_DAYS`; the aggregated domain list (first/last seen, hit
  count) is kept forever.
- **Ignore / Allow / Watch lists**: pattern-based rules (exact/suffix/
  wildcard) checked against every incoming domain. Ignore/Allow suppress
  notifications; Watch always fires an instant 🚨 alert (wins over the
  other two if a domain matches both).
- **Prometheus metrics** at `/metrics`: `events_total`, `new_domains_total`,
  `watchlist_hits_total`, `telegram_errors_total`, `nodes_online`,
  `nodes_total`, `domains_total`.
- **`doctor`/`backup`/`restore`** via the CLI: `domain-monitor-server doctor`
  runs a checklist (Docker, compose, .env, container health, HTTP, DB
  integrity, Telegram reachability, disk space) with plain-English fixes;
  `backup`/`restore` snapshot `./data` + `.env` to/from `./backups/`.

## Requirements

- Docker + Docker Compose (v1 `docker-compose` or the v2 `docker compose`
  plugin - either works).
- A Telegram bot token (from [@BotFather](https://t.me/BotFather)) and
  your own numeric Telegram user id (from e.g. [@userinfobot](https://t.me/userinfobot)).

## Quick start

> If you want the server and an agent on the same box, or you'd rather
> pick interactively, use the unified installer at the repo root instead:
> `curl -fsSL https://raw.githubusercontent.com/Theraf1u/domain-monitor/main/install.sh | sudo bash`

```bash
git clone https://github.com/Theraf1u/domain-monitor.git
cd domain-monitor/server
sudo bash install.sh
```

The wizard asks for an admin API key (auto-generated, just press Enter),
the address agents will reach this server at, a free port, your
`BOT_TOKEN` and `ADMIN_ID`, and (only if this host itself needs one) a
proxy for reaching Telegram. It checks Telegram reachability and that the
chosen port is actually free before starting.

Once it's up, open your bot in Telegram and send `/start`. From
**📡 Ноды → ➕ Добавить** you get a token and the exact command to run on
a new VPN node to install `domain-monitor-agent` there.

## CLI

Once installed, manage the service with `domain-monitor-server <command>`:

```
menu       interactive management menu
status     container + health status
start / stop / restart
logs       follow logs
update     pull latest code, rebuild, restart
doctor     run diagnostics
backup     back up database + config to ./backups
restore <file>  restore from a backup
add-node   add a node in one action (no name needed)
migrate-export           package everything for moving this server to another host
migrate-import <file>    import a package made by migrate-export on another host
uninstall  remove container/image/data (with confirmation)
```

## Configuration (`.env`)

| Variable | Required | Description |
|---|---|---|
| `ADMIN_API_KEY` | yes | Shared secret for the REST API's `X-Admin-Key` auth (direct scripting; day-to-day management is via Telegram). |
| `PORT` | no | Port to listen on (default `8280`). The container uses host networking, so this must be free on the host. |
| `PUBLIC_URL` | no | Shown to you when adding a node, as the `SERVER_URL` the agent installer should use. |
| `BOT_TOKEN` / `ADMIN_ID` | **yes** | The bot token and your numeric Telegram user id - Telegram is the only management interface, so both are required. |
| `TELEGRAM_PROXY` | no | Set if this host itself needs a proxy to reach Telegram (e.g. Telegram is blocked on its network). Plain `socks5://` or `http://` only. |
| `EVENT_RETENTION_DAYS` | no | Detailed event history retention (default `30`, `0` = forever). |
| `NODE_OFFLINE_AFTER_SECONDS` | no | Heartbeat staleness threshold before a node shows offline (default `90`). |
| `LOG_LEVEL` | no | Default `INFO`. |

## REST API

Two auth schemes:
- `X-Admin-Key: <ADMIN_API_KEY>` - node/domain/stats management via direct API calls.
- `Authorization: Bearer <node token>` - what an agent uses to push its
  own events/heartbeat; scoped to that one node.

```
POST   /api/v1/nodes                  create a node, returns its token (shown once)
GET    /api/v1/nodes                  list nodes
GET    /api/v1/nodes/{id}             node detail
PATCH  /api/v1/nodes/{id}             update monitoring/notifications flags
POST   /api/v1/nodes/{id}/regenerate-token
POST   /api/v1/nodes/{id}/revoke
DELETE /api/v1/nodes/{id}
POST   /api/v1/nodes/heartbeat        agent-auth: version/ip/hostname
POST   /api/v1/events                 agent-auth: batch of {domain, source, occurred_at}
GET    /api/v1/domains                search/sort/paginate
GET    /api/v1/stats                  dashboard numbers
```

## Telegram bot

The only way to manage the server. Every action is an inline button - no
commands other than `/start`, no reply keyboards. Covers:

- **📡 Ноды** - list, add, revoke/regenerate token, toggle monitoring/notifications, delete.
- **🌐 Домены** - recent, top by hits, export as `.txt`.
- **📊 Статистика** - nodes online, unique domains, events/new domains today.
- **🔔 Уведомления** - global on/off, batching mode (Instant / 5s / 15s / 30s / 60s).
- **🔍 Фильтры** - Ignore/Allow/Watch list management (exact/suffix/wildcard patterns).
- **⚙️ Настройки** - current retention/offline-threshold values.

If Telegram is blocked on this server's own network, set `TELEGRAM_PROXY`
(plain `socks5://` or `http://` - see Configuration above). The bot
auto-restarts its polling loop if the connection ever drops.

## Architecture notes

- **Why no ORM**: the schema is small (nodes/domains/events/settings/
  filter_rules) and every query is simple; a hand-written `sqlite3`
  wrapper (`app/database.py`) is easier to audit than an ORM layer, at
  zero extra dependency cost. Moving to Postgres later only touches this
  one file.
- **Why Telegram-only, no web UI**: one less thing to secure (no sessions,
  no CSRF, no browser attack surface) and one less thing to keep in sync
  with the bot - a single control surface is simpler to reason about for
  a tool with exactly one operator.
- **Why host networking**: the server (and the bot's own outbound
  connection to Telegram) may need to reach a host-local proxy - bridge
  networking's NAT frequently can't route to a proxy client's own
  TUN/loopback interface, while host networking can (see
  `docker-compose.yml`).

## Roadmap (not yet built)

Full events history browsing (raw per-sighting log, not just the
aggregated domain list), per-domain source-of-traffic attribution
(client IP/Xray user - intentionally not implemented as unreliable
heuristics), an HTTP Host / QUIC / Xray-log detection source (agent
currently supports TLS SNI and DNS, both real), and agent version/update
tracking on the Server side.

## License

MIT.
