"""P3-F2-T3 integration test — real end-to-end P&L report generation
(T-10, plus the "same period requested twice" ADR-008 Decision (e)
boundary case at the database level).

Mirrors `tests/integration/test_cogs_transaction.py`'s pattern exactly:
built through the actual migration runner (`src/db/migrate.py`'s
`apply_pending_migrations()`, the same code path `src/main.py` calls at
real application startup), not `init_schema()`'s direct
`SCHEMA_STATEMENTS` execution. No database or service call is mocked
anywhere in this file — a real `sqlite3.Connection` against a real
temp-file database is used throughout, per `development-process.md` §4's
`tests/integration/` definition ("requires real running services; tests
actual end-to-end behaviour").

Covers two properties this feature's DoD requires beyond
`tests/unit/services/test_reporting.py`'s existing coverage:

1. Sales seeded through the real `record_sale()` path (against a
   migration-built schema) reconcile to the report figures the
   aggregation service returns.
2. Re-running the same period leaves the database byte-identical on
   disk — the reporting service issues no `INSERT`/`UPDATE`/`DELETE`/
   `commit()`/DDL, so a `SELECT`-only read path cannot mutate the file.
"""

import datetime as dt
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from src.config import DEFAULT_REPORTING_TZ_OFFSET_MINUTES
from src.db.migrate import MIGRATIONS_DIR, apply_pending_migrations
from src.db.schema import connect
from src.services.catalog import create_product
from src.services.inventory import set_stock
from src.services.reporting import generate_daily_report
from src.services.sales import CartLine, record_sale

IST = dt.timezone(dt.timedelta(minutes=DEFAULT_REPORTING_TZ_OFFSET_MINUTES))


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "data.db"


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(db_path)
    applied = apply_pending_migrations(connection)
    assert applied, (
        "expected at least one migration to apply against a fresh database "
        "— an empty result means the migration runner silently did nothing "
        "and this test would be exercising no real schema-bootstrap path"
    )
    yield connection
    connection.close()


def _set_created_at(
    conn: sqlite3.Connection, sale_id: int, moment_ist: dt.datetime
) -> None:
    utc = moment_ist.astimezone(dt.timezone.utc)
    ms = utc.microsecond // 1000
    text = f"{utc.strftime('%Y-%m-%dT%H:%M:%S')}.{ms:03d}Z"
    conn.execute("UPDATE sales SET created_at = ? WHERE id = ?", (text, sale_id))
    conn.commit()


def test_generate_daily_report_reconciles_to_sales_seeded_through_record_sale(
    conn: sqlite3.Connection,
) -> None:
    rice = create_product(conn, name="Rice 1kg", price=100, gst_rate=5, cost=70)
    oil = create_product(conn, name="Cooking Oil 1L", price=200, gst_rate=12, cost=150)
    set_stock(conn, rice.id, 10)
    set_stock(conn, oil.id, 10)

    sale_one = record_sale(
        conn, [CartLine(product_id=rice.id, quantity=2)], payment_status="paid"
    )
    sale_two = record_sale(
        conn, [CartLine(product_id=oil.id, quantity=1)], payment_status="paid"
    )
    _set_created_at(conn, sale_one.id, dt.datetime(2026, 6, 15, 10, 0, tzinfo=IST))
    _set_created_at(conn, sale_two.id, dt.datetime(2026, 6, 15, 15, 0, tzinfo=IST))

    report = generate_daily_report(conn, dt.date(2026, 6, 15))

    # rice: net 200, gst 10, cogs 140. oil: net 200, gst 24, cogs 150.
    assert report.revenue == pytest.approx(400)
    assert report.cogs == pytest.approx(290)
    assert report.net == pytest.approx(110)
    assert report.line_count == 2
    assert report.cost_complete is True


def test_generate_daily_report_rerun_leaves_database_byte_identical(
    conn: sqlite3.Connection, db_path: Path
) -> None:
    """ADR-008 Decision (e) — 'the same period requested twice' — and
    Decision (d)'s 'report generation is read-only... no INSERT, UPDATE,
    DELETE, commit() or DDL. Re-running the same period leaves the
    database byte-identical.' Verified at the actual file-byte level, not
    only by comparing the returned dataclass."""
    product = create_product(conn, name="Rice 1kg", price=100, gst_rate=5, cost=70)
    set_stock(conn, product.id, 10)
    sale = record_sale(
        conn, [CartLine(product_id=product.id, quantity=1)], payment_status="paid"
    )
    _set_created_at(conn, sale.id, dt.datetime(2026, 6, 15, 12, 0, tzinfo=IST))
    conn.commit()

    before_bytes = db_path.read_bytes()

    first = generate_daily_report(conn, dt.date(2026, 6, 15))
    after_first_bytes = db_path.read_bytes()
    second = generate_daily_report(conn, dt.date(2026, 6, 15))
    after_second_bytes = db_path.read_bytes()

    assert first == second
    assert after_first_bytes == before_bytes
    assert after_second_bytes == before_bytes


# --- T-9 integration half: pre-migration sale -> cost-incomplete -----------


def test_generate_daily_report_when_sale_predates_cost_migration_marks_incomplete(
    tmp_path: Path,
) -> None:
    """T-9's integration half (`docs/registry/phase3/README.md` § Test
    Strategy §1) — a fixture built by migrating a database that already
    holds a Phase-2-shaped sale, not by seeding through today's
    `record_sale()` (which always writes `unit_cost`, since that column
    exists in every schema this feature's tests otherwise build against).
    Mirrors `tests/unit/db/test_migrate.py`'s "exact shape of the user's
    real pre-existing database" pattern: migrations 0001-0004 applied by
    hand, a sale inserted directly against that pre-0005 schema (there is
    no `unit_cost` column yet to write to), then `apply_pending_migrations()`
    runs migration 0005 for the first time — the pre-existing sale line
    gets a NULL `unit_cost` it can never recover, exactly as a real
    upgraded shop's sales history would."""
    conn = connect(tmp_path / "data.db")
    try:
        for version in (1, 2, 3, 4):
            path = MIGRATIONS_DIR / f"{version:04d}_up.sql"
            conn.executescript(path.read_text())

        conn.execute(
            "INSERT INTO products (id, name, price, gst_rate) VALUES (1, ?, ?, ?)",
            ("Rice 1kg", 100, 5),
        )
        conn.execute(
            "INSERT INTO sales "
            "(id, payment_status, subtotal, gst_total, bill_total, created_at) "
            "VALUES (1, 'paid', 100, 5, 105, ?)",
            ("2026-06-15T06:30:00.000Z",),  # 12:00 IST, well inside the trading day
        )
        conn.execute(
            "INSERT INTO sale_line_items "
            "(sale_id, product_id, quantity, unit_price, gst_rate, "
            "discount_amount, line_gst_amount, line_total) "
            "VALUES (1, 1, 1, 100, 5, 0, 5, 105)"
        )
        conn.commit()

        applied = apply_pending_migrations(conn)  # must not raise
        assert applied == [1, 2, 3, 4, 5], (
            "no schema_migrations rows were stamped by the hand-applied "
            "0001-0004 scripts, so the runner re-applies (and tolerates) "
            "all five — mirrors test_migrate.py's own pre-existing-database "
            "convention; migration 0005 is what actually matters here"
        )

        report = generate_daily_report(conn, dt.date(2026, 6, 15))

        assert report.revenue == pytest.approx(100)
        assert report.line_count == 1
        assert report.lines_missing_cost == 1
        assert report.cost_complete is False
        assert report.cogs == 0.0
    finally:
        conn.close()
