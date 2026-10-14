# Outbound integrations: TradeSmart Noren v2, ChatGPT and FinEdge

Source: `backend/app/config.py`, `backend/app/broker/tradesmart.py`, `backend/app/broker/history.py`,
`backend/app/broker/orders.py`, `backend/app/broker/order_service.py`, `backend/app/broker/stream.py`,
`backend/app/broker/master_contract.py`, `backend/app/agent/tools.py`, `backend/app/agent/trading.py`,
`backend/app/agent/chatgpt_auth.py`, `backend/app/agent/finedge.py`, `backend/app/agent/research.py`.
Tests: `test_checksum.py`, `test_callback.py`, `test_funds.py`, `test_history.py`, `test_orders.py`,
`test_order_service.py`, `test_stream.py`, `test_master_contract.py`, `test_research.py`.

Back to the [API index](README.md).

This page lists every call the SmartTrader backend makes to external services. The browser never talks
to TradeSmart directly, except for the OAuth login page it is redirected to, and never talks to FinEdge.

Examples use placeholders (`<API_KEY>`, `<API_SECRET>`, `<ACCESS_TOKEN>`, `<USER_ID>`, `<FINEDGE_API_KEY>`) and synthetic
market/order data. Secrets and access tokens are encrypted in `backend/data/smarttrader.db`; the
API key and broker user id are stored as ordinary text. Never copy real credentials or account data
into documentation.

## Hosts and constants

| Constant (`config.py`) | Value |
| --- | --- |
| `TRADESMART_HOST` | `https://v2api.tradesmartonline.in` |
| `REST_ROOT` | `https://v2api.tradesmartonline.in/NorenWClientAPIv2` |
| `GENACSTOK_URL` | `https://v2api.tradesmartonline.in/NorenWClientAPIv2/GenAcsTok` |
| `AUTHORIZE_URL` | `https://v2api.tradesmartonline.in/OAuthlogin/authorize/oauth` |
| `STREAM_URL` | `wss://v2api.tradesmartonline.in/NorenWSAPI/` |
| `USER_AGENT` | A desktop Chrome user agent string (the TradeSmart hosts sit behind Cloudflare, which rejects bare client user agents) |
| `API_TIMEOUT` | 20 s for every REST call |
| `DOWNLOAD_CONNECT_TIMEOUT` / `DOWNLOAD_READ_TIMEOUT` | 15 s / 120 s for the symbol master zips |

The REST client is `httpx.Client` with headers `User-Agent: <USER_AGENT>` and
`Accept: application/json, text/plain, */*`. Calls are synchronous; FastAPI runs the sync route handlers
in its thread pool.

## OAuth login

### Authorize URL (browser redirect)

Built by `build_authorize_url(api_key)` and returned by `POST /api/auth/login`:

```
https://v2api.tradesmartonline.in/OAuthlogin/authorize/oauth?client_id=<API_KEY>
```

The user signs in on the TradeSmart portal, which redirects the browser to the registered redirect URL
with `?code=<code>` (SmartTrader's `GET /tradesmart/callback`).

### GenAcsTok (token exchange)

Called from `GET /tradesmart/callback` by `generate_access_token(api_key, api_secret, code)`.

```
POST https://v2api.tradesmartonline.in/NorenWClientAPIv2/GenAcsTok
Content-Type: text/plain
User-Agent: <USER_AGENT>
Accept: application/json, text/plain, */*

jData={"code": "<code>", "checksum": "<checksum>"}
```

Checksum scheme:

```
checksum = lowercase_hex( SHA-256( api_key + api_secret + code ) )
```

The three strings are concatenated in exactly this order with no separator and encoded as UTF-8
(`make_checksum`, verified by `test_checksum.py`). There is no `Authorization` header on this call.

Expected response:

```json
{
  "stat": "Ok",
  "access_token": "<ACCESS_TOKEN>",
  "actid": "<USER_ID>",
  "uname": "<USER NAME>"
}
```

Handling:

| Response | Result |
| --- | --- |
| Not JSON | `BrokerError("Unexpected response from TradeSmart (HTTP <code>)")` |
| JSON but not an object | `BrokerError("Unexpected response from TradeSmart")` |
| `stat` not `Ok` | `BrokerError(<emsg>)`, default `Authentication failed` |
| No `access_token` and no `susertoken` | `BrokerError("Login succeeded but TradeSmart returned no access token")` |
| No `actid`, `uid` or `USERID` | `BrokerError("Login succeeded but TradeSmart returned no user id")` |
| OK | token = `access_token` or `susertoken`; user id = `actid` or `uid` or `USERID`; name = `uname` |

The token is stored Fernet-encrypted in `auth_session.access_token_enc`.

## Authenticated REST calls

Every other REST call goes through `post_authenticated(endpoint, jdata, access_token)`:

```
POST https://v2api.tradesmartonline.in/NorenWClientAPIv2/<Endpoint>
Content-Type: text/plain
Authorization: Bearer <ACCESS_TOKEN>
User-Agent: <USER_AGENT>
Accept: application/json, text/plain, */*

jData={"uid": "<USER_ID>", "actid": "<USER_ID>"}
```

Body format: the literal prefix `jData=` followed by `json.dumps(jdata)`. The body is not form-encoded
(no URL encoding of the JSON). Numeric fields are sent as strings, as Noren expects.

Response handling (shared by all endpoints):

| Condition | Result |
| --- | --- |
| HTTP 401 | `SessionExpiredError` |
| Body is not JSON | `BrokerError("Unexpected response from TradeSmart (HTTP <code>)")` |
| JSON object whose `emsg` contains `session expired` (case-insensitive) | `SessionExpiredError` |
| Anything else | Parsed JSON returned to the caller, which checks `stat` |

There is no general `raise_for_status` call: statuses other than 401 are interpreted through their
JSON body. Callers differ in how they check `stat`; some book-reading paths treat any non-list body
as an empty result. A non-401 HTTP error carrying an apparently successful JSON body can therefore
be treated as success.

How the routers map these: `SessionExpiredError` becomes `401 Session expired, please log in again` and
deactivates the session (funds, chart, orders); `BrokerError` becomes `502`; `httpx.HTTPError` becomes
`502 Could not reach TradeSmart`. The agent tools turn them into error text for the model instead.

Number parsing: broker numbers arrive as strings; `to_float` turns missing, empty, unparsable and
non-finite values into 0.

### Endpoint summary

| Endpoint | jData | Used by |
| --- | --- | --- |
| `Limits` | `{"uid", "actid"}` | `GET /api/funds`, agent `get_funds` |
| `PositionBook` | `{"uid", "actid"}` | `GET /api/funds`, `GET /api/orders/symbol`, agent `get_funds`, `get_positions` |
| `OrderBook` | `{"uid", "actid"}` | `GET /api/orders/symbol`, `POST /api/orders/{id}/modify`, agent `get_order_book`, agent `modify_order` |
| `TradeBook` | `{"uid", "actid"}` | agent `get_trade_book` |
| `Holdings` | `{"uid", "actid", "prd": "C"}` | agent `get_holdings` |
| `GetQuotes` | `{"uid", "exch", "token"}` | `POST /api/orders`, `POST /api/orders/close-position`, agent `get_quote`, agent `place_order` |
| `TPSeries` | `{"uid", "exch", "token", "st", "et", "intrv"}` | `GET /api/chart/history` (intraday), agent `get_history`, `get_technicals` |
| `EODChartData` | `{"sym", "from", "to"}` | `GET /api/chart/history` (`D`), agent `get_history`, `get_technicals` |
| `PlaceOrder` | see below | `POST /api/orders`, `POST /api/orders/close-position`, agent `place_order` |
| `ModifyOrder` | see below | `POST /api/orders/{id}/modify`, agent `modify_order` |
| `CancelOrder` | `{"uid", "norenordno"}` | `POST /api/orders/{id}/cancel`, agent `cancel_order` |

`uid` and `actid` are both the TradeSmart user id from the session.

Index pseudo exchanges are mapped before any data call (`data_exchange`): `NSE_INDEX` to `NSE`,
`BSE_INDEX` to `BSE`.

### Limits and PositionBook

Called in sequence by `fetch_funds` with `{"uid": "<USER_ID>", "actid": "<USER_ID>"}`.

- `Limits` must answer an object with `stat: "Ok"`, else `BrokerError(<emsg>)`, default
  `Limits request failed`. Fields used: `cash`, `payin`, `payout`, `marginused`, `brkcollamt`.
- `PositionBook` answers a list of rows when there are positions, or `{"stat": "Not_Ok", "emsg": "no data"}`
  when there are none. A non-list answer or a `BrokerError` (for example non-JSON) means zero P&L; a
  session expiry still propagates. Fields used: `rpnl`, `urmtom`, `netqty`, `lp`, `netavgprc`, `prcftr`,
  plus `tsym`, `exch`, `prd` for `GET /api/orders/symbol`. The BrokerError-to-zero fallback is specific
  to `fetch_funds`: the chart's per-symbol state propagates a malformed/non-JSON response as HTTP 502,
  while a parsed non-list body still becomes an empty list. PositionBook network failures also propagate.

See [funds.md](funds.md) for the formulas.

### GetQuotes

```
jData={"uid": "<USER_ID>", "exch": "NSE", "token": "3045"}
```

The orders path reads `lp` (LTP); a `stat: "Not_Ok"` answer raises `BrokerError(<emsg>)`, default
`Quote request failed`. The agent `get_quote` tool also reads `c`, `o`, `h`, `l`, `v`, `ap`, `oi`, `uc`,
`lc`, `wk52_h`, `wk52_l`, `ls`, `ti`, `lut`, `cname`.

The agent's `place_order` path does not apply that explicit `Not_Ok` quote check. If no LTP is present,
MARKET is refused; other price types can continue with their supplied price or trigger.

### TPSeries and EODChartData

Window: `start` = local midnight (IST) `days` days before now, `end` = now; both as epoch-second strings.

Intraday (`1m` to `2h`):

```
POST .../TPSeries
jData={"uid": "<USER_ID>", "exch": "NSE", "token": "3045", "st": "1760121000", "et": "1760432400", "intrv": "5"}
```

`intrv` is the interval in minutes: `1`, `3`, `5`, `10`, `15`, `30`, `60`, `120`.

Daily (`D`):

```
POST .../EODChartData
jData={"sym": "NSE:SBIN-EQ", "from": "1665772200", "to": "1760432400"}
```

`sym` is `<data exchange>:<brsymbol>`.

Response parsing (`parse_candles`):

- A list of rows; each row may itself be a JSON string (EODChartData) and is decoded.
- Time: daily rows prefer `ssboe` (epoch seconds); intraday rows use `time` in IST,
  `DD-MM-YYYY HH:MM:SS` or `DD/MM/YYYY HH:MM:SS`, with `ssboe` as a fallback.
- OHLC from `into`, `inth`, `intl`, `intc`; volume from `intv`; cumulative day volume `v` is used to clamp
  intraday volume glitches.
- Rows with all-zero OHLC or no parsable time are dropped; duplicates by time keep the last row; output is
  sorted.
- An object with `stat: "Not_Ok"` and an `emsg` containing `no data` is an empty series; any other
  `Not_Ok` is `BrokerError(<emsg>)`; any other non-list is
  `BrokerError("Unexpected history response from TradeSmart")`.

### PlaceOrder

Built by `place_payload(user_id, order)`:

```
POST .../PlaceOrder
jData={
  "uid": "<USER_ID>",
  "actid": "<USER_ID>",
  "exch": "NSE",
  "tsym": "SBIN-EQ",
  "qty": "2",
  "prc": "963.9",
  "dscqty": "0",
  "prd": "I",
  "trantype": "B",
  "prctyp": "LMT",
  "ret": "DAY",
  "ordersource": "API",
  "remarks": "smarttrader-agent"
}
```

| Field | Source |
| --- | --- |
| `exch` | Order exchange (`NSE`, `BSE`, `NFO`, `BFO`, `CDS`, `MCX`) |
| `tsym` | `brsymbol` from the symbol master with `&` replaced by `%26` (so `M&M-EQ` survives) |
| `qty` | Quantity as a string |
| `prc` | Price formatted with `f"{price:g}"` |
| `prd` | `MIS` to `I`, `CNC` to `C`, `NRML` to `M` |
| `trantype` | `BUY` to `B`, `SELL` to `S` |
| `prctyp` | `MARKET` and `LIMIT` to `LMT`; `SL` and `SL-M` to `SL-LMT` |
| `trgprc` | Only for `SL` and `SL-M`, formatted with `:g` |
| `ret` | Always `DAY` |
| `ordersource` | Always `API` |
| `remarks` | Always `smarttrader-agent` |

Success: `{"stat": "Ok", "norenordno": "<order number>"}`; the order number is returned. Otherwise
`BrokerError(<emsg>)`, default `The order was rejected by TradeSmart`.

Market-type orders are never sent: TradeSmart answers "MKT Order type not allowed for API order". See
[orders.md](orders.md#market-price-protection) for how MARKET and SL-M prices are protected.

### ModifyOrder

```
POST .../ModifyOrder
jData={
  "uid": "<USER_ID>",
  "actid": "<USER_ID>",
  "exch": "NSE",
  "norenordno": "26101400012345",
  "tsym": "SBIN-EQ",
  "qty": "5",
  "prc": "945.5",
  "prctyp": "LMT",
  "ret": "DAY"
}
```

`exch` and `tsym` come from the order book row (`&` encoded as `%26`). `trgprc` is added only for stop
orders (`prctyp` `SL-LMT`), because Noren rejects a trigger of 0 on a limit order. Success requires
`stat: "Ok"` and `norenordno`; otherwise `BrokerError(<emsg>)`, default
`The modification was rejected by TradeSmart`.

### CancelOrder

```
POST .../CancelOrder
jData={"uid": "<USER_ID>", "norenordno": "26101400012345"}
```

Success: `stat: "Ok"` (returns `result`, else `norenordno`, else the requested id). Otherwise
`BrokerError(<emsg>)`, default `The cancellation was rejected by TradeSmart`.

### OrderBook lookups

`find_order` calls `OrderBook` and returns the row whose `norenordno` matches, or None. A non-list answer
(no orders today) means None. Fields used: `status`, `prctyp`, `prc`, `trgprc`, `qty`, `fillshares`,
`exch`, `tsym`, `trantype`, `prd`, `s_prdt_ali`. The chart service filters open statuses as `OPEN`,
`TRIGGER_PENDING`, `PENDING`; `find_order` itself and the agent modify tool do not enforce open status.

## WebSocket (NorenWSAPI)

Used by `WS /api/ws/ticks` (`relay_ticks`). One broker connection per browser connection.

Connection: `websockets.connect("wss://v2api.tradesmartonline.in/NorenWSAPI/")` with the Chrome
`User-Agent`, a 15 s open timeout and library pings disabled.

| Direction | Message | Purpose |
| --- | --- | --- |
| to broker | `{"t": "a", "uid": "<USER_ID>", "actid": "<USER_ID>", "source": "API", "accesstoken": "<ACCESS_TOKEN>"}` | Connect / authenticate. Sent first |
| from broker | `{"t": "ak", "s": "OK", ...}` | Acknowledgement, awaited for up to 15 s. `s` is compared case-insensitively to `ok` (the docs say `Ok`, the live service answers `OK`). Anything else raises `StreamError` |
| to broker | `{"t": "t", "k": "<EXCH>\|<token>"}` | Touchline subscribe, for example `NSE\|3045`, `MCX\|569900` |
| to broker | `{"t": "u", "k": "<EXCH>\|<token>"}` | Unsubscribe the previous instrument when the browser switches |
| to broker | `{"t": "h"}` | Heartbeat every 30 s |
| from broker | `{"t": "tk", "e": "MCX", "tk": "569900", "lp": "8801.00", "v": "21951", ...}` | Touchline acknowledgement with the full snapshot |
| from broker | `{"t": "tf", "e": "MCX", "tk": "569900", "lp": "8802.00"}` | Touchline feed; carries only changed fields |

The key is built from the symbol master: `data_exchange(brexchange or exchange)` and `token`. Only `tk`
and `tf` messages whose `e|tk` matches the current key are used. From each, `lp` (only when above 0) and
`v` (only when 0 or more) are merged into a running state, and the browser receives
`{"type": "tick", "symbol", "exchange", "ltp", "volume", "time"}` with the server clock as `time`. The
access token is never forwarded.

Failures (`StreamError`): could not connect, no or invalid acknowledgement, refused acknowledgement, or the
broker closed the socket.

## Symbol master files

Downloaded by the master contract job (after login and on `POST /api/master-contract/download`).

```
GET https://v2api.tradesmartonline.in/<EXCH>_symbols.txt.zip
User-Agent: <USER_AGENT>
```

- Exchanges, in order: `NSE`, `BSE`, `NFO`, `CDS`, `MCX`, `BFO`.
- No authentication. Redirects are followed. HTTP status other than 200 fails that exchange.
- The zip is streamed to `backend/data/tmp/master_*/`, and the member named `<EXCH>_symbols.txt` (or the
  only member) is extracted to a fixed name, at most 512 MB.
- Each file is CSV with a header row, read with the `csv` module (UTF-8 with BOM tolerated). Columns used
  include `Exchange`, `Token`, `LotSize`, `Symbol`, `TradingSymbol`, `Expiry`, `Instrument`, `OptionType`,
  `StrikePrice`, `TickSize`.
- Conventions follow OpenAlgo: `instrumenttype` `EQ`, `FUT`, `CE`, `PE` or `INDEX`; NSE indices move to
  `NSE_INDEX` with aliases (for example `NIFTY50` to `NIFTY`, `NIFTYBANK` to `BANKNIFTY`); `SENSEX` and
  `BANKEX` are added on `BSE_INDEX`; expiry `DD-MMM-YY` upper case; derivative symbol
  `name + expiry without dashes + [strike] + FUT/CE/PE`; tick size in rupees (not divided by 100).

## ChatGPT (OpenAI) calls made by the agent

These go through LiteLLM's `chatgpt` provider, not through SmartTrader code directly. The model URL
below is the default base plus `/responses`, as constructed by the installed provider; provider
configuration can override the base.

| Purpose | URL | Triggered by |
| --- | --- | --- |
| Request a device code | `https://auth.openai.com/api/accounts/deviceauth/usercode` | `POST /api/agent/chatgpt/login` |
| Poll for approval | `https://auth.openai.com/api/accounts/deviceauth/token` | background thread after the login request |
| Exchange or refresh tokens | `https://auth.openai.com/oauth/token` | end of the device flow, and token refresh before model calls |
| User verification page (opened by the user) | `https://auth.openai.com/codex/device` | returned as `verification_url` |
| Model requests (Responses API) | `https://chatgpt.com/backend-api/codex/responses` | `POST /api/agent/test`, `/chat`, `/chat/confirm` |

LiteLLM's token file storage is redirected to the encrypted `agent_secret` table, and its interactive
console login is replaced by an error asking the user to sign in from Agent settings, so a request never
blocks waiting for a console login. `litellm_patches.py` repairs tool-call arguments that the ChatGPT
backend sends only in `done` events.

## FinEdge Financials API (research tools)

Module: `backend/app/agent/finedge.py` (key storage, HTTP client, cache) and
`backend/app/agent/research.py` (the 14 agent research tools). Called by the research tools during
`POST /api/agent/chat` and `/chat/confirm` (only when a key is saved) and by
`POST /api/agent/finedge/test`. The routes that manage the key are in
[agent.md](agent.md#finedge-api-key-fundamental-research).

### Host, client and auth

| Item | Value |
| --- | --- |
| Base URL | `https://data.finedgeapi.com` (`finedge.BASE_URL`) |
| Auth | The API key in the `token` query parameter on every request. No header auth |
| Client | One process-wide `httpx.Client` (`lru_cache`), header `Accept: application/json`, no custom User-Agent |
| Timeout | 30 s (`TIMEOUT_SECONDS`) per attempt |
| Retries | On HTTP 429, 500, 502, 503 and 504, up to two retries, sleeping 1 s and then 2 s (`RETRY_DELAYS`). Network errors and timeouts are not retried |
| Method | GET only |

```
GET https://data.finedgeapi.com/api/v1/company-profile/ITC?token=<FINEDGE_API_KEY>
Accept: application/json
```

Query parameters with a value of None or an empty string are dropped; `token` is always added last.
A list value is sent as a repeated parameter (`symbol=ITC&symbol=RELIANCE`). Symbols in a URL path
are trimmed, upper-cased and percent-encoded with no safe characters (`finedge.path_symbol`), so
`M&M` becomes `M%26M`. Symbols are NSE symbols or BSE codes with no exchange suffix.

### Responses and error handling

| FinEdge answer | Result |
| --- | --- |
| 200 with JSON | Parsed JSON returned |
| 200 with an empty body | `None` returned |
| 200 with a body that is not JSON | `FinEdgeError(502, "FinEdge returned a response that is not JSON")` |
| 401, plain text `ER006 - missing or invalid token` (wrong key) | `FinEdgeError(401, ...)` with `invalid_key` true (status 401 and `ER006` in the text) |
| 401, plain text `ER002 - user not authorized to view this content` (company or data outside the plan) | `FinEdgeError(401, ...)` with `not_in_plan` true (401 or 403 that is not `ER006`) |
| 403 | `not_in_plan` true |
| 404 | `FinEdgeError(404, ...)`; the tools answer `Not found` |
| 429 or 5xx after the retries, any other status | `FinEdgeError(<status>, <text>)` |
| `httpx.HTTPError` (connect failure, timeout) | `FinEdgeError(0, "FinEdge is unreachable (<ExceptionClass>)")` |

The error text is FinEdge's body with whitespace collapsed, cut to 200 characters, with any occurrence
of the key replaced by `***` (the reason phrase when the body is empty). The exception text of an
`httpx.HTTPError` is never used because it can contain the request URL, and so the key.

Key masking: the key is never logged and never returned to the browser. `main.py` sets the `httpx`
logger to WARNING, because httpx logs full request lines (including the `token` parameter) at INFO.
API responses carry only `finedge.mask(key)`: 8 asterisks plus the last 4 characters.

How the callers map these errors:

| Caller | Mapping |
| --- | --- |
| `POST /api/agent/finedge/test` | `invalid_key` to 422; status 0 to 502 `FinEdge is unreachable. Try again in a minute.`; anything else to 502 `FinEdge error (<status>): <message>`. Never 401, because the frontend logs the user out on any 401 |
| Research tools | Text for the model, never an exception: rejected key, `Not available` (outside the plan, with a note that the free Basic plan only covers ITC, RELIANCE and HDFCBANK), `Not found`, unreachable, or `Error from FinEdge (<status>): <message>` |

### Key test

`finedge.test_key` makes two calls. `/healthcheck` is not used because it answers 200 even for a wrong
key.

1. `GET /api/v1/company-profile/ITC`: ITC is in every plan, including the free Basic plan. Any error
   here is raised (a wrong key gives 401 `ER006`). Its duration is reported as `latency_ms` and its
   `name` as `sample_company`.
2. `GET /api/v1/company-profile/TCS`: TCS is only in paid plans. Success gives `coverage: "all"`; a
   `not_in_plan` refusal gives `coverage: "sample"`; any other error is raised.

### Observed response details

Verified against the live API:

- `GET /api/v2/quote` returns an object keyed by symbol. The price is in `current_price` (not `price`;
  the code reads `price` only as a fallback) and `change` is a percent string such as `"0.19%"`, which
  the code parses to a number. Other fields used: `open_price`, `high_price`, `low_price`, `volume`,
  `high52`, `low52`, `market_cap`, `tradetime`. Prices are delayed 15 to 30 minutes.
- A batch quote fails entirely (401 `ER002`) if one symbol is outside the plan. The tools then quote
  each symbol on its own (8 threads) and skip the refused ones.
- Statement rows from `/api/v1/financials/{symbol}` have no `header`; they carry `year` and
  `period_end` (an integer `YYYYMMDD`, for example `20260331`) and come newest first. The code sorts
  them by `period_end` and `year` anyway and labels periods as `Mar 2026`.
- Money values are absolute rupees; the tools divide by 1e7 to give Rs crore. Ratio values are
  fractions; the tools multiply profitability ratios, growth rates and averages by 100.

### Endpoints used by each tool

All paths are under `https://data.finedgeapi.com` and also carry `token`. `{s}` is the path symbol.
`statement_type` is `c` (consolidated) or `s` (standalone); with `consolidated=True` the tool tries
`c` first and repeats with `s` when FinEdge returns no rows.

| Tool | FinEdge calls |
| --- | --- |
| `search_companies` | `GET /api/v1/stock-symbols` (cached 24 h) |
| `get_company_profile` | `GET /api/v1/company-profile/{s}`, `GET /api/v1/company-reports/{s}` (a failure of the second is ignored) |
| `get_financial_statements` | `GET /api/v1/financials/{s}` with `statement_type`, `statement_code` (`pl`, `bs` or `cf`) and `period` |
| `get_financial_ratios` | `GET /api/v1/ratios/{s}` with `statement_type` and `ratio_type` `pr`, `le`, `li` and `ef` (four calls per basis) |
| `get_growth_metrics` | `GET /api/v1/financial-metrics/{s}` with `statement_type` and `ratio_type` `gr`, `av` and `cu` |
| `get_stock_quotes` | `GET /api/v2/quote` with `symbol` repeated (up to 20 symbols; one by one on a plan refusal) |
| `get_price_performance` | `GET /api/v1/daily-quotes/{s}` with `from` and `to` (calendar years), `GET /api/v1/index/price-returns` (cached 15 min, failure ignored) |
| `get_valuation_history` | `GET /api/v1/annual-price-ratios/{s}` with `statement_type`, `GET /api/v1/daily-price-ratios/{s}` with `statement_type` and `from` (a year, not before 2018) |
| `get_peers` | `GET /api/v1/peers/{s}` with `group` (`sub_industry`, `industry`, `sector` or `macro_sector`), then `GET /api/v2/quote` for the peers and the company in batches of 100 (an IT company can have more than 150 industry peers); BSE codes with an NSE symbol are quoted through the NSE symbol |
| `compare_companies` | Per company, in parallel (up to 6 threads): `company-profile`, `financials` (pl, annual), `financial-metrics` (`gr`, `av`), `ratios` (`pr`, `le`), `annual-price-ratios`; then one `GET /api/v2/quote` for the covered companies |
| `get_shareholding` | `GET /api/v1/shareholdings/pattern/{s}?period=quarterly`, `GET /api/v1/shareholdings/summary/{s}?period=quarterly` (failure ignored), and `GET /api/v1/shareholdings/ownership-current/{s}` when `top_holders` is above 0 |
| `get_dividends_and_actions` | `GET /api/v1/dividend/{s}`, `GET /api/v1/corporate-actions/{action}/{s}` for `bonus`, `split` and `rights` (failures ignored), `GET /api/v2/quote` for the yield |
| `get_announcements` | One of `GET /api/v1/corp-announcements`, `/api/v1/credit-ratings`, `/api/v1/investor-call-transcripts`, `/api/v1/investor-presentations` with `symbol`, `from_date` and `to_date` (ISO dates) |
| `get_index_overview` | `GET /api/v1/index/market-price/daily-feed` and `GET /api/v1/index/price-returns` (both cached 15 min) |
| Key test (`POST /api/agent/finedge/test`) | `GET /api/v1/company-profile/ITC`, then `GET /api/v1/company-profile/TCS` |

### Process cache

`finedge.cached(name, ttl, loader)` keeps large, slow-changing lists in a process-local dictionary
guarded by a lock:

| Cache name | FinEdge endpoint | TTL |
| --- | --- | --- |
| `stock-symbols` | `/api/v1/stock-symbols` | 24 h (`research.SYMBOLS_TTL`) |
| `index-daily-feed` | `/api/v1/index/market-price/daily-feed` | 15 min (`research.INDEX_TTL`) |
| `index-price-returns` | `/api/v1/index/price-returns` | 15 min |

The cache is cleared (`finedge.clear_cache`) when a key is saved or removed, and lost on restart. A
failed load is not cached. Two threads that miss at the same time both call FinEdge. Everything else
is fetched afresh on each tool call.

## Calls made by the AI agent tools

| Tool | Broker calls |
| --- | --- |
| `search_symbols` | none (local `symtoken`, max 15 rows) |
| `get_quote` | `GetQuotes` |
| `get_history` | `TPSeries` or `EODChartData` |
| `get_technicals` | `TPSeries` or `EODChartData` (400 days for `D`) |
| `get_funds` | `Limits`, `PositionBook` |
| `get_positions` | `PositionBook` |
| `get_holdings` | `Holdings` with `prd: "C"` |
| `get_order_book` | `OrderBook` |
| `get_trade_book` | `TradeBook` |
| `get_master_contract_status` | none |
| `place_order` (after approval) | `GetQuotes`, `PlaceOrder` |
| `modify_order` (after approval) | `OrderBook`, `ModifyOrder` |
| `cancel_order` (after approval) | `CancelOrder` |

The 14 research tools make no TradeSmart calls; their FinEdge calls are listed in
[Endpoints used by each tool](#endpoints-used-by-each-tool).

## Notes and possible issues

- `prc` and `trgprc` are formatted with `f"{value:g}"`, which keeps only 6 significant digits. Prices with
  more digits are silently rounded: `55000.25` is sent as `55000.2`, `121345.5` as `121346`, and
  `1234567.0` as `1.23457e+06`. This affects index futures, MCX bullion and any price of 100 000 or more,
  and can send a price off the tick or different from what the user confirmed. The same formatting is
  used by the chart order endpoints and the agent order tools.
- `tsym` is URL-encoded (`&` to `%26`) inside a JSON body that is otherwise not URL-encoded. This mirrors
  OpenAlgo and is intentional for `PlaceOrder` and `ModifyOrder`; it is not applied to data calls.
- The REST client does not retry or reconcile timed-out mutations. Chart HTTP calls map a timeout to
  `502 Could not reach TradeSmart`; agent tools return error text. For PlaceOrder, ModifyOrder and
  CancelOrder a timeout can follow broker acceptance, so it is not proof that nothing happened.
  Blindly retrying a placement can duplicate exposure.
- FinEdge takes its key as a `token` query parameter, so the key is part of every request URL. It is
  kept out of logs only because the `httpx` logger is set to WARNING and FinEdge errors are rebuilt
  without the URL; any proxy or debug logging that records full URLs would capture it.
