"""Agent configuration, loaded from environment variables (`.env`)."""
from __future__ import annotations

import os
from dataclasses import dataclass


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
    server_url: str
    node_token: str
    node_label: str
    interface: str
    tshark_path: str
    sources: list[str]
    data_dir: str
    database_path: str
    log_level: str
    log_dir: str
    batch_interval_seconds: int
    batch_max_size: int
    heartbeat_interval_seconds: int
    max_buffer_bytes: int
    http_timeout_seconds: int


def load_config() -> Config:
    server_url = _require("SERVER_URL").rstrip("/")
    if not (server_url.startswith("http://") or server_url.startswith("https://")):
        raise ConfigError("SERVER_URL must start with http:// or https://")

    node_token = _require("NODE_TOKEN")
    if not node_token.startswith("nmt_"):
        raise ConfigError("NODE_TOKEN does not look like a valid node token (expected to start with 'nmt_')")

    data_dir = _optional("DATA_DIR", "/data")
    log_dir = _optional("LOG_DIR", os.path.join(data_dir, "logs"))
    database_path = _optional("DATABASE_PATH", os.path.join(data_dir, "agent_buffer.db"))

    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(log_dir, exist_ok=True)

    return Config(
        server_url=server_url,
        node_token=node_token,
        node_label=_optional("NODE_NAME", "agent"),
        interface=_optional("INTERFACE", "any"),
        tshark_path=_optional("TSHARK_PATH", "tshark"),
        sources=[s.strip() for s in _optional("SOURCES", "tls_sni").split(",") if s.strip()],
        data_dir=data_dir,
        database_path=database_path,
        log_level=_optional("LOG_LEVEL", "INFO").upper(),
        log_dir=log_dir,
        batch_interval_seconds=int(_optional("BATCH_INTERVAL_SECONDS", "5")),
        batch_max_size=int(_optional("BATCH_MAX_SIZE", "200")),
        heartbeat_interval_seconds=int(_optional("HEARTBEAT_INTERVAL_SECONDS", "30")),
        max_buffer_bytes=int(_optional("MAX_BUFFER_BYTES", str(1024 * 1024 * 1024))),
        http_timeout_seconds=int(_optional("HTTP_TIMEOUT_SECONDS", "10")),
    )
