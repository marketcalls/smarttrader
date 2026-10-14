# Orders (chart trading)

Source: `backend/app/routers/orders.py`, `backend/app/broker/order_service.py`,
`backend/app/broker/orders.py`.
Tests: `backend/tests/test_order_service.py`, `backend/tests/test_orders.py`.

Back to the [API index](README.md).

These endpoints place real orders on the user's TradeSmart account. They serve direct actions from the
chart (order ticket, draggable order lines, position lines); the UI asks for confirmation before calling
them. They are separate from the AI agent's order tools, which have their own approval gate and risk
limits (see [agent.md](agent.md)). The agent's configurable risk limits and audit rows do not apply to
these endpoints; the request bounds and order validation documented below still apply. The HTTP API
does not verify that a UI confirmation occurred.

## Common behaviour

### Market price protection

TradeSmart rejects market-type orders sent through the API, so:

| Requested `price_type` | Sent `prctyp` | Sent price |
| --- | --- | --- |
| `MARKET` | `LMT` | LTP plus a protection buffer, rounded to the tick against the trader |
| `LIMIT` | `LMT` | `price` as given |
| `SL` | `SL-LMT` | `price` as given, with `trgprc = trigger_price` |
| `SL-M` | `SL-LMT` | `trigger_price` plus the protection buffer, with `trgprc = trigger_price` |

Protection buffer (OpenAlgo slabs), applied upward for BUY and downward for SELL:

| Instrument | Reference price | Buffer |
| --- | --- | --- |
| Equity and futures | below 100 | 2% |
| | 100 to below 500 | 1% |
| | 500 and above | 0.5% |
| Options (`CE`, `PE`) | below 10 | 5% |
| | 10 to below 100 | 3% |
| | 100 to below 500 | 2% |
| | 500 and above | 1% |

Rounding: BUY rounds up to the next tick, SELL rounds down (tick from the symbol master, default 0.05).
Example: BUY SBIN at market with LTP 959.10 and tick 0.05 is sent as LMT 963.9 (959.10 x 1.005 = 963.8955,
rounded up).

### Products and sides

| App value | TradeSmart `prd` |
| --- | --- |
| `MIS` | `I` |
| `CNC` | `C` |
| `NRML` | `M` |

`BUY` maps to `trantype` `B`, `SELL` to `S`.

### Error mapping

All endpoints in this router map errors the same way (`_creds` and `_call`):

| Status | `detail` | When |
| --- | --- | --- |
| 401 | `Not authenticated` | No active session |
| 401 | `Session expired, please log in again` | Token missing or undecryptable, or TradeSmart reported the session expired. The session is deactivated. |
| 422 | SmartTrader validation message (see each endpoint) | `OrderInputError`: the request cannot become a valid order |
| 422 | validation list | Pydantic or `Query` validation failed |
| 502 | `TradeSmart: <emsg>` | TradeSmart rejected the request (`stat` not `Ok`, or a quote `Not_Ok`) |
| 502 | `Could not reach TradeSmart` | Network error or timeout |

---

## GET /api/orders/symbol

Open orders and the net position for one instrument, used to draw order and position lines on the
chart and to compute live P&L.

- Auth required: yes

Query parameters:

| Name | Type | Required | Constraints |
| --- | --- | --- | --- |
| `symbol` | string | yes | length 1 to 64; trimmed and upper-cased |
| `exchange` | string | yes | length 2 to 16; trimmed and upper-cased |

Example: `GET /api/orders/symbol?symbol=SBIN&exchange=NSE`

Response `200`:

```json
{
  "symbol": "SBIN",
  "exchange": "NSE",
  "lot_size": 1,
  "tick_size": 0.05,
  "orders": [
    {
      "order_id": "26101400012345",
      "side": "BUY",
      "quantity": 5,
      "price": 940.0,
      "trigger_price": null,
      "line_price": 940.0,
      "type": "limit",
      "price_type": "LMT",
      "product": "MIS",
      "status": "OPEN"
    },
    {
      "order_id": "26101400012346",
      "side": "BUY",
      "quantity": 10,
      "price": 930.0,
      "trigger_price": 931.0,
      "line_price": 931.0,
      "type": "stop_limit",
      "price_type": "SL-LMT",
      "product": "MIS",
      "status": "TRIGGER_PENDING"
    }
  ],
  "positions": [
    {
      "id": "I:SBIN-EQ",
      "product": "MIS",
      "side": "short",
      "quantity": 10,
      "net_quantity": -10,
      "avg_price": 960.0,
      "ltp": 952.45,
      "multiplier": 1.0,
      "realized": 120.0,
      "unrealized": 75.5
    }
  ]
}
```

Top-level fields: `symbol`, `exchange` (from the symbol master), `lot_size` (default 1),
`tick_size` (default 0.05), `orders`, `positions`.

`orders[]` contains rows from the order book for this instrument whose status is `OPEN`,
`TRIGGER_PENDING` or `PENDING`:

| Field | Type | Notes |
| --- | --- | --- |
| `order_id` | string | Noren order number (`norenordno`) |
| `side` | string | `BUY` when `trantype` is `B`, else `SELL` |
| `quantity` | integer | Remaining: `qty - fillshares` |
| `price` | number | `prc` |
| `trigger_price` | number or null | `trgprc`, null when 0 |
| `line_price` | number | Where the chart draws the line: trigger for stop orders, else price |
| `type` | string | `stop_limit` for `SL-LMT`, `stop` for other `SL*`, else `limit` |
| `price_type` | string | Raw `prctyp` |
| `product` | string | `s_prdt_ali` (for example `MIS`), fallback raw `prd` |
| `status` | string | Raw broker status |

`positions[]` contains PositionBook rows for this instrument with non-zero `netqty`:

| Field | Type | Notes |
| --- | --- | --- |
| `id` | string | `<prd>:<brsymbol>` |
| `product` | string | `MIS`, `CNC`, `NRML`, or the raw `prd` |
| `side` | string | `long` or `short` |
| `quantity` | integer | Absolute net quantity |
| `net_quantity` | integer | Signed `netqty` |
| `avg_price` | number | `netavgprc` |
| `ltp` | number | `lp` at the time of the call |
| `multiplier` | number | `prcftr`, default 1 |
| `realized` | number | `rpnl`, rounded to 2 |
| `unrealized` | number | `urmtom`, rounded to 2 |

The chart computes live P&L as `realized + (ltp - avg_price) * net_quantity * multiplier` on every tick.

Endpoint-specific errors:

| Status | `detail` |
| --- | --- |
| 422 | `<SYMBOL> on <EXCHANGE> is not in the symbol master` |

Side effects: outbound `POST /OrderBook` and `POST /PositionBook` with `{"uid", "actid"}`. A non-list
answer from either (for example "no data") is treated as empty. Read only.

---

## POST /api/orders

Places an order.

- Auth required: yes

Request body (`PlaceBody`):

| Field | Type | Required | Default | Constraints and notes |
| --- | --- | --- | --- | --- |
| `symbol` | string | yes | | length 1 to 64; app symbol from the symbol master |
| `exchange` | string | yes | | length 2 to 16; must be `NSE`, `BSE`, `NFO`, `BFO`, `CDS` or `MCX` |
| `side` | string | yes | | `BUY` or `SELL` (case-insensitive) |
| `quantity` | integer | yes | | 1 to 1 000 000; for instruments with lot size above 1, a multiple of the lot size |
| `product` | string | no | `MIS` | `MIS`, `CNC` or `NRML` (case-insensitive) |
| `price_type` | string | no | `LIMIT` | `MARKET`, `LIMIT`, `SL` or `SL-M` (case-insensitive) |
| `price` | number | no | `0.0` | at least 0; required (above 0) for `LIMIT` and `SL`; ignored for `MARKET` and `SL-M` |
| `trigger_price` | number | no | `0.0` | at least 0; required (above 0) for `SL` and `SL-M` |

Example request:

```json
{
  "symbol": "SBIN",
  "exchange": "NSE",
  "side": "BUY",
  "quantity": 2,
  "product": "MIS",
  "price_type": "MARKET"
}
```

Response `200`:

```json
{"order_id": "26101400012347", "sent_price": 963.9, "ltp": 959.1}
```

| Field | Type | Notes |
| --- | --- | --- |
| `order_id` | string | Noren order number |
| `sent_price` | number | Limit price actually sent (protected price for `MARKET` and `SL-M`, else `price`) |
| `ltp` | number | LTP read just before placing (0 when the quote had no `lp`) |

A 200 means TradeSmart accepted the order request, not that it was filled or even passed exchange
checks. The order can still be rejected later; check the order book.

Endpoint-specific `422` messages (`OrderInputError`):

- `Orders cannot be placed on <EXCHANGE>` (for example `NSE_INDEX`)
- `Side must be BUY or SELL`
- `Product must be MIS, CNC or NRML`
- `Price type must be MARKET, LIMIT, SL or SL-M`
- `Quantity must be at least 1`
- `A LIMIT or SL order needs a price`
- `An SL or SL-M order needs a trigger price`
- `<SYMBOL> on <EXCHANGE> is not in the symbol master`
- `Quantity must be a multiple of the lot size <n>`
- `No live price to protect a market order; use a limit order` (MARKET with LTP 0)

Side effects:

1. Reads the `symtoken` row.
2. Outbound `POST /GetQuotes` (`uid`, `exch`, `token`), for every price type.
3. Outbound `POST /PlaceOrder` (see [broker-integration.md](broker-integration.md#placeorder)).
4. Logs the placement. No database write.

---

## POST /api/orders/{order_id}/modify

Modifies an open order: drag a line on the chart, or edit price and quantity.

- Auth required: yes

Path parameter: `order_id` (string), the Noren order number.

Request body (`ModifyBody`):

| Field | Type | Required | Constraints | Notes |
| --- | --- | --- | --- | --- |
| `price` | number | yes | above 0 | See below for stop orders |
| `quantity` | integer or null | no | 1 to 1 000 000 | Null keeps the order's current total `qty` |
| `trigger_price` | number or null | no | above 0 | Only used for stop orders |

Behaviour:

- The order is looked up in today's order book. Its status must be `OPEN`, `TRIGGER_PENDING` or
  `PENDING`.
- Limit order (`prctyp` not starting with `SL`): sent as `LMT` with the new `price`; no trigger is sent.
- Stop order with `trigger_price` given: sent as `SL-LMT` with limit `price` and trigger
  `trigger_price`.
- Stop order without `trigger_price` (dragging a stop line): `price` becomes the new trigger and the
  limit keeps its previous distance from the trigger: `limit = price + (old prc - old trgprc)`, rounded
  to 2 decimals. Example: old trigger 931, old limit 930, drag to 935 sends trigger 935, limit 934.
- Exchange and trading symbol come from the order book row.
- Quantity is the new total order quantity, not the remaining unfilled quantity. The service does not
  check lot-size multiples, tick alignment, quantity against fills, or positivity of the adjusted
  limit after moving a stop. Broker validation determines whether those modifications are accepted.

Example request:

```json
{"price": 945.5}
```

Response `200`:

```json
{"order_id": "26101400012345", "status": "modified"}
```

Endpoint-specific `422` messages:

- `Order <id> is not in today's order book`
- `Order <id> is <status> and cannot be modified` (for example `complete`, `rejected`, `canceled`)
- `Price must be above 0`

Side effects: outbound `POST /OrderBook`, then `POST /ModifyOrder` (see
[broker-integration.md](broker-integration.md#modifyorder)). No database write.

---

## POST /api/orders/{order_id}/cancel

Cancels an open order.

- Auth required: yes
- Path parameter: `order_id` (string)
- Request body: none

Response `200`:

```json
{"order_id": "26101400012345", "status": "cancel requested"}
```

There is no order book lookup first; TradeSmart decides whether the order can still be cancelled.

Errors: see [Error mapping](#error-mapping). A rejected cancellation returns
`502 TradeSmart: <emsg>` (default `The cancellation was rejected by TradeSmart`).

Side effects: outbound `POST /CancelOrder` with `{"uid", "norenordno"}`. No database write.

---

## POST /api/orders/close-position

Squares off a position with an opposite market order (sent as a protected limit).

This route submits an opposite order using the supplied fields; it does not fetch or verify a current
position or cap the quantity to that position. A stale or oversized request can open or reverse a
position, and a protected limit may remain unfilled.

- Auth required: yes

Request body (`ClosePositionBody`):

| Field | Type | Required | Constraints | Notes |
| --- | --- | --- | --- | --- |
| `symbol` | string | yes | length 1 to 64 | |
| `exchange` | string | yes | length 2 to 16 | |
| `product` | string | yes | | `MIS`, `CNC` or `NRML` |
| `side` | string | yes | | The position side: `long` sends a SELL; any other value sends a BUY |
| `quantity` | integer | yes | 1 to 1 000 000 | Quantity to close |

Example request:

```json
{"symbol": "SBIN", "exchange": "NSE", "product": "MIS", "side": "short", "quantity": 10}
```

Response `200`:

```json
{"order_id": "26101400012350", "sent_price": 957.25}
```

Errors: same as [POST /api/orders](#post-apiorders) with `price_type` `MARKET`.

Side effects: outbound `POST /GetQuotes`, then `POST /PlaceOrder` (`prctyp` `LMT`, protected price).

---

## Notes and possible issues

- `close-position` compares `side` to the exact lower-case string `long`. `"LONG"`, `"Long"` or a typo
  is treated as short and sends a BUY, which would add to a long position instead of closing it.
- `PlaceOrder` always carries `remarks: "smarttrader-agent"`, also for orders placed from the chart.
- For `LIMIT` and `SL` orders the given `price` is not rounded to the tick; TradeSmart rejects prices off
  the tick.
- `modify` returns the path `order_id`; the order number TradeSmart returns is not checked against it.
- Detail strings for broker errors are prefixed with `TradeSmart: ` here, but not in the funds and chart
  routers.
- These mutations have no idempotency key or durable local order-intent record. A network failure
  after broker acceptance returns 502 even though an order may already exist. Check the broker order
  book before repeating a request; a retry can place another order. Cancellation or modification
  can likewise have an unknown outcome after a lost response.
- Price fields have lower bounds but no explicit finite-number constraint. Do not send non-finite
  values: positive infinity can pass local range checks and serialize as `inf`, or cause a later
  calculation/response error. The API does not guarantee a clean 422 response for this case.
