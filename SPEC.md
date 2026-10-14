# SmartTrader Specification

A small single-user trading application for TradeSmart Online (Noren v2 API).
The user enters their TradeSmart API key, API secret and redirect URL, presses
Login, authenticates on the TradeSmart portal, and is redirected back to the
app. After login the master contract (symbol master) downloads automatically
in the background and the user lands on a dashboard that shows their funds.

References:
- TradeSmart API docs (Markdown): `../tradesmart api docs/`
  (start with `04-authentication.md`, `15-login-user-details-generate-access-token.md`,
  `26-holdingsandlimits-limits.md`, `21-ordersandtrades-positionbook.md`, `08-script-master.md`)
- OpenAlgo TradeSmart broker plugin: `../openalgo/broker/tradesmart/`
  (`api/auth_api.py`, `api/baseurl.py`, `api/funds.py`, `database/master_contract_db.py`)

## Global rules

- No icons and no emoji anywhere (UI, code, comments, logs, docs). Plain text only.
- No em dashes or en dashes anywhere. Use commas, colons, parentheses or full stops.
- No `.env` files for credentials. API key, API secret and redirect URL live in SQLite.
- The API secret and the broker access token are never sent to the browser.

## Versions (use the latest releases)

- Backend: Python 3.14, FastAPI 0.143.0 (latest), uvicorn 0.54.0, SQLAlchemy 2.x,
  httpx, cryptography. Managed with uv (`uv add`).
- Frontend: React 19.3.0 and react-dom 19.3.0 (latest), TypeScript 7.0.2, Vite 8.3.4,
  @vitejs/plugin-react 6.x, react-router 7.x, @tanstack/react-query 5.x,
  tailwindcss 4.x with @tailwindcss/vite.

## Layout

```
smarttrader/
  SPEC.md
  README.md
  backend/            FastAPI + SQLite (Python 3.14, managed with uv)
    pyproject.toml
    app/
      main.py         FastAPI app, routers, static frontend serving
      config.py       paths and constants (no secrets)
      db.py           SQLAlchemy 2 engine/session, init
      models.py       ORM tables
      crypto.py       Fernet encryption at rest
      broker/
        tradesmart.py REST client: auth checksum, GenAcsTok, Limits, PositionBook
        master_contract.py  download + parse + store symbol master
      routers/
        auth.py       /api/config, /api/auth/*, /tradesmart/callback
        funds.py      /api/funds
        master.py     /api/master-contract/*, /api/symbols/search
        chart.py      /api/chart/intervals, /api/chart/history
        stream.py     /api/ws/ticks (live ticks relayed from the TradeSmart WebSocket)
        agent.py      /api/agent/* (AI agent: settings, ChatGPT sign-in, conversations, streaming chat)
        orders.py     /api/orders/* (chart trading: place, modify, cancel, close position, symbol state)
      agent/          Agno agent: builder.py (LiteLLM chatgpt/ model, SQLite history in data/agent.db),
                      tools.py (read-only TradeSmart tools), chatgpt_auth.py (OAuth device flow, encrypted tokens)
      deps.py         session cookie dependency
    data/             created at runtime: smarttrader.db, secret.key, tmp/
  frontend/           React 19 + TypeScript 7 + Vite 8
```

## Ports and routing

- Backend: `http://127.0.0.1:8000` (uvicorn). The FastAPI route
  `GET /tradesmart/callback` handles the OAuth callback.
- Redirect URL to register with TradeSmart: `http://127.0.0.1:8000/tradesmart/callback`.
  This is the default suggestion shown in the UI.
- Frontend dev server: `http://127.0.0.1:5173` (Vite). Vite proxies `/api` (and
  `/tradesmart`, harmless) to the backend.
- Production: `npm run build` writes `frontend/dist`; the backend serves it at `/`
  with SPA fallback (any GET that is not `/api/*` or `/tradesmart/*` and is not a
  real file returns `index.html`). Everything then runs on port 8000.
- Post-callback redirect target (`FRONTEND_URL`): if `frontend/dist/index.html`
  exists the backend serves the UI itself, so redirect relative (`/dashboard`,
  `/login?...`). Otherwise (dev, UI on Vite) redirect absolute to
  `http://127.0.0.1:5173/dashboard` (and `/login?...`). The dev URL is a constant
  in `config.py`, overridable with the non-secret environment variable
  `SMARTTRADER_FRONTEND_URL`. The session cookie set on 127.0.0.1:8000 is also
  sent to 127.0.0.1:5173 because cookies are not port specific, as long as the
  hostname matches (127.0.0.1 versus localhost matters).

## Data storage (SQLite at backend/data/smarttrader.db)

Encryption key: `backend/data/secret.key`, a Fernet key generated automatically on
first start (not an .env file). `api_secret` and `access_token` are stored
encrypted with it.

Tables:

- `broker_config` (single row, id=1): `api_key` TEXT, `api_secret_enc` TEXT,
  `redirect_url` TEXT, `updated_at` DATETIME.
- `auth_session`: `id` PK, `session_hash` TEXT unique (SHA-256 of the cookie value;
  the raw cookie value is never stored), `user_id` TEXT, `user_name` TEXT,
  `access_token_enc` TEXT, `created_at` DATETIME, `is_active` BOOLEAN.
  Only one active session at a time: a new login deactivates older rows.
- `symtoken`: `id` PK, `symbol`, `brsymbol`, `name`, `exchange`, `brexchange`,
  `token`, `expiry`, `strike` REAL, `lotsize` INTEGER, `instrumenttype`,
  `tick_size` REAL. Indexes on (`symbol`,`exchange`), `token`, `brsymbol`.
- `master_contract_status` (single row, id=1): `status` TEXT
  (`idle` | `downloading` | `success` | `error`), `message` TEXT,
  `total_records` INTEGER, `exchanges_json` TEXT (JSON object exchange -> count),
  `started_at` DATETIME, `finished_at` DATETIME.

## Authentication flow

1. Login page loads `GET /api/config` and pre-fills API key and redirect URL.
2. User presses Login: `POST /api/auth/login` saves the config and returns the
   authorize URL `https://v2api.tradesmartonline.in/OAuthlogin/authorize/oauth?client_id=<api_key>`.
   The frontend sets `window.location.href` to it.
3. TradeSmart redirects the browser to `<redirect_url>?code=<code>`, which is
   `GET /tradesmart/callback?code=...` on the backend.
4. Backend computes `checksum = sha256_hex(api_key + api_secret + code)` and POSTs
   `jData={"code": ..., "checksum": ...}` as `text/plain` to
   `https://v2api.tradesmartonline.in/NorenWClientAPIv2/GenAcsTok` (no Bearer header).
5. On `stat == "Ok"`: token = `access_token` (fallback `susertoken`), user id =
   `actid` or `uid` or `USERID`, user name = `uname`. Create the session row, set
   cookie `st_session` (random `secrets.token_urlsafe(32)`, HttpOnly, SameSite=Lax,
   Path=/, no Secure flag so it works on http://127.0.0.1), start the master contract
   download in a background thread, and respond `302 Location: <FRONTEND_URL>/dashboard`
   (see Ports and routing for how FRONTEND_URL is chosen).
6. On failure respond `302 Location: <FRONTEND_URL>/login?error=<urlencoded message>`.

All authenticated broker calls: POST `<REST_ROOT>/<Endpoint>` with headers
`Content-Type: text/plain` and `Authorization: Bearer <access_token>`, body
`jData=<json>`. If the broker answers with `emsg` containing "Session Expired" or
HTTP 401, deactivate the session and return 401 to the frontend.

## HTTP API contract

All JSON. Errors use FastAPI's shape `{"detail": "<message>"}`.
"Auth" means the `st_session` cookie must map to an active session, else 401.

| Method | Path | Auth | Request | Response |
| --- | --- | --- | --- | --- |
| GET | `/api/health` | no | | `{"status": "ok"}` |
| GET | `/api/config` | no | | `{"api_key": string or null, "redirect_url": string or null, "has_secret": boolean}` |
| POST | `/api/auth/login` | no | `{"api_key": string, "api_secret": string or null, "redirect_url": string}` | `{"authorize_url": string}`. 422/400 if api_key empty, redirect_url not http(s), or api_secret empty while none is stored. An empty or null api_secret keeps the stored one. |
| GET | `/tradesmart/callback?code=` | no | | 302 redirect as described above |
| GET | `/api/auth/session` | no | | `{"authenticated": boolean, "user_id": string or null, "user_name": string or null, "login_time": ISO string or null}` |
| POST | `/api/auth/logout` | no | | `{"status": "ok"}`, deactivates session, deletes cookie |
| GET | `/api/funds` | yes | | see Funds below |
| GET | `/api/master-contract/status` | yes | | `{"status": "idle" or "downloading" or "success" or "error", "message": string or null, "total_records": number, "exchanges": {"NSE": number, ...}, "started_at": ISO or null, "finished_at": ISO or null}` |
| POST | `/api/master-contract/download` | yes | | 202 `{"status": "downloading"}`; 409 if already downloading |
| GET | `/api/symbols/search?q=&exchange=` | yes | q min 2 chars, exchange optional | array (max 50) of `{"symbol", "brsymbol", "name", "exchange", "token", "expiry", "strike", "lotsize", "instrumenttype", "tick_size"}` |
| GET | `/api/chart/intervals` | yes | | `{"intervals": ["1m", "3m", "5m", "10m", "15m", "30m", "1h", "2h", "D"]}` |
| GET | `/api/chart/history?symbol=&exchange=&interval=&days=` | yes | symbol and exchange as in the symbol master, interval from `/api/chart/intervals`, days optional (1 to 1825, default depends on the interval) | `{"symbol", "exchange", "name", "interval", "candles": [{"time": epoch seconds UTC, "open", "high", "low", "close", "volume"}]}`. 404 if the symbol is not in the master contract, 422 for an unknown interval, 502 on a broker error. Intraday uses `/TPSeries` (token, minutes), daily uses `/EODChartData` (`EXCH:BRSYMBOL`); index exchanges fetch from NSE or BSE. |
| WS | `/api/ws/ticks` | yes (cookie; Origin must be this app) | client sends `{"action": "subscribe", "symbol", "exchange"}` | server sends `{"type": "status", "state": "connected" or "error"}`, `{"type": "subscribed", ...}` and `{"type": "tick", "symbol", "exchange", "ltp", "volume" (cumulative day volume), "time" (epoch seconds)}`. The backend holds the Noren socket (`wss://v2api.tradesmartonline.in/NorenWSAPI/`: connect `t=a`, touchline `t=t`, heartbeat `t=h` every 30 s); the access token never reaches the browser. Closes with 4401 without a session; a foreign Origin is refused with 403. |

### Funds response

Built like OpenAlgo `api/funds.py`: call `/Limits` and `/PositionBook` with
`{"uid": user_id, "actid": user_id}`.

```json
{
  "available_cash": 0.0,
  "collateral": 0.0,
  "m2m_unrealized": 0.0,
  "m2m_realized": 0.0,
  "utilised_debits": 0.0,
  "cash": 0.0,
  "payin": 0.0,
  "payout": 0.0,
  "fetched_at": "2026-10-09T10:00:00+05:30"
}
```

- `available_cash = cash + payin - marginused`
- `collateral = brkcollamt`
- `utilised_debits = marginused`
- realized/unrealized summed over PositionBook rows (`rpnl`, `urmtom`, with the
  OpenAlgo fallback `(lp - netavgprc) * netqty * prcftr` when `urmtom` is 0 and netqty != 0).
  PositionBook returning a non-list (for example `{"stat":"Not_Ok","emsg":"no data"}`) means zero P&L.
- Missing numeric fields count as 0.
- Broker error other than session expiry: 502 with the broker `emsg`.

## Master contract

Port the logic of `../openalgo/broker/tradesmart/database/master_contract_db.py`
WITHOUT pandas (use the `csv` module) so the symbol conventions match OpenAlgo:
exchanges NSE, BSE, NFO, BFO, CDS, MCX from
`https://v2api.tradesmartonline.in/<EXCH>_symbols.txt.zip`. Send a normal browser
User-Agent header (the TradeSmart hosts sit behind Cloudflare). Replace the whole
`symtoken` table in one transaction after all files parse, so a failed download
never leaves a half-empty table. Update `master_contract_status` as it progresses
(message such as "Downloading NFO"), and record per-exchange counts. One failing
exchange is logged and skipped; status is `error` only if nothing loaded.

## Frontend

- React 19, TypeScript 7, Vite 8, react-router 7, TanStack Query 5, Tailwind CSS 4
  (via `@tailwindcss/vite`). No icon libraries.
- Routes: `/` redirects to `/dashboard` when authenticated else `/login`;
  `/login`; `/dashboard` (protected, redirects to `/login` on 401).
- Login page: fields API Key, API Secret (password input; when `has_secret` is
  true the placeholder says it is saved and may be left blank), Redirect URL
  (pre-filled from config, else the default suggestion). Shows `?error=` from
  the URL in an error banner. Login button posts and then navigates to
  `authorize_url`. A short note explains the redirect URL must exactly match the
  one registered with TradeSmart.
- Dashboard: header with app name, user name and id, Logout button. Funds cards
  (Available Cash, Collateral, Realized M2M, Unrealized M2M, Utilised Margin,
  plus Cash, Payin, Payout) formatted as Indian rupees (`en-IN`, 2 decimals),
  Refresh button, last fetched time. Master contract card showing status,
  message, per-exchange counts and total; polls status every 2 s while
  `downloading`; a "Download again" button. A small symbol search box that
  calls `/api/symbols/search` and lists results in a table.
- Clean, professional, light and dark aware design.


## AI agent

A ChatGPT-style assistant at `/agent` (settings at `/agent/config`), built with Agno and LiteLLM.

- Model: LiteLLM's `chatgpt/<model>` provider, signed in with a ChatGPT subscription through OpenAI's
  device-code OAuth flow. The flow is started from the settings page; the tokens are stored Fernet-encrypted
  in the `agent_secret` table (LiteLLM's file storage is redirected there) and never reach the browser.
- Agent: Agno `Agent` with SQLite session history (`data/agent.db`, last 8 runs in context), IST date in
  context, at most 20 tool calls per turn, and fixed rules (tool output is data; read only; no invented numbers).
- Read tools (through the user's TradeSmart session): `search_symbols`, `get_quote`, `get_history`,
  `get_technicals`, `get_funds`, `get_positions`, `get_holdings`, `get_order_book`, `get_trade_book`,
  `get_master_contract_status`.
- Order tools (human in the loop): `place_order`, `modify_order`, `cancel_order` exist only while trading is
  switched on (`agent_risk.trading_enabled`, default off). Each is an Agno `requires_confirmation` tool: the run
  pauses, the stream sends a `confirm` frame and ends with `done` reason `paused`, the UI shows an approval card,
  and `POST /api/agent/chat/confirm` resumes the run with the user's decisions. Inside the tool, after approval,
  the risk check runs again (trading on, allowed exchanges, max quantity, max order value, max orders per day,
  no identical order within 60 s). Every proposal, decision and broker answer is written to `agent_order_audit`.
- Tables: `agent_setting` (model, reasoning effort, extra instructions), `agent_secret`, `agent_conversation`,
  `agent_message` (UI transcript with tool calls and usage).

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/agent/status` | sign-in state (email, plan), model, reasoning effort |
| GET / PUT | `/api/agent/settings` | model, reasoning_effort (low, medium, high or null), instructions; GET also lists models and tools |
| POST / GET / DELETE | `/api/agent/chatgpt/login` | start the device flow (returns user_code and verification_url), poll it, cancel it |
| POST | `/api/agent/chatgpt/logout` | forget the tokens |
| POST | `/api/agent/test` | one short completion to check the model |
| GET | `/api/agent/conversations`, `/api/agent/conversations/{id}` | list, transcript |
| PATCH / DELETE | `/api/agent/conversations/{id}` | rename, delete (also the Agno session) |
| POST | `/api/agent/chat` | body `{message, conversation_id or null}`; Server-Sent Events, one `data: {json}` per frame: `start`, `run`, `token`, `reasoning`, `tool_start`, `tool_end`, `usage`, `error`, `done` |
| POST | `/api/agent/chat/{run_id}/cancel` | stop a running turn |
| POST | `/api/agent/chat/confirm` | body `{conversation_id, run_id, decisions: {tool_call_id: bool}}`; resumes a paused run (same frames); 409 when not pending |
| GET / PUT | `/api/agent/risk` | trading_enabled, max_order_value, max_quantity, max_orders_per_day, allowed_exchanges |
| GET | `/api/agent/orders/audit` | last 100 audit rows |

## Orders (chart trading and the agent)

TradeSmart rejects market-type orders from the API, so MARKET goes out as LMT and SL-M as SL-LMT, priced from the
LTP (or trigger) plus OpenAlgo's market price protection slabs (EQ and FUT: 2% under 100, 1% under 500, 0.5% above;
options: 5% under 10, 3% under 100, 2% under 500, 1% above), rounded to the tick against the trader.
Products: MIS (I), CNC (C), NRML (M). Quantities for F&O and MCX must be a multiple of the lot size.

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/orders/symbol?symbol=&exchange=` | open orders (OPEN, TRIGGER_PENDING) and net positions for one instrument, with `multiplier` (prcftr) and `realized` for live P&L |
| POST | `/api/orders` | `{symbol, exchange, side, quantity, product, price_type, price, trigger_price}`; returns `{order_id, sent_price, ltp}` |
| POST | `/api/orders/{order_id}/modify` | `{price, quantity?, trigger_price?}`; a stop order moves its trigger and keeps the limit gap |
| POST | `/api/orders/{order_id}/cancel` | cancel an open order |
| POST | `/api/orders/close-position` | `{symbol, exchange, product, side (long or short), quantity}`; opposite protected market order |

Chart: right-click the price pane for limit, stop and market orders at that price (each opens a ticket that sends
only on an explicit click); open orders are drawn as lines (drag to modify, close button to cancel) and positions as
lines with live P&L = realized + (LTP - average price) x net quantity x multiplier, updated on every tick. Line
gestures open a confirm dialog before anything is sent.
