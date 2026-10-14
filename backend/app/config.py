"""Paths and constants. No secrets live here."""

import os
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_DIR / "data"
DB_PATH = DATA_DIR / "smarttrader.db"
SECRET_KEY_PATH = DATA_DIR / "secret.key"
TMP_DIR = DATA_DIR / "tmp"
FRONTEND_DIST = BACKEND_DIR.parent / "frontend" / "dist"

# Where the Vite dev server runs. Used for post-callback redirects when the
# backend is not serving a frontend build. Overridable (not a secret).
DEV_FRONTEND_URL = "http://127.0.0.1:5173"
FRONTEND_URL_ENV = "SMARTTRADER_FRONTEND_URL"

TRADESMART_HOST = "https://v2api.tradesmartonline.in"
REST_ROOT = f"{TRADESMART_HOST}/NorenWClientAPIv2"
GENACSTOK_URL = f"{REST_ROOT}/GenAcsTok"
AUTHORIZE_URL = f"{TRADESMART_HOST}/OAuthlogin/authorize/oauth"
STREAM_URL = "wss://v2api.tradesmartonline.in/NorenWSAPI/"

# The TradeSmart hosts sit behind Cloudflare, which rejects bare client user agents.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

# Seconds. Master contract zips are large, so they get a longer read timeout.
API_TIMEOUT = 20.0
DOWNLOAD_CONNECT_TIMEOUT = 15.0
DOWNLOAD_READ_TIMEOUT = 120.0

SESSION_COOKIE = "st_session"

CORS_ORIGINS = ["http://127.0.0.1:5173", "http://localhost:5173"]

IST = timezone(timedelta(hours=5, minutes=30), "IST")


def ensure_data_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)


def frontend_url() -> str:
    """Base URL for redirects after the OAuth callback.

    Empty (relative) when frontend/dist/index.html exists and this backend serves
    the UI, else the dev server URL. Checked per call because the build may be
    created after startup.
    """
    if (FRONTEND_DIST / "index.html").is_file():
        return ""
    return (os.environ.get(FRONTEND_URL_ENV) or DEV_FRONTEND_URL).rstrip("/")


def utcnow() -> datetime:
    """Naive UTC timestamp, which is what gets stored in SQLite."""
    return datetime.now(UTC).replace(tzinfo=None)


def to_ist_iso(value: datetime | None) -> str | None:
    """Render a stored naive UTC datetime as an ISO string in IST."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(IST).isoformat(timespec="seconds")


def now_ist_iso() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")
