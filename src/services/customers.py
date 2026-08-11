"""Customer credit (khata) tracking service (P2-F2-T3).

Implements customer CRUD, outstanding-balance derivation, and credit
ledger recording against the `customers`/`credit_transactions` tables
(created in P2-F2-T2 — `src/db/migrations/0004_up.sql` /
`src/db/schema.py`), per `ADR-006`'s schema and balance-derivation
decisions.

The outstanding balance is never stored — `get_outstanding_balance()` is
the single owner of the ledger-aggregate derivation (`ADR-006` Decision
(b)); no other module may cache or re-derive it. `record_credit_sale()`
and `record_payment()` accept an optional `commit: bool = True` parameter
(`ADR-006` Decision (c) / `ADR-005` Decision (c)) so Phase 2's sale-commit
orchestrator (`src/services/sales.py`, `P2-F1-T4`/`P2-F2-T4`) can compose a
credit-balance insert into its own single atomic transaction, while
preserving today's simple commit-per-call behaviour for any standalone
caller.

No raw `customer.name`/`.phone`/`.address` field may reach a
`logger.*()` call anywhere in this module (`ADR-006` Decision (d),
ARCH-D01, SD-004, `.claude/hooks/stop.sh` gate 10) — this module does not
log at all, matching the existing `catalog.py`/`inventory.py` convention
of surfacing sqlite3 I/O failures as domain exceptions without a logging
step.

No UI code here — `src/ui/customers.py` (P2-F2-T5) is the only consumer of
this module's public functions and exceptions.
"""

import sqlite3
from dataclasses import dataclass

from src.exceptions import (
    CustomerNotFoundError,
    CustomerValidationError,
    PaymentExceedsBalanceError,
)


@dataclass(frozen=True)
class Customer:
    id: int
    name: str
    phone: str
    address: str | None = None


@dataclass(frozen=True)
class CreditTransaction:
    """One `credit_transactions` ledger row (ADR-006 Decision (b)).

    `sale_id` is `None` for a standalone payment (not tied to any
    specific sale) and a specific sale id for a `credit_sale` row created
    by the sale-commit transaction. `created_at` defaults to `""` for a
    freshly-`record_credit_sale()`/`record_payment()`-committed row (the
    caller already has the commit-time context; those functions do not
    re-query it) and is always populated for rows returned by
    `list_credit_transactions()` (phase2-feature3-cross-cutting-closure,
    Action 8) — mirrors `src/services/sales.py`'s `Sale.created_at`
    convention exactly.
    """

    id: int
    customer_id: int
    sale_id: int | None
    transaction_type: str
    amount: float
    created_at: str = ""


def _validate_name(name: str) -> None:
    if not name or not name.strip():
        raise CustomerValidationError("customer name must not be empty")


def _validate_phone(phone: str) -> None:
    if not phone or not phone.strip():
        raise CustomerValidationError("customer phone must not be empty")


def _validate_amount(amount: float) -> None:
    if amount <= 0:
        raise CustomerValidationError(f"amount {amount!r} must be positive")


def _validate_customer_exists(conn: sqlite3.Connection, customer_id: int) -> None:
    row = conn.execute(
        "SELECT 1 FROM customers WHERE id = ?", (customer_id,)
    ).fetchone()
    if row is None:
        raise CustomerNotFoundError(f"customer {customer_id} does not exist")


def create_customer(
    conn: sqlite3.Connection, name: str, phone: str, address: str | None = None
) -> Customer:
    """Register a new khata customer.

    Raises `CustomerValidationError` for an empty name/phone, or when
    `phone` collides with an existing customer's phone number (the
    uniqueness key per `ADR-006` Decision (a)).
    """
    _validate_name(name)
    _validate_phone(phone)
    try:
        cursor = conn.execute(
            "INSERT INTO customers (name, phone, address) VALUES (?, ?, ?)",
            (name, phone, address),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise CustomerValidationError(
            f"customer with phone {phone!r} already exists"
        ) from exc
    customer_id = cursor.lastrowid
    assert customer_id is not None
    return Customer(id=customer_id, name=name, phone=phone, address=address)


def update_customer(
    conn: sqlite3.Connection,
    customer_id: int,
    name: str,
    phone: str,
    address: str | None = None,
) -> Customer:
    """Edit an existing customer's name/phone/address.

    Raises `CustomerNotFoundError` when `customer_id` does not exist,
    `CustomerValidationError` on invalid field data or a phone collision
    with a different customer.
    """
    _validate_name(name)
    _validate_phone(phone)
    try:
        cursor = conn.execute(
            "UPDATE customers SET name = ?, phone = ?, address = ?, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
            (name, phone, address, customer_id),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise CustomerValidationError(
            f"customer {customer_id} failed database constraints "
            f"(phone {phone!r} may already be in use by another customer)"
        ) from exc
    if cursor.rowcount == 0:
        raise CustomerNotFoundError(f"customer {customer_id} does not exist")
    return Customer(id=customer_id, name=name, phone=phone, address=address)


def get_customer(conn: sqlite3.Connection, customer_id: int) -> Customer:
    """Fetch a single customer by id. Raises `CustomerNotFoundError` if absent."""
    row = conn.execute(
        "SELECT id, name, phone, address FROM customers WHERE id = ?",
        (customer_id,),
    ).fetchone()
    if row is None:
        raise CustomerNotFoundError(f"customer {customer_id} does not exist")
    return Customer(id=row[0], name=row[1], phone=row[2], address=row[3])


def list_customers(conn: sqlite3.Connection) -> list[Customer]:
    """Return every customer, ordered by name."""
    rows = conn.execute(
        "SELECT id, name, phone, address FROM customers ORDER BY name"
    ).fetchall()
    return [
        Customer(id=row[0], name=row[1], phone=row[2], address=row[3]) for row in rows
    ]


def get_outstanding_balance(conn: sqlite3.Connection, customer_id: int) -> float:
    """Derive a customer's outstanding khata balance from the ledger.

    Implements `ADR-006` Decision (b)'s aggregate formula in a single SQL
    query — `SUM(credit_sale amounts) - SUM(payment amounts)` for this
    customer. No balance value is stored or cached anywhere; this
    function is the single owner of the derivation — any future code
    needing a customer's balance must call this rather than re-deriving
    it. Raises `CustomerNotFoundError` if the customer does not exist.
    """
    _validate_customer_exists(conn, customer_id)
    row = conn.execute(
        "SELECT "
        "COALESCE(SUM(CASE WHEN transaction_type = 'credit_sale' "
        "THEN amount END), 0) - "
        "COALESCE(SUM(CASE WHEN transaction_type = 'payment' "
        "THEN amount END), 0) "
        "FROM credit_transactions WHERE customer_id = ?",
        (customer_id,),
    ).fetchone()
    return float(row[0])


def list_credit_transactions(
    conn: sqlite3.Connection, customer_id: int, limit: int = 100
) -> list[CreditTransaction]:
    """Return this customer's `credit_transactions` ledger rows
    (`credit_sale` and `payment`), most-recent-first, capped at `limit`
    (phase2-feature3-cross-cutting-closure, Action 8) — the
    transaction-level detail `get_outstanding_balance()`'s aggregate does
    not expose; no other function lists individual ledger rows. Mirrors
    `src/services/sales.py::list_sales()`'s query shape (same
    `created_at DESC, id DESC` deterministic tie-break for same-millisecond
    rows). Raises `CustomerNotFoundError` if the customer does not exist,
    mirroring `get_outstanding_balance()`'s validation.
    """
    _validate_customer_exists(conn, customer_id)
    rows = conn.execute(
        "SELECT id, customer_id, sale_id, transaction_type, amount, created_at "
        "FROM credit_transactions WHERE customer_id = ? "
        "ORDER BY created_at DESC, id DESC LIMIT ?",
        (customer_id, limit),
    ).fetchall()
    return [
        CreditTransaction(
            id=row[0],
            customer_id=row[1],
            sale_id=row[2],
            transaction_type=row[3],
            amount=row[4],
            created_at=row[5],
        )
        for row in rows
    ]


def record_credit_sale(
    conn: sqlite3.Connection,
    customer_id: int,
    sale_id: int,
    amount: float,
    commit: bool = True,
) -> CreditTransaction:
    """Record a `credit_sale` ledger row, increasing the customer's
    outstanding balance by `amount`.

    Called by `src/services/sales.py`'s sale-commit orchestrator
    (`ADR-005` Decision (c)) when `payment_status = 'credit'`, composed
    with `commit=False` into that orchestrator's single atomic
    transaction (`ADR-006` Decision (c)); defaults to committing
    immediately for any standalone caller. Raises `CustomerNotFoundError`
    if the customer does not exist, `CustomerValidationError` for a
    non-positive `amount` or if `sale_id` does not reference an existing
    sale.
    """
    _validate_customer_exists(conn, customer_id)
    _validate_amount(amount)
    try:
        cursor = conn.execute(
            "INSERT INTO credit_transactions "
            "(customer_id, sale_id, transaction_type, amount) "
            "VALUES (?, ?, 'credit_sale', ?)",
            (customer_id, sale_id, amount),
        )
        if commit:
            conn.commit()
    except sqlite3.IntegrityError as exc:
        raise CustomerValidationError(
            f"credit sale for customer {customer_id} failed database "
            f"constraints (sale {sale_id} may not exist)"
        ) from exc
    transaction_id = cursor.lastrowid
    assert transaction_id is not None
    return CreditTransaction(
        id=transaction_id,
        customer_id=customer_id,
        sale_id=sale_id,
        transaction_type="credit_sale",
        amount=amount,
    )


def record_payment(
    conn: sqlite3.Connection,
    customer_id: int,
    amount: float,
    commit: bool = True,
) -> CreditTransaction:
    """Record a `payment` ledger row, decreasing the customer's outstanding
    balance by `amount`. Not tied to any specific sale (`sale_id = NULL`
    per `ADR-006` Decision (b)) — a standalone payment against the
    customer's overall balance, e.g. from a dedicated "record payment"
    screen rather than a sale-commit transaction.

    Raises `CustomerNotFoundError` if the customer does not exist,
    `CustomerValidationError` for a non-positive `amount` (validated here
    before any insert is attempted — the DB's `CHECK (amount > 0)`
    constraint is a backstop, not the first line of defense, per
    `development-process.md`'s "no raw library exceptions at the
    boundary" rule), and `PaymentExceedsBalanceError` if `amount` exceeds
    the customer's current outstanding balance.

    Design decision (ADR-006 does not explicitly forbid overpayment): a
    payment that would take the outstanding balance negative is rejected
    rather than silently accepted. This khata ledger models a customer's
    debt to the shop, not a shop credit/store-wallet the customer can draw
    down later — no such flow exists anywhere in `ARCH-F04`'s narrative
    khata description — so accepting an overpayment would create an
    unexplained negative balance with no corresponding feature to spend or
    refund it.
    """
    _validate_customer_exists(conn, customer_id)
    _validate_amount(amount)
    outstanding = get_outstanding_balance(conn, customer_id)
    if amount > outstanding:
        raise PaymentExceedsBalanceError(
            f"payment amount {amount!r} exceeds outstanding balance "
            f"{outstanding!r} for customer {customer_id}"
        )
    try:
        cursor = conn.execute(
            "INSERT INTO credit_transactions "
            "(customer_id, sale_id, transaction_type, amount) "
            "VALUES (?, NULL, 'payment', ?)",
            (customer_id, amount),
        )
        if commit:
            conn.commit()
    except sqlite3.IntegrityError as exc:
        raise CustomerValidationError(
            f"payment for customer {customer_id} failed database constraints"
        ) from exc
    transaction_id = cursor.lastrowid
    assert transaction_id is not None
    return CreditTransaction(
        id=transaction_id,
        customer_id=customer_id,
        sale_id=None,
        transaction_type="payment",
        amount=amount,
    )
