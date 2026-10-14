# SmartTrader: Product Requirements Document

Status: reverse-engineered from the product as built (backend source, built frontend bundle, tests).
Version of the product described: backend 0.1.0 (FastAPI app `SmartTrader`), frontend bundle in `frontend/dist`.
Date of review: 2026-10-14.

Sources of truth, in order of precedence:

1. Backend code: `backend/app/routers/*`, `backend/app/broker/*`, `backend/app/agent/*`, `backend/app/models.py`.
2. Built frontend bundle: `frontend/dist/assets/*.js` (UI strings and API calls were grepped to confirm each screen).
   The frontend source folder (`frontend/src`) exists but is empty, so the bundle is the only UI evidence.
3. Backend tests: `backend/tests/*` (86 test functions, 94 cases with parametrization).
4. `SPEC.md` (original design spec) and `README.md` (feature summary). Where they differ from the code, the code wins
   and the difference is listed in section 14.

Requirement ID prefixes: AUTH (authentication and broker setup), MC (master contract and symbol search), DASH
(dashboard and funds), CHART (charts and market data), ORD (chart trading and orders), AGENT (AI trading agent and its
configuration), UX (theming and general UX), NFR (non-functional requirements).

---

## 1. Product summary

SmartTrader is a small, single-user, locally hosted trading application for clients of TradeSmart Online, an Indian
stock broker, built on the broker's Noren v2 REST and WebSocket API. It covers the Indian exchanges NSE, BSE, NFO, BFO,
CDS and MCX.

The user enters their TradeSmart API key, API secret and redirect URL once, signs in through the TradeSmart OAuth
portal, and lands on a dashboard with their funds. The full symbol master (about 1.7 lakh instruments) downloads in the
background after every login. From there the user can:

- search any instrument and open a candlestick chart with broker history, live ticks, a large indicator library and
  drawing tools;
- trade directly from the chart (order ticket, right-click at a price, drag order lines to modify, close positions
  from position lines), with every action confirmed first;
- chat with an AI assistant (Agno agent on a ChatGPT subscription via LiteLLM) that reads funds, positions, holdings,
  order and trade books, quotes, history and technicals through the user's own broker session, and that can,
  only when explicitly switched on, propose real orders that each wait for the user's approval and pass a risk check;
- with a FinEdge Financials API key saved in Agent settings, ask the same assistant fundamental questions, get a
  research report on a company from fundamental and price data, compare companies side by side, and, only when
  asked, get a buy or sell research view for a medium term (6 to 18 months) or long term (3 years or more) horizon
  that ends with a line saying it is not investment advice.

The application server runs on the user's own machine at `http://127.0.0.1:8000`; broker and model calls use external
services. API secrets, tokens and the FinEdge API key are stored encrypted in local SQLite and are not returned to
the browser. The TradeSmart API key is returned by `/api/config`, and the user enters the API secret in the browser
during setup.

## 2. Problem statement

Retail traders on TradeSmart Online who want programmatic or assisted trading face several gaps:

- The broker's API needs an OAuth code exchange with a SHA-256 checksum, a bearer token and a symbol master that is
  split across six zipped exchange files in different formats. Getting to a first working call is tedious.
- There is no light, self-hosted front end that combines funds, charts with live data, and chart-based order entry on
  top of the API.
- TradeSmart refuses MARKET and SL-M orders sent through the API, so naive API clients fail on the most common order
  types.
- Traders who want to use an LLM to ask questions about their account and the market have no safe way to give the
  model access: the access token must not leak into prompts, and the model must never be able to send an order
  without a human approving it.

SmartTrader solves these by doing the OAuth and symbol master work for the user, presenting a clean trading UI,
translating market-type orders into protected limit orders, and wrapping an AI assistant in read-only tools by
default, with a human-in-the-loop approval gate and server-side risk limits when trading is enabled.

## 3. Target users and personas

### P1. Retail trader with a TradeSmart account ("Ravi")

- Has a TradeSmart Online trading account and has registered an API app (API key, API secret, redirect URL) on the
  TradeSmart developer portal.
- Trades cash equities intraday and delivery, and sometimes index options or MCX commodities.
- Comfortable running two commands in a terminal, not a software developer.
- Wants: one place to see funds and P&L, look up any contract, chart it with live prices and place or adjust orders
  quickly and safely.

### P2. Algo and AI-assisted trader ("Meera")

- Uses SmartTrader as a copilot: asks for quotes, technical summaries, P&L reviews and order book checks in natural
  language.
- Has a ChatGPT Plus, Pro or Team subscription and is willing to sign in with it.
- May let the agent propose orders, but wants hard limits (exchanges, quantity, order value, orders per day), an
  approval step for every order, a kill switch and an audit trail.

### P3. Developer or learner extending the app ("Arjun")

- Uses the codebase as a reference implementation of the TradeSmart (Noren v2) API with OpenAlgo symbol conventions.
- Cares about the documented HTTP contract, tests and local-only deployment.

## 4. Goals and non-goals

### Goals

- G1. Get a TradeSmart user from credentials to an authenticated dashboard in one login flow, with no `.env` files.
- G2. Keep the API secret, the broker access token and the ChatGPT OAuth tokens on the server, encrypted at rest.
- G3. Provide a complete, searchable symbol master for all six exchanges automatically after login.
- G4. Show account funds and day P&L computed the same way as OpenAlgo's TradeSmart plugin.
- G5. Provide a usable charting workspace with broker history in every broker timeframe and live ticks.
- G6. Allow order entry, modification, cancellation and square-off from the chart, with explicit confirmation.
- G7. Provide an AI assistant that is read-only by default and can only trade under human approval and risk limits.
- G8. Run entirely on the user's machine on a single port.
- G9. Let the AI assistant do grounded fundamental research (statements, ratios, growth, valuation, peers,
  shareholding, dividends, filings) with the user's own FinEdge key, quoting every figure from a tool result and
  marking every buy or sell view as research for education, not investment advice.

### Non-goals

- NG1. Multi-user or multi-account operation, user management, roles.
- NG2. Hosting on a server or public network, TLS termination, remote access.
- NG3. Automated strategy execution, backtesting, scheduled or unattended order placement.
- NG4. Investment advice, recommendations or signals presented as advice. The buy or sell views in AGENT-19 are
  research views for education: given only when the user asks, never promising a return, and always ending with a
  not-investment-advice line (see sections 11 and 12).
- NG5. Support for brokers other than TradeSmart Online.
- NG6. Mobile apps.
- NG7. Full order management screens (order book, trade book, positions and holdings pages). These are reachable only
  through the AI agent today (see section 13).

## 5. Scope

### In scope (built)

- Broker credential setup and TradeSmart OAuth login, session cookie, logout.
- Master contract download, status, re-download and symbol search.
- Dashboard with funds, master contract card and symbol search.
- Chart page: symbol search, timeframes, history, live ticks, indicators, drawings, snapshots.
- Chart trading: order ticket, right-click trade menu, order lines, position lines with live P&L, modify, cancel,
  close position.
- AI agent: ChatGPT device-code sign-in, model settings, test connection, streaming chat with tool calls, conversation
  history, read-only tools, approval-gated order tools, risk limits, order audit log.
- Fundamental research in the agent: FinEdge API key save, test and remove on the settings page, 14 read-only
  research tools, research reports, company comparisons, and medium or long term buy or sell research views.
- Theming: light and dark mode, accent color.
- Single-port production serving of the built UI with SPA fallback; development mode with the Vite dev server.

### Out of scope

- Everything in Non-goals.
- Broker-side logout (TradeSmart session invalidation) and token refresh.
- Market-hours awareness, holiday calendars, alerts and notifications.
- Watchlists with multiple simultaneous live subscriptions.
- Exporting data (CSV, reports) and tax or P&L statements.
- Fundamental data from providers other than FinEdge, stock screeners, alerts on fundamentals, and research reports
  saved or exported outside the chat transcript.

---

## 6. Feature requirements

Each feature has user stories with IDs and acceptance criteria (AC). "Verified in" points to the code or bundle
evidence used. Persona shorthand: "trader" is P1, "AI-assisted trader" is P2, "developer" is P3.

### 6.1 Authentication and broker setup (AUTH)

Verified in: `routers/auth.py`, `broker/tradesmart.py`, `deps.py`, `crypto.py`, `config.py`; bundle strings
"Connect Your Broker", "Login with TradeSmart", "A secret is already stored. Enter a new one only to replace it.",
"You opened this page on localhost...", "You are already signed in".

**AUTH-1. Save broker credentials.**
As a trader, I want to enter my TradeSmart API key, API secret and redirect URL on a login page, so that the app can
sign me in without me editing any configuration file.

- AC1. The login page loads `GET /api/config` and pre-fills the API key and redirect URL when they were saved before.
- AC2. When no redirect URL is saved, the field suggests `http://127.0.0.1:8000/tradesmart/callback`.
- AC3. When a secret is already stored (`has_secret` true), the secret field may be left blank and the UI says the
  stored secret is kept.
- AC4. `POST /api/auth/login` rejects an empty API key (400 "API key is required"), a redirect URL that is not http or
  https (400), and an empty secret when none is stored (400 "API secret is required").
- AC5. The API secret is stored only in encrypted form (Fernet, key in `backend/data/secret.key`) and is never
  returned by any endpoint.
- AC6. A short note explains that the redirect URL must exactly match the one registered with TradeSmart.

**AUTH-2. Sign in through the TradeSmart portal.**
As a trader, I want the Login button to take me to the TradeSmart sign-in page, so that I authenticate with the broker
directly and never type my trading password into SmartTrader.

- AC1. On a valid login request the backend saves the config and returns
  `authorize_url = https://v2api.tradesmartonline.in/OAuthlogin/authorize/oauth?client_id=<api_key>`.
- AC2. The browser navigates to that URL ("Redirecting to TradeSmart...").

**AUTH-3. Complete login on callback.**
As a trader, I want the app to finish the login automatically when TradeSmart sends me back, so that I land on my
dashboard without extra steps.

- AC1. `GET /tradesmart/callback?code=...` computes `checksum = sha256_hex(api_key + api_secret + code)` and posts it
  with the code to `GenAcsTok`.
- AC2. On success the backend stores the access token encrypted, deactivates any earlier active session (only one
  active session at a time), and sets cookie `st_session` (random 32-byte URL-safe value, HttpOnly, SameSite=Lax,
  Path=/, not Secure). Only the SHA-256 hash of the cookie value is stored.
- AC3. The master contract download starts in the background (see MC-1).
- AC4. The response is a 302 to `/dashboard` (relative when the backend serves the built UI, else the Vite dev URL
  `http://127.0.0.1:5173`, overridable with `SMARTTRADER_FRONTEND_URL`).

**AUTH-4. See why login failed.**
As a trader, I want a clear error message when login fails, so that I know whether to fix my credentials, my redirect
URL or retry later.

- AC1. Missing code, missing config, broker rejection, network failure and unexpected errors all redirect to
  `/login?error=<message>` (message trimmed to 300 characters).
- AC2. The login page shows the `error` query parameter in an error banner ("Login failed").
- AC3. Broker messages (`emsg`, `error_description`) are included when present.

**AUTH-5. Stay signed in and protect pages.**
As a trader, I want the app to remember my session and send me to the right page, so that I do not log in on every
page load.

- AC1. `GET /api/auth/session` returns `authenticated`, `user_id`, `user_name` and `login_time` (IST ISO string).
- AC2. `/` redirects to `/dashboard` when authenticated, else to `/login`.
- AC3. `/dashboard`, `/trading`, `/agent` and `/agent/config` redirect to `/login` when not authenticated.
- AC4. Visiting `/login` while signed in shows "You are already signed in" with a link to the dashboard.
- AC5. Registered protected HTTP API routes return 401 without an active session. Health, config, login, session
  and logout are public; unknown API paths return 404. The tick WebSocket uses its own handshake checks.

**AUTH-6. Log out.**
As a trader, I want to log out, so that the local session cannot be reused.

- AC1. `POST /api/auth/logout` deactivates the session row and deletes the cookie; it returns `{"status": "ok"}` even
  without a session.
- AC2. The UI clears cached funds, master status, symbols, chart and agent data and navigates to `/login`.

**AUTH-7. Handle broker session expiry.**
As a trader, I want the app to notice when TradeSmart has expired my token, so that I am asked to log in again
instead of seeing confusing errors.

- AC1. Funds, chart history and chart order routes deactivate the local session on a broker HTTP 401 or an `emsg`
  containing "Session Expired", returning 401 "Session expired, please log in again".
- AC2. Those routes also deactivate sessions with undecryptable tokens. Agent chat returns 401 for an undecryptable
  token without deactivating the row; agent tools report broker expiry as tool text without deactivation.
- AC3. The frontend reacts to a 401 by returning to login. A tick connection with missing local credentials closes
  with 4401 and stops reconnecting. Broker stream refusal instead reports an error and closes with 1011, leaving the
  local session active. Existing sockets and agent runs are not cancelled by logout or a later login.

**AUTH-8. Warn about localhost versus 127.0.0.1.**
As a trader, I want to be warned when I open the app on `localhost`, so that I do not get stuck in a login loop
because the session cookie was set for `127.0.0.1`.

- AC1. The login page shows: "You opened this page on localhost. Open it on 127.0.0.1 instead, otherwise the session
  cookie set by the backend will not reach this page."

### 6.2 Master contract and symbol search (MC)

Verified in: `broker/master_contract.py`, `routers/master.py`, `db.py`, `tests/test_master_contract.py`; bundle strings
"Master Contract", "Download again", "Records loaded per exchange", "Symbol Search", "Search the downloaded symbol
master. Select a symbol to open its chart."

**MC-1. Download the symbol master automatically.**
As a trader, I want the symbol master to download by itself after login, so that search, charts and orders work
without a manual step.

- AC1. A successful login starts a background download unless one is already running.
- AC2. Files are fetched from `https://v2api.tradesmartonline.in/<EXCH>_symbols.txt.zip` for NSE, BSE, NFO, CDS, MCX
  and BFO with a browser User-Agent (the hosts sit behind Cloudflare).
- AC3. Status moves through `downloading` with messages such as "Downloading NFO", "Processing NFO",
  "Saving N records", and ends in `success` ("Loaded N records in X s") or `error`.

**MC-2. See download status.**
As a trader, I want to see the master contract status and per-exchange counts, so that I know whether search and
charts are ready.

- AC1. `GET /api/master-contract/status` returns status, message, total_records, exchanges map, started_at and
  finished_at (IST).
- AC2. The dashboard card shows status, message, per-exchange counts and total, and polls every 2 seconds while the
  status is `downloading`.

**MC-3. Download again on demand.**
As a trader, I want to re-download the master contract, so that I can pick up new contracts (for example a new weekly
expiry) without logging in again.

- AC1. `POST /api/master-contract/download` returns 202 `{"status": "downloading"}`.
- AC2. It returns 409 when a download is already in progress; the UI then shows "A download is already in progress.
  Showing its live status."

**MC-4. Never leave a half-empty symbol table.**
As a trader, I want a failed or partial download to keep a usable symbol master, so that a network problem does not
break my charts.

- AC1. The whole `symtoken` table is replaced in one transaction only after all exchanges are parsed.
- AC2. A failing exchange is logged and skipped; the success message lists failed exchanges. If at least one exchange
  loads, its rows replace the entire old master, including removal of old rows for failed exchanges. No loaded rows
  or another fatal failure (such as a database write error) produces status `error`.
- AC3. Duplicate `(token, exchange)` pairs keep the first occurrence.
- AC4. If the process dies during a download, the next start marks the status `error` with "Previous download was
  interrupted".

**MC-5. Use OpenAlgo symbol conventions.**
As a developer, I want symbols to follow OpenAlgo conventions, so that strategies and prompts written for OpenAlgo work
unchanged.

- AC1. Equities drop the series suffix (`BAJAJ-AUTO-EQ` becomes `BAJAJ-AUTO`).
- AC2. Indices live on `NSE_INDEX` and `BSE_INDEX` (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50, SENSEX,
  BANKEX); expiry is `DD-MMM-YY` uppercase; derivatives are `NAME + EXPIRY + [STRIKE] + FUT|CE|PE`
  (for example `CRUDEOIL19OCT26FUT`).
- AC3. Each row stores symbol, broker symbol, name, exchange, broker exchange, token, expiry, strike, lot size,
  instrument type and tick size.

**MC-6. Search symbols.**
As a trader, I want to search by symbol or company name, optionally filtered by exchange, so that I can find any
stock, index, future or option quickly.

- AC1. `GET /api/symbols/search?q=&exchange=` requires at least 2 characters and returns at most 50 results.
- AC2. Every whitespace-separated term (up to 5) must match symbol, broker symbol or name (case-insensitive,
  LIKE wildcards escaped).
- AC3. Results rank an exact symbol match first, then symbols starting with the first term, then shorter symbols.
- AC4. The dashboard search box ("For example RELIANCE or NIFTY") lists results with symbol, name, exchange, expiry,
  strike, lot size and tick size.

**MC-7. Open a chart from search.**
As a trader, I want to click a search result and see its chart, so that I can move from discovery to analysis in one
step.

- AC1. Selecting a dashboard search result navigates to `/trading?symbol=<symbol>&exchange=<exchange>`.

**MC-8. Refresh the symbol master on a schedule (Not implemented).**
As a trader who keeps the app running, I want the symbol master refreshed automatically before market open, so that
new expiries are available without logging in again.

- AC1. A daily refresh runs before 09:00 IST on trading days while the server is running.
- AC2. A scheduled refresh follows the same status, atomic replace and failure rules as MC-1 and MC-4.

### 6.3 Dashboard and funds (DASH)

Verified in: `routers/funds.py`, `broker/tradesmart.py` (`compute_funds`, `fetch_funds`), `tests/test_funds.py`;
bundle strings "Trading Dashboard", "Available Cash", "Collateral", "Realized M2M", "Unrealized M2M",
"Utilised Margin", "Payout", "Refresh", "Signed in as", "User ID".

**DASH-1. See who is signed in and navigate.**
As a trader, I want a header with my name, user id, navigation and logout, so that I always know which account I am
using.

- AC1. The header shows the app name, the user name (fallback "Trader") and user id, links to Dashboard, Charts and
  Agent, the theme controls and a Logout button.

**DASH-2. See my funds.**
As a trader, I want to see available cash, collateral, realized and unrealized M2M, utilised margin, cash, payin and
payout, so that I know my buying power and today's P&L.

- AC1. `GET /api/funds` calls TradeSmart `Limits` and `PositionBook` with `uid` and `actid` set to the user id.
- AC2. `available_cash = cash + payin - marginused`; `collateral = brkcollamt`; `utilised_debits = marginused`.
- AC3. Realized and unrealized P&L are summed over PositionBook rows using `rpnl` and `urmtom`, with the fallback
  `(lp - netavgprc) * netqty * prcftr` (prcftr defaults to 1) when `urmtom` is 0 and `netqty` is not 0.
- AC4. A PositionBook that is not a list (for example "no data") counts as zero P&L. Missing or non-numeric fields
  count as 0.
- AC5. Values are shown in Indian rupee format (`en-IN`, 2 decimals) with short explanations (for example "Cash plus
  payin minus margin used", "Margin blocked by orders and positions").

**DASH-3. Refresh funds.**
As a trader, I want to refresh my funds and see when they were fetched, so that I can trust the numbers.

- AC1. A Refresh button refetches `/api/funds` ("Refreshing...").
- AC2. The response includes `fetched_at` in IST and the UI shows it.

**DASH-4. Understand funds errors.**
As a trader, I want a clear message when funds cannot be loaded, so that I can tell a broker problem from a session
problem.

- AC1. Broker errors other than session expiry return 502 with the broker `emsg`; network errors return 502
  "Could not reach TradeSmart".
- AC2. Session expiry returns 401 and leads to the login page (AUTH-7).
- AC3. The UI shows "Could not load funds" with a retry action.

**DASH-5. Dashboard shows master contract and search together.**
As a trader, I want the master contract card and symbol search on the dashboard, so that I can check readiness and
find instruments without leaving the page.

- AC1. The dashboard contains the funds cards (DASH-2), the master contract card (MC-2, MC-3) and the symbol search
  (MC-6, MC-7).

**DASH-6. See order book, trade book, positions and holdings (Not implemented).**
As a trader, I want dedicated tables for today's orders, trades, positions and my holdings, so that I can review my
account without asking the agent.

- AC1. Each table loads from the matching TradeSmart endpoint (OrderBook, TradeBook, PositionBook, Holdings).
- AC2. Today these are only reachable through the agent tools (AGENT-5) and, per instrument, on the chart (ORD-5).

### 6.4 Charts and market data (CHART)

Verified in: `routers/chart.py`, `broker/history.py`, `routers/stream.py`, `broker/stream.py`, `tests/test_history.py`,
`tests/test_stream.py`; bundle `TradingPage-*.js` (built on the `openalgo-charts` library), strings "Search symbol,
for example SBIN", "Timeframe", "Indicators (/)", "Drawing tools", "Take a snapshot", "Live", "Connecting", "Offline",
"Live data off".

**CHART-1. Pick an instrument on the chart page.**
As a trader, I want a symbol search with an exchange filter on the chart page, so that I can switch instruments
without leaving the chart.

- AC1. The chart page (`/trading`, nav label "Charts") has a symbol search with an exchange filter.
- AC2. With no symbol selected, quick-start choices are offered: NIFTY, BANKNIFTY (NSE_INDEX), SENSEX (BSE_INDEX),
  RELIANCE, HDFCBANK, INFY, TCS, SBIN (NSE).
- AC3. The URL parameters `symbol`, `exchange` and `interval` open a specific chart directly.

**CHART-2. Choose a timeframe.**
As a trader, I want every timeframe the broker supports, so that I can analyse intraday and positional moves.

- AC1. `GET /api/chart/intervals` returns `1m, 3m, 5m, 10m, 15m, 30m, 1h, 2h, D`.
- AC2. An unknown interval returns 422.

**CHART-3. Load historical candles.**
As a trader, I want candles loaded from TradeSmart for the selected instrument and timeframe, so that I see a
meaningful history.

- AC1. `GET /api/chart/history` returns `symbol, exchange, name, interval, candles[time (epoch seconds UTC), open,
  high, low, close, volume]`.
- AC2. Intraday uses `TPSeries` (token, minutes); daily uses `EODChartData` (`EXCH:BRSYMBOL`); NSE_INDEX and
  BSE_INDEX fetch from NSE and BSE.
- AC3. Default lookback: 7 days for 1m, 30 for up to 5m, 60 for up to 15m, 120 for 30m, 180 for hourly, 3 years for
  daily. `days` may override it from 1 to 1825.
- AC4. Candles are sorted, de-duplicated, all-zero rows dropped, and broker volume glitches near the close are clamped
  using the cumulative day volume.
- AC5. 404 when the symbol is not in the master contract; 502 on broker errors; a broker "no data" answer is an empty
  series and the UI explains "The market may be closed for this range, or the contract has not traded. Try a higher
  timeframe."

**CHART-4. See live prices.**
As a trader, I want the last candle to update with live ticks, so that I can act on current prices.

- AC1. The browser opens `ws(s)://<host>/api/ws/ticks` and sends `{"action": "subscribe", "symbol", "exchange"}`.
- AC2. The backend holds the TradeSmart socket (connect `t=a`, touchline `t=t`, unsubscribe `t=u`, heartbeat `t=h`
  every 30 s) and forwards only `{"type": "tick", symbol, exchange, ltp, volume, time}`; the access token never
  reaches the browser.
- AC3. One instrument per connection; switching symbol unsubscribes the previous one.
- AC4. A status badge shows Live, Connecting, Offline or Live data off. On an unexpected close the client reconnects
  with backoff starting at 2 s, doubling to a 30 s cap. Close code 4401 (no session) stops reconnecting.
- AC5. A foreign Origin is refused (close 4403 before accept); an unknown symbol yields `{"type": "error"}`.

**CHART-5. Keep history fresh.**
As a trader, I want the chart history to refresh periodically, so that it stays correct even if ticks are missed.

- AC1. History refetches every 60 s when live data is not running and every 5 minutes while live; a manual Refresh
  button is available.

**CHART-6. Add indicators.**
As a trader, I want to add, configure and remove technical indicators, so that I can analyse the chart my way.

- AC1. An Indicators panel (also opened with the `/` key) lists indicators by category with search ("Search
  indicators", "No indicator matches.").
- AC2. Indicators can be placed as overlay or in a new pane, configured ("Settings") and removed ("Remove all").
- AC3. The active indicator set is remembered in the browser (`smarttrader-indicators`).

**CHART-7. Draw on the chart.**
As a trader, I want drawing tools (lines, channels and pitchforks, Gann and cycles, and others), so that I can mark
levels and patterns.

- AC1. A drawing toolbar with search, magnet, "Stay in drawing mode", undo, redo, remove selected and remove all.
- AC2. Drawings are saved per instrument in the browser (`smarttrader-drawings:<key>`).

**CHART-8. Take a snapshot.**
As a trader, I want to copy or download an image of the chart, so that I can share or journal it.

- AC1. Snapshot offers Copy image, Download image and Open in new tab, with messages "Image copied to the clipboard",
  "Image saved" and "The browser blocked copying. Use Download image instead."

**CHART-9. Remember chart preferences.**
As a trader, I want my last chart choices and panel layout remembered, so that the chart opens the way I left it.

- AC1. Chart state and the side rail are stored in the browser (`smarttrader-chart`, `smarttrader-rail`); storage
  failures are ignored.

### 6.5 Chart trading and orders (ORD)

Verified in: `routers/orders.py`, `broker/order_service.py`, `broker/orders.py`, `tests/test_orders.py`,
`tests/test_order_service.py`; bundle strings "Place an order (or right-click the chart at a price)", "Buy limit at",
"Sell stop at", "Buy at market", "Modify order", "Cancel order", "Close position", "Keep as is", "The order is sent to
TradeSmart as soon as you confirm.", "TradeSmart refuses market orders from the API, so this goes out as a limit
order with market price protection."

**ORD-1. Place an order from an order ticket.**
As a trader, I want an order ticket on the chart, so that I can place an order for the instrument I am looking at.

- AC1. The ticket has side, quantity, product (MIS intraday, CNC delivery, NRML carry), order type (Market, Limit,
  Stop limit SL, Stop market SL-M), price and trigger.
- AC2. Nothing is sent until the user clicks the send button; the ticket says "The order is sent to TradeSmart as soon
  as you confirm."
- AC3. `POST /api/orders` returns `{order_id, sent_price, ltp}`; the UI shows "Order <id> sent: <side> <qty> <symbol>
  at <price>".

**ORD-2. Trade at a price with right-click.**
As a trader, I want to right-click the price pane and choose buy or sell limit, stop or market at that price, so that
I can enter orders at chart levels quickly.

- AC1. The context menu offers Buy or Sell limit at the price, Buy or Sell stop at the price, and Buy or Sell at
  market, depending on where the price is relative to the LTP.
- AC2. Each choice opens the pre-filled ticket; it does not send directly.

**ORD-3. Market orders that TradeSmart accepts.**
As a trader, I want MARKET and SL-M orders to work even though TradeSmart refuses them from the API, so that I can
still trade at market.

- AC1. MARKET is sent as LMT priced from the live LTP (`GetQuotes`) plus market price protection; SL-M is sent as
  SL-LMT priced from the trigger.
- AC2. Protection slabs: equity and futures 2% under 100, 1% under 500, 0.5% above; options (CE, PE) 5% under 10,
  3% under 100, 2% under 500, 1% above.
- AC3. The protected price rounds to the tick against the trader (buys up, sells down); default tick 0.05.
- AC4. A MARKET order with no live price is refused: "No live price to protect a market order; use a limit order".

**ORD-4. Validate before sending.**
As a trader, I want invalid orders caught before they reach the broker, so that I get a clear reason.

- AC1. Exchange must be NSE, BSE, NFO, BFO, CDS or MCX (not an index exchange).
- AC2. Side BUY or SELL; product MIS, CNC or NRML; price type MARKET, LIMIT, SL or SL-M; quantity 1 to 1,000,000.
- AC3. LIMIT and SL need a price; SL and SL-M need a trigger.
- AC4. When the lot size is above 1, quantity must be a multiple of it ("Quantity must be a multiple of the lot size").
- AC5. Validation failures return 422 with the message; the symbol must exist in the master contract.

**ORD-5. See my open orders and positions on the chart.**
As a trader, I want my open orders and net positions for this instrument drawn on the chart, so that I see my exposure
in context.

- AC1. `GET /api/orders/symbol` returns open orders (OPEN, TRIGGER_PENDING, PENDING) with remaining quantity, type and
  line price (trigger for stops), and non-zero net positions with side, quantity, average price, LTP, multiplier
  (`prcftr`), realized and unrealized P&L, plus lot size and tick size.
- AC2. The chart refreshes this every 3 seconds and draws order lines and position lines.

**ORD-6. Modify an order by dragging its line.**
As a trader, I want to drag an order line to a new price, so that I can adjust orders visually.

- AC1. Dropping a line opens a confirm dialog ("Modify order", "Keep as is"); nothing is sent before confirmation.
- AC2. `POST /api/orders/{id}/modify` checks that the order is in today's order book and still open.
- AC3. Dragging a stop order moves its trigger and keeps the same gap to its limit price.
- AC4. On success the UI shows "Order <id> modified to <price>".

**ORD-7. Cancel an order from its line.**
As a trader, I want a close button on each order line, so that I can cancel an order quickly.

- AC1. The close button opens a confirm dialog ("Cancel order"); confirming calls `POST /api/orders/{id}/cancel`.

**ORD-8. See live position P&L.**
As a trader, I want live P&L on my position line, so that I know how my trade is doing tick by tick.

- AC1. P&L = realized + (LTP minus average price) x net quantity x multiplier, recomputed on every tick.
- AC2. The position pill shows product, side, quantity, average price and realized P&L, colored profit or loss.

**ORD-9. Close a position from its line.**
As a trader, I want to square off a position from the chart, so that I can exit quickly.

- AC1. The position line close button opens a confirm dialog ("Close position") describing the opposite order
  "at market to square off the long or short position".
- AC2. `POST /api/orders/close-position` submits a MARKET order (sent as a protected limit) for the supplied quantity
  and product. Exact side `long` sends SELL; every other side sends BUY. The server does not verify that the supplied
  position exists or that the quantity matches it; an accepted order does not guarantee a fill or a flat position.

**ORD-10. Understand order failures.**
As a trader, I want broker rejections shown with the broker's reason, so that I can correct the order.

- AC1. Broker rejections return 502 "TradeSmart: <emsg>"; unreachable broker returns 502 "Could not reach
  TradeSmart"; session expiry returns 401.
- AC2. Placement and close requests log side, quantity, symbol and order id; modification logs order id and price;
  cancellation logs order id. These messages indicate submitted actions, not confirmed fills.

**ORD-11. Apply risk limits and an audit trail to chart orders (Not implemented).**
As a trader, I want optional limits and a persistent record for orders I place from the chart, so that a mis-click
cannot send an oversized order and I can review my actions later.

- AC1. Chart orders are checked against configurable maximum quantity, maximum order value and allowed exchanges.
- AC2. Every chart order action is stored with its arguments, result and order id.
- AC3. Today the agent limits (AGENT-9) do not apply to chart orders and chart actions are only in the server log.

### 6.6 AI trading agent and its configuration (AGENT)

Verified in: `routers/agent.py`, `agent/builder.py`, `agent/tools.py`, `agent/trading.py`, `agent/chatgpt_auth.py`,
`agent/litellm_patches.py`, `agent/research.py`, `agent/finedge.py`, `tests/test_agent.py`, `tests/test_research.py`;
bundles `AgentPage-*.js`, `AgentConfigPage-*.js`, `agent-*.js`.

**AGENT-1. Sign in with ChatGPT.**
As an AI-assisted trader, I want to connect my ChatGPT subscription from the settings page, so that the agent runs on
my plan without an API key.

- AC1. `POST /api/agent/chatgpt/login` starts OpenAI's device-code flow and returns a user code and verification URL;
  the page shows "Enter this code when asked:" and "Open sign-in page".
- AC2. The page polls `GET /api/agent/chatgpt/login` every 3 seconds ("Waiting for you to approve. This page updates
  by itself."); `DELETE` cancels; the flow times out after 15 minutes.
- AC3. Tokens are stored Fernet-encrypted in `agent_secret`, never sent to the browser; LiteLLM's interactive console
  login is disabled.
- AC4. `GET /api/agent/status` shows Connected or Not connected with account email and plan; `POST
  /api/agent/chatgpt/logout` ("Sign out") forgets the tokens.
- AC5. Cancellation clears the current flow reference but does not stop its worker thread. A cancelled or
  superseded flow can still save tokens after completing, including after sign-out; this remains an implementation gap.

**AGENT-2. Choose the model and how it thinks.**
As an AI-assisted trader, I want to pick a model, a reasoning effort and extra instructions, so that the agent fits my
style and plan.

- AC1. `GET /api/agent/settings` lists models LiteLLM knows for `chatgpt/` (default `gpt-5.5` when available),
  reasoning efforts (low, medium, high, or model default), the tools, the research tools (`research_tools`) and the
  FinEdge key status (`finedge`, see AGENT-15).
- AC2. `PUT /api/agent/settings` rejects unknown models and efforts (422); instructions are limited to 4,000 characters.
- AC3. Extra instructions are appended after the built-in rules. Prompt adherence is not enforced by that ordering;
  order confirmation and the checks in AGENT-11 are enforced separately in the runtime and tools.

**AGENT-3. Test the model connection.**
As an AI-assisted trader, I want a test button, so that I know the model responds before I start chatting.

- AC1. `POST /api/agent/test` runs one short completion without tools or history and returns the reply and latency;
  409 when not signed in; 502 with a readable message on failure ("Test failed").

**AGENT-4. Chat with streaming answers.**
As an AI-assisted trader, I want to ask questions and see the answer stream in with the tools it used, so that I can
follow and verify the agent's work.

- AC1. `POST /api/agent/chat` (message up to 16,000 characters) returns Server-Sent Events: `start`, `run`, `token`,
  `reasoning`, `tool_start`, `tool_end`, `usage`, `confirm`, `error`, `done`.
- AC2. The UI renders Markdown, shows reasoning as "Thinking", shows each tool call with arguments and result
  (previews over 4,000 characters keep the first 4,000 plus ` ...`), and shows model and token usage.
- AC3. Stop requests cancellation of a registered running turn (`POST /api/agent/chat/{run_id}/cancel`). A
  disconnect detected while consuming events requests cancellation; task cancellation still saves accumulated output
  in `finally` but re-raises and need not emit a final `done` frame.
- AC4. Chat requires ChatGPT sign-in (409 "Sign in with ChatGPT in Agent settings first.") and an active broker
  session.
- AC5. The agent keeps the last 8 runs of the conversation in context, knows the current IST date and time, and makes
  at most 20 tool calls per turn (40 when the research tools are on, see AGENT-16).

**AGENT-5. Read my account and market data safely.**
As an AI-assisted trader, I want the agent to read live data through my own session, so that its answers are based on
real numbers.

- AC1. Read tools: `search_symbols`, `get_quote`, `get_history`, `get_technicals` (RSI 14, SMA 20, 50, 200, EMA 20,
  ATR 14, 20-bar range), `get_funds`, `get_positions`, `get_holdings`, `get_order_book`, `get_trade_book`,
  `get_master_contract_status`.
- AC2. Tools are closures over the session; the access token never appears in prompts or tool arguments.
- AC3. Tool failures return readable text (for example session expired) rather than crashing the run. Account lists
  are capped at 50 rows, symbol search at 15 results and history at 200 candles.
- AC4. Agent symbol search applies up to four terms, allows LIKE wildcards and sorts alphabetically. The dashboard
  search in MC-6 instead escapes wildcards, uses five terms and ranks results.

**AGENT-6. Manage conversations.**
As an AI-assisted trader, I want my chats saved, listed, renamed and deleted, so that I can return to earlier analysis.

- AC1. A new chat title collapses whitespace and keeps the first 60 characters, falling back to "New chat". A
  whitespace-only message passes raw length validation and is stored as empty text. An unknown conversation id
  creates a new conversation with a generated id.
- AC2. The sidebar lists up to 200 chats, newest first, grouped by date (for example Yesterday, Previous 7 days,
  Previous 30 days).
- AC3. Rename (`PATCH`, 1 to 120 characters before trimming) and delete (`DELETE`) with a "Delete this chat?"
  confirmation. Whitespace-only titles become empty. Deletion removes the transcript and attempts Agno session
  cleanup, but ignores cleanup errors and succeeds for an unknown conversation.
- AC4. Conversation transcripts, settings, ChatGPT tokens and order audit rows are global to the installation, not
  filtered by the current broker user. Logging in with another broker account does not hide previous transcripts.

**AGENT-7. Start from suggested prompts.**
As an AI-assisted trader, I want example prompts on an empty chat, so that I can see what the agent can do.

- AC1. Starter cards: "Funds and positions", "Orders and trades", "Technicals", "Crude oil on MCX".

**AGENT-8. Switch agent trading on or off.**
As an AI-assisted trader, I want agent trading off by default and an explicit switch to turn it on, so that the agent
cannot place orders unless I decide so.

- AC1. `agent_risk.trading_enabled` defaults to off; while off, the order tools are not given to the model and the
  prompt says it can only read.
- AC2. Turning it on in the UI requires ticking "I understand these are real orders with real money on my TradeSmart
  account, and that the model can make mistakes."
- AC3. Switching off works as a kill switch: orders already waiting for approval are refused when approved.
- AC4. The chat shows a "Trading on" badge and a warning banner while trading is on.

**AGENT-9. Set risk limits.**
As an AI-assisted trader, I want limits on what the agent may send, so that a mistake cannot become a large loss.

- AC1. `GET /PUT /api/agent/risk`: max order value (INR, default 50,000, up to 10,000,000), max quantity per order
  (default 500), max agent orders per day (default 20, up to 1,000), allowed exchanges (default NSE and BSE, from NSE,
  BSE, NFO, BFO, CDS, MCX). Unknown exchanges are rejected with 422.

**AGENT-10. Approve or reject each proposed order.**
As an AI-assisted trader, I want every order, modification and cancellation to pause for my approval, so that nothing
is sent without me.

- AC1. `place_order`, `modify_order` and `cancel_order` are confirmation-required tools: the run pauses, a `confirm`
  frame carries one summary line per proposal, and the stream ends with `done` reason `paused`.
- AC2. The approval card ("Your approval is needed") shows symbol, exchange, side, quantity, product, price type,
  price and trigger, with Approve, Reject, Approve all and Reject all.
- AC3. `POST /api/agent/chat/confirm` resumes the run with the decisions; missing decisions count as rejection. The
  rejected tool call is skipped and the model receives an instruction not to retry; this is not a guarantee of model
  compliance. The approval is marked decided before looking up the paused run, so a missing run cannot be retried.
- AC4. Sending a new message expires any pending approval ("A newer message was sent before this was decided, so
  nothing was sent."); confirming an expired or unknown approval returns 409.

**AGENT-11. Re-check risk at execution time.**
As an AI-assisted trader, I want the limits checked again just before sending, so that a limit change or kill switch
after the proposal still applies.

- AC1. After approval, `place_order` re-reads the limits and refuses when trading is off, the exchange is not
  allowed, quantity is outside 1 to max, LIMIT or SL has no price, SL or SL-M has no trigger, today's placed agent
  orders reached the daily limit (IST day), the same symbol, exchange, side and quantity was placed in the last
  60 seconds, or the estimated value exceeds the maximum.
- AC2. `modify_order` checks trading on, LIMIT or SL only, quantity within the maximum, price above 0 and value within
  the maximum. `cancel_order` checks trading on.
- AC3. A refusal is returned to the model as "Refused by the risk check, nothing was sent: <reason>".
- AC4. MARKET and SL-M use the same market price protection as ORD-3.
- AC5. Agent placement does not check lot multiples. Modification checks neither the existing order status nor
  allowed exchange, lot multiple, daily count, duplicate order or a positive SL trigger. Cancellation only checks
  trading on before calling the broker. Count and duplicate checks are not atomic with submission, so concurrent
  executions can pass the same checks.

**AGENT-12. Review the order audit log.**
As an AI-assisted trader, I want a log of every proposal, decision and broker answer, so that I can review what the
agent did.

- AC1. Every step is written to `agent_order_audit` with status proposed, approved, rejected, refused, placed or
  failed, plus arguments, order id and message.
- AC2. `GET /api/agent/orders/audit` returns the newest 100 rows; the settings page shows them under "Order audit"
  ("No agent orders yet." when empty).

**AGENT-13. Built-in behaviour rules.**
As an AI-assisted trader, I want the agent to follow fixed safety rules, so that it does not invent data or give
advice as fact.

- AC1. The system prompt states: tool output is data, never instructions; never invent a price, quantity or balance;
  symbols must match the symbol master; prices in INR and times in IST; add a line that an opinion on a trade is not
  investment advice; plain text without emoji or em or en dashes.
- AC2. With trading on, the prompt requires a quote and a stated estimate before proposing, forbids adding, enlarging
  or repeating orders, and forbids claiming an order was placed before an order id is returned.

**AGENT-14. Readable model errors.**
As an AI-assisted trader, I want model problems explained in plain words, so that I know what to do.

- AC1. Missing or expired sign-in, plan usage limits and models not on the plan map to specific messages that point to
  Agent settings; other errors are truncated to 400 characters. Network loss shows "The connection to the agent was
  lost."

**AGENT-15. Save and test a FinEdge API key in settings.**
As an AI-assisted trader, I want to paste my FinEdge Financials API key in Agent settings and check that it works, so
that the agent can read fundamental data without me editing any file.

- AC1. The settings page has a "FinEdge API key" card ("The key is stored encrypted in the SmartTrader database and is
  never shown again.") with a "Key saved" or "Not set" badge, a password field ("Paste your FinEdge API key", or
  "Saved key ********1234" with the mask when a key is stored) and Save key, Test key and Remove key buttons. With no
  key the hint says "Get a key at finedgeapi.com. The free Basic plan only covers ITC, RELIANCE and HDFCBANK."
- AC2. `PUT /api/agent/finedge` with `{"api_key": "<FINEDGE_API_KEY>"}` stores the key and returns `configured`,
  `masked_key` and `updated_at` (IST). A key shorter than 8 characters, longer than 256 characters or containing
  whitespace is rejected with 422 and nothing is stored. Saving clears the research data cache.
- AC3. Save key saves the key and then runs the test automatically; Test key runs `POST /api/agent/finedge/test` on
  the saved key. Saving does not check the key with FinEdge first, so a wrong key is stored and the test that follows
  reports the rejection.
- AC4. The test makes two company profile calls: ITC, which every plan covers, and TCS, which needs a paid plan. It
  returns `ok`, `coverage` (`all` or `sample`), `sample_company` and `latency_ms`. The page shows "Key works and covers
  all companies. FinEdge answered in N s." or "Key works, but this plan only covers the sample companies ITC, RELIANCE
  and HDFCBANK."
- AC5. Testing with no saved key returns 409 "Save a FinEdge API key first.". A key FinEdge rejects (`ER006`) returns
  422 "FinEdge rejected the API key. Check it and try again.", never 401, because the frontend treats any 401 as an
  expired SmartTrader session. Unreachable FinEdge returns 502 "FinEdge is unreachable. Try again in a minute."; other
  FinEdge errors return 502 "FinEdge error (<status>): <message>". The page shows failures under "Test failed".
- AC6. `DELETE /api/agent/finedge` removes the key and clears the cache; `GET /api/agent/finedge` and the `finedge`
  field of `GET /api/agent/settings` report the status. The Tools card lists the research tools, dimmed with
  "(needs a FinEdge key)" until a key is saved.

**AGENT-16. Ask fundamental questions.**
As an AI-assisted trader, I want to ask the agent about a company's financials, ratios, growth, valuation,
shareholding, dividends and filings, so that I understand the business behind the price.

- AC1. With a FinEdge key saved, each chat turn adds 14 read-only research tools: `search_companies`,
  `get_company_profile`, `get_financial_statements`, `get_financial_ratios`, `get_growth_metrics`,
  `get_stock_quotes`, `get_price_performance`, `get_valuation_history`, `get_peers`, `compare_companies`,
  `get_shareholding`, `get_dividends_and_actions`, `get_announcements` and `get_index_overview`. The prompt adds the
  research rules and the tool call limit rises from 20 to 40 per turn.
- AC2. Without a key the research tools are not given to the model, and the prompt tells it to say that a FinEdge API
  key can be added in Agent settings when the user asks for fundamentals, a report, a comparison or a recommendation.
- AC3. The tools are closures over the key; the key never appears in prompts, tool arguments or tool results.
  FinEdge symbols are NSE symbols or BSE codes with no exchange; `search_companies` matches a name, NSE symbol or BSE
  code with exact matches first (up to 25 rows).
- AC4. Units are converted for the model: statements in Rs crore, oldest period first, with EPS in rupees and revenue
  and profit growth in percent (year on year for quarterly rows); ratios, growth rates, margins and returns in
  percent; multiples in times. Every result states whether figures are consolidated or standalone, and consolidated
  requests fall back to standalone when a company has no consolidated accounts.
- AC5. `get_valuation_history` gives PE, PB, price to sales and price to free cash flow by year and today's percentile
  in the daily range since 2018; `get_price_performance` gives returns, CAGR, 1 year volatility, maximum drawdown, 50
  and 200 day averages and benchmark index returns; `get_announcements` drops routine filings (trading window, share
  certificate and newspaper notices) and lists the rest newest first with PDF links. FinEdge quotes are delayed 15 to
  30 minutes; live prices still come from the TradeSmart tools.
- AC6. The prompt requires every figure to be quoted from a tool result with its unit and period. Tool failures come
  back as readable text: a rejected key points to Agent settings, a 404 suggests `search_companies`, and other errors
  carry FinEdge's short message without the key.

**AGENT-17. Get a research report on a company.**
As an AI-assisted trader, I want a structured research report on a company built from fundamental and price data, so
that I get a complete picture in one answer.

- AC1. The report follows the structure in the research rules: summary, business, financial performance over 5 years,
  profitability and returns, balance sheet and cash flow, valuation against its own history and its peers,
  shareholding, dividends, recent filings, key risks, conclusion.
- AC2. Figures are fetched in the same turn and quoted with units and periods (financial years end in March); the
  report states the accounting basis and does not mix consolidated and standalone figures.
- AC3. The report has no buy or sell rating unless the user asks for one (AGENT-19).
- AC4. The structure is a prompt rule. Model compliance is not enforced by code or tested.

**AGENT-18. Compare companies.**
As an AI-assisted trader, I want to compare the fundamentals of two to six companies, so that I can see who leads on
growth, profitability, balance sheet strength and valuation.

- AC1. `compare_companies` takes 2 to 6 symbols ("Error: give between 2 and 6 symbols." otherwise) and returns per
  company: name, industry, basis, latest financial year, revenue and net profit in Rs crore, 3 and 5 year revenue
  CAGR, 3 year profit and EPS CAGR, 3 year average dividend payout, EBITDA and net margin, ROE, ROCE, debt to equity,
  PE, PB, price and market cap.
- AC2. A company the plan does not cover stays in the list with `error` "not covered by this FinEdge plan" (or "not
  found"), and the other companies are still compared.
- AC3. When some companies report consolidated and others standalone figures, the result carries the note "Mixed
  consolidated and standalone figures: compare with care."
- AC4. `get_peers` lists peers in the same sub-industry, industry, sector or macro sector, without the company itself,
  ranked by market cap (peers with no quote last).
- AC5. The answer shows one table with a row per metric and a column per company, then explains who leads on growth,
  profitability, balance sheet strength and valuation (prompt rule, not tested).

**AGENT-19. Get a buy or sell research view for a medium or long term horizon.**
As an AI-assisted trader, I want a buy or sell view on a company for a medium term (6 to 18 months) or long term (3
years or more) horizon, so that I can weigh a decision with the evidence laid out.

- AC1. The agent gives a recommendation only when the user asks. When no horizon is given it asks whether the user
  means medium term (6 to 18 months) or long term (3 years or more).
- AC2. Medium term weighs recent quarterly earnings momentum, the price trend against the 50 and 200 day averages, the
  valuation percentile and near-term catalysts in filings. Long term weighs 5 year growth, return on equity and
  capital, cash conversion, debt, dividend record and valuation against history.
- AC3. The view states a rating (Buy, Accumulate, Hold, Reduce or Sell) with the horizon, then the thesis, supporting
  numbers, a valuation view with the method used, risks, what would change the view, and the data date. It never
  promises a return.
- AC4. It ends with: "This is a research view for education, not investment advice. Consult a SEBI registered
  investment adviser before investing."
- AC5. A research view never places an order; order tools still need trading on and per-order approval (AGENT-8,
  AGENT-10).
- AC6. AC1 to AC4 are prompt rules. No test asserts model compliance.

**AGENT-20. Research with a sample-only FinEdge plan.**
As an AI-assisted trader on the free FinEdge plan, I want research to work for the companies my plan covers and to
tell me plainly what is missing, so that a partial plan does not break the answer.

- AC1. The key test reports `coverage` `sample` when FinEdge refuses the TCS profile with a 401 or 403 that is not an
  invalid key, and the settings page names the sample companies ITC, RELIANCE and HDFCBANK.
- AC2. A tool call for a company or data set outside the plan returns "Not available: the FinEdge plan on this API key
  does not cover this company or data. The free Basic plan only covers ITC, RELIANCE and HDFCBANK."
- AC3. FinEdge refuses a whole quote batch when one symbol is outside the plan; `get_stock_quotes` then quotes each
  symbol on its own and lists the uncovered ones under `not_found`.
- AC4. `compare_companies` keeps uncovered companies with an error (AGENT-18 AC2), and the prompt tells the model to
  say which data is missing and carry on with the rest.

### 6.7 Theming and UX (UX)

Verified in: bundle `index-*.js` (theme store `smarttrader-theme`, accents, routes), `main.py` (SPA fallback).

**UX-1. Light and dark mode.**
As a trader, I want to switch between light and dark mode, so that the app is comfortable in any lighting.

- AC1. The header has a toggle ("Switch to dark mode" or "Switch to light mode").
- AC2. Until the user chooses, the mode follows the operating system preference and updates when it changes.
- AC3. The choice is stored in the browser (`smarttrader-theme`).

**UX-2. Accent color.**
As a trader, I want to pick an accent color, so that the app feels like mine.

- AC1. An "Accent color" select offers Zinc (default), Green, Blue, Violet, Orange and Slate; the choice is stored with
  the mode.

**UX-3. Consistent navigation and states.**
As a trader, I want clear loading, error and retry states on every page, so that I always know what the app is doing.

- AC1. Pages show loading text (for example "Loading your dashboard...", "Loading charts...", "Loading the agent..."),
  error panels with "Try again" or "Retry", and lazy-loaded routes for Charts and Agent pages.
- AC2. Top navigation: Dashboard, Charts, Agent; agent settings at `/agent/config` with "Back to chat".

**UX-4. Plain text interface.**
As a trader, I want a clean, text-only interface, so that the app stays calm and readable.

- AC1. No icon libraries and no emoji in the UI, code, logs or docs; no em or en dashes. (Small inline SVG glyphs are
  used for chart toolbar buttons only, drawn in code, not from an icon library.)

**UX-5. One port in production, hot reload in development.**
As a developer, I want the backend to serve the built UI, so that one command runs the whole app.

- AC1. When `frontend/dist/index.html` exists, any GET that is not under `/api/` or `/tradesmart/` and is not a real
  file returns `index.html`; real files are served only from inside `dist` (no path traversal).
- AC2. Without a build, those routes return 404 "Frontend not built. Run npm run build in frontend/."
- AC3. In development, Vite on `127.0.0.1:5173` proxies `/api` to the backend; CORS allows only
  `127.0.0.1:5173` and `localhost:5173` with credentials.

---

## 7. Non-functional requirements (NFR)

**NFR-1. Master contract performance.** Target: download, parse and store all six exchanges within 30 seconds on a
normal connection; this audit has no repeatable benchmark proving that target. Parsing uses the `csv` module
(no pandas), inserts in batches of 10,000, and timeouts are 15 s connect and 120 s read per file. Record counts vary
with the broker files and are not a fixed product guarantee.

**NFR-2. Search and lookup performance.** Symbol search returns at most 50 rows using indexes on (symbol, exchange),
token and broker symbol. SQLite runs in WAL mode so searches proceed during a master contract rewrite.

**NFR-3. Secrets at rest.** API secret, broker access token and ChatGPT OAuth tokens are encrypted with Fernet; the key
is generated on first start into `backend/data/secret.key` (created with mode 0600 where supported). No `.env` files
hold credentials. Losing the key makes stored secrets unreadable; the user re-enters them.

**NFR-4. Secrets in transit to the browser.** The API secret, broker access token and ChatGPT tokens are never sent to
the browser; the broker WebSocket is proxied by the backend; agent tools hold the token in closures, never in prompts.

**NFR-5. Session security.** The session cookie is random (32 bytes), HttpOnly, SameSite=Lax; only its SHA-256 hash is
stored; one active session at a time. The WebSocket checks the Origin header against the app's origins before using
the cookie.

**NFR-6. Log hygiene.** The OAuth `code` is redacted from string arguments in the uvicorn access log; httpx request
logging is lowered to WARNING. Application code avoids deliberately logging credentials, but broker/provider error
messages and exception tracebacks are not passed through a general secret scrubber.

**NFR-7. Download safety.** Zip extraction writes to a fixed file name (no archive paths trusted), rejects members
larger than 512 MB, and cleans the temporary work directory.

**NFR-8. Reliability.** Atomic symbol table replacement, per-exchange failure isolation, interrupted download
recovery at startup, a process-wide lock against concurrent downloads, WebSocket reconnect with capped backoff, and
broker timeouts of 20 s for REST calls and 15 s for the stream handshake.

**NFR-9. Local-only deployment.** The documented launch command binds to `127.0.0.1:8000`; the application does not
enforce a loopback bind. It is designed for one user on one machine; the cookie is not Secure for local HTTP use.

**NFR-10. Observability.** Structured Python logging at INFO (timestamp, level, logger, message) for login, logout,
downloads (per-exchange counts and elapsed time), order actions and agent trading changes; `GET /api/health`;
persisted agent audit trail; per-turn token usage, duration and time to first token stored when completion metrics
are available. Error, paused and cancelled turns may have no usage metrics; empty turns may create no assistant row.

**NFR-11. Compatibility.** Python 3.14 with uv, FastAPI 0.143.0, uvicorn 0.54.0, SQLAlchemy 2, Node 24, React 19.3,
TypeScript 7, Vite 8, Tailwind 4, Agno and LiteLLM. The schema self-migrates added columns at startup.

**NFR-12. Testability.** The backend suite contains 86 test functions (94 parametrized cases) across auth callback, checksum, funds,
history, master contract parsing, orders, order service, stream relay, the agent (including approval pause and
resume) and the FinEdge research tools (`tests/test_research.py`, run against canned FinEdge answers). This is selective coverage, not coverage of every requirement; see `docs/bdd/README.md` for explicit gaps.
The approval test uses a fake agent and does not execute an approved order tool. There are no frontend tests.

**NFR-13. Market-hours awareness (Not implemented).** The app should know NSE, BSE and MCX session times and exchange
holidays, label the chart and order ticket when the market is closed, and explain empty history accordingly. Today
there is no market-hours or holiday logic anywhere; the broker alone decides.

**NFR-14. FinEdge API key security.** The FinEdge key is entered only on the Agent settings page and stored
Fernet-encrypted in `agent_secret` (row `finedge_api_key`), never in `.env` or any other file. No endpoint returns it:
the API gives only `configured`, a mask of eight asterisks plus the last 4 characters, and `updated_at`. FinEdge takes
the key as a `token` query parameter, so request URLs are not logged (httpx logging is at WARNING), the key is
replaced with `***` in FinEdge error text, and network failures are reported by exception class only. The key lives
in tool closures, never in prompts, tool arguments or tool results. Saving and removing log only "FinEdge API key
saved" or "FinEdge API key removed". A rejected key answers 422, never 401, so it cannot sign the user out.

## 8. Constraints and assumptions

- C1. Single user, single TradeSmart account, single active session. A new login deactivates the previous session.
- C2. Runs on the user's local machine. Use `127.0.0.1` everywhere, not `localhost`: the session cookie is host-only,
  and cookies ignore ports but not host names, so a cookie set on `127.0.0.1:8000` reaches `127.0.0.1:5173` but not
  `localhost`.
- C3. The redirect URL registered on the TradeSmart developer portal must exactly match the one entered in the app,
  normally `http://127.0.0.1:8000/tradesmart/callback`.
- C4. Indian market hours apply. The app does not check market hours: outside trading hours history may be empty,
  ticks stop, and orders are accepted or rejected by the broker according to its own rules (for example AMO handling).
- C5. The TradeSmart access token is valid for the trading day; there is no refresh. Users log in again daily.
- C6. TradeSmart refuses MARKET-type orders from the API; the app depends on market price protection.
- C7. The AI agent requires a ChatGPT Plus, Pro or Team subscription; model availability depends on the plan.
- C8. The symbol master is refreshed on login or on demand only.
- C9. Fundamental research needs the user's own FinEdge Financials API key. What it can see depends on the FinEdge
  plan: the free Basic plan covers only ITC, RELIANCE and HDFCBANK. FinEdge quotes are delayed 15 to 30 minutes.

## 9. Dependencies

| Dependency | Used for | Notes |
| --- | --- | --- |
| TradeSmart Online Noren v2 REST (`https://v2api.tradesmartonline.in/NorenWClientAPIv2`) | GenAcsTok, Limits, PositionBook, OrderBook, TradeBook, Holdings, GetQuotes, TPSeries, EODChartData, PlaceOrder, ModifyOrder, CancelOrder | Bearer token, `jData` text/plain bodies |
| TradeSmart OAuth portal (`/OAuthlogin/authorize/oauth`) | User sign-in | Redirect URL must match registration |
| TradeSmart WebSocket (`wss://v2api.tradesmartonline.in/NorenWSAPI/`) | Live touchline ticks | Backend-held connection |
| TradeSmart symbol files (`/<EXCH>_symbols.txt.zip`) | Master contract | Behind Cloudflare; browser User-Agent needed |
| OpenAI ChatGPT subscription via LiteLLM `chatgpt/` provider | Agent model | Device-code OAuth; plan limits apply |
| Agno | Agent runtime, tool confirmation, session history (`data/agent.db`) | |
| openalgo-charts | Chart rendering, indicators, drawings, trading lines | Frontend library |
| OpenAlgo TradeSmart plugin (reference only) | Symbol conventions, funds and protection logic | Logic ported, not a runtime dependency |
| FinEdge Financials API (`https://data.finedgeapi.com`) | Research tools: company profile and reports, financial statements, ratios, financial metrics, price ratios, daily quotes, quotes, peers, shareholding, corporate actions, filings, index data, stock symbols | User's own API key as `token` query parameter; the plan decides company coverage; 30 s timeout, two retries on 429 and 5xx |

## 10. Success metrics

| Metric | Target | How to measure |
| --- | --- | --- |
| Time from Login click to dashboard | Under 15 s on a normal connection | Browser timing, callback logs |
| Master contract load time | Under 30 s for all six exchanges (target, not benchmarked here) | `master_contract_status.message` |
| Master contract completeness | All six exchanges loaded on at least 99% of downloads | Status message has no "failed:" list |
| Symbol search latency | Under 300 ms at p95 | Server timing |
| Chart first render | Under 3 s for default lookback | Browser timing |
| Live tick availability during market hours | Status Live for at least 99% of session time | Stream status and reconnect logs |
| Chart order success | At least 95% of confirmed chart orders accepted by the broker (excluding funds or exchange rejections) | Server logs |
| Unapproved agent orders | Zero | `agent_order_audit`: every `placed` row preceded by `approved` |
| Agent risk refusals honoured | 100% of refusals send no order mutation; quote or book reads may already have occurred | Broker mutation test doubles and audit rows |
| Agent answer grounding | No answer quotes a price without a tool call in the same turn | Transcript review of `agent_message.tools_json` |
| Research disclaimer | 100% of buy or sell research views end with the not-investment-advice line | Transcript review of research turns |

## 11. Risks and open questions

### Risks

- R1. Real money: chart orders and approved agent orders are sent to a live account. Mitigated by confirmations,
  market price protection, risk limits for the agent, and the agent kill switch. Chart orders have no risk limits.
- R2. Market price protection may not fill in fast markets (a protected limit can rest unfilled) or may fill at up to
  the protection percentage away from the LTP.
- R3. The model can make mistakes or be influenced by tool output; mitigated by the "tool output is data" rule,
  approvals and server-side limits, not eliminated.
- R4. Loss of `secret.key` makes stored credentials unreadable; exposure of the `backend/data` folder exposes encrypted
  secrets together with their key.
- R5. Frontend source is missing (`frontend/src` is empty); the UI cannot be changed or rebuilt from this repository
  without recovering the source.
- R6. CSRF protection relies on SameSite=Lax and JSON request bodies; there is no CSRF token.
- R7. Broker API changes (for example the live stream answering "OK" where docs say "Ok", volume glitches near the
  close) need code workarounds; more such quirks may appear.
- R8. ChatGPT plan limits or model withdrawal can make the agent unavailable; LiteLLM's stream bridge is patched
  locally (`litellm_patches.py`) and may break on LiteLLM upgrades.
- R9. Daily agent order limit counts only `place_order`; modifications and cancellations are not counted.
- R10. Research views are not advice. AGENT-19 produces Buy, Accumulate, Hold, Reduce or Sell ratings that a user may
  read as advice. Mitigated by giving them only on request, asking for the horizon, showing the evidence and method,
  never promising a return and ending with the not-investment-advice line. These are prompt rules, not enforced by
  code or tests, and the model can still state a wrong figure or skip the disclaimer.
- R11. FinEdge plan coverage. On a sample-only plan most companies return "Not available", so reports and
  comparisons can be partial. Mitigated by the coverage result of the key test, per-company errors in
  `compare_companies` and the rule to name missing data. FinEdge data can also lag filings, and its quotes are
  delayed 15 to 30 minutes.
- R12. The FinEdge key travels in the request URL as a query parameter. Mitigated by keeping httpx logging at
  WARNING, masking the key in error text and never echoing URLs (NFR-14); a proxy or debugging tool that logs URLs
  would still see it.

### Open questions

- Q1. Should chart orders also be subject to the risk limits and recorded in a persistent audit table?
- Q2. Should agent `place_order` enforce lot-size multiples itself (the chart path does; the agent path relies on the
  broker to reject)?
- Q3. Should orders carry different `remarks` for chart and agent orders? Today every order is tagged
  `smarttrader-agent`.
- Q4. Should logout also invalidate the TradeSmart session on the broker side?
- Q5. Should the master contract refresh automatically each morning before market open?
- Q6. Should there be order book, positions and holdings pages in the UI rather than only through the agent?
- Q7. Is the duplicate-order guard (same symbol, exchange, side and quantity within 60 s, price ignored) the right
  rule, and should it apply to chart orders?
- Q8. Should the agent `modify_order` check allowed exchanges (it currently does not)?
- Q9. Should `PUT /api/agent/finedge` test the key with FinEdge before storing it, instead of storing it and testing
  afterwards?

## 12. Regulatory and compliance notes

- No investment advice. SmartTrader is a personal tool. The agent must add a line that any opinion on a trade is not
  investment advice; the UI says "It can make mistakes." The product must not be marketed as an advisory or research
  service. Under SEBI rules, investment advice and research analysis require registration. The FinEdge research
  views (AGENT-19) are produced only on the user's request, for the user's own use, and end with "This is a research
  view for education, not investment advice. Consult a SEBI registered investment adviser before investing." They
  must not be published, shared as recommendations or offered to other people.
- Order safety. Every chart order, modification, cancellation and square-off requires an explicit click; every agent
  order action requires explicit approval and passes server-side checks. Agent trading is off by default and needs an
  explicit acknowledgment to enable.
- Algorithmic trading. Orders are placed through the broker's retail API with `ordersource` API. Unattended or fully
  automated order placement is out of scope. Any move toward automated strategies must be reviewed against current
  SEBI and exchange rules for retail algorithmic trading and the broker's API terms (for example order rate limits
  and strategy registration).
- Data protection. Credentials and tokens stay on the user's machine, encrypted. Account data passes to the LLM
  provider only as tool results requested during a chat; users should be told that chat content and tool results are
  sent to OpenAI under their ChatGPT plan terms.
- Audit. Agent order activity is recorded in `agent_order_audit`; chart order activity is only in server logs.
- Broker terms. The app uses the TradeSmart API on behalf of the account holder only; sharing access or reselling is
  out of scope.

---

## 13. Release status

Status values: Implemented (backend and UI present and consistent), Partial (present with a noted gap),
Not implemented (identified need with no code).

| ID | Feature | Status | Notes |
| --- | --- | --- | --- |
| AUTH-1 | Save broker credentials | Implemented | Secret encrypted, never returned |
| AUTH-2 | TradeSmart portal sign-in | Implemented | |
| AUTH-3 | Callback token exchange and session | Implemented | Single active session |
| AUTH-4 | Login error display | Implemented | |
| AUTH-5 | Session check and protected routes | Implemented | Includes "already signed in" page |
| AUTH-6 | Logout | Partial | Local session only; no broker-side logout call |
| AUTH-7 | Broker session expiry handling | Partial | REST deactivation applies to funds, history and chart orders; agent and stream paths differ |
| AUTH-8 | localhost warning | Implemented | Not in SPEC |
| MC-1 | Automatic download after login | Implemented | |
| MC-2 | Download status and polling | Implemented | 2 s polling while downloading |
| MC-3 | Download again | Implemented | 409 when running |
| MC-4 | Atomic replace and failure isolation | Implemented | Plus startup recovery |
| MC-5 | OpenAlgo symbol conventions | Implemented | |
| MC-6 | Symbol search | Implemented | |
| MC-7 | Open chart from search | Implemented | Not in SPEC |
| MC-8 | Scheduled daily master refresh | Not implemented | Only on login or on demand |
| DASH-1 | Header, identity, navigation | Implemented | |
| DASH-2 | Funds cards and computation | Implemented | |
| DASH-3 | Refresh and fetched time | Implemented | |
| DASH-4 | Funds error handling | Implemented | |
| DASH-5 | Dashboard composition | Implemented | |
| DASH-6 | Order book, positions and holdings screens | Not implemented | Available only through agent tools |
| CHART-1 | Chart symbol picker and quick starts | Implemented | `/trading` route not listed in SPEC routes |
| CHART-2 | Timeframes | Implemented | |
| CHART-3 | Historical candles | Implemented | Volume repair not in SPEC |
| CHART-4 | Live ticks | Partial | One instrument per connection; no watchlist or multi-symbol streaming |
| CHART-5 | Periodic history refresh | Implemented | |
| CHART-6 | Indicators | Implemented | Not in SPEC |
| CHART-7 | Drawing tools | Implemented | Not in SPEC |
| CHART-8 | Snapshots | Implemented | Not in SPEC |
| CHART-9 | Remembered preferences | Implemented | Browser storage only |
| ORD-1 | Order ticket | Implemented | "Trade" button not in SPEC (SPEC mentions right-click only) |
| ORD-2 | Right-click trade at price | Implemented | |
| ORD-3 | Market price protection | Implemented | |
| ORD-4 | Order validation | Implemented | |
| ORD-5 | Order and position lines | Implemented | 3 s refresh |
| ORD-6 | Drag to modify | Implemented | |
| ORD-7 | Cancel from line | Implemented | |
| ORD-8 | Live position P&L | Implemented | |
| ORD-9 | Close position | Partial | Trusts supplied side and quantity; no position or fill verification |
| ORD-10 | Order failure messages | Partial | Chart orders logged only to server log, no persistent audit; all orders tagged `smarttrader-agent` |
| ORD-11 | Risk limits for chart orders | Not implemented | Limits apply to agent orders only |
| AGENT-1 | ChatGPT device-code sign-in | Partial | Cancelled flow workers can still save tokens after sign-out |
| AGENT-2 | Model, effort, instructions | Implemented | |
| AGENT-3 | Test connection | Implemented | Not in SPEC table detail beyond endpoint |
| AGENT-4 | Streaming chat | Implemented | |
| AGENT-5 | Read-only tools | Implemented | |
| AGENT-6 | Conversation management | Implemented | Date grouping not in SPEC |
| AGENT-7 | Starter prompts | Implemented | Not in SPEC |
| AGENT-8 | Trading switch and kill switch | Implemented | Acknowledgment checkbox not in SPEC |
| AGENT-9 | Risk limits | Implemented | |
| AGENT-10 | Approval of proposed orders | Implemented | Pending approvals expire on a new message (not in SPEC) |
| AGENT-11 | Risk re-check at execution | Partial | Tool-specific gaps in AC5; duplicate check ignores price; concurrent checks are not atomic |
| AGENT-12 | Order audit log | Implemented | |
| AGENT-13 | Built-in behaviour rules | Implemented | |
| AGENT-14 | Readable model errors | Implemented | |
| AGENT-15 | Save and test a FinEdge API key | Implemented | Save stores the key before testing it; not in SPEC |
| AGENT-16 | Fundamental questions with research tools | Implemented | 14 tools only with a key; not in SPEC |
| AGENT-17 | Company research report | Implemented | Report structure is a prompt rule, not tested; not in SPEC |
| AGENT-18 | Company comparison | Implemented | Not in SPEC |
| AGENT-19 | Buy or sell research view, medium or long term | Implemented | Horizon question and disclaimer are prompt rules, not tested; not in SPEC |
| AGENT-20 | Research on a sample-only FinEdge plan | Implemented | Not in SPEC |
| UX-1 | Light and dark mode | Implemented | Follows OS until chosen |
| UX-2 | Accent color | Implemented | Not in SPEC |
| UX-3 | Loading, error and retry states | Implemented | |
| UX-4 | Plain text interface | Implemented | Chart toolbar uses inline SVG glyphs from the chart library |
| UX-5 | Single-port serving and dev mode | Implemented | |
| NFR-1 | Master contract performance | Partial | Target lacks a repeatable benchmark |
| NFR-2 to NFR-11 | Non-functional requirements | Implemented | Implementation mechanisms in section 7; runtime limits and test gaps still apply |
| NFR-12 | Testability | Partial | Backend tests only; no frontend tests; frontend source missing |
| NFR-13 | Market-hours awareness | Not implemented | No market-hours or holiday checks anywhere |
| NFR-14 | FinEdge API key security | Implemented | Key sent as a URL query parameter by FinEdge's design (R12) |

## 14. SPEC.md versus the code

### In SPEC.md but not implemented, or implemented differently

- Frontend source: SPEC describes a React source tree; only the built bundle exists (`frontend/src` is empty).
- Login validation: SPEC says 422 or 400; the code returns 400 for validation failures (422 only for a malformed
  body from FastAPI).
- WebSocket foreign origin: SPEC says "refused with 403"; the code closes the socket with code 4403 before accepting,
  which the browser sees as a failed handshake (HTTP 403).
- SPEC frontend routes list only `/`, `/login` and `/dashboard` (plus `/agent` and `/agent/config` in the agent
  section); the chart page route `/trading` is not named in SPEC.
- SPEC says agent orders are checked for "allowed exchanges, max quantity, max order value, max orders per day, no
  identical order within 60 s" without distinguishing tools; in code only `place_order` gets all of these, and
  `modify_order` and `cancel_order` get a subset (see AGENT-11).
- SPEC (Orders section) says F&O and MCX quantities must be lot multiples; enforced for chart orders only.
- `agent/tools.py` module docstring still says "SmartTrader has no order routes"; this is out of date.

### Implemented but not mentioned in SPEC.md

- Login page localhost warning and "You are already signed in" page.
- Dashboard search results open the chart; chart quick-start symbols; chart URL parameters.
- Order ticket via a "Trade" button (in addition to right-click), SL and SL-M order types in the ticket.
- Indicator library, drawing tools, snapshots, persisted chart, rail, indicator and drawing state.
- History `days` parameter defaults per interval and intraday volume repair.
- History auto-refresh (60 s or 5 min) and WebSocket reconnect backoff (2 s to 30 s).
- `PENDING` treated as an open order status alongside OPEN and TRIGGER_PENDING.
- Master contract: interrupted-download recovery at startup, zip size cap, `(token, exchange)` de-duplication,
  status messages per phase.
- Accent color themes and OS-following dark mode.
- Agent: test connection with latency, conversation date grouping, starter prompts, reasoning display, usage and time
  to first token, acknowledgment checkbox to enable trading, kill-switch semantics, pending approvals expiring on a
  new message, readable provider error mapping, LiteLLM stream bridge patch.
- Agent fundamental research with a FinEdge API key: key card in settings, 14 research tools, research reports,
  company comparisons and buy or sell research views (AGENT-15 to AGENT-20, NFR-14).
- OAuth code redaction in the access log; schema self-migration of added columns.
