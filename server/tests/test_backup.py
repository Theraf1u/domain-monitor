"""Backup Manager 2.0: creation, checksum/integrity verification, listing,
and the filename-collision defense (a real silent-data-loss bug found and
fixed this session - two backups created within the same second used to
collide on filename, and tarfile.open(path, "w:gz") silently truncates
an existing file at that path with no error)."""
from __future__ import annotations

import asyncio
import os

import pytest

from app.backup_task import BackupTask
from app.notifier import Notifier


@pytest.fixture
def backup_task(db, config):
    notifier = Notifier(db, config.admin_ids, config)
    return BackupTask(db, config, notifier)


def test_create_and_verify_db_backup(backup_task):
    path = backup_task._create_backup_file("db")
    assert os.path.exists(path)
    ok, detail = backup_task._verify_backup_file(path)
    assert ok, detail


def test_create_and_verify_full_backup_includes_env_snapshot(backup_task):
    path = backup_task._create_backup_file("full")
    ok, detail = backup_task._verify_backup_file(path)
    assert ok, detail

    import tarfile

    with tarfile.open(path, "r:gz") as tf:
        names = tf.getnames()
    assert any("env_snapshot.txt" in n for n in names)


def test_corrupted_archive_fails_verification(backup_task):
    path = backup_task._create_backup_file("db")
    with open(path, "r+b") as fh:
        fh.seek(100)
        fh.write(b"\x00" * 64)  # corrupt the middle of the gzip stream
    ok, detail = backup_task._verify_backup_file(path)
    assert not ok


def test_run_backup_now_end_to_end(backup_task):
    result = asyncio.run(backup_task.run_backup_now("db"))
    assert "✅" in result
    backups = backup_task.list_backups()
    assert len(backups) == 1
    assert backups[0]["verified"] is True
    assert backups[0]["type"] == "db"


def test_failed_verification_never_rotates_out_good_backups(backup_task, monkeypatch):
    """A backup that fails its own integrity check must never cause an
    existing, known-good backup to be rotated away - spec 2.3."""
    asyncio.run(backup_task.run_backup_now("db"))
    assert len(backup_task.list_backups()) == 1

    monkeypatch.setattr(backup_task, "_verify_backup_file", lambda path: (False, "simulated corruption"))
    result = asyncio.run(backup_task.run_backup_now("db"))
    assert "✅" not in result

    backups = backup_task.list_backups()
    assert len(backups) == 2  # the bad one is kept for diagnosis, not silently deleted
    assert any(not b["verified"] for b in backups)
    assert any(b["verified"] for b in backups)


def test_concurrent_backups_never_collide_on_filename(backup_task):
    """Regression test for the exact bug found this session: creating
    two backups back-to-back (as a scheduled backup and a manual
    pre-restore snapshot can) must never produce the same filename -
    a collision would mean the second tarfile.open(..., "w:gz") silently
    truncates the first, an invisible data-loss bug with no error
    anywhere."""
    paths = {backup_task._create_backup_file("db") for _ in range(5)}
    assert len(paths) == 5, "collision: two backups landed on the same filename"
    for p in paths:
        assert os.path.exists(p)
        ok, _ = backup_task._verify_backup_file(p)
        assert ok
