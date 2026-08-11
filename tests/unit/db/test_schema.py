"""Unit tests for src/db/schema.py and the migration pairs.

Covers:
- Schema creation against a throwaway (real, file-backed) SQLite file.
- Migration apply/rollback round-trip using the raw NNNN_up.sql / NNNN_down.sql
  files (no migration-runner module existed at the time this file was first
  written — see space Reference notes action #3 for the "No Orphaned Code"
  rationale; src/db/migrate.py has its own test file, test_migrate.py, for
  runner-level coverage).
- Constraint enforcement (CHECK, FOREIGN KEY) as error-propagation coverage,
  since sqlite3 is the I/O boundary for this infrastructure module.
- sales/sale_line_items (ADR-005, P2-F1-T2): structural creation, CHECK
  constraints, and FOREIGN KEY behaviour. CHECK-constraint tests for these
  two tables use `_connect_without_fk_enforcement` rather than `connect()`
  — retained as a CHECK-constraint isolation helper (see its docstring);
  originally written to work around `customers` not yet existing
  (Feature 2, now landed via migration 0004/ADR-006), kept afterwards so
  these tests do not also depend on FK-referenced row setup unrelated to
  the CHECK constraint under test.
- customers/credit_transactions (ADR-006, P2-F2-T2): structural creation,
  CHECK constraints (`transaction_type`, `amount`), UNIQUE constraint
  (`customers.phone`), and FOREIGN KEY behaviour (`credit_transactions.
  customer_id` -> `customers`, `.sale_id` -> `sales`).
"""

import sqlite3
from pathlib import Path

import pytest

from src.db.schema import SCHEMA_STATEMENTS, connect, init_schema

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "src" / "db" / "migrations"
UP_MIGRATION = MIGRATIONS_DIR / "0001_up.sql"
DOWN_MIGRATION = MIGRATIONS_DIR / "0001_down.sql"
UP_MIGRATION_0002 = MIGRATIONS_DIR / "0002_up.sql"
DOWN_MIGRATION_0002 = MIGRATIONS_DIR / "0002_down.sql"
UP_MIGRATION_0003 = MIGRATIONS_DIR / "0003_up.sql"
DOWN_MIGRATION_0003 = MIGRATIONS_DIR / "0003_down.sql"
UP_MIGRATION_0004 = MIGRATIONS_DIR / "0004_up.sql"
DOWN_MIGRATION_0004 = MIGRATIONS_DIR / "0004_down.sql"
UP_MIGRATION_0005 = MIGRATIONS_DIR / "0005_up.sql"
DOWN_MIGRATION_0005 = MIGRATIONS_DIR / "0005_down.sql"

TABLE_NAMES = ["products", "discounts", "inventory_stock"]
SALES_TABLE_NAMES = ["sales", "sale_line_items"]
CUSTOMER_TABLE_NAMES = ["customers", "credit_transactions"]
ALL_TABLE_NAMES = TABLE_NAMES + SALES_TABLE_NAMES + CUSTOMER_TABLE_NAMES


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row[0] for row in rows}


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def _connect_without_fk_enforcement(db_path: Path) -> sqlite3.Connection:
    """Test-only helper — NOT the production `connect()` path.

    `connect()` always sets `PRAGMA foreign_keys = ON`. This helper turns FK
    enforcement off so CHECK-constraint tests for `sales`/`sale_line_items`
    (and now `customers`/`credit_transactions`) do not also need to satisfy
    every FK-referenced row's existence — a concern independent of the
    CHECK constraint under test. (Originally added because `customers` did
    not yet exist and `sales.customer_id REFERENCES customers (id)` blocked
    every INSERT into `sales` structurally, even with `customer_id = NULL`;
    `customers` now exists via migration 0004/ADR-006, but the helper
    remains useful for CHECK-constraint isolation.)

    Tests that exercise FOREIGN KEY behaviour itself (e.g. `sale_line_items`
    against `products`/`sales`, `credit_transactions` against `customers`/
    `sales`) use the real `connect()` with FK enforcement ON instead — see
    those tests below.
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = OFF")
    for statement in SCHEMA_STATEMENTS:
        conn.execute(statement)
    conn.commit()
    return conn


# --- connect() -------------------------------------------------------------


def test_connect_when_parent_directory_missing_creates_it(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "dir" / "data.db"

    conn = connect(db_path)
    conn.close()

    assert db_path.parent.is_dir()
    assert db_path.exists()


def test_connect_when_called_enables_foreign_key_enforcement(tmp_path: Path) -> None:
    db_path = tmp_path / "data.db"

    conn = connect(db_path)
    try:
        (fk_enabled,) = conn.execute("PRAGMA foreign_keys").fetchone()
        assert fk_enabled == 1
    finally:
        conn.close()


# --- init_schema() ----------------------------------------------------------


def test_init_schema_when_called_creates_all_current_tables(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        init_schema(conn)

        assert _table_names(conn) == set(ALL_TABLE_NAMES)
    finally:
        conn.close()


def test_init_schema_when_called_twice_is_idempotent(tmp_path: Path) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        init_schema(conn)
        init_schema(conn)  # must not raise (IF NOT EXISTS)

        assert _table_names(conn) == set(ALL_TABLE_NAMES)
    finally:
        conn.close()


def test_init_schema_when_called_inserts_and_reads_back_a_single_product(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        init_schema(conn)

        conn.execute(
            "INSERT INTO products (name, price, gst_rate) VALUES (?, ?, ?)",
            ("Rice 1kg", 60, 5),
        )
        conn.commit()

        rows = conn.execute("SELECT name, price, gst_rate FROM products").fetchall()
        assert rows == [("Rice 1kg", 60, 5)]
    finally:
        conn.close()


def test_init_schema_when_called_populates_updated_at_on_products_and_discounts(
    tmp_path: Path,
) -> None:
    """Regression guard for architect-review Finding 1 (fixes.md) — products
    and discounts must carry an updated_at audit column, mirroring
    inventory_stock, populated automatically on insert.
    """
    conn = connect(tmp_path / "data.db")
    try:
        init_schema(conn)

        conn.execute(
            "INSERT INTO products (name, price, gst_rate) VALUES (?, ?, ?)",
            ("Rice 1kg", 60, 5),
        )
        conn.commit()
        product_id = conn.execute("SELECT id FROM products").fetchone()[0]

        conn.execute(
            "INSERT INTO discounts (product_id, discount_type, value) VALUES (?, ?, ?)",
            (product_id, "percentage", 10),
        )
        conn.commit()

        (product_updated_at,) = conn.execute(
            "SELECT updated_at FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        (discount_updated_at,) = conn.execute(
            "SELECT updated_at FROM discounts WHERE product_id = ?", (product_id,)
        ).fetchone()

        assert product_updated_at
        assert discount_updated_at
    finally:
        conn.close()


# --- boundary / invalid input: products -------------------------------------


def test_products_table_when_price_is_zero_is_accepted(tmp_path: Path) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        init_schema(conn)

        conn.execute(
            "INSERT INTO products (name, price, gst_rate) VALUES (?, ?, ?)",
            ("Free Sample", 0, 0),
        )
        conn.commit()

        (price,) = conn.execute("SELECT price FROM products").fetchone()
        assert price == 0
    finally:
        conn.close()


def test_products_table_when_price_negative_raises_integrity_error(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        init_schema(conn)

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO products (name, price, gst_rate) VALUES (?, ?, ?)",
                ("Bad Product", -1, 5),
            )
    finally:
        conn.close()


def test_products_table_when_gst_rate_negative_raises_integrity_error(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        init_schema(conn)

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO products (name, price, gst_rate) VALUES (?, ?, ?)",
                ("Bad Product", 10, -5),
            )
    finally:
        conn.close()


# --- boundary / invalid input / error propagation: discounts ---------------


def test_discounts_table_when_type_and_value_valid_inserts_successfully(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        init_schema(conn)
        conn.execute(
            "INSERT INTO products (name, price, gst_rate) VALUES (?, ?, ?)",
            ("Rice 1kg", 60, 5),
        )
        conn.commit()
        product_id = conn.execute("SELECT id FROM products").fetchone()[0]

        conn.execute(
            "INSERT INTO discounts (product_id, discount_type, value) VALUES (?, ?, ?)",
            (product_id, "percentage", 10),
        )
        conn.commit()

        rows = conn.execute("SELECT discount_type, value FROM discounts").fetchall()
        assert rows == [("percentage", 10)]
    finally:
        conn.close()


def test_discounts_table_when_discount_type_invalid_raises_integrity_error(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        init_schema(conn)
        conn.execute(
            "INSERT INTO products (name, price, gst_rate) VALUES (?, ?, ?)",
            ("Rice 1kg", 60, 5),
        )
        conn.commit()
        product_id = conn.execute("SELECT id FROM products").fetchone()[0]

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO discounts (product_id, discount_type, value) "
                "VALUES (?, ?, ?)",
                (product_id, "buy-one-get-one", 10),
            )
    finally:
        conn.close()


def test_discounts_table_when_value_negative_raises_integrity_error(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        init_schema(conn)
        conn.execute(
            "INSERT INTO products (name, price, gst_rate) VALUES (?, ?, ?)",
            ("Rice 1kg", 60, 5),
        )
        conn.commit()
        product_id = conn.execute("SELECT id FROM products").fetchone()[0]

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO discounts (product_id, discount_type, value) "
                "VALUES (?, ?, ?)",
                (product_id, "flat", -5),
            )
    finally:
        conn.close()


def test_discounts_table_when_product_id_does_not_exist_raises_integrity_error(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        init_schema(conn)

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO discounts (product_id, discount_type, value) "
                "VALUES (?, ?, ?)",
                (999, "flat", 5),
            )
    finally:
        conn.close()


# --- boundary / default: inventory_stock ------------------------------------


def test_inventory_stock_table_when_quantity_omitted_defaults_to_zero(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        init_schema(conn)
        conn.execute(
            "INSERT INTO products (name, price, gst_rate) VALUES (?, ?, ?)",
            ("Rice 1kg", 60, 5),
        )
        conn.commit()
        product_id = conn.execute("SELECT id FROM products").fetchone()[0]

        conn.execute(
            "INSERT INTO inventory_stock (product_id) VALUES (?)", (product_id,)
        )
        conn.commit()

        (quantity,) = conn.execute(
            "SELECT quantity FROM inventory_stock WHERE product_id = ?",
            (product_id,),
        ).fetchone()
        assert quantity == 0
    finally:
        conn.close()


def test_inventory_stock_table_when_product_id_does_not_exist_raises_integrity_error(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        init_schema(conn)

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO inventory_stock (product_id, quantity) VALUES (?, ?)",
                (999, 5),
            )
    finally:
        conn.close()


# --- migration apply/rollback round-trip ------------------------------------


def test_migration_0001_up_when_applied_creates_all_three_tables(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        conn.executescript(UP_MIGRATION.read_text())

        assert _table_names(conn) == set(TABLE_NAMES)
    finally:
        conn.close()


def test_migration_0001_down_when_applied_after_up_drops_all_three_tables(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        conn.executescript(UP_MIGRATION.read_text())
        conn.executescript(DOWN_MIGRATION.read_text())

        assert _table_names(conn) == set()
    finally:
        conn.close()


def test_migration_0001_down_when_applied_with_no_tables_present_is_idempotent(
    tmp_path: Path,
) -> None:
    """DROP TABLE IF EXISTS must not raise against an empty database."""
    conn = connect(tmp_path / "data.db")
    try:
        conn.executescript(DOWN_MIGRATION.read_text())  # must not raise

        assert _table_names(conn) == set()
    finally:
        conn.close()


def test_migration_round_trip_when_up_then_down_then_up_again_succeeds(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        conn.executescript(UP_MIGRATION.read_text())
        conn.executescript(DOWN_MIGRATION.read_text())
        conn.executescript(UP_MIGRATION.read_text())  # must not raise

        assert _table_names(conn) == set(TABLE_NAMES)
    finally:
        conn.close()


def test_migration_0001_up_when_applied_enforces_same_constraints_as_schema_py(
    tmp_path: Path,
) -> None:
    """The migration's DDL must enforce the same CHECK constraints as
    SCHEMA_STATEMENTS — a negative price must be rejected identically
    whether the schema is created via init_schema() or via the migration.
    """
    conn = connect(tmp_path / "data.db")
    try:
        conn.executescript(UP_MIGRATION.read_text())

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO products (name, price, gst_rate) VALUES (?, ?, ?)",
                ("Bad Product", -1, 5),
            )
    finally:
        conn.close()


def test_migration_files_when_read_produce_ddl_present_in_schema_statements(
    tmp_path: Path,
) -> None:
    """Regression guard for the claim (action 1/5 Reference notes) that
    SCHEMA_STATEMENTS and 0001_up.sql are kept in sync: applying 0001_up.sql
    alone must yield exactly TABLE_NAMES, and those tables must be present
    within the full SCHEMA_STATEMENTS-created set (a subset check, not
    equality — SCHEMA_STATEMENTS now also includes migration 0003's
    sales/sale_line_items tables, which 0001_up.sql alone does not create;
    see test_migration_0003_files_when_read_produce_ddl_present_in_schema_statements
    for the equivalent 0001+0002+0003-combined equality check).
    """
    up_conn = connect(tmp_path / "up.db")
    schema_conn = connect(tmp_path / "schema.db")
    try:
        up_conn.executescript(UP_MIGRATION.read_text())
        for statement in SCHEMA_STATEMENTS:
            schema_conn.execute(statement)
        schema_conn.commit()

        assert _table_names(up_conn) == set(TABLE_NAMES)
        assert set(TABLE_NAMES) <= _table_names(schema_conn)
    finally:
        up_conn.close()
        schema_conn.close()


# --- migration 0002 (updated_at on products/discounts) apply/rollback -------


def test_migration_0002_up_when_applied_after_0001_adds_updated_at_columns(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        conn.executescript(UP_MIGRATION.read_text())
        conn.executescript(UP_MIGRATION_0002.read_text())

        assert "updated_at" in _column_names(conn, "products")
        assert "updated_at" in _column_names(conn, "discounts")
    finally:
        conn.close()


def test_migration_0002_up_when_applied_backfills_updated_at_on_existing_rows(
    tmp_path: Path,
) -> None:
    """Regression guard: SQLite rejects a non-constant ALTER TABLE ADD
    COLUMN DEFAULT once a table has rows — 0002_up.sql must add the column
    nullable and backfill via UPDATE instead, so this must succeed (not
    raise) against a products table that already has a row, and the
    backfilled value must be non-empty.
    """
    conn = connect(tmp_path / "data.db")
    try:
        conn.executescript(UP_MIGRATION.read_text())
        conn.execute(
            "INSERT INTO products (name, price, gst_rate) VALUES (?, ?, ?)",
            ("Rice 1kg", 60, 5),
        )
        conn.commit()

        conn.executescript(UP_MIGRATION_0002.read_text())  # must not raise

        (updated_at,) = conn.execute(
            "SELECT updated_at FROM products WHERE name = ?", ("Rice 1kg",)
        ).fetchone()
        assert updated_at
    finally:
        conn.close()


def test_migration_0002_down_when_applied_after_up_removes_updated_at_columns(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        conn.executescript(UP_MIGRATION.read_text())
        conn.executescript(UP_MIGRATION_0002.read_text())
        conn.executescript(DOWN_MIGRATION_0002.read_text())

        assert "updated_at" not in _column_names(conn, "products")
        assert "updated_at" not in _column_names(conn, "discounts")
    finally:
        conn.close()


def test_migration_0002_round_trip_when_up_then_down_then_up_again_succeeds(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        conn.executescript(UP_MIGRATION.read_text())
        conn.executescript(UP_MIGRATION_0002.read_text())
        conn.executescript(DOWN_MIGRATION_0002.read_text())
        conn.executescript(UP_MIGRATION_0002.read_text())  # must not raise

        assert "updated_at" in _column_names(conn, "products")
        assert "updated_at" in _column_names(conn, "discounts")
    finally:
        conn.close()


def test_migration_0002_files_when_read_produce_ddl_present_in_schema_statements(
    tmp_path: Path,
) -> None:
    """Regression guard: applying 0001+0002 independently must yield the
    same products/discounts column set as SCHEMA_STATEMENTS, excluding
    products.cost — added by migration 0005 (ADR-009), which 0001+0002
    alone do not create.
    """
    up_conn = connect(tmp_path / "up.db")
    schema_conn = connect(tmp_path / "schema.db")
    try:
        up_conn.executescript(UP_MIGRATION.read_text())
        up_conn.executescript(UP_MIGRATION_0002.read_text())
        for statement in SCHEMA_STATEMENTS:
            schema_conn.execute(statement)
        schema_conn.commit()

        assert _column_names(up_conn, "products") == _column_names(
            schema_conn, "products"
        ) - {"cost"}
        assert _column_names(up_conn, "discounts") == _column_names(
            schema_conn, "discounts"
        )
    finally:
        up_conn.close()
        schema_conn.close()


# --- migration 0003 (sales / sale_line_items, ADR-005) apply/rollback ------


def test_migration_0003_up_when_applied_after_0001_and_0002_creates_both_tables(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        conn.executescript(UP_MIGRATION.read_text())
        conn.executescript(UP_MIGRATION_0002.read_text())
        conn.executescript(UP_MIGRATION_0003.read_text())

        assert set(SALES_TABLE_NAMES) <= _table_names(conn)
    finally:
        conn.close()


def test_migration_0003_down_when_applied_after_up_drops_both_tables(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        conn.executescript(UP_MIGRATION.read_text())
        conn.executescript(UP_MIGRATION_0002.read_text())
        conn.executescript(UP_MIGRATION_0003.read_text())
        conn.executescript(DOWN_MIGRATION_0003.read_text())

        assert _table_names(conn) == {"products", "discounts", "inventory_stock"}
    finally:
        conn.close()


def test_migration_0003_down_when_applied_with_no_sales_tables_present_is_idempotent(
    tmp_path: Path,
) -> None:
    """DROP TABLE IF EXISTS must not raise against a database that never had
    the sales tables (e.g. a pre-0003 install rolling back a partial apply).
    """
    conn = connect(tmp_path / "data.db")
    try:
        conn.executescript(UP_MIGRATION.read_text())

        conn.executescript(DOWN_MIGRATION_0003.read_text())  # must not raise

        assert _table_names(conn) == {"products", "discounts", "inventory_stock"}
    finally:
        conn.close()


def test_migration_0003_round_trip_when_up_then_down_then_up_again_succeeds(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        conn.executescript(UP_MIGRATION.read_text())
        conn.executescript(UP_MIGRATION_0002.read_text())
        conn.executescript(UP_MIGRATION_0003.read_text())
        conn.executescript(DOWN_MIGRATION_0003.read_text())
        conn.executescript(UP_MIGRATION_0003.read_text())  # must not raise

        assert set(SALES_TABLE_NAMES) <= _table_names(conn)
    finally:
        conn.close()


def test_migration_0003_files_when_read_produce_ddl_present_in_schema_statements(
    tmp_path: Path,
) -> None:
    """Regression guard: applying 0001+0002+0003 independently must yield a
    table set that is a subset of the full SCHEMA_STATEMENTS-created set
    (a subset check, not equality — SCHEMA_STATEMENTS now also includes
    migration 0004's customers/credit_transactions tables, which 0001+0002+
    0003 alone do not create; see
    test_migration_0004_files_when_read_produce_ddl_present_in_schema_statements
    for the equivalent 0001+0002+0003+0004-combined equality check). Column
    comparison for sale_line_items excludes unit_cost — added by migration
    0005 (ADR-009), which 0001+0002+0003 alone do not create.
    """
    up_conn = connect(tmp_path / "up.db")
    schema_conn = connect(tmp_path / "schema.db")
    try:
        up_conn.executescript(UP_MIGRATION.read_text())
        up_conn.executescript(UP_MIGRATION_0002.read_text())
        up_conn.executescript(UP_MIGRATION_0003.read_text())
        for statement in SCHEMA_STATEMENTS:
            schema_conn.execute(statement)
        schema_conn.commit()

        assert set(TABLE_NAMES + SALES_TABLE_NAMES) == _table_names(up_conn)
        assert _table_names(up_conn) <= _table_names(schema_conn)
        for table in SALES_TABLE_NAMES:
            expected = _column_names(schema_conn, table) - {"cost", "unit_cost"}
            assert _column_names(up_conn, table) == expected
    finally:
        up_conn.close()
        schema_conn.close()


# --- migration 0004 (customers / credit_transactions, ADR-006) apply/rollback


def test_migration_0004_up_when_applied_after_0001_0002_0003_creates_both_tables(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        conn.executescript(UP_MIGRATION.read_text())
        conn.executescript(UP_MIGRATION_0002.read_text())
        conn.executescript(UP_MIGRATION_0003.read_text())
        conn.executescript(UP_MIGRATION_0004.read_text())

        assert set(CUSTOMER_TABLE_NAMES) <= _table_names(conn)
    finally:
        conn.close()


def test_migration_0004_down_when_applied_after_up_drops_both_tables(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        conn.executescript(UP_MIGRATION.read_text())
        conn.executescript(UP_MIGRATION_0002.read_text())
        conn.executescript(UP_MIGRATION_0003.read_text())
        conn.executescript(UP_MIGRATION_0004.read_text())
        conn.executescript(DOWN_MIGRATION_0004.read_text())

        assert _table_names(conn) == set(TABLE_NAMES + SALES_TABLE_NAMES)
    finally:
        conn.close()


def test_migration_0004_down_when_applied_with_no_customer_tables_present_is_idempotent(
    tmp_path: Path,
) -> None:
    """DROP TABLE IF EXISTS must not raise against a database that never had
    the customer tables (e.g. a pre-0004 install rolling back a partial
    apply).
    """
    conn = connect(tmp_path / "data.db")
    try:
        conn.executescript(UP_MIGRATION.read_text())
        conn.executescript(UP_MIGRATION_0002.read_text())
        conn.executescript(UP_MIGRATION_0003.read_text())

        conn.executescript(DOWN_MIGRATION_0004.read_text())  # must not raise

        assert _table_names(conn) == set(TABLE_NAMES + SALES_TABLE_NAMES)
    finally:
        conn.close()


def test_migration_0004_round_trip_when_up_then_down_then_up_again_succeeds(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        conn.executescript(UP_MIGRATION.read_text())
        conn.executescript(UP_MIGRATION_0002.read_text())
        conn.executescript(UP_MIGRATION_0003.read_text())
        conn.executescript(UP_MIGRATION_0004.read_text())
        conn.executescript(DOWN_MIGRATION_0004.read_text())
        conn.executescript(UP_MIGRATION_0004.read_text())  # must not raise

        assert set(CUSTOMER_TABLE_NAMES) <= _table_names(conn)
    finally:
        conn.close()


def test_migration_0004_files_when_read_produce_ddl_present_in_schema_statements(
    tmp_path: Path,
) -> None:
    """Regression guard: applying 0001+0002+0003+0004 independently must
    yield the identical full table set that SCHEMA_STATEMENTS produces
    directly. Column comparison for products/sale_line_items excludes
    cost/unit_cost — added by migration 0005 (ADR-009), which 0001+0002+
    0003+0004 alone do not create; see
    test_migration_0005_files_when_read_produce_ddl_present_in_schema_statements
    for the equivalent full-chain-including-0005 equality check.
    """
    up_conn = connect(tmp_path / "up.db")
    schema_conn = connect(tmp_path / "schema.db")
    try:
        up_conn.executescript(UP_MIGRATION.read_text())
        up_conn.executescript(UP_MIGRATION_0002.read_text())
        up_conn.executescript(UP_MIGRATION_0003.read_text())
        up_conn.executescript(UP_MIGRATION_0004.read_text())
        for statement in SCHEMA_STATEMENTS:
            schema_conn.execute(statement)
        schema_conn.commit()

        assert (
            _table_names(up_conn) == _table_names(schema_conn) == set(ALL_TABLE_NAMES)
        )
        for table in SALES_TABLE_NAMES + CUSTOMER_TABLE_NAMES:
            expected = _column_names(schema_conn, table) - {"cost", "unit_cost"}
            assert _column_names(up_conn, table) == expected
    finally:
        up_conn.close()
        schema_conn.close()


# --- migration 0005 (products.cost, sale_line_items.unit_cost, ADR-009) ----
# apply/rollback (P3-F1-T4)


def test_migration_0005_up_when_applied_after_0001_through_0004_adds_both_columns(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        conn.executescript(UP_MIGRATION.read_text())
        conn.executescript(UP_MIGRATION_0002.read_text())
        conn.executescript(UP_MIGRATION_0003.read_text())
        conn.executescript(UP_MIGRATION_0004.read_text())
        conn.executescript(UP_MIGRATION_0005.read_text())

        assert "cost" in _column_names(conn, "products")
        assert "unit_cost" in _column_names(conn, "sale_line_items")
    finally:
        conn.close()


def test_migration_0005_up_when_applied_enforces_same_check_constraints_as_schema_py(
    tmp_path: Path,
) -> None:
    """The migration's CHECK constraints must reject a negative cost/
    unit_cost identically to `SCHEMA_STATEMENTS` (ADR-009 Decision (a)/(b))
    — mirrors `test_migration_0001_up_when_applied_enforces_same_
    constraints_as_schema_py`'s pattern for the products.price constraint."""
    conn = connect(tmp_path / "data.db")
    try:
        conn.executescript(UP_MIGRATION.read_text())
        conn.executescript(UP_MIGRATION_0002.read_text())
        conn.executescript(UP_MIGRATION_0003.read_text())
        conn.executescript(UP_MIGRATION_0004.read_text())
        conn.executescript(UP_MIGRATION_0005.read_text())

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO products (name, price, gst_rate, cost) "
                "VALUES (?, ?, ?, ?)",
                ("Bad Product", 10, 5, -1),
            )
    finally:
        conn.close()


def test_migration_0005_up_when_cost_omitted_is_null_not_zero(tmp_path: Path) -> None:
    """ADR-009 Decision (a) — `NULL` means "cost unknown", never "cost is
    zero"; a column with no default must leave an omitted cost as `NULL`."""
    conn = connect(tmp_path / "data.db")
    try:
        conn.executescript(UP_MIGRATION.read_text())
        conn.executescript(UP_MIGRATION_0002.read_text())
        conn.executescript(UP_MIGRATION_0003.read_text())
        conn.executescript(UP_MIGRATION_0004.read_text())
        conn.executescript(UP_MIGRATION_0005.read_text())

        conn.execute(
            "INSERT INTO products (name, price, gst_rate) VALUES (?, ?, ?)",
            ("Rice 1kg", 60, 5),
        )
        conn.commit()

        (cost,) = conn.execute("SELECT cost FROM products").fetchone()
        assert cost is None
    finally:
        conn.close()


def test_migration_0005_up_when_applied_against_existing_rows_does_not_raise(
    tmp_path: Path,
) -> None:
    """Regression guard mirroring `test_migration_0002_up_when_applied_
    backfills_updated_at_on_existing_rows`'s existing-row concern: adding a
    `NUMERIC ... CHECK (x IS NULL OR x >= 0)` column via `ALTER TABLE ADD
    COLUMN` must succeed against a `products`/`sale_line_items` table that
    already has rows, because the CHECK constraint tolerates the column's
    NULL default on those pre-existing rows (ADR-009 Decision (d))."""
    conn = connect(tmp_path / "data.db")
    try:
        conn.executescript(UP_MIGRATION.read_text())
        conn.executescript(UP_MIGRATION_0002.read_text())
        conn.executescript(UP_MIGRATION_0003.read_text())
        conn.executescript(UP_MIGRATION_0004.read_text())
        conn.execute(
            "INSERT INTO products (name, price, gst_rate) VALUES (?, ?, ?)",
            ("Rice 1kg", 60, 5),
        )
        conn.commit()
        product_id = conn.execute("SELECT id FROM products").fetchone()[0]
        conn.execute(
            "INSERT INTO sales (payment_status, subtotal, gst_total, bill_total) "
            "VALUES ('paid', 60, 3, 63)"
        )
        conn.commit()
        sale_id = conn.execute("SELECT id FROM sales").fetchone()[0]
        conn.execute(
            "INSERT INTO sale_line_items (sale_id, product_id, quantity, "
            "unit_price, gst_rate, line_gst_amount, line_total) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sale_id, product_id, 1, 60, 5, 3, 63),
        )
        conn.commit()

        conn.executescript(UP_MIGRATION_0005.read_text())  # must not raise

        (product_cost,) = conn.execute("SELECT cost FROM products").fetchone()
        (line_unit_cost,) = conn.execute(
            "SELECT unit_cost FROM sale_line_items"
        ).fetchone()
        assert product_cost is None
        assert line_unit_cost is None
    finally:
        conn.close()


def test_migration_0005_down_when_applied_after_up_drops_both_columns(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        conn.executescript(UP_MIGRATION.read_text())
        conn.executescript(UP_MIGRATION_0002.read_text())
        conn.executescript(UP_MIGRATION_0003.read_text())
        conn.executescript(UP_MIGRATION_0004.read_text())
        conn.executescript(UP_MIGRATION_0005.read_text())
        conn.executescript(DOWN_MIGRATION_0005.read_text())

        assert "cost" not in _column_names(conn, "products")
        assert "unit_cost" not in _column_names(conn, "sale_line_items")
    finally:
        conn.close()


def test_migration_0005_round_trip_when_up_then_down_then_up_again_succeeds(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        conn.executescript(UP_MIGRATION.read_text())
        conn.executescript(UP_MIGRATION_0002.read_text())
        conn.executescript(UP_MIGRATION_0003.read_text())
        conn.executescript(UP_MIGRATION_0004.read_text())
        conn.executescript(UP_MIGRATION_0005.read_text())
        conn.executescript(DOWN_MIGRATION_0005.read_text())
        conn.executescript(UP_MIGRATION_0005.read_text())  # must not raise

        assert "cost" in _column_names(conn, "products")
        assert "unit_cost" in _column_names(conn, "sale_line_items")
    finally:
        conn.close()


def test_migration_0005_files_when_read_produce_ddl_present_in_schema_statements(
    tmp_path: Path,
) -> None:
    """Regression guard mirroring `test_migration_0004_files_when_read_
    produce_ddl_present_in_schema_statements`: applying 0001+0002+0003+0004+
    0005 independently must yield the identical full column set that
    `SCHEMA_STATEMENTS` produces directly — the full chain, no exclusions
    this time (0005 is the last migration that touches these columns)."""
    up_conn = connect(tmp_path / "up.db")
    schema_conn = connect(tmp_path / "schema.db")
    try:
        up_conn.executescript(UP_MIGRATION.read_text())
        up_conn.executescript(UP_MIGRATION_0002.read_text())
        up_conn.executescript(UP_MIGRATION_0003.read_text())
        up_conn.executescript(UP_MIGRATION_0004.read_text())
        up_conn.executescript(UP_MIGRATION_0005.read_text())
        for statement in SCHEMA_STATEMENTS:
            schema_conn.execute(statement)
        schema_conn.commit()

        assert (
            _table_names(up_conn) == _table_names(schema_conn) == set(ALL_TABLE_NAMES)
        )
        for table in SALES_TABLE_NAMES + CUSTOMER_TABLE_NAMES + TABLE_NAMES:
            assert _column_names(up_conn, table) == _column_names(schema_conn, table)
    finally:
        up_conn.close()
        schema_conn.close()


# --- boundary / invalid input: products.cost (ADR-009 Decision (a)) --------


def test_products_table_when_cost_negative_raises_integrity_error(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        init_schema(conn)

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO products (name, price, gst_rate, cost) "
                "VALUES (?, ?, ?, ?)",
                ("Bad Product", 10, 5, -1),
            )
    finally:
        conn.close()


def test_products_table_when_cost_zero_is_accepted(tmp_path: Path) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        init_schema(conn)

        conn.execute(
            "INSERT INTO products (name, price, gst_rate, cost) VALUES (?, ?, ?, ?)",
            ("Free Sample", 0, 0, 0),
        )
        conn.commit()

        (cost,) = conn.execute("SELECT cost FROM products").fetchone()
        assert cost == 0
    finally:
        conn.close()


def test_products_table_when_cost_omitted_is_null(tmp_path: Path) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        init_schema(conn)

        conn.execute(
            "INSERT INTO products (name, price, gst_rate) VALUES (?, ?, ?)",
            ("Rice 1kg", 60, 5),
        )
        conn.commit()

        (cost,) = conn.execute("SELECT cost FROM products").fetchone()
        assert cost is None
    finally:
        conn.close()


# --- boundary / invalid input: sale_line_items.unit_cost (ADR-009 (b)) -----


def test_sale_line_items_table_when_unit_cost_negative_raises_integrity_error(
    tmp_path: Path,
) -> None:
    conn = _connect_without_fk_enforcement(tmp_path / "data.db")
    try:
        conn.execute(
            "INSERT INTO products (name, price, gst_rate) VALUES (?, ?, ?)",
            ("Rice 1kg", 60, 5),
        )
        conn.execute(
            "INSERT INTO sales (payment_status, subtotal, gst_total, "
            "bill_total) VALUES ('paid', 60, 3, 63)"
        )
        conn.commit()
        product_id = conn.execute("SELECT id FROM products").fetchone()[0]
        sale_id = conn.execute("SELECT id FROM sales").fetchone()[0]

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO sale_line_items (sale_id, product_id, quantity, "
                "unit_price, gst_rate, line_gst_amount, line_total, unit_cost) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (sale_id, product_id, 1, 60, 5, 3, 63, -1),
            )
    finally:
        conn.close()


def test_sale_line_items_table_when_unit_cost_omitted_is_null(tmp_path: Path) -> None:
    conn = _connect_without_fk_enforcement(tmp_path / "data.db")
    try:
        conn.execute(
            "INSERT INTO products (name, price, gst_rate) VALUES (?, ?, ?)",
            ("Rice 1kg", 60, 5),
        )
        conn.execute(
            "INSERT INTO sales (payment_status, subtotal, gst_total, "
            "bill_total) VALUES ('paid', 60, 3, 63)"
        )
        conn.commit()
        product_id = conn.execute("SELECT id FROM products").fetchone()[0]
        sale_id = conn.execute("SELECT id FROM sales").fetchone()[0]

        conn.execute(
            "INSERT INTO sale_line_items (sale_id, product_id, quantity, "
            "unit_price, gst_rate, line_gst_amount, line_total) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sale_id, product_id, 1, 60, 5, 3, 63),
        )
        conn.commit()

        (unit_cost,) = conn.execute("SELECT unit_cost FROM sale_line_items").fetchone()
        assert unit_cost is None
    finally:
        conn.close()


# --- boundary / invalid input / error propagation: sales -------------------


def test_sales_table_when_customer_id_null_and_payment_status_paid_is_accepted(
    tmp_path: Path,
) -> None:
    conn = _connect_without_fk_enforcement(tmp_path / "data.db")
    try:
        conn.execute(
            "INSERT INTO sales (customer_id, payment_status, subtotal, "
            "gst_total, bill_total) VALUES (NULL, 'paid', 100, 5, 105)"
        )
        conn.commit()

        row = conn.execute(
            "SELECT customer_id, payment_status, subtotal, gst_total, bill_total "
            "FROM sales"
        ).fetchone()
        assert row == (None, "paid", 100, 5, 105)
    finally:
        conn.close()


def test_sales_table_when_payment_status_invalid_raises_integrity_error(
    tmp_path: Path,
) -> None:
    conn = _connect_without_fk_enforcement(tmp_path / "data.db")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO sales (payment_status, subtotal, gst_total, "
                "bill_total) VALUES ('refunded', 100, 5, 105)"
            )
    finally:
        conn.close()


def test_sales_table_when_subtotal_negative_raises_integrity_error(
    tmp_path: Path,
) -> None:
    conn = _connect_without_fk_enforcement(tmp_path / "data.db")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO sales (payment_status, subtotal, gst_total, "
                "bill_total) VALUES ('paid', -1, 5, 105)"
            )
    finally:
        conn.close()


def test_sales_table_when_gst_total_negative_raises_integrity_error(
    tmp_path: Path,
) -> None:
    conn = _connect_without_fk_enforcement(tmp_path / "data.db")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO sales (payment_status, subtotal, gst_total, "
                "bill_total) VALUES ('paid', 100, -1, 105)"
            )
    finally:
        conn.close()


def test_sales_table_when_bill_total_negative_raises_integrity_error(
    tmp_path: Path,
) -> None:
    conn = _connect_without_fk_enforcement(tmp_path / "data.db")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO sales (payment_status, subtotal, gst_total, "
                "bill_total) VALUES ('paid', 100, 5, -1)"
            )
    finally:
        conn.close()


# --- boundary / invalid input / error propagation: sale_line_items ---------


def test_sale_line_items_table_when_valid_data_inserts_successfully(
    tmp_path: Path,
) -> None:
    conn = _connect_without_fk_enforcement(tmp_path / "data.db")
    try:
        conn.execute(
            "INSERT INTO products (name, price, gst_rate) VALUES (?, ?, ?)",
            ("Rice 1kg", 60, 5),
        )
        conn.execute(
            "INSERT INTO sales (payment_status, subtotal, gst_total, "
            "bill_total) VALUES ('paid', 60, 3, 63)"
        )
        conn.commit()
        product_id = conn.execute("SELECT id FROM products").fetchone()[0]
        sale_id = conn.execute("SELECT id FROM sales").fetchone()[0]

        conn.execute(
            "INSERT INTO sale_line_items (sale_id, product_id, quantity, "
            "unit_price, gst_rate, discount_amount, line_gst_amount, "
            "line_total) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (sale_id, product_id, 1, 60, 5, 0, 3, 63),
        )
        conn.commit()

        row = conn.execute(
            "SELECT sale_id, product_id, quantity, unit_price, gst_rate, "
            "discount_amount, line_gst_amount, line_total FROM sale_line_items"
        ).fetchone()
        assert row == (sale_id, product_id, 1, 60, 5, 0, 3, 63)
    finally:
        conn.close()


def test_sale_line_items_table_when_discount_amount_omitted_defaults_to_zero(
    tmp_path: Path,
) -> None:
    conn = _connect_without_fk_enforcement(tmp_path / "data.db")
    try:
        conn.execute(
            "INSERT INTO products (name, price, gst_rate) VALUES (?, ?, ?)",
            ("Rice 1kg", 60, 5),
        )
        conn.execute(
            "INSERT INTO sales (payment_status, subtotal, gst_total, "
            "bill_total) VALUES ('paid', 60, 3, 63)"
        )
        conn.commit()
        product_id = conn.execute("SELECT id FROM products").fetchone()[0]
        sale_id = conn.execute("SELECT id FROM sales").fetchone()[0]

        conn.execute(
            "INSERT INTO sale_line_items (sale_id, product_id, quantity, "
            "unit_price, gst_rate, line_gst_amount, line_total) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sale_id, product_id, 1, 60, 5, 3, 63),
        )
        conn.commit()

        (discount_amount,) = conn.execute(
            "SELECT discount_amount FROM sale_line_items"
        ).fetchone()
        assert discount_amount == 0
    finally:
        conn.close()


def test_sale_line_items_table_when_quantity_not_positive_raises_integrity_error(
    tmp_path: Path,
) -> None:
    conn = _connect_without_fk_enforcement(tmp_path / "data.db")
    try:
        conn.execute(
            "INSERT INTO products (name, price, gst_rate) VALUES (?, ?, ?)",
            ("Rice 1kg", 60, 5),
        )
        conn.execute(
            "INSERT INTO sales (payment_status, subtotal, gst_total, "
            "bill_total) VALUES ('paid', 60, 3, 63)"
        )
        conn.commit()
        product_id = conn.execute("SELECT id FROM products").fetchone()[0]
        sale_id = conn.execute("SELECT id FROM sales").fetchone()[0]

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO sale_line_items (sale_id, product_id, quantity, "
                "unit_price, gst_rate, line_gst_amount, line_total) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sale_id, product_id, 0, 60, 5, 3, 63),
            )
    finally:
        conn.close()


def test_sale_line_items_table_when_unit_price_negative_raises_integrity_error(
    tmp_path: Path,
) -> None:
    conn = _connect_without_fk_enforcement(tmp_path / "data.db")
    try:
        conn.execute(
            "INSERT INTO products (name, price, gst_rate) VALUES (?, ?, ?)",
            ("Rice 1kg", 60, 5),
        )
        conn.execute(
            "INSERT INTO sales (payment_status, subtotal, gst_total, "
            "bill_total) VALUES ('paid', 60, 3, 63)"
        )
        conn.commit()
        product_id = conn.execute("SELECT id FROM products").fetchone()[0]
        sale_id = conn.execute("SELECT id FROM sales").fetchone()[0]

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO sale_line_items (sale_id, product_id, quantity, "
                "unit_price, gst_rate, line_gst_amount, line_total) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sale_id, product_id, 1, -1, 5, 3, 63),
            )
    finally:
        conn.close()


def test_sale_line_items_table_when_gst_rate_negative_raises_integrity_error(
    tmp_path: Path,
) -> None:
    conn = _connect_without_fk_enforcement(tmp_path / "data.db")
    try:
        conn.execute(
            "INSERT INTO products (name, price, gst_rate) VALUES (?, ?, ?)",
            ("Rice 1kg", 60, 5),
        )
        conn.execute(
            "INSERT INTO sales (payment_status, subtotal, gst_total, "
            "bill_total) VALUES ('paid', 60, 3, 63)"
        )
        conn.commit()
        product_id = conn.execute("SELECT id FROM products").fetchone()[0]
        sale_id = conn.execute("SELECT id FROM sales").fetchone()[0]

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO sale_line_items (sale_id, product_id, quantity, "
                "unit_price, gst_rate, line_gst_amount, line_total) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sale_id, product_id, 1, 60, -5, 3, 63),
            )
    finally:
        conn.close()


def test_sale_line_items_table_when_discount_amount_negative_raises_integrity_error(
    tmp_path: Path,
) -> None:
    conn = _connect_without_fk_enforcement(tmp_path / "data.db")
    try:
        conn.execute(
            "INSERT INTO products (name, price, gst_rate) VALUES (?, ?, ?)",
            ("Rice 1kg", 60, 5),
        )
        conn.execute(
            "INSERT INTO sales (payment_status, subtotal, gst_total, "
            "bill_total) VALUES ('paid', 60, 3, 63)"
        )
        conn.commit()
        product_id = conn.execute("SELECT id FROM products").fetchone()[0]
        sale_id = conn.execute("SELECT id FROM sales").fetchone()[0]

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO sale_line_items (sale_id, product_id, quantity, "
                "unit_price, gst_rate, discount_amount, line_gst_amount, "
                "line_total) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (sale_id, product_id, 1, 60, 5, -1, 3, 63),
            )
    finally:
        conn.close()


def test_sale_line_items_table_when_line_gst_amount_negative_raises_integrity_error(
    tmp_path: Path,
) -> None:
    conn = _connect_without_fk_enforcement(tmp_path / "data.db")
    try:
        conn.execute(
            "INSERT INTO products (name, price, gst_rate) VALUES (?, ?, ?)",
            ("Rice 1kg", 60, 5),
        )
        conn.execute(
            "INSERT INTO sales (payment_status, subtotal, gst_total, "
            "bill_total) VALUES ('paid', 60, 3, 63)"
        )
        conn.commit()
        product_id = conn.execute("SELECT id FROM products").fetchone()[0]
        sale_id = conn.execute("SELECT id FROM sales").fetchone()[0]

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO sale_line_items (sale_id, product_id, quantity, "
                "unit_price, gst_rate, line_gst_amount, line_total) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sale_id, product_id, 1, 60, 5, -1, 63),
            )
    finally:
        conn.close()


def test_sale_line_items_table_when_line_total_negative_raises_integrity_error(
    tmp_path: Path,
) -> None:
    conn = _connect_without_fk_enforcement(tmp_path / "data.db")
    try:
        conn.execute(
            "INSERT INTO products (name, price, gst_rate) VALUES (?, ?, ?)",
            ("Rice 1kg", 60, 5),
        )
        conn.execute(
            "INSERT INTO sales (payment_status, subtotal, gst_total, "
            "bill_total) VALUES ('paid', 60, 3, 63)"
        )
        conn.commit()
        product_id = conn.execute("SELECT id FROM products").fetchone()[0]
        sale_id = conn.execute("SELECT id FROM sales").fetchone()[0]

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO sale_line_items (sale_id, product_id, quantity, "
                "unit_price, gst_rate, line_gst_amount, line_total) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sale_id, product_id, 1, 60, 5, 3, -1),
            )
    finally:
        conn.close()


def test_sale_line_items_table_when_product_id_does_not_exist_raises_integrity_error(
    tmp_path: Path,
) -> None:
    """Uses the real connect() (FK enforcement ON) — product_id/sale_id
    referential integrity against products/sales (both already exist) is
    independent of the customers-table gap documented on
    _connect_without_fk_enforcement above.
    """
    conn = connect(tmp_path / "data.db")
    try:
        init_schema(conn)

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO sale_line_items (sale_id, product_id, quantity, "
                "unit_price, gst_rate, line_gst_amount, line_total) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (999, 999, 1, 60, 5, 3, 63),
            )
    finally:
        conn.close()


def test_sale_line_items_table_when_sale_id_does_not_exist_raises_integrity_error(
    tmp_path: Path,
) -> None:
    """Real connect() (FK enforcement ON) — a valid product_id with a
    nonexistent sale_id isolates the sale_id FK check specifically.
    """
    conn = connect(tmp_path / "data.db")
    try:
        init_schema(conn)
        conn.execute(
            "INSERT INTO products (name, price, gst_rate) VALUES (?, ?, ?)",
            ("Rice 1kg", 60, 5),
        )
        conn.commit()
        product_id = conn.execute("SELECT id FROM products").fetchone()[0]

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO sale_line_items (sale_id, product_id, quantity, "
                "unit_price, gst_rate, line_gst_amount, line_total) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (999, product_id, 1, 60, 5, 3, 63),
            )
    finally:
        conn.close()


# --- boundary / invalid input / error propagation: customers (ADR-006) -----


def test_customers_table_when_valid_data_inserts_successfully(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        init_schema(conn)

        conn.execute(
            "INSERT INTO customers (name, phone, address) VALUES (?, ?, ?)",
            ("Ravi Kumar", "9876543210", "12 MG Road"),
        )
        conn.commit()

        row = conn.execute("SELECT name, phone, address FROM customers").fetchone()
        assert row == ("Ravi Kumar", "9876543210", "12 MG Road")
    finally:
        conn.close()


def test_customers_table_when_address_omitted_is_accepted(tmp_path: Path) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        init_schema(conn)

        conn.execute(
            "INSERT INTO customers (name, phone) VALUES (?, ?)",
            ("Ravi Kumar", "9876543210"),
        )
        conn.commit()

        (address,) = conn.execute("SELECT address FROM customers").fetchone()
        assert address is None
    finally:
        conn.close()


def test_customers_table_when_phone_duplicate_raises_integrity_error(
    tmp_path: Path,
) -> None:
    """`phone` is the uniqueness key per ADR-006 Decision (a)."""
    conn = connect(tmp_path / "data.db")
    try:
        init_schema(conn)
        conn.execute(
            "INSERT INTO customers (name, phone) VALUES (?, ?)",
            ("Ravi Kumar", "9876543210"),
        )
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO customers (name, phone) VALUES (?, ?)",
                ("Different Person, Same Number", "9876543210"),
            )
    finally:
        conn.close()


def test_customers_table_when_phone_missing_raises_integrity_error(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        init_schema(conn)

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO customers (name) VALUES (?)", ("Ravi Kumar",))
    finally:
        conn.close()


def test_customers_table_when_name_missing_raises_integrity_error(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        init_schema(conn)

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO customers (phone) VALUES (?)", ("9876543210",))
    finally:
        conn.close()


def test_customers_table_when_called_populates_created_at_and_updated_at(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        init_schema(conn)

        conn.execute(
            "INSERT INTO customers (name, phone) VALUES (?, ?)",
            ("Ravi Kumar", "9876543210"),
        )
        conn.commit()

        (created_at, updated_at) = conn.execute(
            "SELECT created_at, updated_at FROM customers"
        ).fetchone()
        assert created_at
        assert updated_at
    finally:
        conn.close()


# --- boundary / invalid input / error propagation: credit_transactions -----
# (ADR-006 Decision (b) — ledger model)


def _insert_customer(conn: sqlite3.Connection, phone: str = "9876543210") -> int:
    conn.execute(
        "INSERT INTO customers (name, phone) VALUES (?, ?)", ("Ravi Kumar", phone)
    )
    conn.commit()
    result = conn.execute("SELECT id FROM customers WHERE phone = ?", (phone,))
    (customer_id,) = result.fetchone()
    return int(customer_id)


def test_credit_transactions_table_when_credit_sale_valid_inserts_successfully(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        init_schema(conn)
        customer_id = _insert_customer(conn)

        conn.execute(
            "INSERT INTO credit_transactions (customer_id, transaction_type, "
            "amount) VALUES (?, ?, ?)",
            (customer_id, "credit_sale", 500),
        )
        conn.commit()

        row = conn.execute(
            "SELECT customer_id, sale_id, transaction_type, amount "
            "FROM credit_transactions"
        ).fetchone()
        assert row == (customer_id, None, "credit_sale", 500)
    finally:
        conn.close()


def test_credit_transactions_table_when_payment_linked_to_sale_inserts_successfully(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        init_schema(conn)
        customer_id = _insert_customer(conn)
        conn.execute(
            "INSERT INTO sales (customer_id, payment_status, subtotal, "
            "gst_total, bill_total) VALUES (?, 'credit', 500, 25, 525)",
            (customer_id,),
        )
        conn.commit()
        sale_id = conn.execute("SELECT id FROM sales").fetchone()[0]

        conn.execute(
            "INSERT INTO credit_transactions (customer_id, sale_id, "
            "transaction_type, amount) VALUES (?, ?, ?, ?)",
            (customer_id, sale_id, "credit_sale", 525),
        )
        conn.commit()

        row = conn.execute(
            "SELECT customer_id, sale_id, transaction_type, amount "
            "FROM credit_transactions"
        ).fetchone()
        assert row == (customer_id, sale_id, "credit_sale", 525)
    finally:
        conn.close()


def test_credit_transactions_table_when_payment_type_valid_inserts_successfully(
    tmp_path: Path,
) -> None:
    """A standalone `payment` row (not tied to any specific sale) has
    `sale_id = NULL` per ADR-006 Decision (b).
    """
    conn = connect(tmp_path / "data.db")
    try:
        init_schema(conn)
        customer_id = _insert_customer(conn)

        conn.execute(
            "INSERT INTO credit_transactions (customer_id, transaction_type, "
            "amount) VALUES (?, ?, ?)",
            (customer_id, "payment", 200),
        )
        conn.commit()

        row = conn.execute(
            "SELECT sale_id, transaction_type, amount FROM credit_transactions"
        ).fetchone()
        assert row == (None, "payment", 200)
    finally:
        conn.close()


def test_credit_transactions_table_when_transaction_type_invalid_raises_integrity_error(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        init_schema(conn)
        customer_id = _insert_customer(conn)

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO credit_transactions (customer_id, "
                "transaction_type, amount) VALUES (?, ?, ?)",
                (customer_id, "refund", 100),
            )
    finally:
        conn.close()


def test_credit_transactions_table_when_amount_zero_raises_integrity_error(
    tmp_path: Path,
) -> None:
    """`amount > 0` per ADR-006 Decision (b) — every ledger row must carry a
    positive amount; `transaction_type` determines its effect on the
    balance.
    """
    conn = connect(tmp_path / "data.db")
    try:
        init_schema(conn)
        customer_id = _insert_customer(conn)

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO credit_transactions (customer_id, "
                "transaction_type, amount) VALUES (?, ?, ?)",
                (customer_id, "credit_sale", 0),
            )
    finally:
        conn.close()


def test_credit_transactions_table_when_amount_negative_raises_integrity_error(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        init_schema(conn)
        customer_id = _insert_customer(conn)

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO credit_transactions (customer_id, "
                "transaction_type, amount) VALUES (?, ?, ?)",
                (customer_id, "payment", -50),
            )
    finally:
        conn.close()


def test_credit_transactions_table_when_customer_id_missing_row_raises_integrity_error(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        init_schema(conn)

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO credit_transactions (customer_id, "
                "transaction_type, amount) VALUES (?, ?, ?)",
                (999, "credit_sale", 100),
            )
    finally:
        conn.close()


def test_credit_transactions_table_when_sale_id_does_not_exist_raises_integrity_error(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        init_schema(conn)
        customer_id = _insert_customer(conn)

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO credit_transactions (customer_id, sale_id, "
                "transaction_type, amount) VALUES (?, ?, ?, ?)",
                (customer_id, 999, "credit_sale", 100),
            )
    finally:
        conn.close()


def test_credit_transactions_table_when_customer_id_missing_raises_integrity_error(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "data.db")
    try:
        init_schema(conn)

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO credit_transactions (transaction_type, amount) "
                "VALUES (?, ?)",
                ("credit_sale", 100),
            )
    finally:
        conn.close()
