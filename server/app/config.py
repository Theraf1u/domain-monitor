"""Server configuration, loaded entirely from environment variables (`.env`
via docker-compose's `env_file`), mirroring the agent's config module."""
from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass

_BOT_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{30,}$")
_TELEGRAM_PROXY_RE = re.compile(r"^(socks5|http)://\S+:\d+$")


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"Environment variable {name} is required but not set")
    return value


def _optional(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    public_url: str
    admin_api_key: str
    bot_token: str
    admin_id: int
    telegram_proxy: str | None
    data_dir: str
    database_path: str
    log_level: str
    log_dir: str
    event_retention_days: int
    node_offline_after_seconds: int


def load_config() -> Config:
    admin_api_key = _require("ADMIN_API_KEY")
    if len(admin_api_key) < 16:
        raise ConfigError(
            "ADMIN_API_KEY must be at least 16 characters. "
            f"Generate one with: python3 -c \"import secrets; print(secrets.token_urlsafe(32))\" "
            f"(example: {secrets.token_urlsafe(32)})"
        )

    data_dir = _optional("DATA_DIR", "/data")
    log_dir = _optional("LOG_DIR", os.path.join(data_dir, "logs"))
    database_path = _optional("DATABASE_PATH", os.path.join(data_dir, "domain_monitor.db"))

    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(log_dir, exist_ok=True)

    bot_token = _require("BOT_TOKEN")
    if not _BOT_TOKEN_RE.match(bot_token):
        raise ConfigError("BOT_TOKEN does not look like a valid Telegram bot token")

    admin_id_raw = _require("ADMIN_ID")
    if not admin_id_raw.lstrip("-").isdigit():
        raise ConfigError("ADMIN_ID must be a numeric Telegram user id")
    admin_id = int(admin_id_raw)

    port = int(_optional("PORT", "8280"))

    telegram_proxy = os.environ.get("TELEGRAM_PROXY", "").strip() or None
    if telegram_proxy is not None and not _TELEGRAM_PROXY_RE.match(telegram_proxy):
        raise ConfigError(
            "TELEGRAM_PROXY does not look like a valid proxy URL "
            "(expected socks5://host:port or http://host:port, no socks5h)"
        )

    return Config(
        host=_optional("HOST", "0.0.0.0"),
        port=port,
        public_url=_optional("PUBLIC_URL", f"http://localhost:{port}"),
        admin_api_key=admin_api_key,
        bot_token=bot_token,
        admin_id=admin_id,
        telegram_proxy=telegram_proxy,
        data_dir=data_dir,
        database_path=database_path,
        log_level=_optional("LOG_LEVEL", "INFO").upper(),
        log_dir=log_dir,
        event_retention_days=int(_optional("EVENT_RETENTION_DAYS", "30")),
        node_offline_after_seconds=int(_optional("NODE_OFFLINE_AFTER_SECONDS", "90")),
    )
