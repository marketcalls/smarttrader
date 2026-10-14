"""Read-only TradeSmart tools for the agent.

Every tool is a closure over the signed-in user's broker session, so the access
token never enters the prompt or the tool arguments. Nothing here can place,
modify or cancel an order: SmartTrader has no order routes, and the agent only
reads market data and the account.
"""

import functools
import json
import logging
import math
from collections.abc import Callable
from typing import Any

from sqlalchemy import select

from app.broker.history import INTERVALS, data_exchange, fetch_history, lookback_days
from app.broker.tradesmart import BrokerError, SessionExpiredError, fetch_funds, post_authenticated, to_float
from app.db import SessionLocal
from app.models import MasterContractStatus, SymToken

logger = logging.getLogger(__name__)

MAX_ROWS = 50


def _dump(value: Any) -> str:
    return json.dumps(value, default=str, separators=(",", ":"))


def _instrument(symbol: str, exchange: str) -> SymToken | None:
    with SessionLocal() as db:
        return db.scalar(
            select(SymToken)
            .where(SymToken.symbol == symbol.strip().upper(), SymToken.exchange == exchange.strip().upper())
            .limit(1)
        )


def _rows(data: Any, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    if not isinstance(data, list):
        return []
    return [{k: row.get(k) for k in keys if row.get(k) not in (None, "")} for row in data if isinstance(row, dict)][:MAX_ROWS]


def _not_ok(data: Any) -> str | None:
    if isinstance(data, dict) and data.get("stat") == "Not_Ok":
        emsg = str(data.get("emsg") or "Request failed")
        return None if "no data" in emsg.lower() else emsg
    return None


def _sma(values: list[float], n: int) -> float | None:
    return sum(values[-n:]) / n if len(values) >= n else None


def _ema(values: list[float], n: int) -> float | None:
    if len(values) < n:
        return None
    k = 2 / (n + 1)
    ema = sum(values[:n]) / n
    for v in values[n:]:
        ema = v * k + ema * (1 - k)
    return ema


def _rsi(values: list[float], n: int = 14) -> float | None:
    if len(values) <= n:
        return None
    gains = losses = 0.0
    for a, b in zip(values[:n], values[1 : n + 1]):
        d = b - a
        gains += max(d, 0)
        losses += max(-d, 0)
    avg_gain, avg_loss = gains / n, losses / n
    for a, b in zip(values[n:], values[n + 1 :]):
        d = b - a
        avg_gain = (avg_gain * (n - 1) + max(d, 0)) / n
        avg_loss = (avg_loss * (n - 1) + max(-d, 0)) / n
    if avg_loss == 0:
        return 100.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def _atr(candles: list[dict[str, Any]], n: int = 14) -> float | None:
    if len(candles) <= n:
        return None
    trs = [
        max(c["high"] - c["low"], abs(c["high"] - p["close"]), abs(c["low"] - p["close"]))
        for p, c in zip(candles, candles[1:])
    ]
    atr = sum(trs[:n]) / n
    for tr in trs[n:]:
        atr = (atr * (n - 1) + tr) / n
    return atr


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None or math.isnan(value) else round(value, digits)


def build_tools(access_token: str, user_id: str) -> list[Callable[..., str]]:
    """The agent's toolset for one signed-in TradeSmart session."""
    account = {"uid": user_id, "actid": user_id}

    def call(endpoint: str, jdata: dict[str, Any]) -> Any:
        return post_authenticated(endpoint, jdata, access_token)

    def guarded(fn: Callable[..., str]) -> Callable[..., str]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> str:
            try:
                return fn(*args, **kwargs)
            except SessionExpiredError:
                return "Error: the TradeSmart session has expired. Ask the user to log in to SmartTrader again."
            except BrokerError as exc:
                return f"Error from TradeSmart: {exc}"
            except Exception as exc:  # noqa: BLE001 - the model gets a readable failure instead of a crash
                logger.warning("Agent tool %s failed: %s", fn.__name__, exc.__class__.__name__)
                return f"Error: {fn.__name__} failed ({exc.__class__.__name__})."

        return wrapper

    def search_symbols(query: str, exchange: str = "") -> str:
        """Find instruments in the symbol master by symbol or name.

        Args:
            query: Part of a symbol or company name, for example "RELIANCE", "NIFTY 24OCT" or "crude".
            exchange: Optional exchange filter: NSE, BSE, NFO, BFO, CDS, MCX, NSE_INDEX or BSE_INDEX.

        Returns:
            JSON list of matches with symbol, exchange, name, instrument type, expiry, strike and lot size.
        """
        terms = query.strip().upper().split()
        if not terms:
            return "Error: give a symbol or name to search for."
        stmt = select(SymToken)
        for term in terms[:4]:
            like = f"%{term}%"
            stmt = stmt.where(SymToken.symbol.like(like) | SymToken.name.like(like) | SymToken.brsymbol.like(like))
        if exchange.strip():
            stmt = stmt.where(SymToken.exchange == exchange.strip().upper())
        with SessionLocal() as db:
            rows = db.scalars(stmt.order_by(SymToken.symbol).limit(15)).all()
        return _dump([
            {
                "symbol": r.symbol, "exchange": r.exchange, "name": r.name, "type": r.instrumenttype,
                "expiry": r.expiry or None, "strike": r.strike if r.strike and r.strike > 0 else None, "lot_size": r.lotsize,
            }
            for r in rows
        ])

    def get_quote(symbol: str, exchange: str) -> str:
        """Live quote for one instrument: last price, day open/high/low, previous close, change, volume and more.

        Args:
            symbol: Symbol exactly as in the symbol master, for example "SBIN" or "CRUDEOIL19OCT26FUT".
            exchange: Exchange of the symbol, for example NSE, MCX or NSE_INDEX.
        """
        inst = _instrument(symbol, exchange)
        if inst is None or not inst.token:
            return f"Error: {symbol} on {exchange} is not in the symbol master. Use search_symbols first."
        data = call("GetQuotes", {"uid": user_id, "exch": data_exchange(inst.brexchange or inst.exchange or exchange), "token": inst.token})
        if error := _not_ok(data):
            return f"Error from TradeSmart: {error}"
        if not isinstance(data, dict):
            return "Error: unexpected quote response."
        ltp, prev = to_float(data.get("lp")), to_float(data.get("c"))
        return _dump({
            "symbol": inst.symbol, "exchange": inst.exchange, "name": data.get("cname") or inst.name,
            "ltp": ltp, "open": to_float(data.get("o")), "high": to_float(data.get("h")), "low": to_float(data.get("l")),
            "prev_close": prev, "change": _round(ltp - prev) if prev else None,
            "change_pct": _round((ltp - prev) / prev * 100) if prev else None,
            "volume": to_float(data.get("v")), "avg_price": to_float(data.get("ap")),
            "open_interest": to_float(data.get("oi")) or None,
            "upper_circuit": to_float(data.get("uc")) or None, "lower_circuit": to_float(data.get("lc")) or None,
            "week52_high": to_float(data.get("wk52_h")) or None, "week52_low": to_float(data.get("wk52_l")) or None,
            "lot_size": data.get("ls"), "tick_size": data.get("ti"), "feed_time": data.get("lut"),
        })

    def get_history(symbol: str, exchange: str, interval: str = "D", bars: int = 30) -> str:
        """Recent OHLCV candles for an instrument with a short summary.

        Args:
            symbol: Symbol exactly as in the symbol master.
            exchange: Exchange of the symbol.
            interval: One of 1m, 3m, 5m, 10m, 15m, 30m, 1h, 2h or D (daily).
            bars: How many of the latest candles to return (1 to 200).
        """
        if interval not in INTERVALS:
            return f"Error: interval must be one of {', '.join(INTERVALS)}."
        inst = _instrument(symbol, exchange)
        if inst is None or not inst.token:
            return f"Error: {symbol} on {exchange} is not in the symbol master. Use search_symbols first."
        bars = max(1, min(int(bars), 200))
        days = max(bars * 2, 60) if interval == "D" else lookback_days(interval)
        candles = fetch_history(
            access_token=access_token, user_id=user_id, exchange=inst.brexchange or inst.exchange or exchange,
            token=inst.token, brsymbol=inst.brsymbol, interval=interval, days=days,
        )[-bars:]
        if not candles:
            return f"No candles for {inst.symbol} on {interval}. The market may be closed for this range."
        first, last = candles[0], candles[-1]
        return _dump({
            "symbol": inst.symbol, "exchange": inst.exchange, "interval": interval, "timezone": "Asia/Kolkata",
            "summary": {
                "from_close": first["close"], "to_close": last["close"],
                "change_pct": _round((last["close"] - first["close"]) / first["close"] * 100) if first["close"] else None,
                "high": max(c["high"] for c in candles), "low": min(c["low"] for c in candles),
                "total_volume": sum(c["volume"] for c in candles),
            },
            "columns": ["time_epoch", "open", "high", "low", "close", "volume"],
            "candles": [[c["time"], c["open"], c["high"], c["low"], c["close"], c["volume"]] for c in candles],
        })

    def get_technicals(symbol: str, exchange: str, interval: str = "D") -> str:
        """Common indicators on the latest candles: RSI(14), SMA(20/50/200), EMA(20), ATR(14) and the recent range.

        Args:
            symbol: Symbol exactly as in the symbol master.
            exchange: Exchange of the symbol.
            interval: One of 1m, 3m, 5m, 10m, 15m, 30m, 1h, 2h or D (daily).
        """
        if interval not in INTERVALS:
            return f"Error: interval must be one of {', '.join(INTERVALS)}."
        inst = _instrument(symbol, exchange)
        if inst is None or not inst.token:
            return f"Error: {symbol} on {exchange} is not in the symbol master. Use search_symbols first."
        candles = fetch_history(
            access_token=access_token, user_id=user_id, exchange=inst.brexchange or inst.exchange or exchange,
            token=inst.token, brsymbol=inst.brsymbol, interval=interval,
            days=400 if interval == "D" else lookback_days(interval),
        )
        if len(candles) < 15:
            return f"Not enough candles for {inst.symbol} on {interval} to compute indicators."
        closes = [c["close"] for c in candles]
        last = closes[-1]
        recent = candles[-20:]
        return _dump({
            "symbol": inst.symbol, "exchange": inst.exchange, "interval": interval, "bars_used": len(candles),
            "last_close": last, "rsi14": _round(_rsi(closes)), "sma20": _round(_sma(closes, 20)),
            "sma50": _round(_sma(closes, 50)), "sma200": _round(_sma(closes, 200)), "ema20": _round(_ema(closes, 20)),
            "atr14": _round(_atr(candles)), "high_20": max(c["high"] for c in recent), "low_20": min(c["low"] for c in recent),
        })

    def get_funds() -> str:
        """Account funds and margins: available cash, collateral, used margin and today's realized and unrealized P&L."""
        return _dump(fetch_funds(access_token, user_id))

    def get_positions() -> str:
        """Open and closed positions for today with quantity, average price, LTP and P&L."""
        data = call("PositionBook", account)
        if error := _not_ok(data):
            return f"Error from TradeSmart: {error}"
        rows = _rows(data, ("tsym", "exch", "prd", "netqty", "netavgprc", "lp", "rpnl", "urmtom", "daybuyqty", "daysellqty"))
        return _dump(rows) if rows else "No positions today."

    def get_holdings() -> str:
        """Demat holdings with quantity, average price and last price."""
        data = call("Holdings", {**account, "prd": "C"})
        if error := _not_ok(data):
            return f"Error from TradeSmart: {error}"
        if not isinstance(data, list) or not data:
            return "No holdings."
        out = []
        for row in data[:MAX_ROWS]:
            if not isinstance(row, dict):
                continue
            listing = (row.get("exch_tsym") or [{}])[0]
            out.append({
                "symbol": listing.get("tsym"), "exchange": listing.get("exch"), "ltp": to_float(listing.get("lp")),
                "quantity": to_float(row.get("holdqty")) + to_float(row.get("btstqty")),
                "avg_price": to_float(row.get("upldprc")) or None, "used_today": to_float(row.get("usedqty")),
            })
        return _dump(out)

    def get_order_book() -> str:
        """Today's orders with status, side, quantity, price, fills and rejection reasons."""
        data = call("OrderBook", account)
        if error := _not_ok(data):
            return f"Error from TradeSmart: {error}"
        rows = _rows(data, ("norenordno", "tsym", "exch", "trantype", "prctyp", "prd", "qty", "prc", "trgprc", "status", "fillshares", "avgprc", "rejreason", "exch_tm"))
        return _dump(rows) if rows else "No orders today."

    def get_trade_book() -> str:
        """Today's executed trades with fill price, quantity and time."""
        data = call("TradeBook", account)
        if error := _not_ok(data):
            return f"Error from TradeSmart: {error}"
        rows = _rows(data, ("norenordno", "tsym", "exch", "trantype", "prd", "flqty", "flprc", "fltm"))
        return _dump(rows) if rows else "No trades today."

    def get_master_contract_status() -> str:
        """Whether the symbol master is downloaded, with record counts per exchange."""
        with SessionLocal() as db:
            row = db.get(MasterContractStatus, 1)
        if row is None:
            return _dump({"status": "idle"})
        return _dump({"status": row.status, "message": row.message, "total_records": row.total_records,
                      "exchanges": json.loads(row.exchanges_json or "{}")})

    return [
        guarded(fn)
        for fn in (
            search_symbols, get_quote, get_history, get_technicals, get_funds,
            get_positions, get_holdings, get_order_book, get_trade_book, get_master_contract_status,
        )
    ]


TOOL_DESCRIPTIONS = [
    ("search_symbols", "Find instruments by symbol or name"),
    ("get_quote", "Live quote with change, volume, circuits and 52 week range"),
    ("get_history", "Recent OHLCV candles in any broker timeframe"),
    ("get_technicals", "RSI, SMA, EMA, ATR and the recent range"),
    ("get_funds", "Cash, collateral, margin and P&L"),
    ("get_positions", "Today's positions"),
    ("get_holdings", "Demat holdings"),
    ("get_order_book", "Today's orders"),
    ("get_trade_book", "Today's trades"),
    ("get_master_contract_status", "Symbol master status"),
]
