# ADR-001: Technology Stack Selection

**Status:** Accepted
**Date:** 2026-07-31

## Context

SMB Account Software is a local, single-user accounting tool for Indian kirana shop owners, covering sales, inventory, pricing/discounts, GST-inclusive billing, credit tracking, and P&L reporting. It must run entirely offline on the owner's own PC, with no server or cloud dependency, at small-shop data volumes.

## Decision

- **Language:** Python 3.12+
- **UI:** `curses` (stdlib) — terminal/console UI, no web or desktop GUI framework
- **Database:** SQLite via stdlib `sqlite3`, single local file, no server
- **Package manager:** Poetry
- **Testing:** pytest
- **Lint/format:** ruff
- **Type checking:** mypy (strict)

## Rationale

- The confirmed requirement is a terminal-based CUI ("cursors CUI interface"), which maps directly to Python's stdlib `curses` module — no third-party TUI framework needed for a basic-level tool.
- Single-user, offline, single-shop scope means no database server, no network layer, and no concurrency handling are required — SQLite's single-file model fits directly and avoids operational overhead (install, service management) inappropriate for a shop owner's own PC.
- Poetry, pytest, ruff, and mypy are the project's existing default Python toolchain (`framework/technology/backend/python.md`), reused here for consistency and because no project-specific reason to deviate was raised.

## Alternatives Considered

- **Web app (FastAPI + browser UI):** Rejected — requires running a local server and a browser, adding operational complexity with no benefit for a single-user offline tool; the user explicitly confirmed a curses-based terminal UI.
- **PostgreSQL/MySQL:** Rejected — a database server is unnecessary operational overhead for a single-user, single-shop, offline tool; SQLite's file-based model matches the deployment model directly.

## Consequences

- No network-facing attack surface — reduces security scope significantly.
- No horizontal scaling path if requirements grow (multi-shop, multi-user) — would require a stack revisit, explicitly deferred per confirmed scope.
- Terminal UI limits accessibility compared to a GUI, but matches the confirmed requirement and target user (shop owner working from a PC).
