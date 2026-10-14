"""TradeSmart (Noren v2) REST client.

Authenticated calls POST ``jData=<json>`` as text/plain with a Bearer header.
GenAcsTok is the only call without the Bearer header.
"""

import hashlib
import json
import logging
import math
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import API_TIMEOUT, AUTHORIZE_URL, GENACSTOK_URL, REST_ROOT, USER_AGENT, now_ist_iso

logger = logging.getLogger(__name__)


class BrokerError(Exception):
    """The broker rejected a request or answered with something unusable."""


class SessionExpiredError(BrokerError):
    """The broker says the access token is no longer valid."""


@dataclass(frozen=True)
class LoginResult:
    access_token: str
    user_id: str
    user_name: str | None


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_checksum(api_key: str, api_secret: str, code: str) -> str:
    """checksum = SHA-256(api_key + api_secret + code), per the TradeSmart v2 docs."""
    return sha256_hex(f"{api_key}{api_secret}{code}")


def build_authorize_url(api_key: str) -> str:
    return f"{AUTHORIZE_URL}?{urlencode({'client_id': api_key})}"


def new_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/plain, */*"},
        timeout=httpx.Timeout(API_TIMEOUT),
        transport=transport,
    )


@contextmanager
def _client_scope(client: httpx.Client | None) -> Iterator[httpx.Client]:
    if client is not None:
        yield client
    else:
        with new_client() as owned:
            yield owned


def _jdata_body(jdata: dict[str, Any]) -> str:
    return "jData=" + json.dumps(jdata)


def _parse_json(response: httpx.Response, what: str) -> Any:
    try:
        return response.json()
    except ValueError:
        logger.error(
            "%s returned non-JSON (HTTP %s, content-type %s)",
            what,
            response.status_code,
            response.headers.get("content-type", "unknown"),
        )
        raise BrokerError(f"Unexpected response from TradeSmart (HTTP {response.status_code})")


def _is_session_expired(data: Any) -> bool:
    return isinstance(data, dict) and "session expired" in str(data.get("emsg", "")).lower()


def generate_access_token(
    api_key: str, api_secret: str, code: str, client: httpx.Client | None = None
) -> LoginResult:
    """Exchange the authorization code for an access token via GenAcsTok."""
    body = _jdata_body({"code": code, "checksum": make_checksum(api_key, api_secret, code)})
    with _client_scope(client) as c:
        response = c.post(GENACSTOK_URL, content=body, headers={"Content-Type": "text/plain"})
    logger.info("GenAcsTok responded with HTTP %s", response.status_code)
    data = _parse_json(response, "GenAcsTok")
    if not isinstance(data, dict):
        raise BrokerError("Unexpected response from TradeSmart")
    if data.get("stat") != "Ok":
        emsg = str(data.get("emsg") or "Authentication failed")
        logger.warning("GenAcsTok failed: %s", emsg)
        raise BrokerError(emsg)

    token = data.get("access_token") or data.get("susertoken")
    if not token:
        raise BrokerError("Login succeeded but TradeSmart returned no access token")
    user_id = data.get("actid") or data.get("uid") or data.get("USERID")
    if not user_id:
        raise BrokerError("Login succeeded but TradeSmart returned no user id")
    return LoginResult(access_token=str(token), user_id=str(user_id), user_name=data.get("uname"))


def post_authenticated(
    endpoint: str, jdata: dict[str, Any], access_token: str, client: httpx.Client | None = None
) -> Any:
    """POST to an authenticated endpoint and return the parsed JSON.

    Raises SessionExpiredError on HTTP 401 or a "Session Expired" emsg.
    """
    headers = {"Content-Type": "text/plain", "Authorization": f"Bearer {access_token}"}
    with _client_scope(client) as c:
        response = c.post(f"{REST_ROOT}/{endpoint.lstrip('/')}", content=_jdata_body(jdata), headers=headers)
    if response.status_code == 401:
        logger.info("%s returned HTTP 401", endpoint)
        raise SessionExpiredError("Session expired, please log in again")
    data = _parse_json(response, endpoint)
    if _is_session_expired(data):
        logger.info("%s reported an expired session", endpoint)
        raise SessionExpiredError("Session expired, please log in again")
    return data


def to_float(value: Any) -> float:
    """Broker numbers arrive as strings; anything missing or unparsable counts as 0."""
    if value is None or value == "":
        return 0.0
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if math.isfinite(result) else 0.0


def position_pnl(entry: dict[str, Any]) -> tuple[float, float]:
    """Realized and unrealized P&L for one PositionBook row (OpenAlgo logic)."""
    realized = to_float(entry.get("rpnl"))
    unrealized = to_float(entry.get("urmtom"))
    netqty = to_float(entry.get("netqty"))
    if unrealized == 0 and netqty != 0:
        prcftr = to_float(entry.get("prcftr")) if entry.get("prcftr") not in (None, "") else 1.0
        unrealized = (to_float(entry.get("lp")) - to_float(entry.get("netavgprc"))) * netqty * prcftr
    return realized, unrealized


def compute_funds(limits: dict[str, Any], positions: Any) -> dict[str, float]:
    """Build the funds payload from a Limits response and a PositionBook response.

    A PositionBook that is not a list (for example a "no data" Not_Ok object)
    means there are no positions, so P&L is zero.
    """
    realized = 0.0
    unrealized = 0.0
    if isinstance(positions, list):
        for entry in positions:
            if isinstance(entry, dict):
                r, u = position_pnl(entry)
                realized += r
                unrealized += u

    cash = to_float(limits.get("cash"))
    payin = to_float(limits.get("payin"))
    payout = to_float(limits.get("payout"))
    marginused = to_float(limits.get("marginused"))
    return {
        "available_cash": round(cash + payin - marginused, 2),
        "collateral": round(to_float(limits.get("brkcollamt")), 2),
        "m2m_unrealized": round(unrealized, 2),
        "m2m_realized": round(realized, 2),
        "utilised_debits": round(marginused, 2),
        "cash": round(cash, 2),
        "payin": round(payin, 2),
        "payout": round(payout, 2),
    }


def fetch_funds(access_token: str, user_id: str, client: httpx.Client | None = None) -> dict[str, Any]:
    """Call Limits and PositionBook and return the funds payload.

    Raises SessionExpiredError or BrokerError (Limits not Ok).
    """
    jdata = {"uid": user_id, "actid": user_id}
    with _client_scope(client) as c:
        limits = post_authenticated("Limits", jdata, access_token, c)
        if not isinstance(limits, dict) or limits.get("stat") != "Ok":
            emsg = limits.get("emsg") if isinstance(limits, dict) else None
            raise BrokerError(str(emsg or "Limits request failed"))

        try:
            positions = post_authenticated("PositionBook", jdata, access_token, c)
        except SessionExpiredError:
            raise
        except BrokerError:
            logger.warning("PositionBook unusable, treating P&L as zero")
            positions = None

    funds: dict[str, Any] = compute_funds(limits, positions)
    funds["fetched_at"] = now_ist_iso()
    return funds
