import datetime
import os
from pathlib import Path

APP_NAME: str = "SMB Account Software"
APP_VERSION: str = "0.1.0"

DEFAULT_DB_PATH: Path = Path.home() / ".smb-accounting" / "data.db"

# ADR-008 Decision (b): the P&L reporting trading-day/timezone boundary is
# one configurable value for the whole shop install — an environment-
# variable override on a module-level default, the identical shape
# `DEFAULT_DB_PATH`/`get_db_path()` already establishes, not a new
# settings table. Default is IST (UTC+05:30).
DEFAULT_REPORTING_TZ_OFFSET_MINUTES: int = 330  # IST, UTC+05:30


def get_db_path() -> Path:
    raw_path = os.environ.get("SMB_DB_PATH")
    return Path(raw_path) if raw_path else DEFAULT_DB_PATH


def get_reporting_tz() -> datetime.timezone:
    """The single shop-level reporting timezone (ADR-008 Decision (b)).

    Every trading-day/period-bound computation in the reporting service
    MUST call this function — never read `DEFAULT_REPORTING_TZ_OFFSET_MINUTES`
    directly — mirroring how every DB-path caller goes through
    `get_db_path()` rather than `DEFAULT_DB_PATH`.
    """
    raw = os.environ.get("SMB_REPORTING_TZ_OFFSET_MINUTES")
    minutes = int(raw) if raw else DEFAULT_REPORTING_TZ_OFFSET_MINUTES
    return datetime.timezone(datetime.timedelta(minutes=minutes))
