"""Unit tests for src/db/migrate.py (ADR-004 schema migration runner).

Covers:
- apply_pending_migrations() applying every pending migration, in order,
  against a fresh database.
- Idempotent re-run (no pending migrations left, empty result).
- Applying only the genuinely pending migration against a database that
  already has an earlier migration's effect (the exact shape of the
  user's real-world pre-existing database).
- Tolerant handling of a migration whose effect is already present
  (database created directly via SCHEMA_STATEMENTS, no schema_migrations
  row yet).
- schema_migrations version recording.
- Regression test reproducing the user's exact crash: a pre-0002 database,
  update_product() on it — via the real apply_pending_migrations() runner,
  not by hand-executing migration SQL text directly.
"""

import sqlite3
from pathlib import Path

import pytest

from src.db.migrate import MIGRATIONS_DIR, apply_pending_migrations
from src.db.schema import SCHEMA_STATEMENTS, connect
from src.services.catalog import update_product

UP_MIGRATION_0001 = MIGRATIONS_DIR / "0001_up.sql"


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row[0] for row in rows}


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def _applied_versions(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {row[0] for row in rows}


# --- apply_pending_migrations(): fresh database -----------------------------


def test_apply_pending_migrations_when_fresh_database_applies_all_in_order(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        applied = apply_pending_migrations(conn)

        assert applied == [1, 2, 3, 4, 5]
        assert {
            "products",
            "discounts",
            "inventory_stock",
            "sales",
            "sale_line_items",
        } <= _table_names(conn)
        assert "updated_at" in _column_names(conn, "products")
        assert "updated_at" in _column_names(conn, "discounts")
        assert "cost" in _column_names(conn, "products")
        assert "unit_cost" in _column_names(conn, "sale_line_items")
    finally:
        conn.close()


def test_apply_pending_migrations_when_fresh_database_records_versions(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        apply_pending_migrations(conn)

        assert _applied_versions(conn) == {1, 2, 3, 4, 5}
    finally:
        conn.close()


def test_apply_pending_migrations_when_called_twice_is_idempotent_and_no_op(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        first = apply_pending_migrations(conn)
        second = apply_pending_migrations(conn)  # must not raise

        assert first == [1, 2, 3, 4, 5]
        assert second == []
    finally:
        conn.close()


# --- apply_pending_migrations(): partial-pending database -------------------


def test_apply_pending_migrations_when_only_0001_applied_applies_remaining(
    tmp_path: Path,
) -> None:
    """The exact shape of the user's real pre-existing database: tables
    were created before schema_migrations tracking (and before migration
    0002) existed — no schema_migrations rows exist for either version yet.
    0001's CREATE TABLE IF NOT EXISTS is idempotent (a harmless re-run
    against the already-created tables), and 0002 genuinely adds the
    missing column; both get recorded as applied.
    """
    conn = connect(tmp_path / "data.db")
    try:
        conn.executescript(UP_MIGRATION_0001.read_text())  # simulate prior history
        conn.execute(
            "INSERT INTO products (name, price, gst_rate) VALUES (?, ?, ?)",
            ("Rice 1kg", 60, 5),
        )
        conn.commit()

        applied = apply_pending_migrations(conn)  # must not raise

        assert applied == [1, 2, 3, 4, 5]
        assert "updated_at" in _column_names(conn, "products")
        assert "updated_at" in _column_names(conn, "discounts")
        # Pre-existing row survives the idempotent 0001 re-run untouched.
        (name,) = conn.execute("SELECT name FROM products").fetchone()
        assert name == "Rice 1kg"
    finally:
        conn.close()


# --- apply_pending_migrations(): tolerant "already applied" handling -------


def test_apply_pending_migrations_when_schema_already_current_stamps_without_raising(
    tmp_path: Path,
) -> None:
    """A database created directly via SCHEMA_STATEMENTS (not the runner)
    already has every column current migrations would add. Applying
    migrations against it must not raise (SQLite's ALTER TABLE ADD COLUMN
    has no IF NOT EXISTS form) — the runner must tolerate this and stamp
    the versions as applied.
    """
    conn = connect(tmp_path / "data.db")
    try:
        for statement in SCHEMA_STATEMENTS:
            conn.execute(statement)
        conn.commit()

        applied = apply_pending_migrations(conn)  # must not raise

        assert applied == [1, 2, 3, 4, 5]
        assert _applied_versions(conn) == {1, 2, 3, 4, 5}
    finally:
        conn.close()


# --- regression: user's exact crash -----------------------------------------


def test_apply_pending_migrations_then_update_product_on_pre_0002_db_succeeds(
    tmp_path: Path,
) -> None:
    """Regression guard for the demo-review crash: a pre-existing database
    created before migration 0002 (missing products.updated_at, and now
    also missing products.cost per migration 0005/ADR-009) raised
    sqlite3.OperationalError: no such column inside update_product(),
    because init_schema()'s CREATE TABLE IF NOT EXISTS never applies a
    migration to an already-created table. Since ADR-009 (P3-F1-T2) added
    `cost` to update_product()'s SET clause ahead of `updated_at`, SQLite
    now reports the missing `cost` column first — the missing-column name
    is incidental to which column the SQL lists earliest, not to the
    behaviour under test (crash before the fix, success after).

    Reproduces the exact scenario via the real apply_pending_migrations()
    runner (the same function src/main.py calls) — not by hand-executing
    migration SQL text — then calls update_product() and confirms it now
    succeeds without a schema error.
    """
    conn = connect(tmp_path / "data.db")
    try:
        # Simulate the user's pre-existing database: only 0001 ever applied.
        conn.executescript(UP_MIGRATION_0001.read_text())
        conn.execute(
            "INSERT INTO products (name, price, gst_rate) VALUES (?, ?, ?)",
            ("Rice 1kg", 60, 5),
        )
        conn.commit()
        product_id = conn.execute("SELECT id FROM products").fetchone()[0]

        # Sanity: reproduce the crash before the fix is exercised. SQLite
        # reports whichever missing column its SET clause lists first —
        # `cost` (ADR-009/P3-F1-T2), not `updated_at` — but the underlying
        # crash class ("no such column") is what this test guards.
        with pytest.raises(sqlite3.OperationalError, match="no such column"):
            update_product(conn, product_id, "Rice 2kg", 65, 5)
        conn.rollback()

        # The fix: src/main.py's actual startup call.
        apply_pending_migrations(conn)

        product = update_product(conn, product_id, "Rice 2kg", 65, 5)

        assert product.name == "Rice 2kg"
        assert product.price == 65
        (updated_at,) = conn.execute(
            "SELECT updated_at FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        assert updated_at
    finally:
        conn.close()
