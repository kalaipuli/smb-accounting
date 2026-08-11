---
report_type: adr
adr_number: "ADR-003"
status: Accepted
date: 2026-08-02
---

# ADR-003: Local Backup and Recovery Mechanism for the SQLite Data File

**Date:** 2026-08-02
**Status:** Accepted

## Context

SMB Account Software stores all business data (catalog, discounts, inventory
stock, and future sales/credit data) in a single local SQLite file with no
server, no replication, and no cloud sync (`ARCH-T02`, `CTX-MVP` explicitly
defers cloud backup/sync). `framework/project/architecture.md` §8 flags this
as a known constraint: *"No backup mechanism (open question) — Single copy
of data on one machine — Disk failure or accidental deletion loses all
records"* and §10 recommends adding "a simple periodic local backup/export"
as the lightweight default. This decision was drained into DoD condition
`PD-1-BACKUP` (owner: architect) at the Phase 0→1 boundary.

The stack has no daemon, no OS-level scheduler integration, no message
queue, and no background service (`project-context.md` §2: Message Queue:
none; Agentic Stack: none). The tool is a single local process the shop
owner starts and stops directly via `poetry run python -m src.main`. Any
backup mechanism must work within that constraint — it cannot assume a
cron job, a Windows Task Scheduler entry, or a background thread the user
never sees, since none of those are part of the confirmed stack and adding
them would be disproportionate for a single-user offline tool (violates the
"lightweight default" instruction in architecture.md §10).

## Decision

We will implement a standalone backup/restore module, `src/db/backup.py`,
with the following design:

- **Trigger — two mechanisms:**
  1. **Startup-interval check (automatic).** `maybe_backup_on_startup()` is
     called once from `src/main.py` at the top of `main()`, before the
     database connection used by the UI is opened. It takes a new backup
     only if the most recent existing backup is older than `interval`
     (default: 1 day). This approximates "periodic" backup without any
     scheduler — the app's own natural start/stop cadence is the trigger.
  2. **Manual CLI (on-demand).** `python -m src.db.backup backup|restore|list`
     lets the shop owner (or a support technician) take a backup or restore
     one at any time, independent of the app's own run cycle.
- **Storage location:** `<db_path.parent>/backups/` — a `backups/`
  subdirectory sibling to the live database file (e.g.
  `~/.smb-accounting/backups/` under the default `SMB_DB_PATH`). Backup
  files are named `data-<UTC-timestamp>.db`.
- **Backup method:** the stdlib `sqlite3.Connection.backup()` online-backup
  API, not a raw file copy. This produces a transactionally consistent
  snapshot even when a connection to the live file may be open, avoiding
  the risk of copying a torn/partial write (e.g. mid-transaction WAL state).
- **Retention policy:** keep the most recent 7 backups (`DEFAULT_RETENTION`);
  older backups are deleted automatically after each successful backup via
  `prune_backups()`. At a default 1-per-day cadence this retains roughly one
  week of recovery points.
- **Restore procedure:** the shop owner (or support) stops the app, then
  runs `python -m src.db.backup restore <backup_file>`, which copies the
  chosen backup file back over the live database path. `list_backups()` /
  `python -m src.db.backup list` shows available recovery points and their
  timestamps (encoded in the filename) beforehand.
- **Failure isolation:** a backup failure at startup MUST NOT block app
  startup. `src/main.py` wraps the startup-backup call in a broad exception
  handler that logs and continues — the shop owner can still open and use
  the app for today's business even if last night's backup attempt failed
  (e.g. disk full).

## Alternatives Considered

- **OS-level scheduler (cron / Windows Task Scheduler) invoking the backup
  CLI independently of the app.** Rejected: requires per-OS setup scripts
  and, on Windows, a registered scheduled task — this is meaningfully more
  operational surface than a single-user offline install script should
  carry, and none of it is part of the confirmed stack. It remains
  available to a technically inclined owner via the same `python -m
  src.db.backup backup` command this ADR already exposes, without the
  harness building and maintaining OS-specific scheduling code.
- **Manual-only backup, no automatic component.** Rejected: fails the
  "periodic" requirement in architecture.md §10 — it relies entirely on the
  shop owner remembering to run the command, which is exactly the
  human-error failure mode the backup mechanism exists to reduce.
- **Raw file copy (`shutil.copyfile`) of the live `.db` file.** Rejected for
  the automatic path: SQLite files can have in-flight writes or an open
  WAL/journal; a raw copy risks capturing an inconsistent snapshot. The
  stdlib online backup API exists specifically to avoid this. (`shutil.copy`
  is still used, safely, for the *restore* direction — restoring only
  happens with the app stopped and no live connection open.)
- **Continuous replication / cloud sync.** Rejected: explicitly out of scope
  per `CTX-MVP` ("Explicitly deferred: ... cloud backup/sync").

## Consequences

- **Easier:** disk failure or accidental deletion of the live database no
  longer means total data loss — up to ~1 day of data is recoverable from
  the most recent backup, and up to a week of history is retained.
- **Easier:** no new infrastructure, dependency, or background process is
  introduced — `sqlite3` and `shutil` are both stdlib, consistent with
  `ARCH-T02`'s zero-operational-overhead rationale.
- **Harder:** backup cadence is bounded by how often the shop owner actually
  opens the app. If the app is left running for days without restart, or
  opened only occasionally, the "periodic" guarantee weakens — this is a
  known and stated limitation (see Scaling Limit), not a hidden gap.
- **Harder:** restore is a manual, app-stopped operation — there is no
  automatic failover or point-in-time recovery finer than the backup
  interval. Data written between the last successful backup and a failure
  is lost; this is consistent with the "periodic" (not continuous) design
  recommended by architecture.md §10.
- Multi-system impact analysis (this is a cross-cutting change touching
  application startup):
  - **Availability impact:** none beyond this single local process — there
    are no downstream systems. The only effect is a bounded startup delay
    while a backup is taken (see Scaling Limit for when this becomes
    noticeable).
  - **Blast radius:** worst case, a bug in the backup path could raise an
    unexpected exception. This is contained by the failure-isolation design
    above — the exception is caught, logged, and app startup proceeds
    unaffected. A bug in `restore_backup()` is scoped to the explicit,
    user-initiated restore command and never runs implicitly.
  - **Rollback path:** this change is purely additive (one new module, one
    new call site in `main.py`, no schema change, no migration). Reverting
    is a one-line removal of the `maybe_backup_on_startup(db_path)` call in
    `src/main.py`; existing backup files are untouched and harmless if the
    call is removed, and the app's core functionality is unaffected either
    way.

## Scaling Limit

This design is appropriate at kirana-shop data volumes (tens of thousands of
rows, per `architecture.md` §8) where the SQLite file is expected to stay in
the low tens of megabytes for years of small-shop transaction history. The
`sqlite3.Connection.backup()` call is synchronous and runs on the main
thread at app startup, so backup time is added directly to startup latency.
If the database file grows into the hundreds of megabytes to low gigabytes
(well beyond current MVP scope), the startup-blocking backup would become
noticeable and should be revisited — options at that point include running
the backup in a background thread, or moving to an incremental/WAL-based
backup strategy instead of a full-file copy each time. This decision does
not attempt to solve for that scale now, consistent with the "lightweight
default" instruction that produced this ADR.
