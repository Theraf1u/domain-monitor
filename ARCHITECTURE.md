# Domain Monitor — техническая справка по архитектуре

Документ описывает устройство репозитория `domain-monitor-monorepo` по состоянию на момент чтения кода: `server/` (FastAPI REST API + Telegram-бот) и `agent/` (сниффер на tshark, работающий на VPN-нодах). Это справочный документ, а не маркетинговый — каждый файл, функция и таблица описаны на основе фактического чтения исходников.

---

## 1. Обзор и архитектура

Domain Monitor — мультинодовая система мониторинга доменов для VPN-инфраструктуры. Она состоит из двух независимо разворачиваемых компонентов:

- **`agent/`** — лёгкий сниффер трафика, ставится на каждую VPN-ноду. Через `tshark` пассивно читает SNI из TLS-хендшейков и/или имена из DNS-запросов (без MITM и расшифровки трафика), буферизует их локально в SQLite и периодически отправляет на сервер батчами.
- **`server/`** — центральный backend: FastAPI REST API (принимает данные от агентов) + Telegram-бот на `aiogram` (единственный интерфейс управления для оператора — браузера в системе нет вообще). Оба запускаются в одном процессе (uvicorn + бот как фоновая asyncio-задача).

### Поток данных

```
[VPN-нода]
  tshark (SNI / DNS) ──> app/sniffer.py: нормализация домена
                            │
                            ▼
                     app/buffer.py: локальный outbox (SQLite),
                     дедуп по (domain, source), лимит по размеру на диске
                            │
                            ▼
                     app/uplink.py: батч раз в BATCH_INTERVAL_SECONDS,
                     retry с экспоненциальным backoff, Bearer-токен ноды
                            │
                            │  HTTPS, Authorization: Bearer <NODE_TOKEN>
                            ▼
[Сервер]           POST /api/v1/events (app/api/events.py)
                            │
                            ▼
                     app/database.py: events (история) + domains (агрегат)
                            │
                            ├──> app/filters.py: классификация (ignore/allow/watch)
                            ├──> app/notifier.py: батч уведомлений
                            └──> app/metrics.py: Prometheus /metrics
                            │
                            ▼
                     app/telegram/handlers.py — Telegram-бот (aiogram),
                     единственная UI-панель оператора
```

Аутентификация — два независимых механизма:
- **Admin auth** (`X-Admin-Key`) — для прямого скриптового управления REST API (CLI-скрипты вроде `add_node.sh`).
- **Node auth** (`Authorization: Bearer <NODE_TOKEN>`) — для агента; токен привязан к одной ноде, скомпрометированный токен не даёт доступа к данным других нод.

Ключевые архитектурные решения (из README и кода):
- Хранилище — SQLite без ORM, ручной слой `database.py`; миграция на Postgres потребовала бы изменений только в одном файле.
- Только Telegram, без веб-UI — меньше поверхность атаки, меньше движущихся частей.
- У сервера `network_mode: host` в docker-compose — чтобы бот мог достучаться до локального прокси (например, VPN-клиента) на хосте, который bridge-сеть Docker часто не может маршрутизировать.
- У агента `network_mode: host` + `cap_add: NET_ADMIN, NET_RAW` — необходимо для захвата пакетов через tshark.

---

## 2. Структура репозитория

```
domain-monitor-monorepo/
├── README.md                          — общее описание проекта, возможности, быстрый старт
├── install.sh                         — единый установщик верхнего уровня (меню: Оба/Agent/Server/...)
├── server/
│   ├── .env.example                   — шаблон конфигурации сервера
│   ├── Dockerfile                     — образ сервера (python:3.12-slim + uvicorn)
│   ├── docker-compose.yml             — network_mode: host, healthcheck на /healthz
│   ├── install.sh                     — установщик сервера (мастер + меню при повторном запуске)
│   ├── README.md                      — README компонента server
│   ├── requirements.txt               — python-зависимости сервера
│   ├── bin/
│   │   └── domain-monitor-server      — CLI-обёртка (status/start/stop/restart/logs/update/...)
│   ├── migrations/
│   │   ├── 0001_initial.sql           — таблицы nodes, domains, events, settings
│   │   ├── 0003_filter_rules.sql      — таблица filter_rules (ignore/allow/watch)
│   │   ├── 0004_node_notify_routing.sql — notify_destination/group_chat_id/group_topic_id на nodes
│   │   ├── 0005_agent_buffer_size.sql — nodes.agent_buffer_size
│   │   └── 0006_events_hits.sql       — events.hits
│   ├── scripts/
│   │   ├── lib.sh                     — общие shell-хелперы (compose, порты, firewall)
│   │   ├── menu.sh                    — интерактивное меню управления
│   │   ├── doctor.sh                  — диагностика (Docker, .env, healthz, PUBLIC_URL, UFW, БД, Telegram, диск)
│   │   ├── healthcheck.sh             — быстрая проверка состояния контейнера
│   │   ├── backup.sh                  — архивирует ./data + .env в ./backups
│   │   ├── restore.sh                 — восстанавливает данные+.env из архива
│   │   ├── update.sh                  — git pull + пересборка + перезапуск
│   │   ├── uninstall.sh               — полное удаление (контейнер/образ/данные/CLI/папка)
│   │   ├── add_node.sh                — создаёт ноду через REST API, печатает готовую команду установки
│   │   ├── migrate_export.sh          — собирает пакет для переноса сервера на другой хост
│   │   └── migrate_import.sh          — принимает пакет миграции, умно сливает .env
│   └── app/
│       ├── __init__.py
│       ├── main.py                    — FastAPI приложение, lifespan, запуск бота, /healthz, /metrics
│       ├── config.py                  — загрузка Config из переменных окружения
│       ├── database.py                — слой доступа к SQLite (все SQL-запросы сервера)
│       ├── models.py                  — dataclass-модели Node/Domain/Event/FilterRule
│       ├── security.py                — генерация/хэширование node-токенов, constant-time сравнение
│       ├── filters.py                 — нормализация доменов, классификация по filter_rules
│       ├── notifier.py                — батчинг и доставка Telegram-уведомлений о новых доменах
│       ├── metrics.py                 — Prometheus-метрики
│       ├── rate_limit.py              — token-bucket rate limit на /api/v1/events по node_id
│       ├── retention.py               — фоновая очистка старых events по EVENT_RETENTION_DAYS
│       ├── runtime_settings.py        — рантайм-настройки (retention, offline timeout) поверх .env
│       ├── backup_settings.py         — настройки автобэкапа (интервал, кол-во копий, доставка)
│       ├── backup_task.py             — фоновая задача автобэкапа БД + доставка через бота
│       ├── live_view.py               — автообновление экранов бота (LiveViewManager)
│       ├── topic_binding.py           — привязка группы/топика к ноде через /bind
│       ├── fleet_control.py           — два глобальных флага: мониторинг/отправка для всей флотилии
│       ├── logging_config.py          — логирование + скраббинг токенов из логов
│       ├── assets/
│       │   └── bot_avatar.png         — необязательный аватар бота
│       ├── api/
│       │   ├── __init__.py
│       │   ├── deps.py                — FastAPI dependency: get_db/require_admin/require_node
│       │   ├── schemas.py             — Pydantic-схемы запросов/ответов
│       │   ├── nodes.py               — CRUD нод + heartbeat
│       │   ├── events.py              — приём батча событий от агента
│       │   ├── domains.py             — список/поиск доменов (admin-only)
│       │   └── stats.py               — сводная статистика (admin-only)
│       └── telegram/
│           ├── __init__.py
│           ├── bot.py                 — сборка aiogram Bot/Dispatcher, профиль бота
│           ├── handlers.py            — все обработчики команд/кнопок (единственный Router)
│           ├── keyboards.py           — все инлайн-клавиатуры
│           └── middleware.py          — AdminOnlyMiddleware (фильтр по ADMIN_ID)
└── agent/
    ├── .env.example                   — шаблон конфигурации агента
    ├── .gitignore
    ├── Dockerfile                     — образ агента (python:3.12-slim + tshark)
    ├── docker-compose.yml             — network_mode: host, cap_add NET_ADMIN/NET_RAW, mem/cpu лимиты
    ├── install-agent.sh               — установщик агента (мастер + неинтерактивный режим agent <url> <token>)
    ├── README.md                      — README компонента agent
    ├── requirements.txt               — python-зависимости агента
    ├── data/.gitkeep                  — точка монтирования локального буфера
    ├── bin/
    │   └── domain-monitor-agent       — CLI-обёртка (status/start/.../set-server)
    ├── migrations/
    │   ├── 0001_outbox.sql            — таблица outbox (локальный буфер)
    │   └── 0002_outbox_dedupe.sql     — дедуп outbox по (domain, source), колонки hits/last_occurred_at
    ├── scripts/
    │   ├── lib.sh                     — общие shell-хелперы (те же compose-обёртки, без firewall-функций)
    │   ├── menu.sh                    — интерактивное меню управления агентом
    │   ├── doctor.sh                  — диагностика (Docker, .env, tshark в контейнере, NET_ADMIN/NET_RAW, БД, диск)
    │   ├── healthcheck.sh             — быстрая проверка состояния контейнера
    │   ├── backup.sh                  — архивирует ./data + .env
    │   ├── restore.sh                 — восстанавливает данные+.env из архива
    │   ├── update.sh                  — git pull + пересборка + перезапуск
    │   ├── uninstall.sh               — полное удаление
    │   └── set_server.sh              — меняет SERVER_URL без смены токена ноды
    └── app/
        ├── __init__.py
        ├── main.py                    — точка входа: buffer + sniffer + uplink + graceful shutdown
        ├── config.py                  — загрузка Config из переменных окружения
        ├── buffer.py                  — durable bounded local outbox (SQLite)
        ├── filters.py                 — нормализация имён хостов
        ├── sniffer.py                 — управление процессами tshark (по одному на источник)
        ├── uplink.py                  — отправка батчей + heartbeat на сервер
        ├── remote_control.py          — флаги monitoring_enabled/sending_enabled с сервера
        └── logging_config.py          — логирование + скраббинг токенов
```

---

## 3. server/app — по модулям

### `app/config.py`
Загружает `Config` (frozen dataclass) целиком из переменных окружения (`.env` через `env_file` докер-компоуза), зеркалируя модуль агента.

- `_require(name)` — читает обязательную переменную окружения, кидает `ConfigError`, если она пуста.
- `_optional(name, default)` — читает переменную окружения с дефолтом.
- `load_config() -> Config` — валидирует и собирает конфиг: проверяет длину `ADMIN_API_KEY` (≥16 символов, иначе кидает `ConfigError` с примером генерации), создаёт `DATA_DIR`/`LOG_DIR`, проверяет `BOT_TOKEN` регуляркой `^\d+:[A-Za-z0-9_-]{30,}$`, парсит `ADMIN_ID` как список чисел через запятую, проверяет формат `TELEGRAM_PROXY` (`socks5://` или `http://`, без `socks5h`), вычисляет `PUBLIC_URL` по умолчанию как `http://localhost:{port}`.
- `ConfigError` — исключение для отсутствующей/некорректной конфигурации.

### `app/database.py`
Слой доступа к данным: ручная обёртка над `sqlite3`, один метод на операцию, `threading.Lock` для сериализации записи, WAL journal mode. FastAPI-хендлеры синхронные (`def`, не `async def`), поэтому фреймворк сам исполняет их в пуле потоков.

Класс `Database`:
- `__init__(path)` — открывает соединение, включает `PRAGMA foreign_keys = ON`, `PRAGMA journal_mode = WAL`.
- `migrate()` — создаёт таблицу `schema_migrations`, применяет все ещё не применённые `.sql`-файлы из `server/migrations/` по алфавиту.
- `create_node(name, token_hash)` — создаёт ноду с явным именем.
- `create_node_auto(token_hash)` — создаёт ноду без имени: вставляет с временным placeholder-именем `__pending__{ts}`, затем переименовывает в `нода-{id}` (используется в one-tap flow "➕ Добавить").
- `rename_node(node_id, new_name)` — переименовывает ноду, возвращает `False`, если имя занято другой нодой.
- `name_exists(name)` — проверка занятости имени.
- `get_node(node_id)` / `get_node_by_token_hash(token_hash)` — выборка одной ноды.
- `list_nodes()` — список нод, отсортированный по имени.
- `regenerate_token(node_id, token_hash)` — заменяет токен и сбрасывает статус в `active`.
- `set_node_status(node_id, status)` — меняет статус (`active`/`revoked`).
- `delete_node(node_id)` — удаляет ноду (каскадно удаляет её domains/events через `ON DELETE CASCADE`).
- `set_node_monitoring(node_id, enabled)` / `set_node_notifications(node_id, enabled)` — переключатели на карточке ноды.
- `set_node_notify_destination(node_id, destination)` — куда слать уведомления (`dm`/`group`/`both`).
- `set_node_notify_group(node_id, chat_id, topic_id)` — привязка группы/топика.
- `touch_heartbeat(node_id, version, ip, hostname, buffer_size)` — обновляет `last_heartbeat_at`/`last_seen_at` и опциональные поля через `COALESCE`; если имя ноды ещё placeholder (`нода-\d+`) и пришёл `ip`, переименовывает ноду в её реальный IP (если тот ещё не занят другой нодой).
- `touch_last_seen(node_id)` — обновляет только `last_seen_at` (вызывается при приёме событий).
- `_row_to_node(row)` — статический маппер строки БД в `Node`.
- `record_event(node_id, domain, source, occurred_at, hits)` — вставляет строку в `events`, апсертит агрегат в `domains` (обновляет `last_seen`, увеличивает `hits`, если домен новый — создаёт запись); возвращает `(Domain, is_new)`.
- `get_domain(domain_id)` — выборка одного домена.
- `mark_notified(domain_id)` — помечает домен как уведомлённый.
- `pending_notifications(limit)` — домены, по которым ещё не отправлено уведомление и которые не игнорируются.
- `set_ignored(domain_id, ignored)` — ручной флаг игнора на конкретном домене.
- `list_domains(limit, offset, search, order_by, node_id, since, until)` — параметризованная выборка доменов с фильтрами и сортировкой (`last_seen`/`first_seen`/`hits`/`domain`).
- `count_domains(since, until)` — количество доменов в диапазоне.
- `count_events_since(since, until)` — сумма `hits` событий в диапазоне.
- `top_domains(limit)` — топ доменов по `hits`.
- `top_active_nodes_since(since, limit)` — топ-N нод по сумме `hits` событий за период (JOIN с `nodes`, удалённые ноды исключаются).
- `purge_events_older_than(cutoff)` — удаляет старые записи `events` (retention).
- `reset_domains_and_events()` — полностью очищает `domains` и `events` (ноды/фильтры/настройки не трогает); возвращает количество удалённых строк каждой таблицы.
- `_row_to_domain(row)` — статический маппер строки БД в `Domain`.
- `get_setting(key, default)` / `set_setting(key, value)` — key-value хранилище настроек (upsert через `ON CONFLICT`).
- `add_filter_rule(list_type, pattern_type, pattern)` — добавляет правило фильтра, возвращает `None` при дубликате (уникальный индекс).
- `remove_filter_rule(rule_id)` — удаляет правило, возвращает `bool` успеха.
- `list_filter_rules(list_type=None)` — список правил, опционально по типу списка.
- `all_filter_rules_cached()` — алиас `list_filter_rules()` без аргументов, назван отдельно для мест, где проверяется каждое входящее событие (events.py), чтобы это было заметно при чтении кода.
- `_row_to_filter_rule(row)` — статический маппер.
- `close()` — закрывает соединение.

### `app/models.py`
Чистые dataclass-модели без зависимостей от БД/HTTP-фреймворка (переиспользуются в API-слое и боте).

- `Node` — поля: `id, name, token_hash, status, version, ip, hostname, created_at, last_seen_at, last_heartbeat_at, monitoring_enabled, notifications_enabled, notify_destination, notify_group_chat_id, notify_group_topic_id, agent_buffer_size`. Метод `is_online(offline_after_seconds, now)` — онлайн, если `status == "active"` и `last_heartbeat_at` не старше порога.
- `Domain` — `id, domain, first_seen, last_seen, hits, node_id, ignored, notification_sent`.
- `Event` — `id, node_id, domain, source, occurred_at, received_at`.
- `FilterRule` — `id, list_type ("ignore"|"allow"|"watch"), pattern_type ("exact"|"suffix"|"wildcard"), pattern, created_at`.

### `app/main.py`
Точка входа FastAPI: обслуживает REST API для агентов и запускает Telegram-бота как фоновую asyncio-задачу поверх event loop uvicorn.

- `_run_polling_forever(dp, bot, stopped)` — оборачивает `dp.start_polling()` циклом рестарта при падении (например, при обрыве long-poll соединения через SOCKS5-прокси); экспоненциальный backoff (1с → максимум 30с), сбрасывается при чистом возврате; `handle_signals=False`, чтобы не конфликтовать с обработчиками сигналов uvicorn.
- `lifespan(app)` (async context manager) — при старте: грузит `Config`, настраивает логирование, создаёт `Database` и мигрирует её, создаёт `Notifier`, `BackupTask`, `TopicBindingManager`, `LiveViewManager`, кладёт их в `app.state`, создаёт `NodeRateLimiter`, запускает фоновые задачи (`RetentionTask.run()`, `notifier.run()`, `backup_task.run()`, polling бота), настраивает профиль бота (`configure_bot_profile`, ошибки некритичны). При остановке: гасит нотификатор, бэкап-таск, все live-view, останавливает polling, отменяет все фоновые задачи, закрывает сессию бота и БД.
- `healthz()` — `GET /healthz`, всегда `{"status": "ok"}` (используется docker healthcheck и установщиком).
- `metrics(request)` — `GET /metrics`, обновляет gauge-метрики (`NODES_TOTAL`, `NODES_ONLINE`, `DOMAINS_TOTAL`) и отдаёт их в формате Prometheus.

### `app/security.py`
Работа с node-токенами и admin-ключом.

- `generate_node_token()` — генерирует токен вида `nmt_<32 байта urlsafe>`.
- `hash_token(token)` — SHA-256 хэш токена (хранится вместо самого токена — как хэш пароля).
- `constant_time_eq(a, b)` — сравнение строк за постоянное время (`hmac.compare_digest`) для защиты от timing-атак при сравнении admin-ключа.

### `app/filters.py`
Нормализация доменов и логика фильтр-листов. Идентична копии в агенте, чтобы валидный на агенте домен всегда проходил валидацию на сервере.

- `normalize_domain(raw)` — приводит к нижнему регистру, обрезает точки, отбрасывает пустые/содержащие запятую или пробел, проверяет regex-ом имя хоста (RFC-совместимый, до 253 символов, метки до 63 символов без дефисов по краям).
- `matches_ignore(domain, patterns)` — точное совпадение или совпадение как поддомена одного из паттернов.
- `matches_pattern(domain, pattern, pattern_type)` — унифицированное сопоставление: `exact` (точное равенство), `suffix` (сам домен или любой его поддомен), `wildcard` (`fnmatch`, без неявного включения поддоменов).
- `FilterVerdict` — класс-вердикт с полями `is_ignored, is_allowed, is_watched` и свойством `suppresses_notification` (ignore/allow подавляют уведомление, но `watch` всегда побеждает).
- `classify_domain(domain, rules)` — прогоняет домен через все правила, возвращает `FilterVerdict`.

### `app/notifier.py`
Батчинг и доставка Telegram-уведомлений о новых доменах. Окно батча хранится в БД как настройка `notify_batch_mode` (`"instant"` либо число секунд), меняется на лету без рестарта.

- `Notifier.__init__(db, admin_ids)` — инициализация, `_pending` — список (нода, домен) в ожидании отправки.
- `set_bot(bot)` — привязка объекта бота после его создания (порядок инициализации в `main.py`).
- `broadcast(text, parse_mode)` — рассылает сообщение в ЛС каждому admin_id независимо (сбой у одного не блокирует остальных).
- `deliver_for_node(node, text, parse_mode)` — маршрутизирует уведомление по настройке конкретной ноды (`dm`/`group`/`both`); если выбрано `group`, но `notify_group_chat_id` не привязан — фолбэк на DM.
- `is_globally_enabled()` / `set_globally_enabled(enabled)` — глобальный тумблер уведомлений (настройка `notifications_enabled`).
- `batch_mode()` / `set_batch_mode(mode)` — текущий режим группировки.
- `is_watchlist_enabled()` / `set_watchlist_enabled(enabled)` — тумблер watch-уведомлений.
- `schedule(node, domain)` — добавляет домен в очередь батча (если бот настроен, уведомления включены глобально и на ноде).
- `schedule_watchlist(node, domain)` — Watch-хиты обходят батчинг, отправляются мгновенно с текстом `🚨 WATCHLIST DOMAIN`.
- `run()` — фоновый цикл: ждёт интервал (1с для `instant`, иначе значение режима), затем вызывает `_flush()`.
- `stop()` — сигнал остановки.
- `_flush()` — забирает накопленные пары (нода, домен), группирует по ноде, формирует текст (один домен — короткое сообщение, несколько — список до 30 штук с "и ещё N"), доставляет через `deliver_for_node`.

### `app/metrics.py`
Prometheus-метрики, обновляемые инлайн в местах, где факт уже известен (не отдельным поллером).

- `EVENTS_TOTAL` (Counter) — всего событий.
- `NEW_DOMAINS_TOTAL` (Counter) — всего новых уникальных доменов.
- `WATCHLIST_HITS_TOTAL` (Counter) — событий, попавших под watch-правило.
- `TELEGRAM_ERRORS_TOTAL` (Counter) — ошибок отправки в Telegram.
- `NODES_ONLINE` / `NODES_TOTAL` (Gauge) — текущее число нод.
- `DOMAINS_TOTAL` (Gauge) — общее число уникальных доменов.

### `app/rate_limit.py`
Token-bucket rate limit на приём событий (`/api/v1/events`), по одному bucket на `node_id`.

- `_TokenBucket.__init__(capacity, refill_per_second)` — инициализация бакета, `tokens = capacity`.
- `_TokenBucket.allow(cost=1.0)` — пополняет токены пропорционально прошедшему времени, разрешает запрос при достаточном запасе, иначе возвращает `(False, retry_after)`.
- `NodeRateLimiter.__init__(capacity, refill_per_second)` — общие параметры для всех бакетов.
- `NodeRateLimiter.check(node_id)` — создаёт бакет при первом обращении (словарь ограничен реальным числом нод, т.к. `node_id` существует только для явно созданных админом нод), возвращает `(allowed, retry_after_seconds)`.

### `app/retention.py`
Фоновая задача периодической очистки старых `events` (не трогает агрегированную таблицу `domains`).

- `RetentionTask.__init__(db, config)`.
- `run()` — цикл с интервалом проверки `_CHECK_INTERVAL_SECONDS = 3600`; читает текущее значение retention через `runtime_settings.get_event_retention_days` (0 или меньше — хранить вечно), удаляет события старше `cutoff` через `asyncio.to_thread`.
- `stop()` — сигнал остановки.

### `app/runtime_settings.py`
Рантайм-настройки поверх `.env`, редактируемые из Telegram без перезапуска контейнера; если значение в БД не задано — берётся значение из `Config`.

- `get_event_retention_days(db, config)` / `set_event_retention_days(db, days)`.
- `get_node_offline_after_seconds(db, config)` / `set_node_offline_after_seconds(db, seconds)`.

### `app/backup_settings.py`
DB-backed настройки автобэкапа. Каждый setter клэмпит значение в допустимый диапазон — защита от того, чтобы бот не мог выставить интервал ≈0 или огромный `keep_count`.

- `is_enabled(db)` / `set_enabled(db, enabled)`.
- `_clamp(value, lo, hi)` — приватный хелпер.
- `interval_hours(db)` / `set_interval_hours(db, hours)` — диапазон `MIN_INTERVAL_HOURS=1`…`MAX_INTERVAL_HOURS=720`, дефолт 24.
- `keep_count(db)` / `set_keep_count(db, count)` — диапазон `MIN_KEEP_COUNT=1`…`MAX_KEEP_COUNT=100`, дефолт 7.
- `destination(db)` / `set_destination(db, dest)` — `"server"|"dm"|"group"`, невалидное значение молча заменяется на `"server"` при чтении, но `set_destination` кидает `ValueError` на неверном значении.
- `group_chat_id(db)` / `set_group_chat_id(db, chat_id)`.
- `group_topic_id(db)` / `set_group_topic_id(db, topic_id)`.
- `last_run_at(db)` / `set_last_run_at(db, iso_ts)`.

### `app/backup_task.py`
Периодический бэкап SQLite-базы сервера в `/data/backups`, с ротацией и опциональной доставкой через бота.

- `BackupTask.__init__(db, config, notifier)` — создаёт `backup_dir`, `asyncio.Lock`.
- `stop()`.
- `run()` — цикл с `_POLL_INTERVAL_SECONDS = 900`; если автобэкап включён и `_due()` — запускает `run_backup_now()`.
- `_due()` — сравнивает время последнего запуска с текущим интервалом.
- `run_backup_now()` — под общим `asyncio.Lock` (используется и планировщиком, и кнопкой "сделать бэкап сейчас", чтобы никогда не выполняться параллельно): создаёт архив (`_create_backup_file`), обновляет `last_run_at`, ротирует старые копии, доставляет по настроенному каналу; возвращает человекочитаемую строку статуса на русском.
- `_create_backup_file()` — упаковывает в `.tar.gz` файл БД + `-wal`/`-shm`, если есть.
- `_rotate(keep)` — удаляет лишние старые файлы бэкапов сверх `keep` (старые удаляются ТОЛЬКО после успешного создания нового — неудачный бэкап никогда не опустошает существующую ротацию).
- `_deliver(path, dest)` — отправляет файл документом в ЛС всем админам (`dm`) или в группу/топик (`group`); при `group` без настроенного `chat_id` кидает `RuntimeError`.
- `list_backups()` — список `(filename, size_bytes, mtime)`, новые первыми.

### `app/live_view.py`
Опциональное автообновление read-only экранов бота (последние/топ домены, статистика, список бэкапов).

- `LiveViewManager.__init__()` — словарь задач `{(chat_id, message_id): Task}`.
- `is_active(chat_id, message_id)`.
- `stop(chat_id, message_id)` — отменяет задачу для конкретного сообщения.
- `start(chat_id, message_id, render)` — сначала останавливает предыдущую задачу для этого же сообщения (чтобы не плодить дубликаты при повторных нажатиях), возвращает `False` без запуска, если достигнут `MAX_CONCURRENT_VIEWS = 25`.
- `_run(chat_id, message_id, render)` — цикл: спит `INTERVAL_SECONDS = 20`, вызывает `render()`; молча продолжает при ошибке "message is not modified" (нормальный случай — ничего не изменилось), любая другая ошибка (сообщение удалено, бот забанен в чате и т.п.) останавливает эту конкретную live-view; жёстко завершается через `MAX_DURATION_SECONDS = 30 * 60`.
- `stop_all()` — отменяет все активные задачи (вызывается при остановке сервера).

### `app/topic_binding.py`
Позволяет привязать ноду к группе/топику Telegram без ручного ввода `chat_id`/`topic_id`: нажать "🔗 Привязать" в карточке ноды, затем отправить `/bind` прямо в нужном топике/группе.

- `TopicBindingManager.__init__()` — словарь `user_id -> (node_id, expires_at)`.
- `start(user_id, node_id)` — запускает ожидание с TTL `_TTL_SECONDS = 300`.
- `cancel(user_id)`.
- `has_pending(user_id)` — проверяет наличие и не истёк ли TTL (протухшая запись удаляется).
- `pop(user_id)` — забирает и удаляет ожидающий `node_id`, возвращает `None`, если истёк TTL.

Причина, почему не используется штатный FSM aiogram: его ключ хранения включает chat_id, а `/bind` приходит из ДРУГОГО чата (группы), чем тот, где начиналась привязка (ЛС) — поэтому состояние трекается по `user_id` отдельно.

### `app/fleet_control.py`
Два глобальных выключателя всей флотилии агентов, переключаемых из главного меню бота.

- `is_monitoring_enabled(db)` / `set_monitoring_enabled(db, enabled)` — захват трафика в принципе (tshark на нодах не запускается, если выключено).
- `is_sending_enabled(db)` / `set_sending_enabled(db, enabled)` — отправка событий на сервер (захват продолжается, но события копятся в локальном буфере).

Действуют на следующем heartbeat каждой ноды, без её перезапуска.

### `app/logging_config.py`
Логирование: ротация файлов + консоль, вымарывание секретов из логов.

- `SecretScrubFilter.filter(record)` — заменяет любые совпадения с `_TOKEN_PATTERN` (`nmt_[A-Za-z0-9_-]{30,}`) на `***REDACTED***` в `record.msg` и строковых `record.args` (числовые не трогает, чтобы не сломать `%d`-плейсхолдеры).
- `SecretScrubFilter._scrub(text)` — статический метод замены.
- `setup_logging(log_level, log_dir)` — настраивает root-логгер: ротирующий файловый хендлер (`RotatingFileHandler`, 5 МБ × 5 бэкапов) + консольный, оба со `SecretScrubFilter`; понижает уровень `uvicorn.access` до WARNING вне DEBUG.

### `app/api/deps.py`
Общие FastAPI-зависимости: доступ к БД и две независимые схемы аутентификации.

- `get_db(request)` / `get_config(request)` / `get_notifier(request)` / `get_event_rate_limiter(request)` — извлечение объектов из `request.app.state`.
- `require_admin(x_admin_key, config)` — сравнивает заголовок `X-Admin-Key` с `config.admin_api_key` через `constant_time_eq`; при несовпадении/отсутствии — `401`.
- `require_node(authorization, db)` — парсит `Authorization: Bearer <token>`; `401`, если заголовок отсутствует/пуст/не начинается с `Bearer `; ищет ноду по хэшу токена, `401` если не найдена, `403` если статус не `active` (токен отозван); возвращает объект `Node`.

### `app/api/schemas.py`
Pydantic-модели запросов/ответов REST API.

- `NodeCreateRequest` — `name: str | None` (опционально для one-tap flow); валидатор `_clean_name` обрезает пробелы и запрещает пустую строку.
- `NodeCreateResponse` — `id, name, token` (токен показывается только при создании/регенерации).
- `NodeResponse` — публичные поля ноды + `online: bool`; статический `from_node(node, offline_after_seconds)` вычисляет `online` через `node.is_online(...)`.
- `NodeSettingsUpdateRequest` — опциональные `monitoring_enabled`, `notifications_enabled`.
- `HeartbeatRequest` — `version, ip, hostname, buffer_size` (все опциональны).
- `HeartbeatResponse` — `monitoring_enabled, sending_enabled` — эффективное состояние, которое агент должен применить немедленно (уже учитывает и per-node, и fleet-wide флаги).
- `EventIn` — `domain, occurred_at, source="tls_sni", hits=1`; валидатор `_validate_domain` нормализует и проверяет домен через `normalize_domain`, кидает `ValueError` на невалидном; валидатор `_validate_source` проверяет источник по множеству `_VALID_SOURCES = {tls_sni, dns, http_host, quic, xray_log}`; `hits` ограничен `1..1_000_000` как защита от переполнения счётчика скомпрометированным/некорректным агентом.
- `EventBatchRequest` — `events: list[EventIn]`, от 1 до 1000 элементов.
- `EventBatchResponse` — `accepted: int, new_domains: list[str]`.
- `DomainResponse` — публичные поля домена; статический `from_domain(d)`.
- `StatsResponse` — `nodes_online, nodes_total, unique_domains, events_today, new_domains_today`.

### `app/api/nodes.py`
Реестр нод: создание/список/просмотр/ревок/регенерация токена, плюс heartbeat-эндпоинт агента.

- `create_node(body, db)` — `POST /api/v1/nodes`, admin-only. Генерирует токен; если `name` не передан — `create_node_auto` (one-tap flow), иначе проверяет `name_exists` и кидает `409 CONFLICT`, если имя занято.
- `list_nodes(db, config)` — `GET /api/v1/nodes`, admin-only.
- `get_node(node_id, db, config)` — `GET /api/v1/nodes/{id}`, admin-only; `404`, если не найдена.
- `update_node_settings(node_id, body, db, config)` — `PATCH /api/v1/nodes/{id}`, admin-only; обновляет `monitoring_enabled`/`notifications_enabled`, если переданы; `404`, если не найдена.
- `regenerate_token(node_id, db)` — `POST /api/v1/nodes/{id}/regenerate-token`, admin-only; `404`, если не найдена.
- `revoke_node(node_id, db)` — `POST /api/v1/nodes/{id}/revoke`, admin-only, `204`; `404`, если не найдена.
- `delete_node(node_id, db)` — `DELETE /api/v1/nodes/{id}`, admin-only, `204`; `404`, если не найдена.
- `heartbeat(body, node, db)` — `POST /api/v1/nodes/heartbeat`, node-auth (агент сам себя); обновляет heartbeat и возвращает эффективные флаги `monitoring_enabled` (комбинация per-node и fleet-wide) и `sending_enabled`.

### `app/api/events.py`
Приём событий от агента — эндпоинт, который агент дёргает в цикле всё время жизни развёртывания.

- `ingest_events(request, body, node, db, notifier, rate_limiter)` — `POST /api/v1/events`, node-auth:
  - проверяет rate limit по `node.id`; при превышении — `429 TOO_MANY_REQUESTS` с заголовком `Retry-After` (агент это уже умеет обрабатывать через свой существующий httpx-backoff);
  - проверяет `fleet_control.is_sending_enabled(db)`; если отправка на паузе — `503 SERVICE_UNAVAILABLE` (не `200` с `accepted=0`: агент чистит буфер только при успешном ответе, поэтому нужна настоящая ошибка, иначе события будут потеряны);
  - для каждого события в батче: пишет через `db.record_event`, инкрементирует `EVENTS_TOTAL`; если домен новый — инкрементирует `NEW_DOMAINS_TOTAL`, классифицирует через `classify_domain`; при `is_watched` — инкрементирует `WATCHLIST_HITS_TOTAL` и мгновенно шлёт `notifier.schedule_watchlist`; при `suppresses_notification` — помечает `ignored=True` через `db.set_ignored`; иначе — ставит в батч `notifier.schedule`;
  - опционально транслирует событие во внешний `broadcaster` (если настроен в `app.state`);
  - обновляет `last_seen_at` ноды, возвращает `EventBatchResponse(accepted, new_domains)`.

### `app/api/domains.py`
Чтение/поиск агрегированной таблицы доменов.

- `list_domains(db, limit=50, offset=0, search, order_by, node_id)` — `GET /api/v1/domains`, admin-only; `limit` 1..500, сортировка по `last_seen|first_seen|hits|domain`.

### `app/api/stats.py`
Сводные цифры дашборда.

- `get_stats(db, config)` — `GET /api/v1/stats`, admin-only; возвращает online/total ноды, уникальные домены, события и новые домены за сегодня (с начала суток UTC).

### `app/telegram/bot.py`
Сборка `aiogram.Bot`/`Dispatcher` и профиль бота.

- `_AVATAR_PATH` — путь к необязательному аватару (`app/assets/bot_avatar.png`), пропускается молча, если файла нет.
- `BOT_DESCRIPTION` / `BOT_SHORT_DESCRIPTION` — тексты описания бота.
- `configure_bot_profile(bot)` — регистрирует `/start` в подсказках команд, кнопку "Меню", описание, короткое описание, и (если файл есть) фото профиля — всё через Bot API, идемпотентно (Telegram просто перезаписывает теми же значениями).
- `build_bot_and_dispatcher(config, db, notifier, backup_task, topic_binding, live_view)` — создаёт `Bot` (с прокси, если задан `TELEGRAM_PROXY`), `Dispatcher` с `MemoryStorage`, вешает `AdminOnlyMiddleware` на `message` и `callback_query`, регистрирует `handlers.router`, кладёт общие объекты (`db, config, notifier, backup_task, topic_binding, live_view`) в workflow data диспетчера для инъекции в хендлеры.

### `app/telegram/keyboards.py`
Все инлайн-клавиатуры. `callback_data` — короткая строка `prefix:arg`, всегда числовой id ноды/домена (никогда не сырое имя/домен). Кнопки окрашены через поле `style` (Bot API 9.4): `danger`/`success`/`primary`.

Два правила окраски (см. докстринг файла):
- Обычный переключатель (мониторинг, отправка, уведомления, watchlist, автобэкап) показывает ТЕКУЩЕЕ СОСТОЯНИЕ (зелёный = включено, красный = выключено), а не действие кнопки.
- Одноразовое деструктивное действие (удалить, отозвать, подтвердить удаление) окрашено по смыслу действия независимо от состояния.

Функции:
- `_toggle_style(enabled)` / `_toggle_label(on_text, off_text, enabled)` — хелперы для переключателей.
- `PATTERN_TAG` — метки типов паттернов фильтров на русском.
- `EXPORT_PERIODS` / `STATS_PERIODS` — списки периодов для экспорта доменов и статистики.
- `main_menu(monitoring_enabled, sending_enabled)` — главное меню (Ноды/Домены/Статистика/Уведомления/Фильтры/Настройки/Бэкапы + два fleet-переключателя).
- `_add_live_controls(b, refresh_callback, live_key, live_active)` — добавляет "🔄 Обновить" + "🔁 Автообновление" на экраны live-данных.
- `back_button(target="main")` / `cancel_input(target="main")` — навигационные кнопки.
- `nodes_list(nodes, online_ids, fleet_monitoring_enabled)` — список нод; цвет кнопки: `danger` (отозвана/не отвечает), `primary` (на паузе — своя или fleet-wide пауза), `success` (работает).
- `NOTIFY_DEST_LABELS` — метки направлений доставки уведомлений.
- `node_card(node)` — карточка ноды с переключателями и действиями (переименовать/обновить токен/отозвать/удалить).
- `node_notify_dest_menu(node)` — выбор направления доставки уведомлений для ноды + привязка группы.
- `confirm_delete_node(node_id)` — подтверждение удаления.
- `domains_menu()` — меню раздела "Домены".
- `export_period_menu()` — выбор периода экспорта.
- `confirm_reset_data(back_target)` — подтверждение полного сброса доменов/событий.
- `stats_menu(period, live_active)` — меню статистики с периодами и live-controls.
- `domains_recent_menu(live_active)` / `domains_top_menu(live_active)` / `backup_list_menu(live_active)` — экраны со списками + live-controls.
- `_PRESET_MODES` — пресеты режима группировки уведомлений.
- `notify_menu(current_mode, global_enabled)` — меню уведомлений.
- `settings_menu(retention_days, offline_seconds, watchlist_enabled)` — меню настроек.
- `filters_menu(counts)` — меню фильтров с числом правил в каждом списке.
- `filters_list(list_type, rules, page, page_size=10)` — постраничный список правил.
- `confirm_remove_filter(rule, page)` — подтверждение удаления правила.
- `filter_add_list_type()` / `filter_add_pattern_type(list_type)` — выбор типа списка/паттерна при добавлении.
- `domain_notification(domain_id)` — кнопки под уведомлением о новом домене (игнорировать/копировать).
- `BACKUP_DEST_LABELS` — метки направлений доставки бэкапов.
- `backups_menu(enabled, interval_hours, keep_count, destination)` — меню раздела "Бэкапы".
- `backup_destination_menu(current)` — выбор направления доставки бэкапов.

### `app/telegram/middleware.py`
- `AdminOnlyMiddleware.__init__(admin_ids)` — множество разрешённых Telegram user id.
- `AdminOnlyMiddleware.__call__(handler, event, data)` — если отправитель не в списке админов, молча роняет апдейт (для callback_query всё равно отвечает пустым `answer()`, чтобы у клиента не висел спиннер, но без текста — посторонний не получает подтверждения, что бот вообще что-то делает).

### `app/telegram/handlers.py`
Единственный `Router`, все действия кроме `/start` и `/bind` — инлайн-кнопки. Подробный разбор экранов и callback-данных — в разделе 8.

---

## 4. agent/app — по модулям

### `app/config.py`
Конфигурация агента из переменных окружения.

- `ConfigError` — исключение при некорректной/отсутствующей конфигурации.
- `_require(name)` / `_optional(name, default)` — те же хелперы, что на сервере.
- `Config` (frozen dataclass) — `server_url, node_token, node_label, interface, tshark_path, sources, data_dir, database_path, log_level, log_dir, batch_interval_seconds, batch_max_size, heartbeat_interval_seconds, max_buffer_bytes, http_timeout_seconds`.
- `load_config()` — проверяет `SERVER_URL` начинается с `http://`/`https://` (обрезает завершающий `/`), проверяет `NODE_TOKEN` начинается с `nmt_`, парсит `SOURCES` как список через запятую, создаёт `DATA_DIR`/`LOG_DIR`.

### `app/buffer.py`
Durable bounded local outbox — сердце устойчивости агента к обрыву сети.

Захваченные домены пишутся сюда синхронно из sniffer, вычитываются асинхронно uplink-задачей. Одна строка на `(domain, source)`: повторный хит того же домена увеличивает счётчик `hits` вместо новой строки. Ограничение по ОЦЕНОЧНОМУ размеру на диске (не по числу строк) — дешёвый счётчик в памяти (длина строк + фиксированный оверхед на строку), не реальный `stat()` файла.

- `_row_bytes(domain, source)` — оценка размера строки в байтах.
- `OutboxEntry` — слот-класс `(id, domain, source, occurred_at, hits)`.
- `Buffer.__init__(path, max_bytes)` — открывает SQLite, `PRAGMA journal_mode = WAL`, `PRAGMA synchronous = NORMAL`; держит в памяти `_row_count`, `_pending_hits`, `_estimated_bytes`, пересчитываемые из реального состояния только один раз при `migrate()`.
- `migrate()` — применяет миграции из `agent/migrations/`, затем один раз пересчитывает счётчики через агрегатный `SELECT`.
- `enqueue(domain, source, occurred_at)` — сначала пытается `UPDATE` существующей строки (инкремент `hits`, обновление `last_occurred_at`); если `rowcount == 0` — `INSERT` новой строки; раз в `_SIZE_CHECK_EVERY = 50` вызовов проверяет лимит размера (не после каждой вставки — экономия CPU под нагрузкой).
- `_enforce_size_cap()` — если превышен `max_bytes`, вычищает ~5% таблицы (`to_drop = row_count // 20`), сортируя по `last_occurred_at ASC` (сначала удаляются домены, дольше всего не встречавшиеся), рекурсивно повторяет, если всё ещё не уложились.
- `peek_batch(limit)` — читает до `limit` строк, отсортированных по `last_occurred_at ASC`, не удаляя их.
- `delete_ids(ids)` — удаляет подтверждённые сервером строки, корректирует счётчики.
- `size()` — суммарное число буферизованных (ещё не отправленных) хитов, а не число уникальных доменов — именно это идёт в heartbeat и показывается админу.
- `close()`.

### `app/filters.py`
- `normalize_domain(raw)` — идентична серверной копии (см. раздел 3, `app/filters.py`).

### `app/main.py`
Точка входа агента: захват → локальный буфер → uplink на сервер.

- `_run()` (async) — грузит конфиг, настраивает логирование, создаёт и мигрирует `Buffer`, создаёт `RemoteControl`, `Sniffer`, `UplinkTask`; регистрирует обработчики `SIGTERM`/`SIGINT` (кроме Windows dev-окружений, где `add_signal_handler` не реализован); запускает три фоновые задачи (`sniffer.run()`, `uplink.run()`, `uplink.run_heartbeat()`); по сигналу останова — грациозно гасит sniffer и uplink, закрывает буфер.
- `main()` — точка входа CLI: запускает `asyncio.run(_run())`, при `ConfigError` печатает сообщение и завершает процесс с кодом 1.

### `app/sniffer.py`
Живой захват через `tshark`. По одному подпроцессу tshark на источник, каждый со своим capture filter; sniffer ничего не знает о сервере/HTTP/ретраях — это забота uplink.

- `_MAX_SESSION_SECONDS = 600` — период принудительного перезапуска процесса tshark: живой захват копит per-TCP-stream состояние без ограничения (в отличие от разбора конечного pcap-файла, где память освобождается по EOF), периодический рестарт сбрасывает это состояние, прежде чем оно станет проблемой; потеря ~1-2с захвата — приемлемая цена.
- `_PAUSE_POLL_SECONDS = 5` — частота опроса состояния паузы/остановки.
- `_SourceSpec` (frozen dataclass) — `name, tshark_filter, tshark_field, capture_filter`. `capture_filter` — BPF-фильтр ядра (реальный рычаг производительности), `tshark_filter` — display-фильтр userspace (сам по себе не экономит CPU на диссекцию).
- `_AVAILABLE_SOURCES` — словарь только реально реализованных источников: `tls_sni` (TCP, поле `tls.handshake.extensions_server_name`), `dns` (UDP порт 53, поле `dns.qry.name`, фильтр только на запросы `dns.flags.response == 0`).
- `resolve_sources(raw)` — парсит `SOURCES` env, отбрасывает с предупреждением неизвестные имена, при пустом результате — fallback на `tls_sni`.
- `_SourceWorker.__init__(spec, buffer, interface, tshark_path, remote_control)`.
- `_SourceWorker.run()` — основной цикл: если `monitoring_enabled` выключен — не запускает tshark вообще, просто ждёт; иначе запускает `_run_once_or_pause()` с таймаутом `_MAX_SESSION_SECONDS` (по истечении — плановый рестарт); при падении процесса — рестарт через 5с с логом `exception`.
- `_SourceWorker._run_once_or_pause()` — запускает захват и параллельно ждёт возможной паузы; если пауза наступила раньше завершения захвата — гасит процесс (не считая это крашем); иначе дожидается результата захвата (перевызывая исключение, если оно было).
- `_SourceWorker._wait_until_paused()` — poll-цикл ожидания флага паузы.
- `_SourceWorker.stop()` — сигнал остановки + завершение процесса.
- `_SourceWorker._terminate_process()` — `terminate()`, при таймауте 5с — `kill()`.
- `_SourceWorker._run_once()` — собирает и запускает команду `tshark`: `-i <interface>`, `-f <capture_filter>` (BPF на уровне ядра), `-n` (без резолвинга имён — известный источник нагрузки CPU), `-Q` (тихий режим), `-o tcp.desegment_tcp_streams:false` и `-o tls.desegment_ssl_records:false` (реассемблинг TCP-потоков не нужен — TLS ClientHello с SNI практически всегда в одном пакете), `-o tcp.analyze_sequence_numbers:false` и `-o tcp.calculate_timestamps:false` (анализ TCP-последовательностей держит своё состояние на поток — крупнейший источник роста памяти на ноде с активной сменой соединений), `-Y <tshark_filter>`, `-T fields -e <tshark_field> -l`; читает stdout построчно через `_handle_line`, параллельно дренирует stderr через `_drain_stderr` (фильтрует строку "Capturing on").
- `_SourceWorker._drain_stderr(stream)` — построчно логирует stderr на уровне debug.
- `_SourceWorker._handle_line(raw_line)` — разбивает строку на токены (по запятым/пробелам — tshark иногда выводит несколько значений через запятую), нормализует каждый через `normalize_domain`, кладёт валидные в буфер с текущим временем UTC.
- `Sniffer.__init__(buffer, interface, tshark_path, sources, remote_control)` — создаёт по одному `_SourceWorker` на каждый resolved source.
- `Sniffer.run()` — запускает все воркеры параллельно.
- `Sniffer.stop()` — гасит все воркеры и отменяет задачи.

### `app/uplink.py`
Общение с сервером: вычитывает локальный outbox батчами, шлёт периодические heartbeat'ы. Оба цикла ретраят с экспоненциальным backoff и никогда не выбрасывают исключение из `run()` — отказ сервера должен деградировать агента (события копятся в ограниченном буфере), но не ронять его.

- `AGENT_VERSION = "1.0.0"` — версия агента, отправляется в heartbeat.
- `_MAX_BACKOFF_SECONDS = 120`.
- `UplinkTask.__init__(config, buffer, remote_control)` — создаёт `httpx.AsyncClient` с базовым URL сервера, заголовком `Authorization: Bearer <token>`, таймаутом.
- `run()` — цикл отправки батчей: если `sending_enabled == False` (пауза с Telegram) — ничего не шлёт, буфер продолжает копиться; иначе вызывает `_send_pending_batch()`; backoff растёт при неудаче, сбрасывается при успехе.
- `run_heartbeat()` — отдельный цикл, каждые `heartbeat_interval_seconds` (с тем же паттерном backoff) шлёт heartbeat.
- `stop()` — сигнал остановки + закрытие HTTP-клиента.
- `_send_pending_batch()` — читает до `batch_max_size` записей из буфера, формирует payload `{"events": [...]}`; при HTTP `403` (токен отозван) логирует ошибку и возвращает `None` без удаления из буфера; при успехе удаляет отправленные id из буфера (`delete_ids`), возвращает число отправленных.
- `_send_heartbeat()` — шлёт `{version, hostname, ip, buffer_size}` на `/api/v1/nodes/heartbeat`, при успехе применяет ответ через `_apply_remote_control`.
- `_apply_remote_control(resp)` — парсит JSON-ответ, если старый сервер без тела ответа — молча оставляет текущие флаги; иначе обновляет `remote_control.monitoring_enabled`/`sending_enabled`.
- `_best_effort_local_ip()` — определяет локальный IP через UDP-сокет к `8.8.8.8:80` (без реальной отправки пакета), возвращает `None` при ошибке.

### `app/remote_control.py`
Два флага, которые сервер может переключить из главного меню Telegram, применяются агентом на следующем ответе heartbeat, без рестарта.

- `RemoteControl` (dataclass) — `monitoring_enabled: bool = True`, `sending_enabled: bool = True`. Обычные bool-атрибуты достаточны, т.к. GIL CPython делает чтение/запись одного атрибута атомарным, а sniffer и uplink читают только свой собственный флаг.

### `app/logging_config.py`
Идентична серверной версии (см. раздел 3), только целевой файл лога — `domain-monitor-agent.log`, и дополнительно понижает уровень `httpx`/`httpcore` до WARNING вне DEBUG.

---

## 5. База данных сервера

Файл БД: `config.database_path` (по умолчанию `/data/domain_monitor.db`). Каждый ниже описанный `.sql`-файл применяется один раз (`schema_migrations`), в алфавитном порядке имён файлов.

### Таблица `schema_migrations` (создаётся кодом `database.migrate()`, не миграцией)
| Колонка | Тип | Назначение |
|---|---|---|
| `filename` | TEXT PK | Имя применённого файла миграции |
| `applied_at` | TEXT | Когда применена |

### `0001_initial.sql` — базовые таблицы

**`nodes`** — одна строка на зарегистрированного агента.
| Колонка | Тип | Назначение |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | |
| `name` | TEXT NOT NULL, уникальный индекс | Имя ноды (уникальность обеспечивает `idx_nodes_name`) |
| `token_hash` | TEXT NOT NULL, уникальный индекс | SHA-256 токена (сам токен никогда не хранится) |
| `status` | TEXT DEFAULT 'active' | `active` \| `revoked` |
| `version` | TEXT | Версия агента из heartbeat |
| `ip` | TEXT | IP из heartbeat |
| `hostname` | TEXT | hostname из heartbeat |
| `created_at` | TEXT NOT NULL | |
| `last_seen_at` | TEXT | Обновляется при heartbeat и при приёме событий |
| `last_heartbeat_at` | TEXT | Обновляется только при heartbeat, используется для online/offline |
| `monitoring_enabled` | INTEGER DEFAULT 1 | Per-node переключатель мониторинга |
| `notifications_enabled` | INTEGER DEFAULT 1 | Per-node переключатель уведомлений |

Читают/пишут: почти все методы блока "Nodes" в `database.py`.

**`domains`** — одна строка на глобально уникальный домен.
| Колонка | Тип | Назначение |
|---|---|---|
| `id` | INTEGER PK | |
| `domain` | TEXT NOT NULL, уникальный индекс | |
| `first_seen` / `last_seen` | TEXT NOT NULL | Индексированы под сортировку |
| `hits` | INTEGER DEFAULT 0, индексирован | Суммарный счётчик хитов (с учётом `events.hits`) |
| `node_id` | INTEGER FK → nodes(id) ON DELETE CASCADE, индексирован | Нода последнего сообщения об этом домене |
| `ignored` | INTEGER DEFAULT 0 | Флаг подавления уведомлений (ручной или через filter_rules) |
| `notification_sent` | INTEGER DEFAULT 0 | (Задел под будущую доставку — реально используется `pending_notifications`) |

Читают/пишут: `record_event`, `get_domain`, `mark_notified`, `pending_notifications`, `set_ignored`, `list_domains`, `count_domains`, `top_domains`, `reset_domains_and_events`.

**`events`** — полная история наблюдений, подлежит retention-очистке.
| Колонка | Тип | Назначение |
|---|---|---|
| `id` | INTEGER PK | |
| `node_id` | INTEGER FK → nodes(id) ON DELETE CASCADE, индексирован | |
| `domain` | TEXT NOT NULL, индексирован | |
| `source` | TEXT DEFAULT 'tls_sni' | |
| `occurred_at` | TEXT NOT NULL, индексирован | Время фактического наблюдения на ноде |
| `received_at` | TEXT NOT NULL | Время приёма сервером |

Читают/пишут: `record_event`, `count_events_since`, `top_active_nodes_since`, `purge_events_older_than`, `reset_domains_and_events`.

**`settings`** — key-value хранилище рантайм-настроек.
| Колонка | Тип |
|---|---|
| `key` | TEXT PK |
| `value` | TEXT NOT NULL |

Используется модулями `notifier.py`, `runtime_settings.py`, `backup_settings.py`, `fleet_control.py` — все настройки, редактируемые из Telegram без рестарта, живут здесь.

### `0003_filter_rules.sql` — фильтр-листы

**`filter_rules`**
| Колонка | Тип | Назначение |
|---|---|---|
| `id` | INTEGER PK | |
| `list_type` | TEXT CHECK IN ('ignore','allow','watch') | |
| `pattern_type` | TEXT CHECK IN ('exact','suffix','wildcard') | |
| `pattern` | TEXT NOT NULL | |
| `created_at` | TEXT NOT NULL | |

Уникальный индекс на `(list_type, pattern_type, pattern)` — не даёт добавить дубликат правила. Отдельно от `domains.ignored` (тот — разовый флаг на уже записанной строке, `filter_rules` — паттерн, проверяемый при приёме КАЖДОГО входящего события). Читают/пишут: `add_filter_rule`, `remove_filter_rule`, `list_filter_rules`, `all_filter_rules_cached`.

### `0004_node_notify_routing.sql` — маршрутизация уведомлений по ноде
Добавляет к `nodes`: `notify_destination TEXT DEFAULT 'dm'`, `notify_group_chat_id INTEGER`, `notify_group_topic_id INTEGER`. Читают/пишут: `set_node_notify_destination`, `set_node_notify_group`, используется в `notifier.deliver_for_node`.

### `0005_agent_buffer_size.sql` — индикатор локального буфера агента
Добавляет к `nodes`: `agent_buffer_size INTEGER`. Пишется только из `touch_heartbeat()`, читается на карточке ноды и в статистике бота (`_stats_text`).

### `0006_events_hits.sql` — счётчик хитов в событии
Добавляет к `events`: `hits INTEGER NOT NULL DEFAULT 1`. Нужна из-за того, что агент теперь схлопывает повторные хиты одного домена в одну буферизованную строку со счётчиком (см. `agent/app/buffer.py`) — одно полученное событие может представлять больше одного реального хита; используется в `record_event`, `count_events_since`.

---

## 6. Локальный буфер агента (outbox)

Файл БД агента: `config.database_path` (по умолчанию `/data/agent_buffer.db`).

### `0001_outbox.sql`

**`outbox`** — каждый захваченный домен сначала попадает сюда и удаляется только после подтверждения сервером.
| Колонка | Тип | Назначение |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | |
| `domain` | TEXT NOT NULL | |
| `source` | TEXT DEFAULT 'tls_sni' | |
| `occurred_at` | TEXT NOT NULL | |
| `created_at` | TEXT NOT NULL | |

Индекс на `created_at`.

### `0002_outbox_dedupe.sql` — схлопывание повторов
Добавляет `hits INTEGER NOT NULL DEFAULT 1` и `last_occurred_at TEXT` (бэкфилл из `occurred_at`), затем чистит уже накопившиеся до этой миграции дубликаты (оставляет только строку с максимальным `id` для каждой пары `(domain, source)` — без попытки восстановить их истинный совокупный счётчик, это разовая очистка деградировавших до-дедуп данных, а не штатное поведение), затем создаёт уникальный индекс `idx_outbox_domain_source` на `(domain, source)` и индекс на `last_occurred_at`.

Итоговая схема `outbox`, с которой работает `agent/app/buffer.py`:
| Колонка | Тип | Кто читает/пишет |
|---|---|---|
| `id` | INTEGER PK | `peek_batch`, `delete_ids` |
| `domain`, `source` | TEXT | `enqueue` (UPDATE-then-INSERT по этой паре), уникальный индекс |
| `occurred_at` | TEXT | Время первого наблюдения этой пары (не обновляется после создания строки) |
| `last_occurred_at` | TEXT | Обновляется при каждом повторном `enqueue`; по нему сортируется `peek_batch` (ASC — старые сначала) и по нему же вытесняются строки при переполнении (`_enforce_size_cap`, тоже ASC — вытесняются дольше всего не встречавшиеся) |
| `created_at` | TEXT | Время создания строки |
| `hits` | INTEGER | Счётчик повторов; отправляется на сервер как `EventIn.hits`, обнуляется удалением строки после подтверждённой отправки |

Ограничение — по оценочному размеру на диске (`max_bytes`, из `MAX_BUFFER_BYTES`, по умолчанию 1 ГиБ), не по числу строк — подробности в разделе 4 (`app/buffer.py`).

---

## 7. REST API

Базовый префикс всех бизнес-эндпоинтов — `/api/v1`. Плюс служебные `GET /healthz` (без авторизации) и `GET /metrics` (без авторизации, Prometheus-формат).

### `/api/v1/nodes` (`server/app/api/nodes.py`)

| Метод и путь | Авторизация | Тело / параметры | Ответ | Коды ошибок |
|---|---|---|---|---|
| `POST /api/v1/nodes` | `X-Admin-Key` | `NodeCreateRequest {name?: str}` | `NodeCreateResponse {id, name, token}` | `409 CONFLICT` — имя уже занято (если `name` передан явно) |
| `GET /api/v1/nodes` | `X-Admin-Key` | — | `list[NodeResponse]` | — |
| `GET /api/v1/nodes/{node_id}` | `X-Admin-Key` | — | `NodeResponse` | `404` — нода не найдена |
| `PATCH /api/v1/nodes/{node_id}` | `X-Admin-Key` | `NodeSettingsUpdateRequest {monitoring_enabled?, notifications_enabled?}` | `NodeResponse` | `404` |
| `POST /api/v1/nodes/{node_id}/regenerate-token` | `X-Admin-Key` | — | `NodeCreateResponse` (новый токен) | `404` |
| `POST /api/v1/nodes/{node_id}/revoke` | `X-Admin-Key` | — | `204 No Content` | `404` |
| `DELETE /api/v1/nodes/{node_id}` | `X-Admin-Key` | — | `204 No Content` | `404` |
| `POST /api/v1/nodes/heartbeat` | `Bearer <NODE_TOKEN>` | `HeartbeatRequest {version?, ip?, hostname?, buffer_size?}` | `HeartbeatResponse {monitoring_enabled, sending_enabled}` | `401` — токен неверный/отсутствует; `403` — токен отозван |

### `/api/v1/events` (`server/app/api/events.py`)

| Метод и путь | Авторизация | Тело | Ответ | Коды ошибок |
|---|---|---|---|---|
| `POST /api/v1/events` | `Bearer <NODE_TOKEN>` | `EventBatchRequest {events: [EventIn], 1..1000 шт.}` | `EventBatchResponse {accepted, new_domains}` | `401`/`403` — как у heartbeat; `429 TOO_MANY_REQUESTS` (+ `Retry-After`) — превышен rate limit по ноде; `503 SERVICE_UNAVAILABLE` — глобальная отправка событий поставлена на паузу в Telegram |

`EventIn`: `domain` (валидируется/нормализуется), `occurred_at?` (по умолчанию — текущее время сервера), `source="tls_sni"` (только из `_VALID_SOURCES`), `hits=1` (1..1_000_000).

### `/api/v1/domains` (`server/app/api/domains.py`)

| Метод и путь | Авторизация | Параметры запроса | Ответ |
|---|---|---|---|
| `GET /api/v1/domains` | `X-Admin-Key` | `limit` (1..500, деф. 50), `offset` (≥0), `search`, `order_by` (`last_seen`\|`first_seen`\|`hits`\|`domain`), `node_id` | `list[DomainResponse]` |

### `/api/v1/stats` (`server/app/api/stats.py`)

| Метод и путь | Авторизация | Ответ |
|---|---|---|
| `GET /api/v1/stats` | `X-Admin-Key` | `StatsResponse {nodes_online, nodes_total, unique_domains, events_today, new_domains_today}` |

### Служебные

| Метод и путь | Авторизация | Назначение |
|---|---|---|
| `GET /healthz` | нет | `{"status": "ok"}` — используется docker healthcheck и установщиком |
| `GET /metrics` | нет | Prometheus-метрики (текстовый формат), обновляет gauge'и перед отдачей |

---

## 8. Telegram-бот: экраны и кнопки

Все обработчики находятся в `server/app/telegram/handlers.py`, единственный `Router`. Кроме `/start` (открывает главное меню) и `/bind` (завершает привязку группы/топика к ноде), весь ввод текста идёт через конечный автомат `aiogram.fsm` (класс `Inputs(StatesGroup)`), а всё управление — через `callback_data` инлайн-кнопок.

### Состояния FSM (`Inputs`)
`waiting_for_node_rename`, `waiting_for_filter_pattern`, `waiting_for_filter_check`, `waiting_for_export_range`, `waiting_for_custom_batch_seconds`, `waiting_for_retention_days`, `waiting_for_offline_seconds`, `waiting_for_backup_interval`, `waiting_for_backup_keep`, `waiting_for_backup_group_chat_id`, `waiting_for_backup_group_topic_id`.

### Навигация / главное меню

| `F.data` | Хендлер | Действие | Клавиатура |
|---|---|---|---|
| команда `/start` | `cmd_start` | Сбрасывает FSM, показывает главное меню | `kb.main_menu` |
| `main` | `cb_main` | То же, но через edit_text (возврат "Назад") | `kb.main_menu` |
| `fleet_toggle_monitoring` | `cb_fleet_toggle_monitoring` | Переключает `fleet_control.monitoring_enabled` для всей флотилии | возврат в `cb_main` |
| `fleet_toggle_sending` | `cb_fleet_toggle_sending` | Переключает `fleet_control.sending_enabled` | возврат в `cb_main` |

### Ноды

| `F.data` | Хендлер | Действие | Клавиатура |
|---|---|---|---|
| `nodes` | `cb_nodes` | Список нод с легендой цветов | `kb.nodes_list` |
| `node:{id}` | `cb_node_card` | Карточка ноды: статус, heartbeat, версия, IP, hostname, число доменов, буфер, куда идут уведомления | `kb.node_card` |
| `node_toggle_mon:{id}` | `cb_node_toggle_mon` | Переключает `monitoring_enabled` конкретной ноды | возврат в карточку |
| `node_toggle_notif:{id}` | `cb_node_toggle_notif` | Переключает `notifications_enabled` ноды | возврат в карточку |
| `node_notify_dest:{id}` | `cb_node_notify_dest` | Экран выбора направления доставки (ЛС/группа/оба) | `kb.node_notify_dest_menu` |
| `node_notify_dest_set:{id}:{dest}` | `cb_node_notify_dest_set` | Устанавливает `notify_destination` | возврат в `cb_node_notify_dest` |
| `node_notify_bind:{id}` | `cb_node_notify_bind` | Запускает `TopicBindingManager.start`, инструкция отправить `/bind` в целевом чате | `kb.cancel_input` |
| команда `/bind` (в любом чате) | `cmd_bind` | `TopicBindingManager.pop`; если есть ожидающая привязка — сохраняет `chat_id`/`message_thread_id` через `db.set_node_notify_group` | — |
| `node_regen:{id}` | `cb_node_regen` | Генерирует новый токен, показывает его один раз | — |
| `node_revoke:{id}` | `cb_node_revoke` | `set_node_status(revoked)` | возврат в карточку |
| `node_delete:{id}` | `cb_node_delete_confirm_prompt` | Запрос подтверждения удаления | `kb.confirm_delete_node` |
| `node_delete_confirm:{id}` | `cb_node_delete` | Удаляет ноду (каскадно её домены/события) | возврат в список нод |
| `node_add` | `cb_node_add` | One-tap: создаёт ноду без имени (`create_node_auto`), выдаёт готовую команду установки с сервером+токеном внутри | `kb.back_button("nodes")` |
| `node_rename:{id}` | `cb_node_rename` | Запрашивает новое имя (FSM `waiting_for_node_rename`) | `kb.cancel_input` |
| (текст в состоянии `waiting_for_node_rename`) | `on_node_rename_input` | Валидирует и переименовывает через `db.rename_node` | — |

### Домены

| `F.data` | Хендлер | Действие | Клавиатура |
|---|---|---|---|
| `domains` | `cb_domains` | Меню раздела | `kb.domains_menu` |
| `domains_recent` | `cb_domains_recent` | Последние 20 доменов по `last_seen` | `kb.domains_recent_menu` (+ live) |
| `domains_top` | `cb_domains_top` | Топ 20 по `hits` | `kb.domains_top_menu` (+ live) |
| `domains_export` | `cb_domains_export_menu` | Выбор периода экспорта | `kb.export_period_menu` |
| `domains_export_period:{period}` | `cb_domains_export_period` | Формирует `.txt` и отправляет документом | — |
| `domains_export_custom` | `cb_domains_export_custom` | Запрашивает произвольный диапазон дат (FSM `waiting_for_export_range`) | `kb.cancel_input` |
| (текст в `waiting_for_export_range`) | `on_export_range_input` | Парсит `ГГГГ-ММ-ДД [ГГГГ-ММ-ДД]`, формирует `.txt` | — |
| `data_reset:{target}` | `cb_data_reset_prompt` | Предупреждение перед полным сбросом доменов/событий | `kb.confirm_reset_data` |
| `data_reset_confirm:{target}` | `cb_data_reset_confirm` | `db.reset_domains_and_events()`, показывает alert со счётчиками удалённого | возврат на `domains` или `stats` |

### Статистика

| `F.data` | Хендлер | Действие |
|---|---|---|
| `stats` | `cb_stats` | Статистика за "Сегодня" по умолчанию |
| `stats_period:{period}` | `cb_stats_period` | Статистика за выбранный период (today/week/month/all) |

Текст статистики (`_stats_text`) включает: онлайн/всего нод, уникальные домены всего, новые за период, события за период, скорость событий/час, суммарный объём в буферах агентов (если есть), размер файла БД (`_database_size_bytes`, включая `-wal`/`-shm`), топ-3 активных ноды.

### Уведомления

| `F.data` | Хендлер | Действие |
|---|---|---|
| `notify_menu` | `cb_notify_menu` | Экран настройки группировки уведомлений |
| `notify_toggle_global` | `cb_notify_toggle` | Глобальный вкл/выкл уведомлений |
| `notify_mode:{value}` | `cb_notify_mode` | Устанавливает пресет батчинга (`instant`/5/15/30/60 сек) |
| `notify_custom` | `cb_notify_custom` | Запрашивает свой интервал (FSM `waiting_for_custom_batch_seconds`) |
| (текст) | `on_custom_batch_seconds_input` | Валидирует 1..3600, сохраняет |

### Фильтры (Ignore/Allow/Watch)

| `F.data` | Хендлер | Действие |
|---|---|---|
| `filters` | `cb_filters` | Меню со счётчиками правил по каждому списку |
| `filters_list:{type}:{page}` | `cb_filters_list` | Постраничный список правил |
| `filter_remove_confirm:{id}:{page}` | `cb_filter_remove_confirm` | Подтверждение удаления правила |
| `filter_remove:{id}:{type}:{page}` | `cb_filter_remove` | Удаляет правило, возвращает список |
| `filter_add` | `cb_filter_add` | Выбор списка (Watch/Ignore/Allow) |
| `filter_add_type:{type}` | `cb_filter_add_type` | Выбор типа паттерна (exact/suffix/wildcard) |
| `filter_add_ptype:{type}:{ptype}` | `cb_filter_add_ptype` | Запрашивает паттерн(ы) (FSM `waiting_for_filter_pattern`), поддерживает множественный ввод построчно |
| (текст) | `on_filter_pattern_input` | Парсит строки, дедуплицирует, добавляет все правила разом, отчитывается о добавленных/дублях |
| `filter_check` | `cb_filter_check` | Запрашивает домен для проверки (FSM `waiting_for_filter_check`) |
| (текст) | `on_filter_check_input` | Прогоняет домен через `classify_domain`, показывает вердикт |

### Настройки

| `F.data` | Хендлер | Действие |
|---|---|---|
| `settings` | `cb_settings` | Экран настроек: адрес сервера, хост, retention, offline timeout, watchlist-уведомления, список админов |
| `settings_retention` | `cb_settings_retention` | Запрашивает число дней хранения событий (FSM `waiting_for_retention_days`) |
| (текст) | `on_retention_days_input` | Валидирует 0..3650, сохраняет через `runtime_settings.set_event_retention_days` |
| `settings_offline` | `cb_settings_offline` | Запрашивает таймаут offline (FSM `waiting_for_offline_seconds`) |
| (текст) | `on_offline_seconds_input` | Валидирует 10..86400, сохраняет |
| `settings_toggle_watchlist` | `cb_settings_toggle_watchlist` | Переключает watch-уведомления |

### Уведомление о новом домене (кнопки под сообщением)

| `F.data` | Хендлер | Действие |
|---|---|---|
| `domain_ignore:{id}` | `cb_domain_ignore` | Ставит `ignored=True` и добавляет suffix-правило в Ignore List на будущее |
| `domain_copy:{id}` | `cb_domain_copy` | Присылает домен отдельным `<code>`-сообщением для удобного копирования |

### Бэкапы

| `F.data` | Хендлер | Действие |
|---|---|---|
| `backups` | `cb_backups` | Экран статуса автобэкапа |
| `backup_toggle` | `cb_backup_toggle` | Вкл/выкл автобэкап |
| `backup_set_interval` | `cb_backup_set_interval` | Запрашивает периодичность в часах (FSM `waiting_for_backup_interval`) |
| (текст) | `on_backup_interval_input` | Сохраняет (клэмп в `backup_settings`) |
| `backup_set_keep` | `cb_backup_set_keep` | Запрашивает число хранимых копий (FSM `waiting_for_backup_keep`) |
| (текст) | `on_backup_keep_input` | Сохраняет |
| `backup_set_destination` | `cb_backup_set_destination` | Выбор направления доставки |
| `backup_dest:{dest}` | `cb_backup_dest` | При `group` — переводит в FSM `waiting_for_backup_group_chat_id`, иначе сразу сохраняет |
| (текст, `waiting_for_backup_group_chat_id`) | `on_backup_group_chat_id_input` | Сохраняет chat_id, переходит к запросу topic_id |
| (текст, `waiting_for_backup_group_topic_id`) | `on_backup_group_topic_id_input` | Сохраняет topic_id (или `None` при вводе `-`) |
| `backup_now` | `cb_backup_now` | Немедленно запускает `backup_task.run_backup_now()` |
| `backup_list` | `cb_backup_list` | Список сделанных бэкапов (+ live) |

### Live-views (автообновление)

| `F.data` | Хендлер | Действие |
|---|---|---|
| `live_toggle:{kind}[:{arg}]` | `cb_live_toggle` | Включает/выключает автообновление конкретного экрана (`domains_recent`, `domains_top`, `stats[:period]`, `backup_list`) через `LiveViewManager` |

### Fallback

Любое сообщение вне активного FSM-состояния (`fallback`) — просто показывает главное меню.

---

## 9. CLI-команды

### `server/bin/domain-monitor-server`
```
domain-monitor-server            интерактивное меню (scripts/menu.sh)
domain-monitor-server status     bash scripts/healthcheck.sh
domain-monitor-server start      compose up -d
domain-monitor-server stop       compose stop
domain-monitor-server restart    compose restart
domain-monitor-server logs       compose logs --tail 100 -f
domain-monitor-server update     bash scripts/update.sh
domain-monitor-server uninstall  bash scripts/uninstall.sh
domain-monitor-server doctor     bash scripts/doctor.sh
domain-monitor-server backup     bash scripts/backup.sh
domain-monitor-server restore <f> bash scripts/restore.sh <f>
domain-monitor-server add-node   bash scripts/add_node.sh
domain-monitor-server migrate-export        bash scripts/migrate_export.sh
domain-monitor-server migrate-import <файл> bash scripts/migrate_import.sh <файл>
```
Резолвит `PROJECT_DIR` через символическую ссылку (`readlink -f`), чтобы работать при вызове как `domain-monitor-server` из `/usr/local/bin`, а не только изнутри чекаута.

### `agent/bin/domain-monitor-agent`
```
domain-monitor-agent            интерактивное меню (scripts/menu.sh)
domain-monitor-agent status     bash scripts/healthcheck.sh
domain-monitor-agent start/stop/restart/logs — как у сервера
domain-monitor-agent update      bash scripts/update.sh
domain-monitor-agent uninstall   bash scripts/uninstall.sh
domain-monitor-agent doctor      bash scripts/doctor.sh
domain-monitor-agent backup      bash scripts/backup.sh
domain-monitor-agent restore <f> bash scripts/restore.sh <f>
domain-monitor-agent set-server <url> bash scripts/set_server.sh <url>
```

### `server/scripts/*.sh`
- **`lib.sh`** — общие хелперы: `have_compose()` (определяет `docker compose` plugin/standalone/none), `compose(...)` (обёртка), `port_is_free(port)` (через `ss -tulpn`), `find_free_port(port=8280)` (сканирует вверх до свободного), `compose_build_quiet()` (запускает `compose up -d --build` в фоне с точками прогресса вместо полного лога, дампит хвост лога только при ошибке), `open_firewall_port(port)` (только если UFW реально активен — открывает порт, не трогая другие фаерволы), `close_firewall_port(port)` (закрывает ТОЛЬКО правило, которое похоже на добавленное самим установщиком — точный паттерн `<port>/tcp ALLOW Anywhere`).
- **`menu.sh`** — интерактивное меню (12 пунктов: статус/логи/рестарт/обновить/диагностика/бэкап/восстановить/добавить ноду/экспорт миграции/импорт миграции/удалить/выход).
- **`doctor.sh`** — проверяет: Docker запущен, Compose доступен, `.env` существует и содержит `ADMIN_API_KEY`, контейнер существует и запущен, healthcheck контейнера здоров, `/healthz` отвечает на заданном порту, `PUBLIC_URL` задан и содержит правильный порт (критично — иначе агенты будут стучаться не туда), UFW разрешает входящие на порту (если UFW активен), целостность БД (`PRAGMA integrity_check`) и число применённых миграций, доступность Telegram API с текущим `BOT_TOKEN`/`TELEGRAM_PROXY`, свободное место на диске (≥500 МБ). Возвращает код `1` при любом провале.
- **`backup.sh`** — архивирует `./data` + `.env` в `./backups/domain-monitor-server-backup-<timestamp>.tar.gz`, не останавливая контейнер (WAL-режим SQLite делает это безопасным).
- **`restore.sh <файл>`** — требует root, показывает предупреждение, останавливает контейнер, переименовывает текущую `data/` в `data.pre-restore.<timestamp>`, распаковывает архив, запускает контейнер заново.
- **`healthcheck.sh`** — быстрый статус: `2` — не установлен, `1` — остановлен, `0`/вывод статуса healthcheck (healthy/starting), иначе дампит последние 20 строк лога.
- **`uninstall.sh`** — требует root, подтверждение, `compose down --rmi local`, закрывает firewall-порт, удаляет CLI-обёртку, опционально удаляет `./data`, опционально `./backups`, опционально всю папку проекта.
- **`update.sh`** — требует root, `git fetch` + `git reset --hard origin/HEAD` (если это git-чекаут), пересобирает образ, перезапускает.
- **`add_node.sh`** — создаёт ноду через локальный REST API (`POST /api/v1/nodes` с пустым телом → auto-name), печатает готовую однострочную команду установки агента с URL и токеном внутри.
- **`migrate_export.sh`** — упаковывает `./data` + `.env` в `domain-monitor-migration-<timestamp>.tar.gz` (тот же формат, что и `backup.sh` — пакет миграции ЕСТЬ бэкап, просто предназначенный для другого хоста), печатает пошаговую инструкцию переноса.
- **`migrate_import.sh <файл>`** — требует root, проверяет наличие `data/domain_monitor.db` внутри архива, сохраняет текущие данные как `*.pre-migration.<timestamp>`, разворачивает пакет, **умно сливает `.env`**: переносит только `BOT_TOKEN`, `ADMIN_ID`, `ADMIN_API_KEY`, `TELEGRAM_PROXY` из пакета, оставляя `PORT`/`PUBLIC_URL` этого сервера нетронутыми; запускает сервер, ждёт готовности до 60с; в конце через `docker exec` заходит в контейнер и печатает Python-скриптом список всех нод, для каждой выводит готовую команду `domain-monitor-agent set-server <новый URL>`.

### `agent/scripts/*.sh`
- **`lib.sh`** — тот же набор хелперов, что у сервера, за вычетом функций работы с firewall (агенту порт слушать не нужно).
- **`menu.sh`** — интерактивное меню (10 пунктов, включая "Сменить сервер").
- **`doctor.sh`** — проверяет Docker/Compose, `.env` (`SERVER_URL` начинается с `http(s)://`, `NODE_TOKEN` начинается с `nmt_`), контейнер запущен, healthcheck здоров, `tshark -v` реально работает внутри контейнера, права `NET_ADMIN`+`NET_RAW` заданы у контейнера, сервер отвечает на `/healthz` по `SERVER_URL`, целостность локального буфера (`PRAGMA integrity_check`, при провале — совет просто удалить файл, т.к. это лишь буфер повторной отправки, не источник истины), свободное место на диске (≥200 МБ).
- **`backup.sh`** / **`restore.sh`** — идентичны по структуре серверным, но архивируют/восстанавливают только `./data` (локальный буфер) + `.env`.
- **`healthcheck.sh`** — идентична серверной, только для контейнера `domain-monitor-agent`.
- **`uninstall.sh`** — идентична серверной (без шага с firewall).
- **`update.sh`** — идентична серверной.
- **`set_server.sh <url>`** — переписывает `SERVER_URL` в `.env` (без смены `NODE_TOKEN`), перезапускает контейнер. Используется после `migrate_import.sh` на сервере, когда控制-центр переехал на новый хост.

---

## 10. install.sh / server/install.sh / agent/install-agent.sh

### `install.sh` (корень репозитория)
Единая точка входа. Определяет, откуда запущен (`resolve_project_dir`): если рядом уже есть `server/` и `agent/` — использует текущий чекаут; иначе ищет `/opt/domain-monitor`; иначе клонирует репозиторий туда. Устанавливает сам себя как `/usr/local/bin/dm` (копией, не симлинком — работает даже при запуске через `curl | bash`, где нет исходного файла; запись через временный файл + `mv` — атомарная замена, безопасная даже если процесс сам исполняется из перезаписываемого файла).

Главное меню (`show_menu`):
```
1) Оба          - сервер (бот) и агент на этой машине
2) Agent        - только сниффер трафика
3) Server       - сервер + Telegram-бот
4) Статус       - что установлено и работает
5) Диагностика  - проверить установленные компоненты
6) Управление скриптом  - переустановка, обновление
0) Выход
```

**Пункт 1) "Оба" (`install_both`)**:
1. Если `server/.env` уже существует — пропускает установку сервера, иначе запускает `server/install.sh` (мастер).
2. Читает `PORT` и `ADMIN_API_KEY` из свежесозданного `.env`.
3. Ждёт готовности сервера до 60с через `curl .../healthz`; если HTTP-проверка не прошла, но `docker inspect` показывает `healthy`/`starting` — считает сервер рабочим (fallback на нативный healthcheck Docker, полагаясь на то, что тот опрашивает из того же network namespace).
4. Если `agent/.env` уже существует — пропускает, иначе: определяет имя ноды как публичный IP (`detect_node_ip`), интерфейс `any`, создаёт ноду через `POST /api/v1/nodes` с `X-Admin-Key`, получает `token` из ответа (через `grep -oP`), копирует `agent/.env.example` → `agent/.env`, подставляет `SERVER_URL=http://127.0.0.1:${port}`, `NODE_TOKEN`, `NODE_NAME`, `INTERFACE`, собирает и запускает контейнер агента.

**Пункт 2/3** — просто передают управление (`exec bash`) в `agent/install-agent.sh` / `server/install.sh` без аргументов (интерактивный мастер).

**Неинтерактивный режим `agent <url> <token>`** (используется one-tap кнопкой "➕ Добавить" в боте и в `add_node.sh`): пропускает меню полностью, сразу вызывает `agent/install-agent.sh "$2" "$3"`, что пропускает и мастеровские вопросы внутри агента-установщика.

**Пункт 6) "Управление скриптом"**:
- **Переустановить** (`reinstall_all`) — подтверждение → `_do_uninstall()` (полный снос: контейнеры/образы/CLI/автообновление/папка проекта) → заново клонирует/резолвит проект → показывает меню установки.
- **Удалить** (`uninstall_all`) — то же самое подтверждение и снос, без переустановки.
- **Обновить** (`update_all` / `_update_all_impl`) — под `flock`-блокировкой на `/tmp/domain-monitor-update.lock` (защита от гонки между cron-автообновлением и ручным запуском из меню — без лока второй `git reset --hard` + сборка могли бы столкнуться с первым и повредить чекаут или столкнуть два docker build за один тег образа); `git fetch` + `git reset --hard origin/HEAD`, пересобирает только реально установленные компоненты (проверка по наличию `.env`).
- **Автообновление** (`toggle_auto_update` / `enable_auto_update` / `disable_auto_update`) — управляет строкой в root-crontab, помеченной `CRON_MARKER = "# domain-monitor-auto-update"` (снятие/включение никогда не трогает остальные строки crontab администратора); варианты расписания: ежедневно 03:00, еженедельно (вс 03:00) или произвольное cron-выражение; лог автообновления — `$PROJECT_DIR/auto-update.log`.

### `server/install.sh`
Идемпотентен: при существующем `.env` сразу открывает `scripts/menu.sh` вместо мастера. Требует root, ставит Docker через `get.docker.com`, если его нет.

**Мастер (`run_wizard`)**:
- Генерирует `ADMIN_API_KEY` (через `python3 secrets.token_urlsafe(32)`, фолбэк на `/dev/urandom` + base64, если Python недоступен).
- Находит свободный порт начиная с 8280 (`find_free_port`).
- Формирует `PUBLIC_URL` как `http://<публичный IP через ifconfig.me, либо hostname>` и **обязательно дописывает порт**, если он не 80 — это критично: `PUBLIC_URL` становится `SERVER_URL` каждого нового агента, без порта агенты стучались бы на порт 80 вместо реального.
- Открывает порт в UFW, если тот активен (`open_firewall_port`).
- Валидирует `BOT_TOKEN` регуляркой, `ADMIN_ID` (число или список чисел через запятую), опциональный `TELEGRAM_PROXY` (формат `socks5://`/`http://`).
- Копирует `.env.example` → `.env`, подставляет значения через `sed`, `chmod 600`.
- Если прокси не задан — сразу же проверяет доступность Telegram напрямую (`getMe`), предупреждает, если не отвечает.
- `compose_build_quiet()` — сборка и запуск; при неудаче — явная инструкция, что делать.
- Устанавливает CLI-обёртку (`install_cli_wrapper` — симлинк `/usr/local/bin/domain-monitor-server` → `bin/domain-monitor-server`).

### `agent/install-agent.sh`
Тоже идемпотентен, требует root, ставит Docker при отсутствии.

**Мастер (`run_wizard`)** принимает опциональные `$1`/`$2` (server_url, node_token) — если оба переданы и хорошо сформированы (`$1` начинается с `http(s)://`, `$2` с `nmt_`), интерактивные вопросы полностью пропускаются (это и есть путь **"неинтерактивный режим `agent <url> <token>`"**, который используется в one-tap кнопке бота и в корневом `install.sh -- agent <url> <token>`). Иначе — спрашивает оба значения в цикле с валидацией по тем же регуляркам.

Имя ноды для локальных логов (`NODE_NAME`) определяется как публичный IP (`detect_node_ip`, тот же подход, что и в корневом `install.sh`), интерфейс жёстко `any`. Копирует `.env.example` → `.env`, подставляет значения, собирает и запускает контейнер, ставит CLI-обёртку `/usr/local/bin/domain-monitor-agent`.

---

## 11. Переменные окружения

### `server/.env.example`

| Переменная | Обязательна | Дефолт | Назначение |
|---|---|---|---|
| `ADMIN_API_KEY` | Да | — | Секрет для заголовка `X-Admin-Key` (прямой доступ к REST API в обход бота). Минимум 16 символов (проверяется в `config.py`) |
| `PORT` | Нет | `8280` | Порт, на котором слушает сервер (host-networking — должен быть реально свободен на хосте) |
| `PUBLIC_URL` | Нет | `http://localhost:{PORT}` | Адрес, который выдаётся новым агентам как `SERVER_URL` |
| `BOT_TOKEN` | Да | — | Токен Telegram-бота от @BotFather |
| `ADMIN_ID` | Да | — | Telegram user id (список через запятую — несколько админов) |
| `TELEGRAM_PROXY` | Нет | — (закомментировано) | `socks5://host:port` или `http://host:port` для доступа бота к Telegram API |
| `EVENT_RETENTION_DAYS` | Нет | `30` | Сколько дней хранить детальную историю событий (0 — вечно); список доменов не затрагивается |
| `NODE_OFFLINE_AFTER_SECONDS` | Нет | `90` | Через сколько секунд без heartbeat нода считается offline |
| `EVENTS_RATE_LIMIT_CAPACITY` | Нет | `30` | Burst-размер token bucket на `/api/v1/events` |
| `EVENTS_RATE_LIMIT_PER_SECOND` | Нет | `5` | Скорость пополнения bucket |
| `LOG_LEVEL` | Нет | `INFO` | Уровень логирования |

Бэкапы (интервал, число копий, способ доставки) настраиваются только из Telegram (меню "💾 Бэкапы"), в `.env` для них ничего нет.

### `agent/.env.example`

| Переменная | Обязательна | Дефолт | Назначение |
|---|---|---|---|
| `SERVER_URL` | Да | — | Адрес сервера Domain Monitor |
| `NODE_TOKEN` | Да | — | Токен ноды, выданный сервером (`POST /api/v1/nodes`), начинается с `nmt_` |
| `NODE_NAME` | Нет | `agent` | Локальная метка только для собственных логов агента (реальное имя ноды хранится на сервере, привязано к токену) |
| `INTERFACE` | Нет | `any` | Сетевой интерфейс захвата |
| `SOURCES` | Нет | `tls_sni` | Список источников через запятую: `tls_sni`, `dns` (реализованные; HTTP Host/QUIC/Xray-log — на роадмапе) |
| `LOG_LEVEL` | Нет | `INFO` | Уровень логирования |
| `BATCH_INTERVAL_SECONDS` | Нет | `5` | Интервал отправки батча событий |
| `BATCH_MAX_SIZE` | Нет | `200` | Максимум событий в одном батче |
| `HEARTBEAT_INTERVAL_SECONDS` | Нет | `30` | Интервал heartbeat |
| `MAX_BUFFER_BYTES` | Нет | `1073741824` (1 ГиБ) | Лимит размера локального outbox |
| `HTTP_TIMEOUT_SECONDS` | Нет | `10` | Таймаут HTTP-запросов к серверу |
| `AGENT_MEM_LIMIT` | Нет | `1g` | Жёсткий лимит памяти контейнера (docker-compose) |
| `AGENT_CPU_LIMIT` | Нет | `2.0` | Жёсткий лимит CPU контейнера (docker-compose) |

Последние два (`AGENT_MEM_LIMIT`/`AGENT_CPU_LIMIT`) не читаются Python-кодом агента — они подставляются напрямую в `agent/docker-compose.yml` (`mem_limit: ${AGENT_MEM_LIMIT:-1g}`, `cpus: ${AGENT_CPU_LIMIT:-2.0}`).

---

## 12. Миграции — сквозной список

| # | Компонент | Файл | Что меняет |
|---|---|---|---|
| 1 | server | `0001_initial.sql` | Создаёт `nodes`, `domains`, `events`, `settings` |
| 2 | server | `0003_filter_rules.sql` | Создаёт `filter_rules` (Ignore/Allow/Watch) |
| 3 | server | `0004_node_notify_routing.sql` | Добавляет к `nodes`: `notify_destination`, `notify_group_chat_id`, `notify_group_topic_id` |
| 4 | server | `0005_agent_buffer_size.sql` | Добавляет к `nodes`: `agent_buffer_size` |
| 5 | server | `0006_events_hits.sql` | Добавляет к `events`: `hits` (учёт схлопнутых на агенте повторов) |
| 6 | agent | `0001_outbox.sql` | Создаёт `outbox` (локальный буфер отправки) |
| 7 | agent | `0002_outbox_dedupe.sql` | Добавляет `hits`/`last_occurred_at`, чистит старые дубликаты, добавляет уникальный индекс `(domain, source)` |

Примечание: в файловой системе сервера отсутствует файл `0002_*.sql` — нумерация в каталоге `server/migrations/` прыгает с `0001` сразу на `0003` (файл `0002` в репозитории физически не существует). Порядок применения определяется алфавитной сортировкой имён файлов в `Database.migrate()`, поэтому пропуск номера не ломает ничего — просто в истории есть "дыра" в нумерации.

---

## 13. Известные особенности и нюансы

- **Дедуп outbox агента и его лимит по размеру, а не по числу строк.** `agent/app/buffer.py` схлопывает повторные хиты одного `(domain, source)` в одну строку со счётчиком `hits`, чтобы длительный обрыв связи не раздувал буфер тысячами почти одинаковых строк. Лимит (`MAX_BUFFER_BYTES`, по умолчанию 1 ГиБ) — это ОЦЕНКА размера на диске (длина строк + фиксированный оверхед на строку), обновляемая инкрементально в памяти, а не реальный `stat()` файла; при переполнении вытесняются домены, дольше всего не встречавшиеся (`ORDER BY last_occurred_at ASC`), пакетами ~5% таблицы за раз, а не по одной строке.

- **Двухуровневая цветовая конвенция кнопок Telegram.** В `keyboards.py` явно разделены два правила: обычный переключатель (мониторинг, отправка, уведомления, watchlist, автобэкап) красится по ТЕКУЩЕМУ СОСТОЯНИЮ (зелёный=включено/красный=выключено — "как лампочка"), а одноразовое деструктивное действие (удалить/отозвать/подтвердить) красится по смыслу действия независимо от состояния ("✅ Да, удалить" — всё равно `danger`, потому что зелёный на подтверждении удаления сказал бы обратное тому, что оно значит).

- **Анти-луп защиты в фоновых задачах, трогающих внешние API/диск сами по себе:**
  - `LiveViewManager` (автообновление экранов бота): один таск на `(chat_id, message_id)` — повторное включение всегда сначала гасит предыдущий таск для того же сообщения; жёсткий `MAX_DURATION_SECONDS = 30 мин` — забытое включённым автообновление само выключится; `MAX_CONCURRENT_VIEWS = 25` — защита от одновременно открытых экранов несколькими админами; ошибка "message is not modified" молча проглатывается (это норма — ничего не изменилось), любая ДРУГАЯ ошибка (сообщение удалено, бот забанен) сразу останавливает конкретно эту live-view вместо повторных попыток против явно сломанного адресата.
  - `BackupTask`: общий `asyncio.Lock` между планировщиком и кнопкой "сделать бэкап сейчас" — гарантирует, что два бэкапа никогда не выполняются параллельно и не гонятся за один и тот же файл ротации; интервал клэмпится в `backup_settings` (1..720 часов) — никакой ввод в боте не может превратить это в тесный цикл; старая копия удаляется ТОЛЬКО после успешного создания новой; неудачная доставка сообщается один раз в ЛС и просто ждёт следующего планового цикла, не ретраясь в горячем цикле против явно сломанного адресата.
  - `TopicBindingManager`: TTL 300с на "ожидающую" привязку группы/топика — забытая наполовину настроенная привязка не остаётся вооружённой навсегда.
  - `_run_polling_forever` в `main.py`: перезапускает `dp.start_polling()` при падении (замечено на практике: SOCKS5-прокси может молча оборвать long-poll соединение) с экспоненциальным backoff 1с→30с.
  - Установщик `install.sh` в корне: `flock` на `/tmp/domain-monitor-update.lock` вокруг `update_all()` — не даёт cron-автообновлению и ручному запуску "Обновить" из меню столкнуться в один момент времени.

- **`PUBLIC_URL` должен содержать порт.** Гочта, явно проверяемая и в `server/install.sh` (мастер), и в `server/scripts/doctor.sh`: `PUBLIC_URL` становится `SERVER_URL` каждого нового агента; если порт не 80 и не зашит в `PUBLIC_URL`, агенты будут стучаться на порт 80 по умолчанию — все heartbeat'ы и события молча уйдут в никуда, без единой видимой на сервере ошибки. `doctor.sh` явно проверяет это (`[ "$PORT" != "80" ] && [[ "$PUBLIC_URL" != *":$PORT" ]]` → fail с готовой инструкцией исправления).

- **UFW открывается/закрывается только "своим" правилом.** `open_firewall_port` трогает порт только если UFW реально активен (`ufw status` показывает `Status: active`) — никогда не активирует UFW сам и не трогает другие фаерволы (firewalld, security groups в облаке, ручной iptables). `close_firewall_port` при удалении удаляет правило, только если оно совпадает с точным паттерном `<port>/tcp ALLOW Anywhere` — той формой, которую сам установщик и добавляет, чтобы никогда не снести правило, добавленное администратором вручную по другой причине.

- **Дырка в нумерации миграций сервера (`0001` → `0003`, файла `0002` нет).** См. раздел 12 — не является ошибкой: `Database.migrate()` сортирует файлы по имени, а не полагается на непрерывность номеров, поэтому пропуск не влияет на корректность применения.

- **One-tap добавление ноды не требует ввода имени ни в боте, ни в CLI.** `db.create_node_auto()` создаёт ноду с временным именем `нода-{id}` (через промежуточный placeholder `__pending__{timestamp}`, поскольку id строки неизвестен до самой вставки), а `touch_heartbeat()` при первом же heartbeat с известным `ip` автоматически переименовывает её в реальный публичный IP ноды — если только это имя уже не занято другой нодой, тогда placeholder-имя остаётся как есть. Ответ на создание ноды включает уже готовую однострочную команду установки с сервером и токеном внутри (`node_add` в `handlers.py`, `add_node.sh`, неинтерактивный режим `install.sh -- agent <url> <token>`).

- **`X-Admin-Key` и `Authorization: Bearer` — полностью независимые и непересекающиеся области доступа.** Node-токен хэшируется SHA-256 при хранении (как пароль) и даёт доступ ТОЛЬКО к операциям от имени этой одной ноды (`require_node` в `deps.py`); скомпрометированный токен одной ноды не может прочитать/изменить данные другой. Admin-ключ сравнивается через `hmac.compare_digest` (защита от timing-атак) и даёт полный доступ к управлению нодами/данными через REST — используется CLI-скриптами, не ботом (бот работает напрямую с `Database`, минуя HTTP-слой).

- **`fleet_control` (два глобальных флага) — это НЕ то же самое, что `node.monitoring_enabled`/`notifications_enabled` на конкретной ноде.** Итоговое значение, которое агент получает в ответе на heartbeat (`HeartbeatResponse.monitoring_enabled`), уже комбинирует оба уровня (`node.monitoring_enabled AND fleet_control.is_monitoring_enabled(db)`) — агенту не нужно знать, что это разделение вообще существует.

- **Приём событий (`POST /api/v1/events`) возвращает `503`, а не `200 {accepted: 0}`, когда отправка глобально на паузе.** Это намеренно: агент чистит свой локальный буфер ТОЛЬКО после успешного (2xx) ответа сервера — если бы пауза отправки маскировалась под "успех" с нулём принятых, агент решил бы, что события сохранены, и удалил бы их из буфера безвозвратно.

- **Rate limit на события — по запросам, а не по отдельным событиям внутри батча.** Обычный агент, шлющий один батч раз в `BATCH_INTERVAL_SECONDS` (по умолчанию 5с), никогда не приближается к лимиту (burst 30, пополнение 5/сек) — сконструировано так, чтобы задеть только скомпрометированного/сломанного агента, долбящего эндпоинт напрямую.

- **`hits` — сквозной механизм схлопывания повторов через весь конвейер.** Введён миграцией `agent/0002` (буфер агента) и зеркально `server/0006` (таблица `events`): один физически полученный сервером "event" может представлять множество реальных наблюдений одного домена, накопленных агентом за время недоступности сервера. Ограничен `1..1_000_000` в Pydantic-схеме (`EventIn.hits`) как защита от переполнения счётчика скомпрометированным или неисправным агентом.

- **`server/app/topic_binding.py` намеренно не использует штатный FSM aiogram.** Причина явно описана в докстринге: ключ хранения состояния FSM в aiogram включает `chat_id`, а команда `/bind`, завершающая привязку, приходит из ДРУГОГО чата (целевой группы), чем тот, где привязка была инициирована (ЛС с ботом) — поэтому состояние трекается вручную по `user_id`, с собственным TTL.

- **Периодический рестарт процессов tshark каждые 10 минут (`_MAX_SESSION_SECONDS = 600`) — не костыль, а осознанное решение против неограниченного роста памяти.** У живого захвата (в отличие от разбора законченного pcap-файла) нет момента "EOF", когда per-TCP-stream состояние освобождается само; на ноде с сотнями одновременных соединений это состояние копится непрерывно. Рестарт с потерей ~1-2с захвата — сознательно принятая цена. Дополнительно к этому в самой команде `tshark` явно отключены TCP-реассемблинг (`tcp.desegment_tcp_streams:false`, `tls.desegment_ssl_records:false`) и анализ TCP-последовательностей (`tcp.analyze_sequence_numbers:false`, `tcp.calculate_timestamps:false`) — ни то, ни другое не нужно для чтения одного поля SNI из ClientHello, но оба держат собственное состояние на каждый поток и являются, по комментарию в коде, крупнейшим источником роста RAM/CPU на ноде с активной сменой соединений.

- **Разделение `domains.ignored` (ручной разовый флаг) и `filter_rules` (проверяемый паттерн).** Кнопка "🚫 Игнорировать" под уведомлением о новом домене не просто ставит `ignored=True` на уже записанной строке — она ЕЩЁ И добавляет suffix-правило в Ignore List (`db.add_filter_rule("ignore", "suffix", domain)`), чтобы этот домен (и его поддомены) не порождал новых уведомлений и в будущем, а не только для уже увиденного экземпляра.

- **Перенос сервера на другой хост (`migrate-export`/`migrate-import`) сливает `.env`, а не перезаписывает его целиком.** Переносятся только `BOT_TOKEN`, `ADMIN_ID`, `ADMIN_API_KEY`, `TELEGRAM_PROXY` — специфичные для "личности" бота значения; `PORT` и `PUBLIC_URL` остаются собственными для нового хоста (заданными его же мастером установки). После импорта скрипт заходит в свежезапущенный контейнер и через встроенный Python-скрипт (`docker exec ... python3 -c "..."`) читает список нод прямо из перенесённой БД, чтобы напечатать готовую команду `domain-monitor-agent set-server` для каждой без похода в бота.
