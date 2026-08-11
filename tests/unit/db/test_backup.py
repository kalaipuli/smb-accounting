"""Unit tests for src/db/backup.py (PD-1-BACKUP).

Covers: online-backup consistency (via sqlite3.Connection.backup()),
retention pruning, restore round-trip, and the startup-interval trigger
(including the "must not crash / no-op when nothing exists yet" edge
cases) documented in ADR-003.
"""

import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from src.db.backup import (
    create_backup,
    default_backup_dir,
    list_backups,
    maybe_backup_on_startup,
    prune_backups,
    restore_backup,
)


def _make_db_with_one_row(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO products (name) VALUES ('Rice 1kg')")
        conn.commit()
    finally:
        conn.close()


# --- default_backup_dir() ---------------------------------------------------


def test_default_backup_dir_when_given_db_path_returns_sibling_backups_dir(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "data.db"

    assert default_backup_dir(db_path) == tmp_path / "backups"


# --- create_backup() ---------------------------------------------------------


def test_create_backup_when_called_produces_a_readable_consistent_copy(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "data.db"
    _make_db_with_one_row(db_path)

    backup_path = create_backup(db_path)

    assert backup_path.exists()
    assert backup_path.parent == default_backup_dir(db_path)
    conn = sqlite3.connect(backup_path)
    try:
        rows = conn.execute("SELECT name FROM products").fetchall()
        assert rows == [("Rice 1kg",)]
    finally:
        conn.close()


def test_create_backup_when_backup_dir_missing_creates_it(tmp_path: Path) -> None:
    db_path = tmp_path / "data.db"
    _make_db_with_one_row(db_path)
    backup_dir = tmp_path / "nested" / "backups"

    backup_path = create_backup(db_path, backup_dir)

    assert backup_dir.is_dir()
    assert backup_path.parent == backup_dir


# --- list_backups() / prune_backups() ----------------------------------------


def test_list_backups_when_dir_does_not_exist_returns_empty_list(
    tmp_path: Path,
) -> None:
    assert list_backups(tmp_path / "missing") == []


def test_prune_backups_when_count_exceeds_retention_deletes_oldest_first(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "data.db"
    _make_db_with_one_row(db_path)
    backup_dir = default_backup_dir(db_path)
    backup_dir.mkdir(parents=True)

    # Simulate 5 pre-existing backup files with distinct mtimes/names.
    paths = []
    for i in range(5):
        p = backup_dir / f"data-2026010{i}T000000Z.db"
        p.write_bytes(b"x")
        paths.append(p)

    deleted = prune_backups(backup_dir, retention=3)

    assert deleted == paths[:2]
    assert list_backups(backup_dir) == paths[2:]


def test_prune_backups_when_count_within_retention_deletes_nothing(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "data.db"
    _make_db_with_one_row(db_path)
    backup_dir = default_backup_dir(db_path)
    backup_dir.mkdir(parents=True)
    (backup_dir / "data-20260101T000000Z.db").write_bytes(b"x")

    deleted = prune_backups(backup_dir, retention=7)

    assert deleted == []


# --- restore_backup() ---------------------------------------------------------


def test_restore_backup_when_called_overwrites_live_db_with_backup_contents(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "data.db"
    _make_db_with_one_row(db_path)
    backup_path = create_backup(db_path)

    # Corrupt/change the live DB after the backup was taken.
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM products")
        conn.commit()
    finally:
        conn.close()

    restore_backup(backup_path, db_path)

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT name FROM products").fetchall()
        assert rows == [("Rice 1kg",)]
    finally:
        conn.close()


def test_restore_backup_when_backup_file_missing_raises_file_not_found_error(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "data.db"
    _make_db_with_one_row(db_path)

    with pytest.raises(FileNotFoundError):
        restore_backup(tmp_path / "does-not-exist.db", db_path)


# --- maybe_backup_on_startup() -----------------------------------------------


def test_maybe_backup_on_startup_when_db_missing_returns_none_and_does_not_raise(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "data.db"  # never created

    result = maybe_backup_on_startup(db_path)

    assert result is None


def test_maybe_backup_on_startup_when_no_prior_backup_exists_takes_one(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "data.db"
    _make_db_with_one_row(db_path)

    result = maybe_backup_on_startup(db_path)

    assert result is not None
    assert result.exists()


def test_maybe_backup_on_startup_when_recent_backup_exists_skips(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "data.db"
    _make_db_with_one_row(db_path)
    create_backup(db_path)  # "recent" backup already present

    result = maybe_backup_on_startup(db_path, interval=timedelta(days=1))

    assert result is None


def test_maybe_backup_on_startup_when_prior_backup_is_stale_takes_a_new_one(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "data.db"
    _make_db_with_one_row(db_path)
    stale = create_backup(db_path)
    # Backdate the mtime so the existing backup looks older than the interval.
    import os
    import time

    old_time = time.time() - timedelta(days=2).total_seconds()
    os.utime(stale, (old_time, old_time))

    result = maybe_backup_on_startup(db_path, interval=timedelta(days=1))

    assert result is not None
    assert result != stale
