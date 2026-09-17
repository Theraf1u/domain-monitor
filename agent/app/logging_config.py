"""Logging setup: rotating file handler + console handler, scrubbing
anything that looks like a node token from log records."""
from __future__ import annotations

import logging
import os
import re
from logging.handlers import RotatingFileHandler

_TOKEN_PATTERN = re.compile(r"nmt_[A-Za-z0-9_-]{30,}")


class SecretScrubFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._scrub(str(record.msg))
        if record.args:
            # Only touch string args - numbers are passed through untouched
            # so %d placeholders keep working (str-ifying them here breaks
            # logging's own % formatting).
            if isinstance(record.args, dict):
                record.args = {
                    k: (self._scrub(v) if isinstance(v, str) else v) for k, v in record.args.items()
                }
            else:
                record.args = tuple(
                    self._scrub(a) if isinstance(a, str) else a for a in record.args
                )
        return True

    @staticmethod
    def _scrub(text: str) -> str:
        return _TOKEN_PATTERN.sub("***REDACTED***", text)


def setup_logging(log_level: str, log_dir: str) -> None:
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "domain-monitor-agent.log")

    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level, logging.INFO))

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    scrub = SecretScrubFilter()

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.addFilter(scrub)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    file_handler.addFilter(scrub)
    root.addHandler(file_handler)

    if log_level != "DEBUG":
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
