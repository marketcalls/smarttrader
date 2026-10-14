"""Orders the user places from the chart: validation, market price protection, and the broker calls.

The agent's order tools have their own path (app/agent/trading.py) with its
approval gate and risk limits; this one serves direct, user-confirmed actions
from the chart ticket, order lines and position lines.
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.broker.history import data_exchange
from app.broker.orders import (
    PRICE_TYPES,
    PRODUCTS,
    OrderRequest,
    cancel_order,
    find_order,
    modify_order,
    place_order,
    protected_price,
)
from app.broker.tradesmart import BrokerError, post_authenticated, to_float
from app.db import SessionLocal
from app.models import SymToken

TRADABLE_EXCHANGES = ("NSE", "BSE", "NFO", "BFO", "CDS", "MCX")
OPEN_STATUSES = ("OPEN", "TRIGGER_PENDING", "PENDING")


class OrderInputError(ValueError):
    """The request cannot become a valid order; the message is shown to the user."""


@dataclass(frozen=True)
class Placed:
    order_id: str
    sent_price: float
    ltp: float


def instrument(symbol: str, exchange: str) -> SymToken:
    with SessionLocal() as db:
        row = db.scalar(select(SymToken).where(SymToken.symbol == symbol, SymToken.exchange == exchange).limit(1))
    if row is None or not row.token:
        raise OrderInputError(f"{symbol} on {exchange} is not in the symbol master")
    return row


def ltp(access_token: str, user_id: str, inst: SymToken) -> float:
    data = post_authenticated(
        "GetQuotes", {"uid": user_id, "exch": data_exchange(inst.brexchange or inst.exchange), "token": inst.token}, access_token
    )
    if isinstance(data, dict) and data.get("stat") == "Not_Ok":
        raise BrokerError(str(data.get("emsg") or "Quote request failed"))
    return to_float(data.get("lp")) if isinstance(data, dict) else 0.0


def place(
    access_token: str, user_id: str, *, symbol: str, exchange: str, side: str, quantity: int, product: str,
    price_type: str, price: float = 0.0, trigger_price: float = 0.0,
) -> Placed:
    symbol, exchange, side = symbol.strip().upper(), exchange.strip().upper(), side.strip().upper()
    product, price_type = product.strip().upper(), price_type.strip().upper()
    if exchange not in TRADABLE_EXCHANGES:
        raise OrderInputError(f"Orders cannot be placed on {exchange}")
    if side not in ("BUY", "SELL"):
        raise OrderInputError("Side must be BUY or SELL")
    if product not in PRODUCTS:
        raise OrderInputError("Product must be MIS, CNC or NRML")
    if price_type not in PRICE_TYPES:
        raise OrderInputError("Price type must be MARKET, LIMIT, SL or SL-M")
    if quantity <= 0:
        raise OrderInputError("Quantity must be at least 1")
    if price_type in ("LIMIT", "SL") and price <= 0:
        raise OrderInputError("A LIMIT or SL order needs a price")
    if price_type in ("SL", "SL-M") and trigger_price <= 0:
        raise OrderInputError("An SL or SL-M order needs a trigger price")
    inst = instrument(symbol, exchange)
    lot = inst.lotsize or 1
    if lot > 1 and quantity % lot:
        raise OrderInputError(f"Quantity must be a multiple of the lot size {lot}")
    tick = inst.tick_size or 0.05
    last = ltp(access_token, user_id, inst)
    if price_type == "MARKET":
        if last <= 0:
            raise OrderInputError("No live price to protect a market order; use a limit order")
        send_price = protected_price(last, side, inst.instrumenttype, tick)
    elif price_type == "SL-M":
        send_price = protected_price(trigger_price, side, inst.instrumenttype, tick)
    else:
        send_price = price
    order = OrderRequest(exchange=exchange, brsymbol=inst.brsymbol, side=side, quantity=quantity, product=product,
                         price_type=price_type, price=send_price, trigger_price=trigger_price)
    return Placed(order_id=place_order(access_token, user_id, order), sent_price=send_price, ltp=last)


def modify(access_token: str, user_id: str, order_id: str, *, price: float, quantity: int | None = None,
           trigger_price: float | None = None) -> str:
    row = find_order(access_token, user_id, order_id)
    if row is None:
        raise OrderInputError(f"Order {order_id} is not in today's order book")
    if str(row.get("status", "")).upper() not in OPEN_STATUSES:
        raise OrderInputError(f"Order {order_id} is {str(row.get('status', '')).lower()} and cannot be modified")
    if price <= 0:
        raise OrderInputError("Price must be above 0")
    is_stop = str(row.get("prctyp", "")).upper().startswith("SL")
    price_type = "SL" if is_stop else "LIMIT"
    qty = quantity or int(to_float(row.get("qty")))
    trigger = trigger_price if trigger_price is not None else to_float(row.get("trgprc"))
    if is_stop and trigger_price is None:
        # Dragging a stop line moves its trigger; keep the limit the same distance away.
        old_trigger = to_float(row.get("trgprc"))
        gap = to_float(row.get("prc")) - old_trigger
        trigger, price = price, round(price + gap, 2)
    return modify_order(access_token, user_id, order_id=order_id, exchange=str(row.get("exch")),
                        brsymbol=str(row.get("tsym")), quantity=qty, price_type=price_type, price=price,
                        trigger_price=trigger)


def cancel(access_token: str, user_id: str, order_id: str) -> str:
    return cancel_order(access_token, user_id, order_id)


def _order_line(row: dict[str, Any]) -> dict[str, Any]:
    prctyp = str(row.get("prctyp", "")).upper()
    is_stop = prctyp.startswith("SL")
    return {
        "order_id": str(row.get("norenordno")),
        "side": "BUY" if row.get("trantype") == "B" else "SELL",
        "quantity": int(to_float(row.get("qty"))) - int(to_float(row.get("fillshares"))),
        "price": to_float(row.get("prc")),
        "trigger_price": to_float(row.get("trgprc")) or None,
        "line_price": to_float(row.get("trgprc")) if is_stop else to_float(row.get("prc")),
        "type": "stop_limit" if prctyp == "SL-LMT" else "stop" if is_stop else "limit",
        "price_type": prctyp,
        "product": row.get("s_prdt_ali") or row.get("prd"),
        "status": row.get("status"),
    }


def symbol_state(access_token: str, user_id: str, symbol: str, exchange: str) -> dict[str, Any]:
    """Open orders and the net position for one instrument, for the chart's order and position lines."""
    inst = instrument(symbol.strip().upper(), exchange.strip().upper())
    account = {"uid": user_id, "actid": user_id}
    book = post_authenticated("OrderBook", account, access_token)
    positions = post_authenticated("PositionBook", account, access_token)
    exch = data_exchange(inst.brexchange or inst.exchange)

    def mine(row: Any) -> bool:
        return isinstance(row, dict) and row.get("tsym") == inst.brsymbol and row.get("exch") == exch

    orders = [
        _order_line(row) for row in (book if isinstance(book, list) else [])
        if mine(row) and str(row.get("status", "")).upper() in OPEN_STATUSES
    ]
    lines = []
    for row in positions if isinstance(positions, list) else []:
        if not mine(row):
            continue
        net = int(to_float(row.get("netqty")))
        if net == 0:
            continue
        factor = to_float(row.get("prcftr")) if row.get("prcftr") not in (None, "") else 1.0
        lines.append({
            "id": f"{row.get('prd')}:{inst.brsymbol}",
            "product": {"I": "MIS", "C": "CNC", "M": "NRML"}.get(str(row.get("prd")), str(row.get("prd"))),
            "side": "long" if net > 0 else "short",
            "quantity": abs(net),
            "net_quantity": net,
            "avg_price": to_float(row.get("netavgprc")),
            "ltp": to_float(row.get("lp")),
            # Live P&L on the chart: (ltp - avg_price) * net_quantity * multiplier + realized.
            "multiplier": factor or 1.0,
            "realized": round(to_float(row.get("rpnl")), 2),
            "unrealized": round(to_float(row.get("urmtom")), 2),
        })
    return {"symbol": inst.symbol, "exchange": inst.exchange, "lot_size": inst.lotsize or 1,
            "tick_size": inst.tick_size or 0.05, "orders": orders, "positions": lines}
