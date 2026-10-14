# Funds

Source: `backend/app/routers/funds.py`, `backend/app/broker/tradesmart.py` (`fetch_funds`,
`compute_funds`, `position_pnl`).
Tests: `backend/tests/test_funds.py`.

Back to the [API index](README.md).

---

## GET /api/funds

Funds, margin and today's P&L for the logged-in account, computed from TradeSmart `Limits` and
`PositionBook` (same logic as OpenAlgo's TradeSmart `funds.py`).

- Auth required: yes
- Request: none

Response `200`:

```json
{
  "available_cash": 152340.75,
  "collateral": 48000.0,
  "m2m_unrealized": -1250.4,
  "m2m_realized": 3120.0,
  "utilised_debits": 22659.25,
  "cash": 175000.0,
  "payin": 0.0,
  "payout": 0.0,
  "fetched_at": "2026-10-14T10:02:17+05:30"
}
```

| Field | Type | Computation |
| --- | --- | --- |
| `available_cash` | number | `cash + payin - marginused` from Limits |
| `collateral` | number | Limits `brkcollamt` |
| `m2m_unrealized` | number | Sum over PositionBook rows of `urmtom`. When a row has `urmtom == 0` and `netqty != 0`, `(lp - netavgprc) * netqty * prcftr` is used instead (`prcftr` defaults to 1 when missing) |
| `m2m_realized` | number | Sum over PositionBook rows of `rpnl` |
| `utilised_debits` | number | Limits `marginused` |
| `cash` | number | Limits `cash` |
| `payin` | number | Limits `payin` |
| `payout` | number | Limits `payout` |
| `fetched_at` | string | Server time after both broker calls and computation, IST ISO 8601; not a broker market-data timestamp |

All numbers are rounded to 2 decimals. Broker values arrive as strings; missing, empty, non-numeric
or non-finite values count as 0.

When PositionBook is not a list (TradeSmart answers `{"stat": "Not_Ok", "emsg": "no data"}` when there
are no positions), or it returns non-JSON, P&L is 0 and the request still succeeds.
This fallback also hides other parsed non-list error bodies. The response has no completeness or
degraded-data flag, so zero P&L cannot distinguish no positions from an unusable position response.
PositionBook network errors and session expiry still fail the request. Numeric normalization also
does not distinguish a missing price or balance from an actual zero.

Errors:

| Status | `detail` | When |
| --- | --- | --- |
| 401 | `Not authenticated` | No active session cookie |
| 401 | `Session expired, please log in again` | Stored token or user id missing or undecryptable, or TradeSmart answered HTTP 401 or `emsg` containing "Session Expired" on Limits or PositionBook |
| 502 | broker `emsg` (for example `Limits request failed`) | Limits answered `stat` other than `Ok`, or non-JSON (`Unexpected response from TradeSmart (HTTP <code>)`) |
| 502 | `Could not reach TradeSmart` | Network error or timeout (20 s) |

Side effects:

- Outbound: `POST /Limits` then `POST /PositionBook` with `{"uid": user_id, "actid": user_id}`
  (see [broker-integration.md](broker-integration.md#limits-and-positionbook)).
- On session expiry the `auth_session` row is set inactive.
- Nothing is cached; each call hits the broker.
