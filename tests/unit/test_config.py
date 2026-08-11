"""Unit tests for `src/config.py` (P3-F2-T10).

Covers `get_db_path()`'s default-value and `SMB_DB_PATH`-override paths,
and `get_reporting_tz()`'s default-value and
`SMB_REPORTING_TZ_OFFSET_MINUTES`-override paths (`ADR-008` Decision (b))
— raising this module to the >=90% Phase 3 per-module coverage floor,
which the `get_reporting_tz()` addition removes the prior carve-out for.
"""

import datetime
from pathlib import Path

import pytest

from src.config import (
    DEFAULT_DB_PATH,
    DEFAULT_REPORTING_TZ_OFFSET_MINUTES,
    get_db_path,
    get_reporting_tz,
)

# --- get_db_path ---------------------------------------------------------------


def test_get_db_path_when_no_env_override_returns_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SMB_DB_PATH", raising=False)

    assert get_db_path() == DEFAULT_DB_PATH


def test_get_db_path_when_env_override_set_returns_overridden_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SMB_DB_PATH", "/tmp/custom-smb-accounting/data.db")

    assert get_db_path() == Path("/tmp/custom-smb-accounting/data.db")


# --- get_reporting_tz (ADR-008 Decision (b)) ------------------------------------


def test_get_reporting_tz_when_no_env_override_returns_default_ist_offset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SMB_REPORTING_TZ_OFFSET_MINUTES", raising=False)

    tz = get_reporting_tz()

    assert tz == datetime.timezone(
        datetime.timedelta(minutes=DEFAULT_REPORTING_TZ_OFFSET_MINUTES)
    )
    assert tz.utcoffset(None) == datetime.timedelta(hours=5, minutes=30)


def test_get_reporting_tz_when_env_override_set_returns_overridden_offset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SMB_REPORTING_TZ_OFFSET_MINUTES", "60")

    tz = get_reporting_tz()

    assert tz == datetime.timezone(datetime.timedelta(minutes=60))
    assert tz.utcoffset(None) == datetime.timedelta(hours=1)


def test_get_reporting_tz_when_env_override_negative_returns_negative_offset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SMB_REPORTING_TZ_OFFSET_MINUTES", "-300")

    tz = get_reporting_tz()

    assert tz.utcoffset(None) == datetime.timedelta(hours=-5)
