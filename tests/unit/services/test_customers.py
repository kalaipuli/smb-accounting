"""Unit tests for src/services/customers.py (P2-F2-T3).

Covers customer CRUD (create/update/get/list), outstanding-balance
derivation (ADR-006 Decision (b) ledger aggregate across multiple
`credit_sale`/`payment` rows), credit-sale/payment recording (including
the `commit: bool = True` convention, ADR-006 Decision (c)), and
error-path coverage for the sqlite3 I/O boundary (duplicate phone,
missing customer, invalid amount, overpayment — all surfaced as domain
exceptions, never a raw `sqlite3.IntegrityError`).
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from src.db.schema import connect, init_schema
from src.exceptions import (
    CustomerNotFoundError,
    CustomerValidationError,
    PaymentExceedsBalanceError,
)
from src.services.customers import (
    CreditTransaction,
    Customer,
    create_customer,
    get_customer,
    get_outstanding_balance,
    list_credit_transactions,
    list_customers,
    record_credit_sale,
    record_payment,
    update_customer,
)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(tmp_path / "data.db")
    init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def customer_id(conn: sqlite3.Connection) -> int:
    customer = create_customer(conn, name="Ravi Kumar", phone="9990001111")
    return customer.id


def _make_sale_row(
    conn: sqlite3.Connection, customer_id: int, bill_total: float
) -> int:
    """Insert a minimal `sales` row directly — this test module does not
    exercise `src/services/sales.py`'s orchestrator (that is action 5's
    scope), only `record_credit_sale()`'s FK relationship to an existing
    sale row."""
    cursor = conn.execute(
        "INSERT INTO sales (customer_id, payment_status, subtotal, gst_total, "
        "bill_total) VALUES (?, 'credit', ?, 0, ?)",
        (customer_id, bill_total, bill_total),
    )
    conn.commit()
    sale_id = cursor.lastrowid
    assert sale_id is not None
    return sale_id


# --- create_customer -----------------------------------------------------------


def test_create_customer_when_valid_returns_persisted_customer(
    conn: sqlite3.Connection,
) -> None:
    customer = create_customer(
        conn, name="Ravi Kumar", phone="9990001111", address="MG Road"
    )

    assert customer == Customer(
        id=customer.id, name="Ravi Kumar", phone="9990001111", address="MG Road"
    )
    row = conn.execute(
        "SELECT name, phone, address FROM customers WHERE id = ?", (customer.id,)
    ).fetchone()
    assert row == ("Ravi Kumar", "9990001111", "MG Road")


def test_create_customer_when_address_omitted_defaults_to_none(
    conn: sqlite3.Connection,
) -> None:
    customer = create_customer(conn, name="Ravi Kumar", phone="9990001111")

    assert customer.address is None


def test_create_customer_when_name_empty_raises_customer_validation_error(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(CustomerValidationError):
        create_customer(conn, name="  ", phone="9990001111")


def test_create_customer_when_phone_empty_raises_customer_validation_error(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(CustomerValidationError):
        create_customer(conn, name="Ravi Kumar", phone="")


def test_create_customer_when_phone_duplicate_raises_customer_validation_error(
    conn: sqlite3.Connection,
) -> None:
    create_customer(conn, name="Ravi Kumar", phone="9990001111")

    with pytest.raises(CustomerValidationError):
        create_customer(conn, name="Another Person", phone="9990001111")


# --- update_customer -------------------------------------------------------------


def test_update_customer_when_customer_exists_updates_all_fields(
    conn: sqlite3.Connection, customer_id: int
) -> None:
    updated = update_customer(
        conn, customer_id, name="Ravi K.", phone="9990002222", address="New Address"
    )

    assert updated == Customer(
        id=customer_id, name="Ravi K.", phone="9990002222", address="New Address"
    )
    assert get_customer(conn, customer_id) == updated


def test_update_customer_when_customer_missing_raises_customer_not_found_error(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(CustomerNotFoundError):
        update_customer(conn, 999, name="Ghost", phone="9990009999")


def test_update_customer_when_phone_collides_raises_customer_validation_error(
    conn: sqlite3.Connection, customer_id: int
) -> None:
    create_customer(conn, name="Other Person", phone="9990002222")

    with pytest.raises(CustomerValidationError):
        update_customer(conn, customer_id, name="Ravi Kumar", phone="9990002222")


# --- get_customer / list_customers -------------------------------------------------


def test_get_customer_when_customer_exists_returns_it(
    conn: sqlite3.Connection, customer_id: int
) -> None:
    assert get_customer(conn, customer_id) == Customer(
        id=customer_id, name="Ravi Kumar", phone="9990001111", address=None
    )


def test_get_customer_when_customer_missing_raises_customer_not_found_error(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(CustomerNotFoundError):
        get_customer(conn, 999)


def test_list_customers_when_none_returns_empty_list(conn: sqlite3.Connection) -> None:
    assert list_customers(conn) == []


def test_list_customers_when_multiple_returns_them_ordered_by_name(
    conn: sqlite3.Connection,
) -> None:
    create_customer(conn, name="Zara", phone="9990001111")
    create_customer(conn, name="Amit", phone="9990002222")

    names = [customer.name for customer in list_customers(conn)]

    assert names == ["Amit", "Zara"]


# --- get_outstanding_balance -----------------------------------------------------


def test_get_outstanding_balance_when_no_transactions_returns_zero(
    conn: sqlite3.Connection, customer_id: int
) -> None:
    assert get_outstanding_balance(conn, customer_id) == 0


def test_get_outstanding_balance_aggregates_credit_sales_and_payments(
    conn: sqlite3.Connection, customer_id: int
) -> None:
    sale_id = _make_sale_row(conn, customer_id, 500)
    record_credit_sale(conn, customer_id, sale_id, 500)
    record_payment(conn, customer_id, 200)
    sale_id_2 = _make_sale_row(conn, customer_id, 100)
    record_credit_sale(conn, customer_id, sale_id_2, 100)

    # 500 + 100 (credit sales) - 200 (payment) = 400
    assert get_outstanding_balance(conn, customer_id) == 400


def test_get_outstanding_balance_when_fully_paid_returns_zero(
    conn: sqlite3.Connection, customer_id: int
) -> None:
    sale_id = _make_sale_row(conn, customer_id, 300)
    record_credit_sale(conn, customer_id, sale_id, 300)
    record_payment(conn, customer_id, 300)

    assert get_outstanding_balance(conn, customer_id) == 0


def test_get_outstanding_balance_when_customer_missing_raises_customer_not_found_error(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(CustomerNotFoundError):
        get_outstanding_balance(conn, 999)


def test_get_outstanding_balance_is_scoped_per_customer(
    conn: sqlite3.Connection, customer_id: int
) -> None:
    """A second customer's ledger rows must not leak into the first
    customer's derived balance — the WHERE customer_id = ? clause is the
    only thing preventing cross-customer aggregation."""
    other = create_customer(conn, name="Sunita Rao", phone="9990003333")
    sale_id = _make_sale_row(conn, customer_id, 400)
    record_credit_sale(conn, customer_id, sale_id, 400)
    other_sale_id = _make_sale_row(conn, other.id, 900)
    record_credit_sale(conn, other.id, other_sale_id, 900)
    record_payment(conn, other.id, 300)

    assert get_outstanding_balance(conn, customer_id) == 400
    assert get_outstanding_balance(conn, other.id) == 600


# --- record_credit_sale -----------------------------------------------------------


def test_record_credit_sale_when_valid_inserts_ledger_row_and_commits(
    conn: sqlite3.Connection, customer_id: int
) -> None:
    sale_id = _make_sale_row(conn, customer_id, 250)

    transaction = record_credit_sale(conn, customer_id, sale_id, 250)

    assert transaction == CreditTransaction(
        id=transaction.id,
        customer_id=customer_id,
        sale_id=sale_id,
        transaction_type="credit_sale",
        amount=250,
    )
    row = conn.execute(
        "SELECT customer_id, sale_id, transaction_type, amount "
        "FROM credit_transactions WHERE id = ?",
        (transaction.id,),
    ).fetchone()
    assert row == (customer_id, sale_id, "credit_sale", 250)


def test_record_credit_sale_when_commit_false_does_not_commit(
    conn: sqlite3.Connection, customer_id: int
) -> None:
    sale_id = _make_sale_row(conn, customer_id, 250)

    record_credit_sale(conn, customer_id, sale_id, 250, commit=False)
    conn.rollback()

    assert get_outstanding_balance(conn, customer_id) == 0


def test_record_credit_sale_when_customer_missing_raises_customer_not_found_error(
    conn: sqlite3.Connection, customer_id: int
) -> None:
    sale_id = _make_sale_row(conn, customer_id, 250)

    with pytest.raises(CustomerNotFoundError):
        record_credit_sale(conn, 999, sale_id, 250)


def test_record_credit_sale_when_amount_not_positive_raises_customer_validation_error(
    conn: sqlite3.Connection, customer_id: int
) -> None:
    sale_id = _make_sale_row(conn, customer_id, 250)

    with pytest.raises(CustomerValidationError):
        record_credit_sale(conn, customer_id, sale_id, 0)


def test_record_credit_sale_when_sale_missing_raises_customer_validation_error(
    conn: sqlite3.Connection, customer_id: int
) -> None:
    with pytest.raises(CustomerValidationError):
        record_credit_sale(conn, customer_id, 999, 250)


# --- record_payment ---------------------------------------------------------------


def test_record_payment_when_valid_inserts_ledger_row_with_null_sale_id(
    conn: sqlite3.Connection, customer_id: int
) -> None:
    sale_id = _make_sale_row(conn, customer_id, 250)
    record_credit_sale(conn, customer_id, sale_id, 250)

    transaction = record_payment(conn, customer_id, 100)

    assert transaction == CreditTransaction(
        id=transaction.id,
        customer_id=customer_id,
        sale_id=None,
        transaction_type="payment",
        amount=100,
    )
    row = conn.execute(
        "SELECT sale_id FROM credit_transactions WHERE id = ?", (transaction.id,)
    ).fetchone()
    assert row == (None,)


def test_record_payment_when_commit_false_does_not_commit(
    conn: sqlite3.Connection, customer_id: int
) -> None:
    sale_id = _make_sale_row(conn, customer_id, 250)
    record_credit_sale(conn, customer_id, sale_id, 250)

    record_payment(conn, customer_id, 100, commit=False)
    conn.rollback()

    assert get_outstanding_balance(conn, customer_id) == 250


def test_record_payment_when_customer_missing_raises_customer_not_found_error(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(CustomerNotFoundError):
        record_payment(conn, 999, 100)


def test_record_payment_when_amount_zero_raises_customer_validation_error(
    conn: sqlite3.Connection, customer_id: int
) -> None:
    with pytest.raises(CustomerValidationError):
        record_payment(conn, customer_id, 0)


def test_record_payment_when_amount_negative_raises_customer_validation_error(
    conn: sqlite3.Connection, customer_id: int
) -> None:
    with pytest.raises(CustomerValidationError):
        record_payment(conn, customer_id, -50)


def test_record_payment_when_amount_exceeds_balance_raises_exceeds_error(
    conn: sqlite3.Connection, customer_id: int
) -> None:
    sale_id = _make_sale_row(conn, customer_id, 250)
    record_credit_sale(conn, customer_id, sale_id, 250)

    with pytest.raises(PaymentExceedsBalanceError):
        record_payment(conn, customer_id, 300)


def test_record_payment_when_no_balance_raises_payment_exceeds_balance_error(
    conn: sqlite3.Connection, customer_id: int
) -> None:
    with pytest.raises(PaymentExceedsBalanceError):
        record_payment(conn, customer_id, 10)


def test_record_payment_when_amount_equals_outstanding_balance_zeroes_it_out(
    conn: sqlite3.Connection, customer_id: int
) -> None:
    sale_id = _make_sale_row(conn, customer_id, 250)
    record_credit_sale(conn, customer_id, sale_id, 250)

    record_payment(conn, customer_id, 250)

    assert get_outstanding_balance(conn, customer_id) == 0


# --- list_credit_transactions (phase2-feature3-cross-cutting-closure, Action 8) --


def test_list_credit_transactions_when_none_returns_empty_list(
    conn: sqlite3.Connection, customer_id: int
) -> None:
    assert list_credit_transactions(conn, customer_id) == []


def test_list_credit_transactions_when_multiple_returns_most_recent_first(
    conn: sqlite3.Connection, customer_id: int
) -> None:
    sale_id = _make_sale_row(conn, customer_id, 500)
    credit = record_credit_sale(conn, customer_id, sale_id, 500)
    payment = record_payment(conn, customer_id, 200)

    transactions = list_credit_transactions(conn, customer_id)

    # Deterministic `id DESC` tie-break (both rows share the same
    # millisecond-precision `created_at` under a fast in-process test) —
    # mirrors `list_sales()`'s own tie-break convention.
    assert [t.id for t in transactions] == [payment.id, credit.id]
    assert transactions[0].transaction_type == "payment"
    assert transactions[0].amount == 200
    assert transactions[1].transaction_type == "credit_sale"
    assert transactions[1].amount == 500
    assert transactions[1].sale_id == sale_id
    assert all(t.created_at for t in transactions)


def test_list_credit_transactions_is_scoped_per_customer(
    conn: sqlite3.Connection, customer_id: int
) -> None:
    other = create_customer(conn, name="Sunita Rao", phone="9990003333")
    sale_id = _make_sale_row(conn, customer_id, 400)
    record_credit_sale(conn, customer_id, sale_id, 400)
    other_sale_id = _make_sale_row(conn, other.id, 900)
    record_credit_sale(conn, other.id, other_sale_id, 900)

    transactions = list_credit_transactions(conn, customer_id)

    assert len(transactions) == 1
    assert transactions[0].customer_id == customer_id


def test_list_credit_transactions_when_customer_missing_raises_customer_not_found_error(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(CustomerNotFoundError):
        list_credit_transactions(conn, 999)


# --- PII field handling (ADR-006 Decision (d) / ARCH-D01 / SD-004) ---------------


def test_customers_module_never_logs_raw_pii_across_full_service_surface(
    conn: sqlite3.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    """Exercises every public function in `src/services/customers.py` —
    including the error paths (duplicate phone, missing customer,
    overpayment) — while capturing all log records at DEBUG level across
    every logger, then asserts that none of the customer's actual PII
    values (name/phone/address) appear in any captured record's message.
    This is a positive, test-observable check per `ADR-006` Decision (d) /
    `ARCH-D01` — it does not rely on "the module has no logging calls" as
    an informal code-review claim; it proves it by running the code with
    logging captured and inspecting the result.
    """
    caplog.set_level("DEBUG")

    name, phone, address = "Priya Sharma", "9990004444", "12 Nehru Nagar"
    customer = create_customer(conn, name=name, phone=phone, address=address)
    update_customer(conn, customer.id, name=name, phone="9990005555", address=address)
    get_customer(conn, customer.id)
    list_customers(conn)
    sale_id = _make_sale_row(conn, customer.id, 500)
    record_credit_sale(conn, customer.id, sale_id, 500)
    get_outstanding_balance(conn, customer.id)
    record_payment(conn, customer.id, 200)

    # Error paths — duplicate phone, missing customer, overpayment.
    other = create_customer(conn, name="Second Person", phone="9990006666")
    with pytest.raises(CustomerValidationError):
        create_customer(conn, name="Dup", phone="9990005555")
    with pytest.raises(CustomerNotFoundError):
        get_customer(conn, 999)
    with pytest.raises(PaymentExceedsBalanceError):
        record_payment(conn, other.id, 1)

    assert caplog.records == []
    for record in caplog.records:
        rendered = record.getMessage()
        assert name not in rendered
        assert phone not in rendered
        assert address not in rendered
