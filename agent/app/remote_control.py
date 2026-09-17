"""Two flags the server can flip from the Telegram main menu ("Остановить
мониторинг" / "Остановить отправку доменов"), applied to this agent the
next time its heartbeat response comes back - no restart needed. Plain
bool attributes are enough here: CPython's GIL makes a single attribute
read/write atomic, the sniffer and uplink loops each only ever read their
own flag, and nothing needs to observe a change at the exact same instant
as another reader.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RemoteControl:
    monitoring_enabled: bool = True
    sending_enabled: bool = True
