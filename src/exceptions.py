"""Canonical location for domain-specific exception types.

Per `framework/technology/backend/python.md` §Error Handling and
`framework/guidelines/anti-patterns.md` (API & Integration — "Propagate raw
framework exceptions to the API surface"), no service module raises a bare
`sqlite3.IntegrityError` or `ValueError` to its caller (the curses UI layer,
which is this offline tool's API surface). Every service-layer failure is
wrapped in one of the types below.
"""


class CatalogError(Exception):
    """Base class for all product-catalog domain errors."""


class GstRateInvalidError(CatalogError):
    """Raised when a product's GST rate is not one of the valid slabs."""


class ProductValidationError(CatalogError):
    """Raised when product field data fails validation (e.g. negative price)."""


class ProductNotFoundError(CatalogError):
    """Raised when a product lookup/update targets a non-existent product id."""


class DiscountError(Exception):
    """Base class for all discount-rule domain errors."""


class DiscountValidationError(DiscountError):
    """Raised when discount field data fails validation."""


class DiscountNotFoundError(DiscountError):
    """Raised when a discount lookup/update targets a non-existent discount id."""


class DiscountExceedsPriceError(DiscountError):
    """Raised when applying a discount would reduce a price below zero."""


class InventoryError(Exception):
    """Base class for all inventory stock-tracking domain errors."""


class InventoryValidationError(InventoryError):
    """Raised when inventory field data fails validation (e.g. negative
    absolute quantity, or a non-positive decrement `qty`)."""


class InsufficientStockError(InventoryError):
    """Raised when an adjustment or decrement would take a product's stock
    below zero (the negative-stock guard)."""


class SalesError(Exception):
    """Base class for all sale-entry domain errors (ADR-005 Decision (c))."""


class SaleValidationError(SalesError):
    """Raised when cart/line-item data fails validation (e.g. an empty
    cart, a non-positive quantity, a discount that does not belong to
    the line's product, an unrecognised `payment_status`, or a `credit`
    sale with no `customer_id`)."""


class SaleNotFoundError(SalesError):
    """Raised when a sale lookup targets a non-existent sale id."""


class SaleTransactionError(SalesError):
    """Raised when any step of `record_sale()`'s atomic sale-commit
    sequence (sale insert, `sale_line_items` inserts, inventory decrement,
    and — on credit — the customer credit-balance update) fails after the
    transaction has begun writing (`ADR-005` Decision (c)). Wraps the
    original underlying error; `conn.rollback()` is always issued before
    this is raised, so no partial commit can occur."""


class CustomerError(Exception):
    """Base class for all customer/khata-credit domain errors
    (ADR-006 Decision (c))."""


class CustomerValidationError(CustomerError):
    """Raised when customer field data fails validation (e.g. an empty
    name/phone, a phone collision with an existing customer, a
    non-positive credit-transaction amount, or a `record_credit_sale()`
    call referencing a non-existent sale)."""


class CustomerNotFoundError(CustomerError):
    """Raised when a customer lookup/update/balance-read targets a
    non-existent customer id."""


class PaymentExceedsBalanceError(CustomerError):
    """Raised when `record_payment()`'s `amount` exceeds the customer's
    current outstanding balance — this ledger models a customer's debt to
    the shop, not a store-wallet credit the customer can draw down later,
    so a payment is never allowed to take the balance negative."""


class ReportingError(Exception):
    """Base class for all P&L reporting domain errors (ADR-008 Decision
    (d) — "any `sqlite3` failure inside the reporting service is wrapped
    in a domain exception declared in `src/exceptions.py`; a raw
    `sqlite3.Error` must not cross the service boundary.")."""


class ReportingValidationError(ReportingError):
    """Raised when a reporting-period parameter is invalid (e.g. a month
    outside 1-12 or a quarter outside 1-4)."""
