"""FinEdge Financials API: the encrypted API key and a small HTTP client.

The key is entered on the Agent settings page and stored Fernet-encrypted in the
`agent_secret` table (never in a file or environment variable). FinEdge takes the
key as a `token` query parameter, so request URLs are never logged or echoed:
errors carry only the status code and FinEdge's short error text.
"""

import logging
import threading
import time
import urllib.parse
from functools import lru_cache
from typing import Any

import httpx

from app.config import to_ist_iso, utcnow
from app.crypto import decrypt, encrypt
from app.db import SessionLocal
from app.models import AgentSecret

logger = logging.getLogger(__name__)

SECRET_NAME = "finedge_api_key"
BASE_URL = "https://data.finedgeapi.com"
TIMEOUT_SECONDS = 30.0
RETRY_STATUSES = (429, 500, 502, 503, 504)
RETRY_DELAYS = (1.0, 2.0)
# ITC is in every FinEdge plan, including the free Basic plan; TCS is not in Basic.
TEST_SYMBOL = "ITC"
PAID_PLAN_PROBE = "TCS"


class FinEdgeError(Exception):
    """A failed FinEdge call. `message` is safe to show: it never contains the key."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message

    @property
    def invalid_key(self) -> bool:
        return self.status_code == 401 and "ER006" in self.message

    @property
    def not_in_plan(self) -> bool:
        return self.status_code in (401, 403) and not self.invalid_key


# ---------- the stored key ----------


def read_key() -> str | None:
    with SessionLocal() as db:
        row = db.get(AgentSecret, SECRET_NAME)
        return decrypt(row.value_enc) if row else None


def write_key(api_key: str) -> None:
    value = encrypt(api_key)
    with SessionLocal.begin() as db:
        row = db.get(AgentSecret, SECRET_NAME)
        if row is None:
            db.add(AgentSecret(name=SECRET_NAME, value_enc=value, updated_at=utcnow()))
        else:
            row.value_enc = value
            row.updated_at = utcnow()


def clear_key() -> None:
    with SessionLocal.begin() as db:
        row = db.get(AgentSecret, SECRET_NAME)
        if row is not None:
            db.delete(row)


def mask(api_key: str) -> str:
    return "*" * 8 + api_key[-4:] if len(api_key) > 8 else "*" * 8


def key_status() -> dict[str, Any]:
    """What the settings page may know about the key: never the key itself."""
    with SessionLocal() as db:
        row = db.get(AgentSecret, SECRET_NAME)
        key = decrypt(row.value_enc) if row else None
        updated_at = row.updated_at if row else None
    return {
        "configured": bool(key),
        "masked_key": mask(key) if key else None,
        "updated_at": to_ist_iso(updated_at) if key else None,
    }


# ---------- HTTP ----------


@lru_cache(maxsize=1)
def _client() -> httpx.Client:
    return httpx.Client(base_url=BASE_URL, timeout=httpx.Timeout(TIMEOUT_SECONDS),
                        headers={"Accept": "application/json"})


def path_symbol(symbol: str) -> str:
    """A symbol or BSE code for a URL path segment (M&M and L&TFH contain '&')."""
    return urllib.parse.quote(symbol.strip().upper(), safe="")


def get(path: str, api_key: str, params: dict[str, Any] | None = None, *,
        client: httpx.Client | None = None) -> Any:
    """GET a FinEdge endpoint and return parsed JSON. Retries rate limits and server errors twice."""
    query = {k: v for k, v in (params or {}).items() if v not in (None, "")}
    query["token"] = api_key
    http = client or _client()
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            resp = http.get(path, params=query)
        except httpx.HTTPError as exc:
            # The exception text can contain the request URL, and so the key.
            raise FinEdgeError(0, f"FinEdge is unreachable ({exc.__class__.__name__})") from None
        if resp.status_code in RETRY_STATUSES and attempt < len(RETRY_DELAYS):
            time.sleep(RETRY_DELAYS[attempt])
            continue
        break
    if resp.status_code != 200:
        text = " ".join(resp.text.split())[:200].replace(api_key, "***") or resp.reason_phrase
        raise FinEdgeError(resp.status_code, text)
    if not resp.content.strip():
        return None
    try:
        return resp.json()
    except ValueError:
        raise FinEdgeError(502, "FinEdge returned a response that is not JSON") from None


def test_key(api_key: str, *, client: httpx.Client | None = None) -> dict[str, Any]:
    """Check a key with two cheap profile calls: one any plan can read, one that needs a paid plan.

    The /healthcheck endpoint is not used because it answers 200 even for a wrong key.
    """
    started = time.perf_counter()
    profile = get(f"/api/v1/company-profile/{TEST_SYMBOL}", api_key, client=client)
    latency_ms = int((time.perf_counter() - started) * 1000)
    try:
        get(f"/api/v1/company-profile/{PAID_PLAN_PROBE}", api_key, client=client)
        coverage = "all"
    except FinEdgeError as exc:
        if not exc.not_in_plan:
            raise
        coverage = "sample"
    return {
        "ok": True,
        "coverage": coverage,
        "sample_company": (profile or {}).get("name") if isinstance(profile, dict) else None,
        "latency_ms": latency_ms,
    }


# ---------- small process cache for large, slow-changing lists ----------

_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = threading.Lock()


def cached(name: str, ttl_seconds: float, loader: Any) -> Any:
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(name)
        if hit and now - hit[0] < ttl_seconds:
            return hit[1]
    value = loader()
    with _cache_lock:
        _cache[name] = (now, value)
    return value


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
