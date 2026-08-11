"""Local backup/export mechanism for the SQLite data file (PD-1-BACKUP).

Design decisions are recorded in `docs/adr/ADR-003-backup-recovery.md`. Summary:

- **Trigger:** two mechanisms, both lightweight (no daemon, no scheduler,
  no message queue — none of those exist in this stack per
  `project-context.md` §2):
  1. `maybe_backup_on_startup()` — called once from `src/main.py` at app
     startup. Takes a backup only if the most recent backup is older than
     `interval` (default: 1 day). This approximates "periodic" backup
     without any OS-level scheduling infrastructure.
  2. Manual: `python -m src.db.backup backup` / `restore <file>` / `list`
     from the command line at any time.
- **Storage location:** `<db_path.parent>/backups/`, sibling to the live
  database file (e.g. `~/.smb-accounting/backups/`).
- **Retention:** keep the most recent `DEFAULT_RETENTION` (7) backups;
  older files are pruned automatically after each successful backup.
- **Backup method:** the stdlib `sqlite3.Connection.backup()` online-backup
  API — not a raw file copy — so a backup taken while the app holds an open
  connection is always a transactionally consistent snapshot.
- **Restore procedure:** `restore_backup()` copies a chosen backup file back
  over the live database path. The caller MUST ensure no connection is open
  against the live database file when restoring (the app should not be
  running).

This module is a standalone routine — it is not embedded in `schema.py`,
`config.py`, or any service module. `src/main.py` calls into it; it does not
call back into application code.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_RETENTION: int = 7
DEFAULT_INTERVAL: timedelta = timedelta(days=1)
BACKUP_FILENAME_FORMAT = "data-%Y%m%dT%H%M%S%fZ.db"


def default_backup_dir(db_path: Path) -> Path:
    """Canonical backup directory: a `backups/` sibling of the DB file."""
    return db_path.parent / "backups"


def create_backup(db_path: Path, backup_dir: Path | None = None) -> Path:
    """Take a consistent online backup of the SQLite file at `db_path`.

    Uses `sqlite3.Connection.backup()` (stdlib online backup API) rather
    than a raw file copy, so a backup taken while the app holds an open
    connection to `db_path` is always transactionally consistent — see
    module docstring and ADR-003 for the rationale.
    """
    if backup_dir is None:
        backup_dir = default_backup_dir(db_path)
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime(BACKUP_FILENAME_FORMAT)
    dest_path = backup_dir / timestamp

    source_conn = sqlite3.connect(db_path)
    try:
        dest_conn = sqlite3.connect(dest_path)
        try:
            source_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        source_conn.close()

    return dest_path


def list_backups(backup_dir: Path) -> list[Path]:
    """Return backup files in `backup_dir`, oldest first."""
    if not backup_dir.exists():
        return []
    return sorted(backup_dir.glob("data-*.db"))


def prune_backups(backup_dir: Path, retention: int = DEFAULT_RETENTION) -> list[Path]:
    """Delete backups beyond the most recent `retention` count.

    Returns the list of deleted paths.
    """
    backups = list_backups(backup_dir)
    excess = len(backups) - retention
    if excess <= 0:
        return []
    to_delete = backups[:excess]
    for path in to_delete:
        path.unlink()
    return to_delete


def restore_backup(backup_file: Path, db_path: Path) -> None:
    """Restore `backup_file` over the live database at `db_path`.

    Caller is responsible for ensuring no connection is open against
    `db_path` when calling this (the app must not be running).
    """
    if not backup_file.exists():
        raise FileNotFoundError(f"Backup file does not exist: {backup_file}")
    shutil.copyfile(backup_file, db_path)


def maybe_backup_on_startup(
    db_path: Path,
    backup_dir: Path | None = None,
    interval: timedelta = DEFAULT_INTERVAL,
    retention: int = DEFAULT_RETENTION,
) -> Path | None:
    """Take a backup if the most recent one is older than `interval`.

    Called once from application startup. Approximates "periodic" backup
    without any OS-level scheduler — see module docstring. Returns the new
    backup path if one was taken, else `None`.

    Does not raise on backup failure — startup must not be blocked by a
    failed backup. Callers that want failures surfaced should catch around
    this call at the call site if that policy changes.
    """
    if backup_dir is None:
        backup_dir = default_backup_dir(db_path)

    if not db_path.exists():
        # Nothing to back up yet (first run, schema not yet initialized).
        return None

    existing = list_backups(backup_dir)
    if existing:
        latest = existing[-1]
        age = datetime.now(timezone.utc) - datetime.fromtimestamp(
            latest.stat().st_mtime, tz=timezone.utc
        )
        if age < interval:
            return None

    new_backup = create_backup(db_path, backup_dir)
    prune_backups(backup_dir, retention)
    return new_backup


def _cli_output(message: str) -> None:
    """CLI-facing stdout output, via structured logging rather than `print()`
    per `framework/guidelines/anti-patterns.md` (Code Quality — no `print()`
    for output; use structured logging). A dedicated stdout-only, message-only
    handler keeps this readable as plain CLI output for the shop owner /
    support technician invoking `python -m src.db.backup` directly.
    """
    cli_logger = logging.getLogger("src.db.backup.cli")
    if not cli_logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        cli_logger.addHandler(handler)
        cli_logger.setLevel(logging.INFO)
        cli_logger.propagate = False
    cli_logger.info(message)


def _cli() -> None:
    from src.config import get_db_path

    parser = argparse.ArgumentParser(description="SMB Account Software backup/restore")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("backup", help="Take a backup now")

    restore_parser = subparsers.add_parser("restore", help="Restore from a backup file")
    restore_parser.add_argument("backup_file", type=Path)

    subparsers.add_parser("list", help="List available backups")

    args = parser.parse_args()
    db_path = get_db_path()

    if args.command == "backup":
        dest = create_backup(db_path)
        prune_backups(default_backup_dir(db_path))
        _cli_output(f"Backup created: {dest}")
    elif args.command == "restore":
        restore_backup(args.backup_file, db_path)
        _cli_output(f"Restored {args.backup_file} -> {db_path}")
    elif args.command == "list":
        for path in list_backups(default_backup_dir(db_path)):
            _cli_output(str(path))


if __name__ == "__main__":
    _cli()
    sys.exit(0)
