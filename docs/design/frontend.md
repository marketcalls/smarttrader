# Frontend Design (reconstructed from the build)

Important: the frontend source is missing. `frontend/src` contains only empty
folders (`components/agent`, `components/chart`, `components/layout`,
`components/ui`, `lib`). This document was reconstructed from:

- `SPEC.md` (Frontend, AI agent and Orders sections),
- `frontend/index.html`, `frontend/vite.config.ts`, `frontend/package.json`,
- the built bundle in `frontend/dist` (`index.html` and the minified chunks in
  `frontend/dist/assets`), by searching for route paths, `/api/` calls, query keys,
  `localStorage` keys and UI strings.

Component and function names inside the bundle are minified, so names used below
describe roles, not original identifiers. Anything not visible in the bundle is
marked as inferred.

One chunk is no longer the original build output: `assets/AgentConfigPage-C4TKen9_.js`
was hand patched in place (2026-10-14) to add the FinEdge API key card and the
research tools list (section 5.6.1). There is no source for this change. The
original chunk can be restored from git (`git checkout -- frontend/dist/assets/AgentConfigPage-C4TKen9_.js`
reverts the patch).

## 1. Build output

| File | Size | Content |
| --- | --- | --- |
| `dist/index.html` | small | Theme bootstrap script, loads `index-*.js` and `index-*.css` |
| `assets/index-CLqf0qmU.js` | 364 KB | React, react-router, TanStack Query, API client, app shell, Login, Dashboard, routes |
| `assets/index-DgGJuggg.css` | 52 KB | Tailwind 4 output with theme tokens |
| `assets/TradingPage-D_x_c_oF.js` | 827 KB | Lazy chunk: chart page with the openalgo-charts engine, indicators, drawing tools, order ticket, tick socket |
| `assets/AgentPage-MHF7gvG9.js` | 179 KB | Lazy chunk: chat page with react-markdown and remark-gfm |
| `assets/AgentConfigPage-C4TKen9_.js` | 20 KB (17 KB before the patch) | Lazy chunk: agent settings page. Hand patched to add the FinEdge API key card; the file name (content hash) was kept, so `index-*.js` still loads it. |
| `assets/agent-BopMnDiO.js` | 2 KB | Shared agent API client, query keys and SSE reader (used by both agent pages) |

`vite.config.ts` raises `chunkSizeWarningLimit` to 900 KB for the chart chunk.

## 2. Routing

React Router 7 routes found in the main chunk:

| Path | Element | Loading | Guard |
| --- | --- | --- | --- |
| `/` | Session check, then redirect to `/dashboard` or `/login` | eager | n/a |
| `/login` | Login page ("Connect Your Broker") | eager | none |
| `/dashboard` | Dashboard ("Trading Dashboard") | eager | Authenticated wrapper: redirects to `/login` when `authenticated` is false |
| `/trading` | Trading (chart) page, reads `symbol`, `exchange`, `interval` from the query string | `React.lazy`, fallback "Loading charts..." | Authenticated (inferred from the shared header and 401 handling) |
| `/agent` | Agent chat | lazy, fallback "Loading the agent..." | Authenticated |
| `/agent/config` | Agent settings | lazy, fallback "Loading agent settings..." | Authenticated |
| `*` | Redirect to `/` | | |

Navigation labels in the header: Dashboard, Charts, Agent. Symbol search results link
to `/trading?symbol=...&exchange=...` ("Open SYMBOL chart").

## 3. State management

### 3.1 Server state (TanStack Query)

Query client defaults: `refetchOnWindowFocus: false`; retry once, except no retry on
4xx responses. Query and mutation caches have a global `onError` that calls a logout
handler on HTTP 401.

The global 401 handler (a component mounted above the routes) sets the cached session
to unauthenticated, removes the `funds`, `master-contract`, `symbols`, `chart` and
`agent` queries, and navigates to `/login`.

Query keys:

| Key | Endpoint | Polling |
| --- | --- | --- |
| `["config"]` | `GET /api/config` | |
| `["session"]` | `GET /api/auth/session` | |
| `["funds"]` | `GET /api/funds` | Manual Refresh button |
| `["master-contract","status"]` | `GET /api/master-contract/status` | Every 2 s while status is `downloading` |
| `["symbols", q, exchange]` | `GET /api/symbols/search` | |
| `["chart","intervals"]` | `GET /api/chart/intervals` | `staleTime: Infinity` |
| `["chart","history", symbol, exchange, interval]` | `GET /api/chart/history` | Every 5 minutes while the tick stream is live, every 60 s for intraday intervals otherwise, none for D when not live |
| symbol order state (key built from symbol and exchange) | `GET /api/orders/symbol` | Every 3 s, only for tradable (non `_INDEX`) exchanges |
| `["agent","status"]`, `["agent","settings"]`, `["agent","risk"]`, `["agent","audit"]` | agent settings endpoints | |
| `["agent","login"]` | `GET /api/agent/chatgpt/login` | Every 3 s while a sign-in flow is active |
| `["agent","finedge"]` | `GET /api/agent/finedge` | Invalidated (with `["agent","settings"]`) after save and remove |
| `["agent","conversations"]`, `["agent","conversation", id]` | conversation endpoints | |

Mutations: login, logout, master download, place, modify, cancel, close position,
save settings, save risk, ChatGPT login start, cancel and logout, model test, rename
and delete conversation, cancel run, FinEdge key save (`PUT /api/agent/finedge`),
test (`POST /api/agent/finedge/test`) and remove (`DELETE /api/agent/finedge`).

### 3.2 API client

A small `fetch` wrapper (main chunk, exported from `index-*.js`; the patched
settings chunk imports it as `Qe` for the FinEdge calls):

- Sends `Accept: application/json`, adds `Content-Type: application/json` when there is
  a body, always `credentials: "include"`, supports `AbortSignal`.
- Parses the response as JSON when possible; on non-2xx throws an error object with
  `status`, a readable message built from FastAPI's `detail` (string or validation
  list joined as `loc: msg`) and the raw detail.
- A network failure becomes status 0 with "Cannot reach the SmartTrader server. Is the
  backend running?".

The agent chunk adds a streaming client for `/api/agent/chat` and
`/api/agent/chat/confirm`: `fetch` POST with `Accept: text/event-stream`, read with
`body.getReader()`, split on blank lines, parse each `data:` line as JSON and dispatch
it. A 401 triggers the global logout handler.

### 3.3 Live data

The trading page opens `ws(s)://<current host>/api/ws/ticks`. On open it sends
`{action: "subscribe", symbol, exchange}` and re-sends it whenever the symbol changes.
States: `off`, `connecting`, `live`, `error`. On close it reconnects with exponential
backoff from 2 s up to 30 s, except close code 4401, which triggers the logout
handler. Each tick updates the last bar on the chart and the live P&L of position
lines.

### 3.4 Local state and storage

| `localStorage` key | Purpose |
| --- | --- |
| `smarttrader-theme` | `{mode: "light" or "dark", accent}` |
| `smarttrader-chart` | Chart page preferences (inferred: symbol, interval, layout) |
| `smarttrader-drawings:<instrument>` | Drawings per instrument |
| `smarttrader-indicators` | Indicators on the chart |
| `smarttrader-rail` | Drawing tool side rail state |

No secrets or tokens are stored in the browser; the session cookie is HttpOnly.

## 4. Theming

- `index.html` runs an inline script before first paint: reads `smarttrader-theme`,
  falls back to `prefers-color-scheme`, adds the `dark` class on `<html>` and sets
  `data-theme` to the accent. The comment says it mirrors `src/lib/theme.ts`.
- Accents: zinc (default), green, blue, violet, orange, slate. The CSS defines
  `[data-theme=...]` variants of shadcn-style tokens: `--background`, `--foreground`,
  `--card`, `--muted`, `--accent`, `--primary`, `--ring`, `--border`, `--destructive`
  and trading colors `--buy` and `--sell` (with foregrounds). The chart reads these
  tokens for order and position line colors.
- The header has a light and dark toggle ("Switch to dark mode") and an accent picker.
- Fonts are the system stack (no web fonts). No icon libraries (per the spec); the
  chart engine draws its own tool glyphs.
- UI primitives use `class-variance-authority`, `clsx` and `tailwind-merge`
  (shadcn-like Button, Card and Alert components in `components/ui`, inferred from the
  folder names and class patterns).

## 5. Pages and main components

### 5.1 App shell (`components/layout`)

Header with the app name SmartTrader, user name and id, navigation (Dashboard,
Charts, Agent), theme toggle, accent picker and Logout. The trading page uses a fluid
full-height variant of the header.

### 5.2 Login (`/login`)

- Title "Connect Your Broker", subtitle "Sign in with your TradeSmart API credentials."
- Fields: API Key, API Secret (password; when a secret is stored the hint says "A
  secret is already stored. Enter a new one only to replace it."), Redirect URL
  (pre-filled from config or the default suggestion).
- Shows `?error=` from the URL in a "Login failed" banner.
- A "How sign in works" note and "Credentials are stored on your own machine. The API
  secret never leaves the server."
- Submit posts `/api/auth/login` and sets `window.location.href` to `authorize_url`.

### 5.3 Dashboard (`/dashboard`)

- Funds card: Available Cash, Collateral, Realized M2M, Unrealized M2M, Utilised
  Margin, Cash, Payin, Payout formatted with `en-IN` and two decimals, Refresh button,
  last fetched time.
- Master Contract card: status, message, started and finished times, total symbols,
  records per exchange table, progress indicator while downloading, "Download again".
- Symbol Search: text box ("For example RELIANCE or NIFTY"), exchange filter (All or
  one exchange), results table (Symbol, Name, Exchange, Type, Expiry, Strike, Lot size,
  Tick size, Token, Broker symbol). Selecting a row opens its chart.

### 5.4 Trading (`/trading`, `components/chart`)

- Toolbar: symbol search with exchange filter, timeframe group (intervals from the
  API), indicators dialog (categories, search, settings, "Press / on the chart"),
  drawing tools rail (cursor, magnet, stay in drawing mode, undo, redo, remove
  selected or all), snapshot (copy, download, open in new tab).
- Chart: openalgo-charts 2.6.0 engine with history plus live ticks.
- Trading overlay (not for `NSE_INDEX` or `BSE_INDEX`):
  - right-click the price pane: "Trade at price" with Limit, Market, Stop limit (SL)
    and Stop market (SL-M);
  - order ticket: side, quantity, product (MIS intraday, CNC delivery, NRML carry),
    order type, price, trigger; sends only on an explicit click;
  - open orders drawn as draggable lines (drag to modify) with a close button
    (cancel);
  - positions drawn as lines with live P&L = realized + (LTP - average) x net quantity x
    multiplier, updated on each tick;
  - every line gesture opens a native `<dialog>` confirmation ("Modify order", "Cancel
    order", "Close position") describing the exact change before calling the API.

### 5.5 Agent chat (`/agent`, `components/agent`)

- Sidebar "Chats": list, New chat, rename, delete ("Delete this chat?").
- Empty state "What can I help with?" with suggestion chips (Funds and positions,
  Orders and trades, Technicals, Crude oil on MCX).
- Composer "Ask about your funds, positions, quotes or any symbol", Send and Stop.
- Streaming answer rendered with react-markdown and remark-gfm, a reasoning display,
  and collapsible tool calls (Arguments, Result).
- Approval card ("Order approval") listing each proposed order (Symbol, Exchange,
  Quantity, Price type, Price, Trigger, Product, Order id) with Approve, Reject,
  Approve all and Reject all.
- Badges: model in use and "Trading on" when the agent may propose orders.
- When not signed in to ChatGPT: "Connect ChatGPT to start" with a link to settings.

### 5.6 Agent settings (`/agent/config`)

- ChatGPT subscription: Connected or Not connected, "Open sign-in page", the device
  code to enter, polling until signed in, sign out.
- Model select (models available through LiteLLM), reasoning effort (Model default,
  low, medium, high), extra instructions textarea, Save, Test.
- Tools list: read tools, then research tools, then order tools, each with its
  description. The card text says the agent calls tools "through your TradeSmart
  session and, for fundamentals, your FinEdge key". Research tools (from
  `research_tools` in `GET /api/agent/settings`) are dimmed with "(needs a FinEdge
  key)" until `finedge.configured` is true; order tools are dimmed while trading is
  off.
- FinEdge API key card (see 5.6.1), rendered after the ChatGPT, Model and Tools cards
  and before the trading settings.
- Trading: "Allow the agent to place, modify and cancel orders" toggle, max order value
  (INR), max quantity per order, max agent orders per day, allowed exchanges.
- Order audit table: newest first, last 100 rows.

#### 5.6.1 FinEdge API key card (hand patch of the build)

| Element | Behaviour |
| --- | --- |
| Title and text | "FinEdge API key". "Fundamental data for research reports, company comparisons and buy or sell views. The key is stored encrypted in the SmartTrader database and is never shown again." |
| Badge | "Key saved" (profit style) when `configured`, otherwise "Not set"; a skeleton while loading |
| Input | Password field, `autoComplete=off`, no spell check. Label "API key", or "Replace API key" when one is saved. Placeholder "Paste your FinEdge API key", or "Saved key " followed by the `masked_key` (eight `*` and the last 4 characters). Hint: "Get a key at finedgeapi.com. The free Basic plan only covers ITC, RELIANCE and HDFCBANK.", or "Saved <local time>. Paste a new key to replace it." |
| Save key | Submit; disabled while the field is empty. Sends `PUT /api/agent/finedge {api_key}` with the trimmed value, then clears the field, invalidates `["agent","finedge"]` and `["agent","settings"]` (so the Tools card updates) and runs the test automatically. |
| Test key | `POST /api/agent/finedge/test`; disabled until a key is saved. Success with coverage `all`: "Key works and covers all companies. FinEdge answered in N s." Coverage `sample`: "Key works, but this plan only covers the sample companies ITC, RELIANCE and HDFCBANK." Failure: "Test failed" alert with the server message. |
| Remove key | Shown only when a key is saved. `DELETE /api/agent/finedge`, resets the test result and invalidates the queries. |
| Errors | Load failure: "Could not load the FinEdge key status". Save failure: the server message (for example a 422 for a too short key). The backend never returns 401 for FinEdge problems, so these errors do not trigger the global logout. |

The key is never in browser storage or query cache; the page only ever holds
`configured`, `masked_key` and `updated_at`, plus what the user is typing.

This card exists only in the minified chunk. Because `frontend/src` is missing, the
change was written directly into `AgentConfigPage-C4TKen9_.js`. If the frontend is
ever rebuilt from recovered source, the card and the research tools rows disappear
unless they are re-implemented in source. Because the file name was not changed, a
browser that cached the old chunk shows the page without the card until a hard
reload. See [risks.md](risks.md) R1 and R11.

## 6. Spec discrepancies (frontend)

| Topic | SPEC.md | Build |
| --- | --- | --- |
| Routes | Frontend section lists `/`, `/login`, `/dashboard` | Also `/trading`, `/agent`, `/agent/config` (the agent section of the spec mentions the agent routes; the chart route path is not named in the spec) |
| Chart history `days` | API supports `days` | The client never sends `days`; the backend default lookback is always used |
| Symbol search | "small symbol search box" | Adds an exchange filter and opens the chart on selection |
| Source tree | Implied present | Missing; only the compiled bundle exists, so the frontend cannot be rebuilt. The settings chunk was modified by a hand patch of the minified file (FinEdge card), not from source. |
| FinEdge key card | Not in the spec | Hand-patched card on `/agent/config` (section 5.6.1) |
