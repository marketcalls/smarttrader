# Chart data

Source: `backend/app/routers/chart.py`, `backend/app/broker/history.py`.
Tests: `backend/tests/test_history.py`.

Back to the [API index](README.md).

---

## GET /api/chart/intervals

Candle intervals the history endpoint accepts.

- Auth required: yes
- Request: none

Response `200`:

```json
{"intervals": ["1m", "3m", "5m", "10m", "15m", "30m", "1h", "2h", "D"]}
```

Errors: `401 Not authenticated`.
Side effects: none.

---

## GET /api/chart/history

Historical OHLCV candles for one instrument from TradeSmart.

- Auth required: yes

Query parameters:

| Name | Type | Required | Constraints | Notes |
| --- | --- | --- | --- | --- |
| `symbol` | string | yes | length 1 to 64 | App symbol as in the symbol master. Trimmed and upper-cased. |
| `exchange` | string | yes | length 2 to 16 | Trimmed and upper-cased. Index exchanges `NSE_INDEX` and `BSE_INDEX` are allowed. |
| `interval` | string | yes | one of the values from `/api/chart/intervals` | Case sensitive (`D`, not `d`) |
| `days` | integer | no | 1 to 1825 | Look-back window. Default depends on the interval (table below). |

Default look-back (`lookback_days`):

| Interval | Default days |
| --- | --- |
| `1m` | 7 |
| `3m`, `5m` | 30 |
| `10m`, `15m` | 60 |
| `30m` | 120 |
| `1h`, `2h` | 180 |
| `D` | 1095 (3 years) |

The window starts at local midnight (IST) `days` days before now and ends now.

Example: `GET /api/chart/history?symbol=SBIN&exchange=NSE&interval=5m&days=2`

Response `200`:

```json
{
  "symbol": "SBIN",
  "exchange": "NSE",
  "name": "SBIN",
  "interval": "5m",
  "candles": [
    {"time": 1760413500, "open": 951.2, "high": 953.0, "low": 950.6, "close": 952.45, "volume": 184230},
    {"time": 1760413800, "open": 952.45, "high": 952.9, "low": 951.1, "close": 951.5, "volume": 97311}
  ]
}
```

| Field | Type | Notes |
| --- | --- | --- |
| `symbol`, `exchange`, `name` | string | From the matching `symtoken` row |
| `interval` | string | Echo of the request |
| `candles` | array | Sorted ascending by `time`, one entry per timestamp (later duplicates win) |
| `candles[].time` | integer | Epoch seconds (UTC). Intraday: the broker's IST `time` string converted; daily: the broker's `ssboe` |
| `candles[].open`, `high`, `low`, `close` | number | From `into`, `inth`, `intl`, `intc` |
| `candles[].volume` | integer | From `intv`. For intraday the value is clamped to be non-negative and not above the rise of the cumulative day volume `v` (repairs a TradeSmart glitch near the close) |

Candles where open, high, low and close are all 0 are dropped. A broker "no data" answer becomes an
empty `candles` array with status 200.

Errors:

| Status | `detail` | When |
| --- | --- | --- |
| 401 | `Not authenticated` | No active session |
| 401 | `Session expired, please log in again` | Token missing or undecryptable, or TradeSmart reported the session expired |
| 404 | `<SYMBOL> on <EXCHANGE> is not in the master contract. Download it or pick another symbol.` | No `symtoken` row with that symbol and exchange, or the row has no token |
| 422 | `Unsupported interval. Supported: 1m, 3m, 5m, 10m, 15m, 30m, 1h, 2h, D` | `interval` not in the list |
| 422 | validation list | Missing parameter, length limits, or `days` outside 1 to 1825 |
| 502 | broker `emsg` or `Unexpected history response from TradeSmart` | TradeSmart answered `Not_Ok` with anything other than "no data", or an unexpected shape |
| 502 | `Could not reach TradeSmart` | Network error or timeout |

The `require_session` dependency runs before the handler. Once authenticated and query validation
has passed, the handler checks interval, symbol lookup (404), stored token/user id (401), then calls
the broker.

Side effects:

- Outbound (see [broker-integration.md](broker-integration.md#tpseries-and-eodchartdata)):
  - intraday intervals: `POST /TPSeries` with `uid`, `exch`, `token`, `st`, `et`, `intrv` (minutes);
  - `D`: `POST /EODChartData` with `sym = "<EXCH>:<BRSYMBOL>"`, `from`, `to`.
  - `NSE_INDEX` and `BSE_INDEX` are fetched from `NSE` and `BSE` respectively.
- On session expiry the `auth_session` row is set inactive.
