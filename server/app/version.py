"""Server version/build identity, for the "🖥 Сервер" and "ℹ️ О системе"
Settings screens (spec 2.0 Part 2, section 1.1/1.5). Process start time is
recorded at first import, which happens during app startup - close enough
to "server uptime" for a status screen, no need to reach for the OS
process table.
"""
from __future__ import annotations

import os
import time

SERVER_VERSION = "2.0"

_GIT_REV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "GIT_REV")
_START_TIME = time.monotonic()


def git_revision() -> str:
    try:
        with open(_GIT_REV_FILE, encoding="utf-8") as fh:
            rev = fh.read().strip()
            return rev or "unknown"
    except OSError:
        return "unknown"


def uptime_seconds() -> int:
    return int(time.monotonic() - _START_TIME)
