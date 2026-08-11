# Seed data

`demo.db` is a pre-populated SQLite database (customers, products, sales,
sale line items, inventory stock, discounts, credit transactions) used for
demos and onboarding, so a new install doesn't start from an empty shop.

## Usage

Point the app at it via `SMB_DB_PATH` (see `src/config.py`) instead of the
default `~/.smb-accounting/data.db`:

```bash
SMB_DB_PATH=data/seed/demo.db poetry run smb-accounting
```

This runs the app directly against the seed file — mutations during the
session write to it. To keep it pristine, copy it first:

```bash
cp data/seed/demo.db /tmp/demo-session.db
SMB_DB_PATH=/tmp/demo-session.db poetry run smb-accounting
```
