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
  `watchlist_hits_total`, `ignore_hits_total`, `telegram_errors_total`,
  `nodes_online`, `nodes_offline`, `nodes_total`, `domains_total`,
  `database_size_bytes`, `node_buffer_bytes` (per node id),
  `events_rate_limited_total`, `backup_result_total` (by kind/result),
  `migration_active`. No raw domains, filenames, or secrets in any label.
- **`doctor`** (`domain-monitor-server doctor`): a checklist grouped by
  category (DOCKER/SECURITY/SERVER/NETWORK/TELEGRAM/SYSTEM) - Docker/
  Compose presence, `.env`/backup file permissions, container health, DB
  integrity, PUBLIC_URL correctness, UFW port status, Telegram
  reachability, disk space - with plain-English fixes for each failure.
- **Backup Manager 2.0**: two backup types (DB-only or "full", which adds
  an `.env`-derived snapshot for disaster recovery), automatic checksum +
  integrity verification on every backup (a backup that fails
  verification is never used for rotation or restore), restore always
  takes its own pre-restore safety snapshot first and rolls back
  automatically if anything after that fails. Manage from the bot's
  💾 Бэкапы menu, or `domain-monitor-server backup`/`restore <file>` on
  the CLI.
- **Migration 2.0**: `domain-monitor-server migrate-to root@NEW_HOST`
  automates moving the whole control center to a fresh server - SSHes in,
  bootstraps Docker/Compose/UFW there if needed, brings the new server up
  in a verified standby mode (API live, Telegram polling off, so it can
  never conflict with this still-running server), then a separate
  `migrate-cutover` step makes every agent verify and switch over on its
  own next heartbeat - no manual per-node commands. See "Migration 2.0"
  below.

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
migrate-to <root@host> [-i key]  automate a full migration to a fresh server (standby)
migrate-cutover <job_id>         start switching agents over (each verifies, then switches)
migrate-status [job_id]          per-node migrated/waiting/offline breakdown
migrate-finish <job_id>          switch the Telegram bot over, complete the migration
migrate-cancel <job_id>          cancel an in-progress migration
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
POST   /api/v1/events                 agent-auth: batch of {domain, source, occurred_at, hits}
GET    /api/v1/domains                search/sort/paginate
GET    /api/v1/stats                  dashboard numbers
```

## Telegram bot

The only way to manage the server. Every action is an inline button - no
commands other than `/start`, no reply keyboards. Covers:

- **📡 Ноды** - search/filter, add, revoke/regenerate token, toggle
  monitoring/notifications/sending, bulk actions across multiple nodes,
  per-node live telemetry card (buffer size, capture status, agent
  version, last send error).
- **🌐 Домены** - search, combined filters (node/period/source/filter
  status/min hits), per-domain card with Watch/Allow/Ignore right from
  it, CSV/JSON/TXT export, data-management (delete old events, clear
  history) with confirmation.
- **📊 Статистика** - unified periods (1h/6h/today/yesterday/24h/7d/30d/
  all/custom), period-over-period comparison, source distribution, node
  offline-incident history, per-node stats screen.
- **🔔 Уведомления** - per-event-type toggles, batching presets,
  per-node destination override (DM vs. a specific group/topic), quiet
  hours, a real "send test notification" button.
- **🔍 Фильтры** - Ignore/Allow/Watch list management (exact/suffix/
  wildcard), bulk import (paste/`.txt`/`.csv` with a preview and conflict
  detection) and export, per-rule hit counts and enable/disable.
- **⚙️ Настройки** - a hub: Сервер / Ноды по умолчанию / Часовой пояс /
  Хранение данных / Администраторы / Безопасность / Диагностика /
  🔄 Обновления (checks GitHub for a newer commit) / 🚚 Миграция (status
  of an in-progress `migrate-to`, with 🔄 Обновить/✅ Начать
  переключение/❌ Отменить where the container can safely act, and a
  printed CLI command where it genuinely can't - see below) / О системе.
- **💾 Бэкапы** - two backup types, list with checksum/verified status,
  restore with a confirmation step.

If Telegram is blocked on this server's own network, set `TELEGRAM_PROXY`
(plain `socks5://` or `http://` - see Configuration above). The bot
auto-restarts its polling loop if the connection ever drops.

## Migration 2.0

Moving the whole control center (bot + API + data) to a new server, with
every agent switching over automatically:

```bash
domain-monitor-server migrate-to root@NEW_HOST   # needs password-less SSH to it
```

This bootstraps Docker/Compose/UFW on the target if they're missing
(never reinstalls what's already there), ships a migration package
(same format as `migrate-export`) and the current code, and brings the
target up in **standby**: its API and heartbeat endpoint work (so it can
be verified before anything depends on it), but Telegram polling is off,
so it can never conflict with this still-fully-running server for the
same bot token. Prints a job id.

```bash
domain-monitor-server migrate-cutover <job_id>
```

Nothing restarts. This just tells the heartbeat endpoint to start
including the new server's address in its response - each agent
verifies the target itself (checks its `/healthz`, sends an authenticated
heartbeat with its own token) before persisting the switch and pointing
itself there, on its own next heartbeat cycle. An agent that fails that
check, or one running old code that doesn't understand the field yet,
just keeps working against this server exactly as before - nothing here
is a hard cutover.

```bash
domain-monitor-server migrate-status <job_id>    # ✅ migrated / 🔄 waiting / 🔴 offline, per node
domain-monitor-server migrate-finish <job_id>    # once you're satisfied: switches the bot itself
domain-monitor-server migrate-cancel <job_id>    # stop offering the new address to any more agents
```

`migrate-finish` turns Telegram polling off on THIS server first, then on
on the new one - never the other way round, so there's no window with
both polling the same bot token. It never touches or deletes this
server's own data; tearing the old one down (if you want to) is a
separate, manual `uninstall`.

The same status is also in the bot itself: ⚙️ Настройки → 🚚 Миграция.

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

Per-domain source-of-traffic attribution (client IP/Xray user -
intentionally not implemented as unreliable heuristics), an HTTP Host /
QUIC / Xray-log detection source (agent currently supports TLS SNI and
DNS, both real), a formal automated test suite, and a stable-hostname
guide for a migrated server's DNS record (Migration 2.0 handles the
server-side and agent-side switchover; keeping a domain name pointed at
wherever the current server actually is is still a manual DNS step).

## Tests

```bash
cd server
python -m venv .venv && . .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements-dev.txt
pytest
```

Every test runs against a real throwaway SQLite database (via a `tmp_path`
fixture), never mocks - migrations (each one simulated on top of
pre-existing data to confirm it's truly additive), filter classification
and priority, the Migration 2.0 REST API and heartbeat gating, and Backup
Manager 2.0 (including a regression test for the same-second filename
collision bug found and fixed this session). Not a full test suite for
every screen and feature in the project - see `tests/` for exactly what's
covered.

## License

MIT.
