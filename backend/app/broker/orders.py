"""TradeSmart order calls: PlaceOrder, ModifyOrder and CancelOrder.

TradeSmart's OMS rejects market-type orders sent through the API ("MKT Order
type not allowed for API order"), so, as in OpenAlgo's TradeSmart plugin, a
MARKET order goes out as a LIMIT order priced from the live LTP plus a
protection buffer (Market Price Protection), and SL-M goes out as SL-LMT priced
from the trigger. The buffer slabs are OpenAlgo's.
"""

import math
from dataclasses import dataclass
from typing import Any

from app.broker.tradesmart import BrokerError, post_authenticated, to_float

PRODUCTS = {"CNC": "C", "NRML": "M", "MIS": "I"}
PRICE_TYPES = {"MARKET": "LMT", "LIMIT": "LMT", "SL": "SL-LMT", "SL-M": "SL-LMT"}
SIDES = {"BUY": "B", "SELL": "S"}

_EQ_FUT_SLABS = ((100.0, 2.0), (500.0, 1.0), (math.inf, 0.5))
_OPTION_SLABS = ((10.0, 5.0), (100.0, 3.0), (500.0, 2.0), (math.inf, 1.0))


def protection_pct(price: float, instrument_type: str | None) -> float:
    slabs = _OPTION_SLABS if (instrument_type or "").upper() in ("CE", "PE") else _EQ_FUT_SLABS
    return next(pct for limit, pct in slabs if price < limit)


def round_to_tick(price: float, tick: float, side: str) -> float:
    """Buy prices round up and sell prices round down, so the protection is never lost to rounding."""
    tick = tick if tick and tick > 0 else 0.05
    steps = price / tick
    steps = math.ceil(steps - 1e-9) if side == "BUY" else math.floor(steps + 1e-9)
    return round(steps * tick, 4)


def protected_price(reference: float, side: str, instrument_type: str | None, tick: float) -> float:
    pct = protection_pct(reference, instrument_type) / 100
    raw = reference * (1 + pct) if side == "BUY" else reference * (1 - pct)
    return round_to_tick(raw, tick, side)


@dataclass(frozen=True)
class OrderRequest:
    """A validated order in SmartTrader terms, before it is mapped to TradeSmart fields."""

    exchange: str
    brsymbol: str
    side: str  # BUY or SELL
    quantity: int
    product: str  # CNC, NRML or MIS
    price_type: str  # MARKET, LIMIT, SL or SL-M
    price: float  # limit price sent to the exchange (already protected for MARKET and SL-M)
    trigger_price: float = 0.0


def place_payload(user_id: str, order: OrderRequest) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "uid": user_id,
        "actid": user_id,
        "exch": order.exchange,
        # PlaceOrder takes the symbol URL-encoded so names like M&M survive.
        "tsym": order.brsymbol.replace("&", "%26"),
        "qty": str(order.quantity),
        "prc": f"{order.price:g}",
        "dscqty": "0",
        "prd": PRODUCTS[order.product],
        "trantype": SIDES[order.side],
        "prctyp": PRICE_TYPES[order.price_type],
        "ret": "DAY",
        "ordersource": "API",
        "remarks": "smarttrader-agent",
    }
    if order.price_type in ("SL", "SL-M"):
        payload["trgprc"] = f"{order.trigger_price:g}"
    return payload


def _order_number(data: Any, action: str) -> str:
    if isinstance(data, dict) and data.get("stat") == "Ok" and data.get("norenordno"):
        return str(data["norenordno"])
    emsg = data.get("emsg") if isinstance(data, dict) else None
    raise BrokerError(str(emsg or f"{action} was rejected by TradeSmart"))


def place_order(access_token: str, user_id: str, order: OrderRequest) -> str:
    """Send the order; returns the Noren order number or raises BrokerError with TradeSmart's message."""
    return _order_number(post_authenticated("PlaceOrder", place_payload(user_id, order), access_token), "The order")


def modify_order(
    access_token: str, user_id: str, *, order_id: str, exchange: str, brsymbol: str, quantity: int,
    price_type: str, price: float, trigger_price: float = 0.0,
) -> str:
    payload: dict[str, Any] = {
        "uid": user_id,
        "actid": user_id,
        "exch": exchange,
        "norenordno": order_id,
        "tsym": brsymbol.replace("&", "%26"),
        "qty": str(quantity),
        "prc": f"{price:g}",
        "prctyp": PRICE_TYPES[price_type],
        "ret": "DAY",
    }
    # Noren rejects a trigger of 0 on a limit order, so it is only sent for stop orders.
    if price_type in ("SL", "SL-M"):
        payload["trgprc"] = f"{trigger_price:g}"
    return _order_number(post_authenticated("ModifyOrder", payload, access_token), "The modification")


def cancel_order(access_token: str, user_id: str, order_id: str) -> str:
    data = post_authenticated("CancelOrder", {"uid": user_id, "norenordno": order_id}, access_token)
    if isinstance(data, dict) and data.get("stat") == "Ok":
        return str(data.get("result") or data.get("norenordno") or order_id)
    emsg = data.get("emsg") if isinstance(data, dict) else None
    raise BrokerError(str(emsg or "The cancellation was rejected by TradeSmart"))


def find_order(access_token: str, user_id: str, order_id: str) -> dict[str, Any] | None:
    """The order book row for one order, or None."""
    data = post_authenticated("OrderBook", {"uid": user_id, "actid": user_id}, access_token)
    if not isinstance(data, list):
        return None
    for row in data:
        if isinstance(row, dict) and str(row.get("norenordno")) == str(order_id):
            return row
    return None


def ltp_of(quote: Any) -> float:
    return to_float(quote.get("lp")) if isinstance(quote, dict) else 0.0
