"""Schema migration runner (ADR-004).

Applies every not-yet-applied `src/db/migrations/NNNN_up.sql` file against
a live database, in order, tracked via a `schema_migrations` table. This is
the module `src/main.py` calls at startup — it replaces relying on
`init_schema()`'s `CREATE TABLE IF NOT EXISTS`, which is a no-op against a
table that already exists and therefore cannot add a column (or any other
schema change) to an already-created database.

See `docs/adr/ADR-004-schema-migration-runner.md` for the full design
rationale, alternatives considered, and stated limitations.
"""

import logging
import re
import sqlite3
from pathlib import Path

MIGRATIONS_DIR: Path = Path(__file__).resolve().parent / "migrations"

_MIGRATION_FILENAME_RE = re.compile(r"^(\d{4})_up\.sql$")

# SQLite has no `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` form, so a
# migration whose effect is already present on the target database raises
# an OperationalError rather than being a silent no-op. These message
# fragments identify that specific "already applied" class of error so the
# runner can tolerate it (log + stamp the version as applied) instead of
# failing startup. Any other OperationalError is a genuine failure and is
# re-raised.
_ALREADY_APPLIED_ERROR_FRAGMENTS = ("already exists", "duplicate column name")

logger = logging.getLogger(__name__)


def _ensure_schema_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
        """
    )
    conn.commit()


def _applied_versions(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {row[0] for row in rows}


def _discover_migrations(migrations_dir: Path) -> list[tuple[int, Path]]:
    migrations: list[tuple[int, Path]] = []
    for path in migrations_dir.iterdir():
        match = _MIGRATION_FILENAME_RE.match(path.name)
        if match:
            migrations.append((int(match.group(1)), path))
    return sorted(migrations, key=lambda item: item[0])


def _is_already_applied_error(exc: sqlite3.OperationalError) -> bool:
    message = str(exc).lower()
    return any(fragment in message for fragment in _ALREADY_APPLIED_ERROR_FRAGMENTS)


def apply_pending_migrations(
    conn: sqlite3.Connection, migrations_dir: Path = MIGRATIONS_DIR
) -> list[int]:
    """Apply every migration not yet recorded in `schema_migrations`, in
    ascending version order.

    Returns the list of versions newly applied this call (empty if the
    database was already up to date). Tolerant of a migration whose effect
    is already present on the target database (e.g. a database created via
    `SCHEMA_STATEMENTS` directly, or a partially-applied prior run) — such
    a migration is stamped as applied without re-raising, since SQLite's
    `ALTER TABLE ADD COLUMN` has no idempotent `IF NOT EXISTS` form.
    """
    _ensure_schema_migrations_table(conn)
    applied = _applied_versions(conn)
    newly_applied: list[int] = []

    for version, path in _discover_migrations(migrations_dir):
        if version in applied:
            continue

        try:
            conn.executescript(path.read_text())
        except sqlite3.OperationalError as exc:
            if not _is_already_applied_error(exc):
                raise
            logger.warning(
                "migration %04d already applied to database schema "
                "(structure already present) — recording without "
                "re-executing: %s",
                version,
                exc,
            )

        conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
        conn.commit()
        newly_applied.append(version)

    return newly_applied
