"""Historical candles from TradeSmart, following the OpenAlgo TradeSmart plugin.

Intraday intervals use /TPSeries (token + exchange, candle size in minutes).
Daily uses /EODChartData (sym = "EXCH:BRSYMBOL"). Timestamps are returned as
epoch seconds (UTC); the broker's "time" strings are IST wall-clock times.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Any

import httpx

from app.broker.tradesmart import BrokerError, post_authenticated, to_float
from app.config import IST

logger = logging.getLogger(__name__)

# OpenAlgo interval token -> TPSeries "intrv" (minutes). "D" goes to EODChartData.
INTERVALS: dict[str, str] = {
    "1m": "1",
    "3m": "3",
    "5m": "5",
    "10m": "10",
    "15m": "15",
    "30m": "30",
    "1h": "60",
    "2h": "120",
    "D": "D",
}

MAX_LOOKBACK_DAYS = 5 * 365


def lookback_days(interval: str) -> int:
    """Default history window per interval: deeper for coarser frames (OpenAlgo values)."""
    if interval == "D":
        return 3 * 365
    if interval.endswith("h"):
        return 180
    minutes = int(INTERVALS.get(interval, "1"))
    if minutes <= 1:
        return 7
    if minutes <= 5:
        return 30
    if minutes <= 15:
        return 60
    return 120


def data_exchange(exchange: str) -> str:
    """Index pseudo-exchanges fetch data from their parent cash exchange."""
    return {"NSE_INDEX": "NSE", "BSE_INDEX": "BSE"}.get(exchange, exchange)


def _parse_time(candle: dict[str, Any], prefer_epoch: bool = False) -> int | None:
    """Epoch seconds for a candle. Daily rows carry "ssboe"; intraday rows an IST "time"."""
    ssboe = candle.get("ssboe")
    if prefer_epoch and ssboe not in (None, ""):
        try:
            return int(float(ssboe))
        except (TypeError, ValueError):
            pass
    raw = candle.get("time")
    if isinstance(raw, str):
        for fmt in ("%d-%m-%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
            try:
                return int(datetime.strptime(raw, fmt).replace(tzinfo=IST).timestamp())
            except ValueError:
                continue
    if ssboe not in (None, ""):
        try:
            return int(float(ssboe))
        except (TypeError, ValueError):
            return None
    return None


def parse_candles(response: Any, daily: bool) -> list[dict[str, Any]]:
    """Turn a TPSeries or EODChartData response into sorted, de-duplicated candles.

    A Not_Ok "no data" answer is an empty series, not an error.
    """
    if isinstance(response, dict):
        if response.get("stat") == "Not_Ok":
            emsg = str(response.get("emsg") or "History request failed")
            if "no data" in emsg.lower():
                return []
            raise BrokerError(emsg)
        raise BrokerError("Unexpected history response from TradeSmart")
    if not isinstance(response, list):
        raise BrokerError("Unexpected history response from TradeSmart")

    by_time: dict[int, dict[str, Any]] = {}
    for item in response:
        if isinstance(item, str):
            try:
                item = json.loads(item)
            except ValueError:
                continue
        if not isinstance(item, dict):
            continue
        ts = _parse_time(item, prefer_epoch=daily)
        if ts is None:
            continue
        o, h, lo, c = (to_float(item.get(k)) for k in ("into", "inth", "intl", "intc"))
        if o == 0 and h == 0 and lo == 0 and c == 0:
            continue
        by_time[ts] = {
            "time": ts,
            "open": o,
            "high": h,
            "low": lo,
            "close": c,
            "volume": int(to_float(item.get("intv"))),
            "_cum": to_float(item.get("v")) if item.get("v") not in (None, "") else None,
        }
    candles = [by_time[t] for t in sorted(by_time)]
    if not daily:
        _repair_intraday_volume(candles)
    for candle in candles:
        candle.pop("_cum", None)
    return candles


def _repair_intraday_volume(candles: list[dict[str, Any]]) -> None:
    """Clamp broker volume glitches around the close.

    TradeSmart's interval volume ("intv") can go negative and then jump to roughly the
    whole day's volume on the last bars, while the cumulative day volume ("v") resets.
    Per IST day, a bar's volume is capped by the rise of "v" over its running maximum,
    which equals "intv" on normal bars.
    """
    day = None
    peak = 0.0
    for candle in candles:
        current = datetime.fromtimestamp(candle["time"], IST).date()
        if current != day:
            day, peak = current, 0.0
        volume = max(0, candle["volume"])
        cumulative = candle["_cum"]
        if cumulative is not None:
            volume = min(volume, int(max(0.0, cumulative - peak)))
            peak = max(peak, cumulative)
        candle["volume"] = volume


def fetch_history(
    *,
    access_token: str,
    user_id: str,
    exchange: str,
    token: str,
    brsymbol: str,
    interval: str,
    days: int,
    now: datetime | None = None,
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    """Fetch candles for one instrument. Raises SessionExpiredError or BrokerError."""
    if interval not in INTERVALS:
        raise BrokerError(f"Unsupported interval {interval}")
    end = now or datetime.now(IST)
    start = (end - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    exch = data_exchange(exchange)

    if interval == "D":
        jdata = {"sym": f"{exch}:{brsymbol}", "from": str(int(start.timestamp())), "to": str(int(end.timestamp()))}
        response = post_authenticated("EODChartData", jdata, access_token, client)
    else:
        jdata = {
            "uid": user_id,
            "exch": exch,
            "token": token,
            "st": str(int(start.timestamp())),
            "et": str(int(end.timestamp())),
            "intrv": INTERVALS[interval],
        }
        response = post_authenticated("TPSeries", jdata, access_token, client)
    return parse_candles(response, daily=interval == "D")
