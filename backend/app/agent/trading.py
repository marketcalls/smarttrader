"""Order tools for the agent, gated by human approval and a risk check.

Three layers keep a real order from going out by accident:
1. The tools exist only while trading is switched on in Agent settings.
2. Every call is marked `requires_confirmation`: Agno pauses the run and the
   user approves or rejects it in the chat before the tool body runs.
3. The tool body re-reads the risk settings and refuses anything outside them,
   so a limit changed (or trading switched off) between proposal and approval
   still applies. A refusal is returned to the model as the tool result.
Every proposal, decision and broker answer lands in `agent_order_audit`.
"""

import json
import logging
from datetime import timedelta
from typing import Any

from agno.tools import tool
from sqlalchemy import func, select

from app.broker.history import data_exchange
from app.broker.orders import (
    PRICE_TYPES,
    PRODUCTS,
    OrderRequest,
    find_order,
    protected_price,
)
from app.broker.orders import cancel_order as broker_cancel_order
from app.broker.orders import modify_order as broker_modify_order
from app.broker.orders import place_order as broker_place_order
from app.broker.tradesmart import BrokerError, SessionExpiredError, post_authenticated, to_float
from app.config import utcnow
from app.db import SessionLocal
from app.models import AgentOrderAudit, AgentRisk, SymToken

logger = logging.getLogger(__name__)

ORDER_TOOLS = ("place_order", "modify_order", "cancel_order")
ALL_EXCHANGES = ("NSE", "BSE", "NFO", "BFO", "CDS", "MCX")
DUPLICATE_WINDOW = timedelta(seconds=60)


def risk_settings() -> dict[str, Any]:
    with SessionLocal() as db:
        row = db.get(AgentRisk, 1)
        if row is None:
            row = AgentRisk(id=1, trading_enabled=False, max_order_value=50_000.0, max_quantity=500,
                            max_orders_per_day=20, allowed_exchanges="NSE,BSE", updated_at=utcnow())
            db.add(row)
            db.commit()
        return {
            "trading_enabled": bool(row.trading_enabled),
            "max_order_value": float(row.max_order_value),
            "max_quantity": int(row.max_quantity),
            "max_orders_per_day": int(row.max_orders_per_day),
            "allowed_exchanges": [e for e in row.allowed_exchanges.split(",") if e],
        }


def save_risk_settings(values: dict[str, Any]) -> dict[str, Any]:
    risk_settings()
    with SessionLocal.begin() as db:
        row = db.get(AgentRisk, 1)
        row.trading_enabled = bool(values["trading_enabled"])
        row.max_order_value = float(values["max_order_value"])
        row.max_quantity = int(values["max_quantity"])
        row.max_orders_per_day = int(values["max_orders_per_day"])
        row.allowed_exchanges = ",".join(e for e in ALL_EXCHANGES if e in values["allowed_exchanges"])
        row.updated_at = utcnow()
    return risk_settings()


def audit(conversation_id: str | None, run_id: str | None, tool_name: str, args: dict[str, Any],
          status: str, order_id: str | None = None, message: str | None = None) -> None:
    with SessionLocal.begin() as db:
        db.add(AgentOrderAudit(
            created_at=utcnow(), conversation_id=conversation_id, run_id=run_id, tool=tool_name,
            args_json=json.dumps(args, default=str), status=status, order_id=order_id, message=message,
        ))


def _placed_today() -> int:
    now_ist = utcnow().replace(tzinfo=None) + timedelta(hours=5, minutes=30)
    start_utc = now_ist.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=5, minutes=30)
    with SessionLocal() as db:
        return db.scalar(
            select(func.count()).select_from(AgentOrderAudit).where(
                AgentOrderAudit.status == "placed", AgentOrderAudit.tool == "place_order",
                AgentOrderAudit.created_at >= start_utc,
            )
        ) or 0


def _recent_duplicate(args: dict[str, Any]) -> bool:
    since = utcnow() - DUPLICATE_WINDOW
    key = (args.get("symbol"), args.get("exchange"), args.get("side"), args.get("quantity"))
    with SessionLocal() as db:
        rows = db.scalars(
            select(AgentOrderAudit.args_json).where(
                AgentOrderAudit.tool == "place_order", AgentOrderAudit.status == "placed",
                AgentOrderAudit.created_at >= since,
            )
        ).all()
    for raw in rows:
        prev = json.loads(raw)
        if (prev.get("symbol"), prev.get("exchange"), prev.get("side"), prev.get("quantity")) == key:
            return True
    return False


def _instrument(symbol: str, exchange: str) -> SymToken | None:
    with SessionLocal() as db:
        return db.scalar(select(SymToken).where(SymToken.symbol == symbol, SymToken.exchange == exchange).limit(1))


def order_summary(tool_name: str, args: dict[str, Any]) -> str:
    """One line a human can check before approving."""
    a = args or {}
    if tool_name == "place_order":
        price = "at market (sent as a protected limit)" if a.get("price_type", "MARKET") == "MARKET" else f"at {a.get('price')}"
        trigger = f", trigger {a.get('trigger_price')}" if a.get("price_type") in ("SL", "SL-M") else ""
        return (f"{a.get('side', '?')} {a.get('quantity', '?')} {a.get('symbol', '?')} on {a.get('exchange', '?')} "
                f"{price}{trigger}, {a.get('product', 'MIS')}, {a.get('price_type', 'MARKET')}")
    if tool_name == "modify_order":
        return (f"Modify order {a.get('order_id', '?')}: quantity {a.get('quantity', 'unchanged')}, "
                f"{a.get('price_type', 'LIMIT')} at {a.get('price', '?')}")
    if tool_name == "cancel_order":
        return f"Cancel order {a.get('order_id', '?')}"
    return tool_name


def build_order_tools(access_token: str, user_id: str, context: dict[str, Any]) -> list[Any]:
    """The three confirmation-gated order tools. `context` carries conversation_id and run_id for the audit."""

    def refuse(tool_name: str, args: dict[str, Any], reason: str) -> str:
        audit(context.get("conversation_id"), context.get("run_id"), tool_name, args, "refused", message=reason)
        return f"Refused by the risk check, nothing was sent: {reason}"

    def broker_call(tool_name: str, args: dict[str, Any], fn, *a: Any, **kw: Any) -> tuple[str | None, str | None]:
        try:
            return fn(*a, **kw), None
        except SessionExpiredError:
            message = "the TradeSmart session has expired; the user must log in again"
        except BrokerError as exc:
            message = str(exc)
        except Exception as exc:  # noqa: BLE001
            message = f"{exc.__class__.__name__} while talking to TradeSmart"
        audit(context.get("conversation_id"), context.get("run_id"), tool_name, args, "failed", message=message)
        return None, message

    @tool(requires_confirmation=True)
    def place_order(
        symbol: str,
        exchange: str,
        side: str,
        quantity: int,
        product: str = "MIS",
        price_type: str = "MARKET",
        price: float = 0.0,
        trigger_price: float = 0.0,
    ) -> str:
        """Place a real order on the user's TradeSmart account. The user must approve it in the app first.

        Args:
            symbol: Symbol exactly as in the symbol master, for example "SBIN" or "CRUDEOIL19OCT26FUT".
            exchange: NSE, BSE, NFO, BFO, CDS or MCX (not an index exchange).
            side: BUY or SELL.
            quantity: Number of shares or units. For F&O and MCX use a multiple of the lot size.
            product: MIS (intraday), CNC (delivery) or NRML (carry forward F&O).
            price_type: MARKET, LIMIT, SL or SL-M.
            price: Limit price for LIMIT and SL orders; ignored for MARKET.
            trigger_price: Trigger price for SL and SL-M orders.
        """
        args = {"symbol": symbol.strip().upper(), "exchange": exchange.strip().upper(), "side": side.strip().upper(),
                "quantity": int(quantity), "product": product.strip().upper(), "price_type": price_type.strip().upper(),
                "price": float(price or 0), "trigger_price": float(trigger_price or 0)}
        audit(context.get("conversation_id"), context.get("run_id"), "place_order", args, "approved")
        risk = risk_settings()
        if not risk["trading_enabled"]:
            return refuse("place_order", args, "trading is switched off in Agent settings")
        if args["side"] not in ("BUY", "SELL"):
            return refuse("place_order", args, "side must be BUY or SELL")
        if args["product"] not in PRODUCTS:
            return refuse("place_order", args, "product must be MIS, CNC or NRML")
        if args["price_type"] not in PRICE_TYPES:
            return refuse("place_order", args, "price type must be MARKET, LIMIT, SL or SL-M")
        if args["exchange"] not in risk["allowed_exchanges"]:
            return refuse("place_order", args, f"{args['exchange']} is not in the allowed exchanges ({', '.join(risk['allowed_exchanges']) or 'none'})")
        if args["quantity"] <= 0 or args["quantity"] > risk["max_quantity"]:
            return refuse("place_order", args, f"quantity must be between 1 and {risk['max_quantity']}")
        if args["price_type"] in ("LIMIT", "SL") and args["price"] <= 0:
            return refuse("place_order", args, "a LIMIT or SL order needs a price")
        if args["price_type"] in ("SL", "SL-M") and args["trigger_price"] <= 0:
            return refuse("place_order", args, "an SL or SL-M order needs a trigger price")
        if _placed_today() >= risk["max_orders_per_day"]:
            return refuse("place_order", args, f"the daily limit of {risk['max_orders_per_day']} agent orders is reached")
        if _recent_duplicate(args):
            return refuse("place_order", args, "the same order was placed less than a minute ago")

        inst = _instrument(args["symbol"], args["exchange"])
        if inst is None or not inst.token:
            return refuse("place_order", args, f"{args['symbol']} on {args['exchange']} is not in the symbol master")
        quote, error = broker_call("place_order", args, post_authenticated, "GetQuotes",
                                   {"uid": user_id, "exch": data_exchange(inst.brexchange or args["exchange"]), "token": inst.token},
                                   access_token)
        if error:
            return f"Order not placed: could not read the live price ({error})."
        ltp = to_float(quote.get("lp")) if isinstance(quote, dict) else 0.0
        tick = inst.tick_size or 0.05
        if args["price_type"] == "MARKET":
            if ltp <= 0:
                return refuse("place_order", args, "no live price to protect a market order")
            send_price = protected_price(ltp, args["side"], inst.instrumenttype, tick)
        elif args["price_type"] == "SL-M":
            send_price = protected_price(args["trigger_price"], args["side"], inst.instrumenttype, tick)
        else:
            send_price = args["price"]
        value = (send_price or ltp) * args["quantity"]
        if value > risk["max_order_value"]:
            return refuse("place_order", args, f"estimated value {value:,.2f} is above the limit of {risk['max_order_value']:,.2f}")

        order = OrderRequest(exchange=args["exchange"], brsymbol=inst.brsymbol, side=args["side"], quantity=args["quantity"],
                             product=args["product"], price_type=args["price_type"], price=send_price,
                             trigger_price=args["trigger_price"])
        order_id, error = broker_call("place_order", args, broker_place_order, access_token, user_id, order)
        if error:
            return f"Order not placed. TradeSmart said: {error}"
        audit(context.get("conversation_id"), context.get("run_id"), "place_order", args, "placed", order_id=order_id,
              message=f"sent as {order.price_type} {send_price}")
        note = f" Sent as a limit at {send_price} (market price protection on LTP {ltp})." if args["price_type"] == "MARKET" else ""
        return json.dumps({"status": "placed", "order_id": order_id, "sent_price": send_price, "estimated_value": round(value, 2),
                           "note": "Check get_order_book for the fill." + note})

    @tool(requires_confirmation=True)
    def modify_order(order_id: str, quantity: int, price_type: str, price: float, trigger_price: float = 0.0) -> str:
        """Modify an open order. The user must approve it in the app first.

        Args:
            order_id: The Noren order number from the order book.
            quantity: New total quantity.
            price_type: LIMIT or SL (market-type orders cannot be modified into).
            price: New limit price.
            trigger_price: New trigger price for SL orders.
        """
        args = {"order_id": str(order_id), "quantity": int(quantity), "price_type": price_type.strip().upper(),
                "price": float(price), "trigger_price": float(trigger_price or 0)}
        audit(context.get("conversation_id"), context.get("run_id"), "modify_order", args, "approved")
        risk = risk_settings()
        if not risk["trading_enabled"]:
            return refuse("modify_order", args, "trading is switched off in Agent settings")
        if args["price_type"] not in ("LIMIT", "SL"):
            return refuse("modify_order", args, "price type must be LIMIT or SL")
        if args["quantity"] <= 0 or args["quantity"] > risk["max_quantity"] or args["price"] <= 0:
            return refuse("modify_order", args, f"quantity must be 1 to {risk['max_quantity']} and price above 0")
        if args["price"] * args["quantity"] > risk["max_order_value"]:
            return refuse("modify_order", args, f"value above the limit of {risk['max_order_value']:,.2f}")
        row, error = broker_call("modify_order", args, find_order, access_token, user_id, args["order_id"])
        if error:
            return f"Not modified: {error}"
        if row is None:
            return refuse("modify_order", args, f"order {args['order_id']} is not in today's order book")
        _, error = broker_call("modify_order", args, broker_modify_order, access_token, user_id, order_id=args["order_id"],
                               exchange=str(row.get("exch")), brsymbol=str(row.get("tsym")), quantity=args["quantity"],
                               price_type=args["price_type"], price=args["price"], trigger_price=args["trigger_price"])
        if error:
            return f"Not modified. TradeSmart said: {error}"
        audit(context.get("conversation_id"), context.get("run_id"), "modify_order", args, "placed", order_id=args["order_id"])
        return json.dumps({"status": "modified", "order_id": args["order_id"]})

    @tool(requires_confirmation=True)
    def cancel_order(order_id: str) -> str:
        """Cancel an open order. The user must approve it in the app first.

        Args:
            order_id: The Noren order number from the order book.
        """
        args = {"order_id": str(order_id)}
        audit(context.get("conversation_id"), context.get("run_id"), "cancel_order", args, "approved")
        if not risk_settings()["trading_enabled"]:
            return refuse("cancel_order", args, "trading is switched off in Agent settings")
        _, error = broker_call("cancel_order", args, broker_cancel_order, access_token, user_id, args["order_id"])
        if error:
            return f"Not cancelled. TradeSmart said: {error}"
        audit(context.get("conversation_id"), context.get("run_id"), "cancel_order", args, "placed", order_id=args["order_id"])
        return json.dumps({"status": "cancel requested", "order_id": args["order_id"]})

    return [place_order, modify_order, cancel_order]

