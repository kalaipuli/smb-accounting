---
report_type: adr
adr_number: "ADR-004"
status: Accepted
date: 2026-08-02
---

# ADR-004: Schema Migration Runner for Startup

**Date:** 2026-08-02
**Status:** Accepted

## Context

`src/db/migrations/` already holds versioned `NNNN_up.sql` / `NNNN_down.sql`
pairs (SD-003, resolved) — `0001` creates the core Phase 1 tables, `0002`
adds an `updated_at` audit column to `products`/`discounts` (architect
review Finding 1). Nothing in the application ever applied these files
against a live, already-created database. `src/main.py`'s startup path
called only `init_schema()`, which runs `CREATE TABLE IF NOT EXISTS` for
each table in `src/db/schema.py`'s `SCHEMA_STATEMENTS` — this is a no-op
against a table that already exists; it does not add a column, or apply
any other change, to a database created before that column existed.

This surfaced as a real crash during Phase 1 demo review: the user ran the
app against their pre-existing local database (created before migration
`0002` existed) and hit `sqlite3.OperationalError: no such column:
updated_at` inside `update_product`, because that database's `products`
table predates the `updated_at` column and `init_schema()` never applied
`0002_up.sql` to add it. This is a structural gap affecting every future
schema change against any pre-existing install, not limited to this one
column — the migration files existed and were correctly authored, but
nothing in the startup path ever read and applied them.

## Decision

We will implement a schema migration runner, `src/db/migrate.py`, with the
following design:

- **Tracking table.** `schema_migrations(version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')))`
  records which migration versions have been applied to this database.
- **Runner function.** `apply_pending_migrations(conn, migrations_dir=
  MIGRATIONS_DIR)` discovers every `NNNN_up.sql` file in
  `src/db/migrations/`, filters out versions already present in
  `schema_migrations`, and executes each remaining one — in ascending
  version order — via `conn.executescript()`, recording its version
  immediately after it runs.
- **Startup wiring.** `src/main.py`'s startup path calls
  `apply_pending_migrations(conn)` in place of the previous bare
  `init_schema(conn)` call. For a brand-new database this produces the
  identical resulting schema `init_schema()` would have (verified by the
  existing `test_migration_files_when_read_produce_ddl_present_in_schema_
  statements` / `test_migration_0002_files_when_read_produce_ddl_present_
  in_schema_statements` tests) — `0001_up.sql` then `0002_up.sql` create
  and then extend the tables in order. For a pre-existing database, only
  the genuinely pending migrations run, so a database that already has
  `0001`'s tables but not `0002`'s column now correctly gains it on the
  next app start.
- **Tolerant-idempotent application.** SQLite's `ALTER TABLE ADD COLUMN`
  has no `IF NOT EXISTS` form (verified directly against SQLite 3.45.3),
  so a migration whose effect is already present on the target database
  (e.g. a database created directly via `SCHEMA_STATEMENTS`, which already
  has every column current migrations would add) raises
  `sqlite3.OperationalError: duplicate column name: ...` rather than being
  a silent no-op the way `CREATE TABLE IF NOT EXISTS` is. The runner
  catches `OperationalError` whose message indicates an "already exists" /
  "duplicate column name" condition, logs a warning, and stamps that
  version as applied without re-raising. Any other `OperationalError` (a
  genuine migration bug) is not swallowed — it propagates and startup
  fails loudly, which is the correct behaviour for a real schema error.
- `init_schema()` / `SCHEMA_STATEMENTS` in `src/db/schema.py` are
  unchanged and remain the direct schema-creation path used by existing
  test fixtures that build a throwaway database without going through the
  migrations directory; only `src/main.py`'s real startup path changes.

## Alternatives Considered

- **`PRAGMA user_version` (a single integer) instead of a
  `schema_migrations` table.** Rejected: records only the latest applied
  version as one number, not which individual migrations ran — it cannot
  represent "0001 applied, 0002 not yet applied" distinctly from "nothing
  applied", and gives no auditable per-migration timestamp, unlike the
  `created_at`/`updated_at` audit-trail discipline already enforced
  elsewhere in this schema.
- **A third-party migration library (e.g. Alembic, yoyo-migrations).**
  Rejected: a new dependency for a single-user offline tool with two
  migration files today is disproportionate, consistent with the
  "lightweight default" stance ADR-003 already established for this
  project, and would require a Dependency Governance review
  (`architecture-rules.md`) that is not warranted at this scale.
  `sqlite3` (stdlib) is sufficient.
- **Calling both `init_schema()` and `apply_pending_migrations()` at
  startup.** Rejected: redundant for a fresh database (the runner alone
  already produces the full schema) and would make the tolerant-catch
  path — meant for the rare "already applied" case — fire on every fresh
  install instead, since `init_schema()` would have already created every
  column before the runner ran.
- **Hand-maintained "baseline version" lookup mapping known pre-runner
  schema shapes to a stamped version number.** Rejected: does not
  generalise to future migrations without a new manual entry each time a
  migration is added — the opposite of what a migration runner exists to
  automate. The tolerant-catch design generalises without per-migration
  hardcoding, at the cost of relying on SQLite's error message shape
  (see Consequences).
- **Failing hard on any `OperationalError` during migration application.**
  Rejected: would leave the user's exact crash scenario only half-fixed —
  a database that happens to already have partial or full target schema
  state (not just the specific pre-0002 shape) would still crash startup
  instead of degrading gracefully to "already applied, continue".

## Consequences

- **Easier:** any future schema change (new migration file) now actually
  reaches every existing installed database on next app start, not only
  freshly created ones — closing the class of bug this ADR responds to,
  not only the one `updated_at` instance.
- **Easier:** `schema_migrations` gives a queryable, timestamped audit
  record of which migrations have run against a given database file —
  useful for support/debugging a shop owner's local install.
- **Harder:** the tolerant-catch path relies on matching substrings in
  SQLite's `OperationalError` message text ("already exists", "duplicate
  column name"). This is a stated, accepted limitation — SQLite does not
  offer a structured error code distinguishing "already applied" from
  other schema errors for `ALTER TABLE ADD COLUMN`, so message-matching is
  the only mechanism available without a hand-maintained baseline table
  (rejected above). A future SQLite version changing its error message
  wording would require updating `_ALREADY_APPLIED_ERROR_FRAGMENTS`.
- **Harder:** `conn.executescript()` does not provide per-statement
  rollback. If a multi-statement migration file (e.g. `0002_up.sql`, which
  has four statements) raises partway through — including a tolerated
  "already applied" error on an early statement — later statements in that
  same script do not run. For a table-by-table migration like `0002`
  (products, then discounts) this could in principle leave one table
  migrated and the sibling table's column still pending; this is a stated
  edge case, not hidden, and is mitigated in practice by every migration
  in this repository being idempotent per-statement (`CREATE TABLE IF NOT
  EXISTS`, or an `ALTER TABLE ADD COLUMN` whose only failure mode is
  "already present") — a subsequent app start will attempt the remaining
  statements again since the version was not recorded as applied when the
  exception propagated past the tolerant catch. If a future migration adds
  a non-idempotent, non-tolerated statement after an idempotent one in the
  same file, this ordering risk should be re-evaluated.
- Multi-system impact analysis (this is a cross-cutting change touching
  application startup, replacing the previous `init_schema()` call):
  - **Availability impact:** none beyond this single local process — no
    downstream systems exist. The effect is a bounded startup delay while
    pending migrations (if any) are applied, and — for a database on an
    older schema — an actual write to the live file before the UI opens.
  - **Blast radius:** worst case, a genuine migration bug raises an
    unhandled `OperationalError` (or any other exception) and startup
    fails with a traceback, rather than silently launching against a
    stale or partially-migrated schema. This is an intentional trade-off:
    failing loudly at startup is preferable to a downstream data-write
    crash mid-session (the exact failure mode this ADR responds to).
  - **Rollback path:** each migration's `NNNN_down.sql` already exists and
    is verified reversible (SD-003 gate). To roll back this specific
    change (the runner itself), reverting `src/main.py`'s call from
    `apply_pending_migrations(conn)` back to `init_schema(conn)` is a
    one-line change; the `schema_migrations` table left behind is inert
    (unread by `init_schema()`) and harmless if the runner is removed. To
    roll back a specific migration's schema effect on a given database,
    run its `NNNN_down.sql` directly and delete its row from
    `schema_migrations` so the runner does not consider it applied.

## Scaling Limit

This design is appropriate for the current and near-future migration count
(two files today) and for kirana-shop data volumes, where
`conn.executescript()` on a handful of small DDL/backfill statements
completes in well under a second. If the migration count grows into the
tens (multi-year evolution of this schema) or a future migration performs
a large data backfill against a populated table (not just a DDL change),
the synchronous, main-thread, startup-blocking application model used here
would become noticeable and should be revisited — options at that point
include a progress indicator for long-running migrations, or moving large
backfills into a background/lazy-migration step outside the blocking
startup path. This decision does not attempt to solve for that scale now,
consistent with the "lightweight default" instruction that produced
ADR-003 and this ADR.
