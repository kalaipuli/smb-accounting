import curses
import logging
import sqlite3

from src.config import APP_NAME, APP_VERSION, get_db_path
from src.db.backup import maybe_backup_on_startup
from src.db.migrate import apply_pending_migrations
from src.db.schema import connect
from src.ui.main_menu import run_main_menu

logger = logging.getLogger(__name__)


def get_app_info() -> dict[str, str]:
    return {"name": APP_NAME, "version": APP_VERSION}


def run_app(stdscr: "curses.window", conn: sqlite3.Connection) -> None:
    run_main_menu(stdscr, conn)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    db_path = get_db_path()

    # PD-1-BACKUP: periodic local backup, approximated at startup since this
    # stack has no scheduler/daemon (see src/db/backup.py docstring and
    # ADR-003). A backup failure must never block app startup.
    try:
        maybe_backup_on_startup(db_path)
    except Exception:
        logger.exception("Startup backup failed; continuing without backup")

    conn = connect(db_path)
    try:
        # PD-1-004 follow-up / ADR-004: apply every not-yet-applied
        # src/db/migrations/NNNN_up.sql against the live database, tracked
        # via the schema_migrations table. This replaces a bare
        # init_schema() call, which is a CREATE TABLE IF NOT EXISTS no-op
        # against an already-existing table and therefore cannot add a
        # schema change (e.g. migration 0002's updated_at column) to a
        # database created before that migration existed.
        apply_pending_migrations(conn)
        curses.wrapper(run_app, conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
