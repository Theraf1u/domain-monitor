"""Backup Manager 2.0 (spec 2.0 Part 2, section 2): periodic + on-demand
backups of the server's own SQLite DB to /data/backups, with rotation,
checksum + integrity verification, and a restore flow with an automatic
pre-restore safety snapshot.

Anti-loop / anti-runaway safeguards, since this is a scheduled task that
touches disk and an external API on its own:
- A single asyncio.Lock shared between the scheduler, "backup now", and
  restore - none of backup/restore/rotation can ever run concurrently
  with another (spec 2.5/10).
- The interval is clamped to a sane minimum by app.backup_settings, so
  no bot input can turn this into a tight loop.
- Rotation deletes an old backup only AFTER the new one is written AND
  verified - a failed or unverified backup never empties the existing
  rotation (spec 2.3: "не удалять старый хороший backup, если новый не
  прошёл verification").
- A failed delivery (bad chat_id, bot kicked from the group, etc.) is
  reported once to the admins via DM and then the task just waits for
  the next scheduled cycle - it never retries in a hot loop against a
  destination that's clearly broken right now.
- Restore always creates its own pre-restore snapshot first and rolls
  back to it automatically if anything after that point fails (spec 2.4).
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import shutil
import sqlite3
import tarfile
import tempfile
from datetime import datetime, timezone

from aiogram.types import FSInputFile

from app import backup_settings
from app.config import Config
from app.database import Database
from app.notifier import Notifier

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 900  # how often we check "is a backup due" - not how often we actually back up

# Full-backup-only: env vars worth snapshotting for disaster recovery/
# migration (spec 2.1). The container never has the host's literal .env
# file mounted (see docker-compose.yml's `env_file:` vs `volumes:`), but
# every secret in it reaches the process as one of these anyway, so
# reconstructing a .env-shaped file from os.environ has the same effect
# without needing host filesystem access.
_FULL_BACKUP_ENV_KEYS = [
    "HOST", "PORT", "PUBLIC_URL", "ADMIN_API_KEY", "BOT_TOKEN", "ADMIN_ID", "TELEGRAM_PROXY",
    "DATA_DIR", "DATABASE_PATH", "LOG_LEVEL", "LOG_DIR", "EVENT_RETENTION_DAYS",
    "NODE_OFFLINE_AFTER_SECONDS", "EVENTS_RATE_LIMIT_CAPACITY", "EVENTS_RATE_LIMIT_PER_SECOND",
]


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _meta_path(archive_path: str) -> str:
    return archive_path + ".meta.json"


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
                    await self.run_backup_now(backup_settings.kind(self.db))
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

    async def run_backup_now(self, kind: str = "db") -> str:
        """Runs one full backup cycle (create + verify + rotate + deliver)
        and returns a human-readable Russian status line. `kind` is "db"
        (SQLite + WAL + SHM only, spec 2.1) or "full" (adds an env
        snapshot for disaster recovery/migration). Safe to call from the
        scheduler, the "backup now" button, or before a restore - the
        lock means concurrent callers just queue up instead of racing."""
        async with self._lock:
            try:
                path = await asyncio.to_thread(self._create_backup_file, kind)
            except Exception:
                logger.exception("Failed to create backup archive")
                if self.notifier.should_deliver("backup_failed"):
                    await self.notifier.broadcast("❌ Автобэкап не удался: не получилось создать архив (см. логи сервера).")
                return "❌ Не удалось создать архив бэкапа - подробности в логах сервера."

            ok, detail = await asyncio.to_thread(self._verify_backup_file, path)
            checksum = await asyncio.to_thread(_sha256, path)
            await asyncio.to_thread(self._write_meta, path, kind, checksum, ok, detail)
            name = os.path.basename(path)

            backup_settings.set_last_run_at(self.db, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"))

            if not ok:
                logger.error("Backup verification failed for %s: %s", name, detail)
                if self.notifier.should_deliver("backup_failed"):
                    await self.notifier.broadcast(
                        f"❌ Бэкап <code>{name}</code> создан, но не прошёл проверку целостности ({detail}). "
                        f"Файл сохранён для диагностики, но НЕ используется для ротации/восстановления - "
                        f"старые рабочие копии не тронуты."
                    )
                return f"❌ Бэкап создан, но не прошёл проверку целостности: {detail}. Ротация пропущена, старые копии сохранены."

            try:
                await asyncio.to_thread(self._rotate, backup_settings.keep_count(self.db))
            except Exception:
                logger.exception("Backup rotation failed (the backup itself succeeded)")

            dest = backup_settings.destination(self.db)
            if dest == "server":
                return f"✅ Бэкап сохранён на сервере: <code>{name}</code> (проверен, checksum ok)"

            if self.notifier.bot is None:
                return f"✅ Бэкап сохранён на сервере: <code>{name}</code> (бот недоступен, доставка пропущена)"

            try:
                await self._deliver(path, dest)
            except Exception:
                logger.exception("Failed to deliver backup via Telegram (destination=%s)", dest)
                if self.notifier.should_deliver("backup_failed"):
                    await self.notifier.broadcast(
                        f"⚠️ Автобэкап создан и проверен, но не удалось отправить его через Telegram "
                        f"(способ доставки: {dest}). Файл остался на сервере: <code>{name}</code>"
                    )
                return f"⚠️ Бэкап создан, но доставка не удалась: <code>{name}</code> (файл остался на сервере)"

            if self.notifier.should_deliver("backup_success"):
                await self.notifier.broadcast(f"✅ Автобэкап создан, проверен и отправлен: <code>{name}</code>")
            return f"✅ Бэкап создан, проверен и отправлен: <code>{name}</code>"

    def _create_backup_file(self, kind: str) -> str:
        # Microsecond resolution, not just seconds - two backups created
        # within the same second (the scheduler firing right as an admin
        # taps "backup now", or a restore's own pre-restore snapshot
        # landing in the same second as the backup being restored) would
        # otherwise collide on filename and one tarfile.open(..., "w:gz")
        # would silently overwrite - and truncate - the other. A restore
        # racing its own source backup this way looked like a successful
        # no-op restore in testing (the pre-restore snapshot clobbered
        # the target archive before it was ever read) with no error
        # anywhere - exactly the kind of silent data loss backups exist
        # to prevent, so this can't be a "close enough" timestamp.
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        out_path = os.path.join(self.backup_dir, f"domain-monitor-{kind}-{stamp}.tar.gz")
        # Belt-and-suspenders on top of the microsecond stamp: never write
        # over an existing filename, on the off chance a coarser system
        # clock still produces a collision.
        suffix = 1
        while os.path.exists(out_path):
            out_path = os.path.join(self.backup_dir, f"domain-monitor-{kind}-{stamp}-{suffix}.tar.gz")
            suffix += 1
        base = self.config.database_path
        with tarfile.open(out_path, "w:gz") as tar:
            for candidate in (base, base + "-wal", base + "-shm"):
                if os.path.isfile(candidate):
                    tar.add(candidate, arcname=os.path.basename(candidate))
            if kind == "full":
                env_lines = [
                    f"{key}={os.environ[key]}" for key in _FULL_BACKUP_ENV_KEYS if key in os.environ
                ]
                env_blob = ("\n".join(env_lines) + "\n").encode("utf-8")
                info = tarfile.TarInfo(name="env_snapshot.txt")
                info.size = len(env_blob)
                info.mode = 0o600
                tar.addfile(info, io.BytesIO(env_blob))
        os.chmod(out_path, 0o600)
        return out_path

    def _verify_backup_file(self, path: str) -> tuple[bool, str]:
        """Extracts to a throwaway temp dir and runs PRAGMA integrity_check
        on the contained DB via its OWN sqlite3 connection - never touches
        self.db's live connection, so a bad backup can't disturb the
        running server just by being checked."""
        try:
            with tempfile.TemporaryDirectory() as tmp:
                self._extract_archive(path, tmp)
                db_name = os.path.basename(self.config.database_path)
                extracted_db = os.path.join(tmp, db_name)
                if not os.path.isfile(extracted_db):
                    return False, "архив не содержит файл БД"
                conn = sqlite3.connect(extracted_db)
                try:
                    result = conn.execute("PRAGMA integrity_check").fetchone()[0]
                finally:
                    conn.close()
                if result != "ok":
                    return False, f"integrity_check: {result}"
                return True, "ok"
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"

    def _extract_archive(self, archive_path: str, dest_dir: str) -> None:
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(dest_dir, filter="data")

    def reverify(self, filename: str) -> tuple[bool, str]:
        """Re-runs integrity verification for an existing backup file and
        updates its sidecar metadata - the "🔍 Проверить целостность"
        button. Public (unlike the file-level helpers it composes) since
        it's a whole unit of work a handler should be able to call
        without reaching into BackupTask's internals."""
        full = os.path.join(self.backup_dir, filename)
        ok, detail = self._verify_backup_file(full)
        existing = self._read_meta(full)
        checksum = existing.get("checksum") or _sha256(full)
        kind = existing.get("type", "db")
        self._write_meta(full, kind, checksum, ok, detail)
        return ok, detail

    def _write_meta(self, archive_path: str, kind: str, checksum: str, verified: bool, detail: str) -> None:
        meta = {"type": kind, "checksum": checksum, "verified": verified, "verify_detail": detail}
        meta_path = _meta_path(archive_path)
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f)
        os.chmod(meta_path, 0o600)

    def _read_meta(self, archive_path: str) -> dict:
        try:
            with open(_meta_path(archive_path), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _rotate(self, keep: int) -> None:
        """Only ever called after a NEW backup passed verification, so
        this never trims the rotation down to nothing but a bad file."""
        files = sorted(
            (f for f in os.listdir(self.backup_dir) if f.endswith(".tar.gz")),
            reverse=True,
        )
        for stale in files[keep:]:
            try:
                os.remove(os.path.join(self.backup_dir, stale))
                meta = _meta_path(os.path.join(self.backup_dir, stale))
                if os.path.isfile(meta):
                    os.remove(meta)
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

    def list_backups(self) -> list[dict]:
        """[{filename, size, mtime, type, checksum, verified}, ...], newest
        first (spec 2.2: filename/type/created_at/size/checksum/verified)."""
        entries = []
        for f in os.listdir(self.backup_dir):
            if not f.endswith(".tar.gz"):
                continue
            full = os.path.join(self.backup_dir, f)
            st = os.stat(full)
            meta = self._read_meta(full)
            entries.append({
                "filename": f, "size": st.st_size, "mtime": st.st_mtime,
                "type": meta.get("type", "db"), "checksum": meta.get("checksum"),
                "verified": meta.get("verified"),
            })
        entries.sort(key=lambda e: e["mtime"], reverse=True)
        return entries

    # ------------------------------------------------------------------
    # Restore (spec 2.4)
    # ------------------------------------------------------------------

    def _swap_db_files(self, extracted_dir: str, db_filename: str) -> None:
        """Closes the live connection, replaces the DB/WAL/SHM files with
        the extracted ones, and leaves the connection closed - the caller
        is responsible for db.reopen() afterward. Runs under self.db's own
        lock so nothing else can be mid-query while the files move."""
        base = self.config.database_path
        with self.db._lock:
            self.db._conn.close()
            for suffix in ("", "-wal", "-shm"):
                src = os.path.join(extracted_dir, db_filename + suffix)
                dst = base + suffix
                if os.path.isfile(src):
                    shutil.move(src, dst)
                elif os.path.isfile(dst):
                    # the backup being restored didn't have this file (e.g.
                    # no -wal at backup time) - don't leave a stale one
                    # that doesn't belong to the restored data.
                    os.remove(dst)

    def _apply_archive_to_live_db(self, archive_path: str) -> None:
        db_filename = os.path.basename(self.config.database_path)
        with tempfile.TemporaryDirectory() as tmp:
            self._extract_archive(archive_path, tmp)
            self._swap_db_files(tmp, db_filename)

    async def restore_from_backup(self, filename: str) -> str:
        """1. pre-restore snapshot, 2. verify target, 3. swap files,
        4. reopen + migrate + healthcheck, 5. roll back to the pre-restore
        snapshot automatically if 3 or 4 fails (spec 2.4)."""
        async with self._lock:
            archive_path = os.path.join(self.backup_dir, filename)
            if not os.path.isfile(archive_path):
                return "❌ Файл бэкапа не найден."

            try:
                pre_path = await asyncio.to_thread(self._create_backup_file, "db")
                pre_ok, pre_detail = await asyncio.to_thread(self._verify_backup_file, pre_path)
                pre_checksum = await asyncio.to_thread(_sha256, pre_path)
                await asyncio.to_thread(self._write_meta, pre_path, "db", pre_checksum, pre_ok, pre_detail)
                if not pre_ok:
                    raise RuntimeError(f"pre-restore snapshot failed verification: {pre_detail}")
            except Exception:
                logger.exception("Failed to create a safe pre-restore snapshot - aborting, nothing touched")
                return "❌ Не удалось создать снимок перед восстановлением - восстановление отменено, текущие данные не тронуты."

            ok, detail = await asyncio.to_thread(self._verify_backup_file, archive_path)
            if not ok:
                return f"❌ Выбранный бэкап не прошёл проверку целостности ({detail}) - восстановление отменено."

            try:
                await asyncio.to_thread(self._apply_archive_to_live_db, archive_path)
            except Exception:
                logger.exception("Restore failed while swapping DB files - rolling back")
                try:
                    await asyncio.to_thread(self._apply_archive_to_live_db, pre_path)
                finally:
                    self.db.reopen()
                return (
                    "❌ Восстановление не удалось при замене файлов БД. Выполнен откат к состоянию "
                    f"до восстановления (снимок <code>{os.path.basename(pre_path)}</code>)."
                )

            try:
                self.db.reopen()
                await asyncio.to_thread(self.db.migrate)
                healthy = await asyncio.to_thread(self.db.integrity_check)
                if not healthy:
                    raise RuntimeError("integrity_check failed on the restored database")
            except Exception:
                logger.exception("Post-restore healthcheck failed - rolling back to pre-restore snapshot")
                try:
                    await asyncio.to_thread(self._apply_archive_to_live_db, pre_path)
                finally:
                    self.db.reopen()
                return (
                    "❌ После восстановления проверка целостности не прошла. Выполнен откат к состоянию "
                    f"до восстановления (снимок <code>{os.path.basename(pre_path)}</code>)."
                )

            meta = self._read_meta(archive_path)
            full_note = (
                "\n\nЭто full-бэкап - в архиве также есть env_snapshot.txt (не применяется автоматически, "
                "смотри вручную при необходимости)." if meta.get("type") == "full" else ""
            )
            return (
                f"✅ Восстановлено из <code>{filename}</code>. Снимок состояния до восстановления сохранён как "
                f"<code>{os.path.basename(pre_path)}</code>.{full_note}"
            )
