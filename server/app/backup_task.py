"""Periodic backup of the server's own SQLite DB to /data/backups, with
rotation and optional delivery via the bot (DM to every admin, or a
group + topic).

Anti-loop / anti-runaway safeguards, since this is a scheduled task that
touches disk and an external API on its own:
- A single asyncio.Lock shared between the scheduler and the "backup
  now" button - two backups can never run concurrently and race on
  rotation or both try to write the same second's filename.
- The interval is clamped to a sane minimum by app.backup_settings, so
  no bot input can turn this into a tight loop.
- Rotation deletes an old backup only AFTER the new one is written
  successfully - a failed backup attempt never empties the existing
  rotation.
- A failed delivery (bad chat_id, bot kicked from the group, etc.) is
  reported once to the admins via DM and then the task just waits for
  the next scheduled cycle - it never retries in a hot loop against a
  destination that's clearly broken right now.
"""
from __future__ import annotations

import asyncio
import logging
import os
import tarfile
from datetime import datetime, timezone

from aiogram.types import FSInputFile

from app import backup_settings
from app.config import Config
from app.database import Database
from app.notifier import Notifier

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 900  # how often we check "is a backup due" - not how often we actually back up


class BackupTask:
    def __init__(self, db: Database, config: Config, notifier: Notifier) -> None:
        self.db = db
        self.config = config
        self.notifier = notifier
        self._stopped = asyncio.Event()
        self._lock = asyncio.Lock()
        self.backup_dir = os.path.join(config.data_dir, "backups")
        os.makedirs(self.backup_dir, exist_ok=True)

    def stop(self) -> None:
        self._stopped.set()

    async def run(self) -> None:
        while not self._stopped.is_set():
            try:
                if backup_settings.is_enabled(self.db) and self._due():
                    await self.run_backup_now()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Scheduled backup cycle failed")
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=_POLL_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass

    def _due(self) -> bool:
        last_raw = backup_settings.last_run_at(self.db)
        if not last_raw:
            return True
        try:
            last = datetime.strptime(last_raw, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        except ValueError:
            return True
        elapsed_hours = (datetime.now(timezone.utc) - last).total_seconds() / 3600
        return elapsed_hours >= backup_settings.interval_hours(self.db)

    async def run_backup_now(self) -> str:
        """Runs one full backup cycle (create + rotate + deliver) and
        returns a human-readable Russian status line. Safe to call both
        from the scheduler and from the "backup now" button - the lock
        means whichever call arrives second just waits its turn instead
        of racing the first."""
        async with self._lock:
            try:
                path = await asyncio.to_thread(self._create_backup_file)
            except Exception:
                logger.exception("Failed to create backup archive")
                return "❌ Не удалось создать архив бэкапа - подробности в логах сервера."

            backup_settings.set_last_run_at(self.db, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"))

            try:
                await asyncio.to_thread(self._rotate, backup_settings.keep_count(self.db))
            except Exception:
                logger.exception("Backup rotation failed (the backup itself succeeded)")

            dest = backup_settings.destination(self.db)
            name = os.path.basename(path)
            if dest == "server":
                return f"✅ Бэкап сохранён на сервере: <code>{name}</code>"

            if self.notifier.bot is None:
                return f"✅ Бэкап сохранён на сервере: <code>{name}</code> (бот недоступен, доставка пропущена)"

            try:
                await self._deliver(path, dest)
            except Exception:
                logger.exception("Failed to deliver backup via Telegram (destination=%s)", dest)
                await self.notifier.broadcast(
                    f"⚠️ Автобэкап создан, но не удалось отправить его через Telegram "
                    f"(способ доставки: {dest}). Файл остался на сервере: <code>{name}</code>"
                )
                return f"⚠️ Бэкап создан, но доставка не удалась: <code>{name}</code> (файл остался на сервере)"

            return f"✅ Бэкап создан и отправлен: <code>{name}</code>"

    def _create_backup_file(self) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        out_path = os.path.join(self.backup_dir, f"domain-monitor-server-{stamp}.tar.gz")
        base = self.config.database_path
        with tarfile.open(out_path, "w:gz") as tar:
            for candidate in (base, base + "-wal", base + "-shm"):
                if os.path.isfile(candidate):
                    tar.add(candidate, arcname=os.path.basename(candidate))
        return out_path

    def _rotate(self, keep: int) -> None:
        files = sorted(
            (f for f in os.listdir(self.backup_dir) if f.endswith(".tar.gz")),
            reverse=True,
        )
        for stale in files[keep:]:
            try:
                os.remove(os.path.join(self.backup_dir, stale))
            except OSError:
                logger.warning("Could not remove stale backup file %s", stale)

    async def _deliver(self, path: str, dest: str) -> None:
        bot = self.notifier.bot
        assert bot is not None
        caption = f"💾 Бэкап Domain Monitor Server ({os.path.basename(path)})"
        if dest == "dm":
            for admin_id in self.notifier.admin_ids:
                await bot.send_document(admin_id, FSInputFile(path), caption=caption)
        elif dest == "group":
            chat_id = backup_settings.group_chat_id(self.db)
            if chat_id is None:
                raise RuntimeError("backup destination is 'group' but no chat_id is configured")
            topic_id = backup_settings.group_topic_id(self.db)
            await bot.send_document(chat_id, FSInputFile(path), caption=caption, message_thread_id=topic_id)

    def list_backups(self) -> list[tuple[str, int, float]]:
        """[(filename, size_bytes, mtime)], newest first."""
        entries = []
        for f in os.listdir(self.backup_dir):
            if not f.endswith(".tar.gz"):
                continue
            full = os.path.join(self.backup_dir, f)
            st = os.stat(full)
            entries.append((f, st.st_size, st.st_mtime))
        entries.sort(key=lambda e: e[2], reverse=True)
        return entries
