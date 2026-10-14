# SmartTrader HTTP API Reference

This folder documents the HTTP, WebSocket and Server-Sent Events API exposed by the
SmartTrader backend (FastAPI, `backend/app`), and the outbound calls the backend makes
to the TradeSmart Online Noren v2 API.

The code is the source of truth. Where the code differs from `SPEC.md`, these pages
describe the code and the difference is listed in [Known discrepancies](#known-discrepancies-with-specmd).

## Contents

| File | Covers |
| --- | --- |
| [auth.md](auth.md) | Health check, broker config, login, OAuth callback, session, logout, SPA fallback |
| [funds.md](funds.md) | Funds and margin summary |
| [master.md](master.md) | Master contract status and download, symbol search |
| [chart.md](chart.md) | Chart intervals and historical candles |
| [stream.md](stream.md) | Live tick WebSocket (`/api/ws/ticks`) |
| [orders.md](orders.md) | Chart trading: place, modify, cancel, close position, per-symbol state |
| [agent.md](agent.md) | AI agent: settings, ChatGPT sign-in, conversations, SSE chat, order approval, risk limits, audit |
| [broker-integration.md](broker-integration.md) | Outbound calls to TradeSmart (REST, WebSocket, symbol files) and to OpenAI for ChatGPT sign-in |

## Base URL

```
http://127.0.0.1:8000
```

- The backend runs under uvicorn on `127.0.0.1:8000`.
- In development the Vite dev server on `http://127.0.0.1:5173` proxies `/api` (and `/tradesmart`)
  to the backend, so the same paths work from either origin.
- When `frontend/dist/index.html` exists the backend also serves the built UI on port 8000
  (see the SPA fallback in [auth.md](auth.md)).
- Use the hostname `127.0.0.1` everywhere, not `localhost`. The session cookie is host-only, so
  it is shared between ports 8000 and 5173 only when the hostname matches.

There is no versioning prefix. Application JSON endpoints live under `/api/`. The OAuth callback
is `GET /tradesmart/callback`; the SPA fallback and FastAPI's generated documentation also use
non-`/api` paths.

## Authentication model

SmartTrader is a single-user application. Authentication is a server-side session tied to an
HTTP cookie.

1. `POST /api/auth/login` stores the TradeSmart API key, API secret (encrypted) and redirect URL
   and returns the TradeSmart authorize URL. The browser navigates there.
2. TradeSmart redirects the browser to `GET /tradesmart/callback?code=...`. The backend exchanges
   the code for a broker access token (`GenAcsTok`), creates an `auth_session` row and sets the
   session cookie.
3. Every protected endpoint reads the cookie and looks up an active session.

Cookie details:

| Attribute | Value |
| --- | --- |
| Name | `st_session` |
| Value | Random `secrets.token_urlsafe(32)`; only its SHA-256 hex digest is stored (`auth_session.session_hash`) |
| HttpOnly | yes |
| SameSite | Lax |
| Path | `/` |
| Secure | no (the app runs on plain `http://127.0.0.1`) |
| Domain | not set (host-only) |
| Expiry | session cookie (no Max-Age); the server-side row stays active until logout, a new login, or a broker "session expired" answer |

Rules:

- Only one session is active at a time. A new successful login deactivates every older session.
- "Auth required: yes" in these pages means the `require_session` dependency is applied. Without a
  cookie that maps to an active session the response is `401 {"detail": "Not authenticated"}`.
- The broker access token and API secret are stored Fernet-encrypted (`backend/data/secret.key`) and
  are never returned to the browser. The browser only ever holds the opaque `st_session` cookie.
- When TradeSmart reports an expired session (HTTP 401 or an `emsg` containing "Session Expired"),
  the funds, chart and orders endpoints mark the session inactive and return
  `401 {"detail": "Session expired, please log in again"}`. The client should send the user back to
  `/login`.
- The WebSocket endpoint authenticates with the same cookie and additionally checks the `Origin`
  header (see [stream.md](stream.md)).

CORS: `CORSMiddleware` allows the origins `http://127.0.0.1:5173` and `http://localhost:5173` with
credentials, all methods and all headers. CORS controls browser access to responses; it does not
guarantee that a request or cookie cannot be sent. HTTP routes do not validate Origin or a CSRF token.
Cookie delivery also depends on the browser's SameSite and host rules. The WebSocket has a separate
explicit Origin check.

## Request conventions

- Request bodies are JSON (`Content-Type: application/json`) and are validated by Pydantic models
  declared in the router modules. Unknown fields are ignored.
- Query parameters are validated by FastAPI `Query` declarations (length and range limits are listed
  per endpoint).
- Symbols and exchanges follow the OpenAlgo conventions stored in the `symtoken` table (for example
  `SBIN` on `NSE`, `NIFTY` on `NSE_INDEX`, `CRUDEOIL19OCT26FUT` on `MCX`). Most endpoints upper-case
  and trim them before lookup.
- Exchanges known to the app: `NSE`, `BSE`, `NFO`, `BFO`, `CDS`, `MCX`, plus the index pseudo
  exchanges `NSE_INDEX` and `BSE_INDEX` (data only, not tradable).

## Response conventions

- Responses are JSON unless stated otherwise (the OAuth callback answers with a 302 redirect, the agent
  chat endpoints stream `text/event-stream`, and the tick endpoint is a WebSocket).
- Timestamps produced by the app (`login_time`, `fetched_at`, `started_at`, `created_at`, and so on)
  are ISO 8601 strings in IST with an explicit offset and second precision, for example
  `2026-10-14T09:15:02+05:30`. They are stored as naive UTC in SQLite and converted on output.
- Candle and tick times are epoch seconds (UTC).
- Money values are plain JSON numbers in INR. Funds values are rounded to 2 decimals.

## Error format

Handled HTTP errors use FastAPI's default shape. OAuth errors use redirects, WebSocket errors use
frames or close codes, and errors during an agent stream use SSE frames. Unhandled exceptions can
produce HTTP 500 responses that do not follow the JSON `detail` shape.

Errors raised by the application (`HTTPException`) carry a string:

```json
{"detail": "Session expired, please log in again"}
```

Request validation errors (missing field, wrong type, a `Query` or `Field` limit violated) are
generated by FastAPI and carry a list:

```json
{
  "detail": [
    {
      "type": "string_too_short",
      "loc": ["query", "q"],
      "msg": "String should have at least 2 characters",
      "input": "a",
      "ctx": {"min_length": 2}
    }
  ]
}
```

Clients should therefore handle `detail` being either a string or an array.

Status codes used across the API:

| Status | Meaning in SmartTrader |
| --- | --- |
| 200 | Success |
| 202 | Accepted, work started in the background (master contract download) |
| 302 | Redirect (OAuth callback only) |
| 400 | Business validation failed on login input |
| 401 | No active session (`Not authenticated`), or the broker session expired (`Session expired, please log in again`) |
| 404 | Unknown resource (symbol not in the master contract, conversation not found, unknown `/api/*` path) |
| 405 | HTTP method not supported for the requested route |
| 409 | Conflict with current state (download already running, ChatGPT not signed in, approval no longer pending) |
| 422 | Request validation failed (FastAPI) or an order input was rejected by SmartTrader's own checks |
| 500 | Unhandled server failure; no uniform JSON error contract |
| 502 | TradeSmart or the model provider answered with an error or could not be reached |

## Endpoint index

37 application routes: 34 HTTP endpoints under `/api`, one WebSocket, the OAuth callback and the
SPA fallback. Generated OpenAPI and documentation routes are additional.

| # | Method | Path | Auth | Purpose | Doc |
| --- | --- | --- | --- | --- | --- |
| 1 | GET | `/api/health` | no | Liveness check | [auth.md](auth.md#get-apihealth) |
| 2 | GET | `/api/config` | no | Saved API key and redirect URL, whether a secret is stored | [auth.md](auth.md#get-apiconfig) |
| 3 | POST | `/api/auth/login` | no | Save broker credentials, get the TradeSmart authorize URL | [auth.md](auth.md#post-apiauthlogin) |
| 4 | GET | `/tradesmart/callback` | no | OAuth callback, creates the session, sets the cookie | [auth.md](auth.md#get-tradesmartcallback) |
| 5 | GET | `/api/auth/session` | no | Current session state | [auth.md](auth.md#get-apiauthsession) |
| 6 | POST | `/api/auth/logout` | no | End the session, delete the cookie | [auth.md](auth.md#post-apiauthlogout) |
| 7 | GET | `/{full_path:path}` | no | SPA fallback for the built frontend | [auth.md](auth.md#get-path-spa-fallback) |
| 8 | GET | `/api/funds` | yes | Funds, margin and day P&L | [funds.md](funds.md#get-apifunds) |
| 9 | GET | `/api/master-contract/status` | yes | Symbol master download status | [master.md](master.md#get-apimaster-contractstatus) |
| 10 | POST | `/api/master-contract/download` | yes | Start a symbol master download | [master.md](master.md#post-apimaster-contractdownload) |
| 11 | GET | `/api/symbols/search` | yes | Search the symbol master | [master.md](master.md#get-apisymbolssearch) |
| 12 | GET | `/api/chart/intervals` | yes | Supported candle intervals | [chart.md](chart.md#get-apichartintervals) |
| 13 | GET | `/api/chart/history` | yes | Historical OHLCV candles | [chart.md](chart.md#get-apicharthistory) |
| 14 | WS | `/api/ws/ticks` | yes (cookie and Origin) | Live LTP and volume ticks | [stream.md](stream.md) |
| 15 | GET | `/api/orders/symbol` | yes | Open orders and net positions for one instrument | [orders.md](orders.md#get-apiorderssymbol) |
| 16 | POST | `/api/orders` | yes | Place an order | [orders.md](orders.md#post-apiorders) |
| 17 | POST | `/api/orders/{order_id}/modify` | yes | Modify an open order | [orders.md](orders.md#post-apiordersorder_idmodify) |
| 18 | POST | `/api/orders/{order_id}/cancel` | yes | Cancel an open order | [orders.md](orders.md#post-apiordersorder_idcancel) |
| 19 | POST | `/api/orders/close-position` | yes | Square off a position | [orders.md](orders.md#post-apiordersclose-position) |
| 20 | GET | `/api/agent/status` | yes | ChatGPT sign-in state and current agent settings | [agent.md](agent.md#get-apiagentstatus) |
| 21 | GET | `/api/agent/settings` | yes | Agent settings plus model, effort and tool lists | [agent.md](agent.md#get-apiagentsettings) |
| 22 | PUT | `/api/agent/settings` | yes | Update model, reasoning effort, instructions | [agent.md](agent.md#put-apiagentsettings) |
| 23 | POST | `/api/agent/chatgpt/login` | yes | Start the ChatGPT device-code sign-in | [agent.md](agent.md#post-apiagentchatgptlogin) |
| 24 | GET | `/api/agent/chatgpt/login` | yes | Poll the sign-in flow | [agent.md](agent.md#get-apiagentchatgptlogin) |
| 25 | DELETE | `/api/agent/chatgpt/login` | yes | Abandon the sign-in flow | [agent.md](agent.md#delete-apiagentchatgptlogin) |
| 26 | POST | `/api/agent/chatgpt/logout` | yes | Forget the ChatGPT tokens | [agent.md](agent.md#post-apiagentchatgptlogout) |
| 27 | POST | `/api/agent/test` | yes | One short completion to test the model | [agent.md](agent.md#post-apiagenttest) |
| 28 | GET | `/api/agent/conversations` | yes | List conversations | [agent.md](agent.md#get-apiagentconversations) |
| 29 | GET | `/api/agent/conversations/{conversation_id}` | yes | Conversation transcript | [agent.md](agent.md#get-apiagentconversationsconversation_id) |
| 30 | PATCH | `/api/agent/conversations/{conversation_id}` | yes | Rename a conversation | [agent.md](agent.md#patch-apiagentconversationsconversation_id) |
| 31 | DELETE | `/api/agent/conversations/{conversation_id}` | yes | Delete a conversation | [agent.md](agent.md#delete-apiagentconversationsconversation_id) |
| 32 | POST | `/api/agent/chat` | yes | Send a message, stream the reply (SSE) | [agent.md](agent.md#post-apiagentchat) |
| 33 | POST | `/api/agent/chat/confirm` | yes | Approve or reject proposed orders, resume the run (SSE) | [agent.md](agent.md#post-apiagentchatconfirm) |
| 34 | POST | `/api/agent/chat/{run_id}/cancel` | yes | Stop a running turn | [agent.md](agent.md#post-apiagentchatrun_idcancel) |
| 35 | GET | `/api/agent/risk` | yes | Agent trading switch and risk limits | [agent.md](agent.md#get-apiagentrisk) |
| 36 | PUT | `/api/agent/risk` | yes | Update the trading switch and limits | [agent.md](agent.md#put-apiagentrisk) |
| 37 | GET | `/api/agent/orders/audit` | yes | Last 100 agent order audit rows | [agent.md](agent.md#get-apiagentordersaudit) |

FastAPI also serves its generated OpenAPI document at `/openapi.json` and the interactive docs at
`/docs` and `/redoc`, because they are registered before the SPA fallback.

## Background work and side effects at a glance

| Trigger | Side effect |
| --- | --- |
| Backend start (lifespan) | Creates `backend/data/`, the SQLite tables and `secret.key` if missing; a master contract status left at `downloading` by a crashed process is reset to `error` |
| `GET /tradesmart/callback` success | Deactivates old sessions, inserts a session row, starts the master contract download thread |
| `POST /api/master-contract/download` | Starts the master contract download thread |
| `POST /api/orders`, `/modify`, `/cancel`, `/close-position` | Placement, modification or cancellation requests on the TradeSmart account |
| `POST /api/agent/chat`, `/chat/confirm` | Writes conversation and message rows, Agno session history in `data/agent.db`, and (when approved) real orders plus `agent_order_audit` rows |
| `POST /api/agent/chatgpt/login` | Starts a background thread that polls OpenAI until the device code is approved, then stores encrypted tokens |

## Known discrepancies with SPEC.md

- SPEC.md says errors use `{"detail": "<message>"}`. Validation errors produced by FastAPI use a
  list of error objects instead (see [Error format](#error-format)).
- SPEC.md says a WebSocket with a foreign Origin "is refused with 403". The code closes the socket
  with code `4403` before accepting it; over a real ASGI server this surfaces to the browser as an
  HTTP 403 on the handshake, while the Starlette test client reports close code 4403.
- SPEC.md does not list the WebSocket server message `{"type": "error", "message": ...}` sent for an
  unknown symbol, nor the broker unsubscribe (`t=u`) sent when the browser switches instruments.
- SPEC.md (Orders) says open orders are those with status `OPEN` or `TRIGGER_PENDING`. The code also
  treats `PENDING` as open.
- The SSE frame list in the SPEC.md agent table omits the `confirm` frame (it is described in the
  prose above the table). The code emits it.
- SPEC.md's layout lists only `tradesmart.py` and `master_contract.py` under `broker/`. The code also
  has `history.py`, `stream.py`, `orders.py` and `order_service.py`, and `agent/` has `trading.py` and
  `litellm_patches.py`.
- SPEC.md says authenticated calls that hit an expired broker session "deactivate the session and
  return 401". The funds, chart and orders routers do this. The agent tools and the tick stream do
  not deactivate the session: the agent tool returns an error string to the model, and the stream
  sends a `status: error` message and closes with 1011.

Further notes on behaviour that looks unintended are listed at the end of each page.
