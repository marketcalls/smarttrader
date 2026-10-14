# Key Flows

Sequence diagrams for the main runtime flows. Participants:

- SPA: the React app in the browser.
- API: the FastAPI app (routers plus `deps.py`).
- DB: `data/smarttrader.db`.
- Noren: TradeSmart Noren v2 (REST, OAuth pages, WebSocket, symbol files).
- LLM: the ChatGPT backend reached through LiteLLM's `chatgpt/` provider.
- FinEdge: the FinEdge Financials API at `https://data.finedgeapi.com`. Every call is
  a GET with the key in the `token` query parameter (`?token=<API_KEY>`), written in
  the diagrams as `token=(API key)` only where the key matters.

All authenticated REST calls to Noren are `POST <REST_ROOT>/<Endpoint>` with
`Content-Type: text/plain`, `Authorization: Bearer <access_token>` and body
`jData=<json>`. They are written below as `POST Endpoint`.

## 1. OAuth login and callback

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant SPA
    participant API
    participant DB
    participant Noren

    SPA->>API: GET /api/config
    API->>DB: read broker_config row 1
    API-->>SPA: api_key, redirect_url, has_secret (never the secret)
    U->>SPA: enter API key, secret (optional if stored), redirect URL, press Login
    SPA->>API: POST /api/auth/login {api_key, api_secret, redirect_url}
    API->>API: validate key not empty, redirect is http(s), secret present or stored
    API->>DB: upsert broker_config (secret Fernet-encrypted)
    API-->>SPA: {authorize_url}
    SPA->>Noren: window.location = OAuthlogin/authorize/oauth?client_id=KEY
    U->>Noren: log in on the TradeSmart portal
    Noren-->>API: 302 browser to /tradesmart/callback?code=CODE
    alt no code
        API-->>SPA: 302 /login?error=TradeSmart did not return an authorization code
    else code present
        API->>DB: read broker_config, decrypt api_secret
        API->>Noren: POST GenAcsTok jData={code, checksum=sha256(key+secret+code)} (no Bearer)
        alt stat is Ok
            Noren-->>API: access_token (or susertoken), actid or uid, uname
            API->>DB: deactivate all active auth_session rows
            API->>DB: insert auth_session (sha256 of new cookie, user, encrypted token)
            API->>API: master_contract.start_download_in_background()
            API-->>SPA: 302 FRONTEND_URL/dashboard + Set-Cookie st_session (HttpOnly, SameSite=Lax, Path=/)
            SPA->>API: GET /api/auth/session
            API-->>SPA: authenticated true, user_id, user_name, login_time (IST)
        else broker error or network error
            API-->>SPA: 302 FRONTEND_URL/login?error=message
        end
    end
```

Notes:

- `FRONTEND_URL` is empty (relative redirect) when `frontend/dist/index.html` exists,
  otherwise the Vite dev URL.
- Only one session is active at a time; a new login invalidates older browser
  sessions.
- Literal OAuth `code=` parameters are redacted from the uvicorn access log; encoded
  parameter names are not covered by the current filter.
- Logout: `POST /api/auth/logout` sets the matching row inactive and deletes the cookie.

## 2. Master contract download

```mermaid
sequenceDiagram
    autonumber
    participant SPA
    participant API
    participant T as Download thread
    participant DB
    participant Noren

    alt after login
        API->>API: callback calls start_download_in_background()
    else manual
        SPA->>API: POST /api/master-contract/download
    end
    API->>API: _download_lock.acquire(blocking=False)
    alt lock busy
        API-->>SPA: 409 already in progress (manual path only)
    else acquired
        API->>DB: status=downloading, message=Queued, started_at
        API->>T: start daemon thread
        API-->>SPA: 202 {status: downloading}
    end
    loop each exchange NSE, BSE, NFO, CDS, MCX, BFO
        T->>DB: message=Downloading EXCH
        T->>Noren: GET EXCH_symbols.txt.zip (browser User-Agent, 120 s read timeout)
        T->>T: extract expected member to fixed name (size cap 512 MB)
        T->>DB: message=Processing EXCH
        T->>T: parse CSV with the exchange parser
        Note over T: a failing exchange is logged and skipped
    end
    T->>T: de-duplicate on (token, exchange)
    alt no rows at all
        T->>DB: status=error, message lists failures
    else rows loaded
        T->>DB: one transaction: DELETE symtoken, INSERT in 10,000-row batches
        T->>DB: status=success, total_records, exchanges_json, finished_at
    end
    T->>T: remove temp dir, release lock
    loop every 2 s while status is downloading
        SPA->>API: GET /api/master-contract/status
        API->>DB: read status row
        API-->>SPA: status, message, counts, times (IST)
    end
```

WAL mode keeps symbol search and chart lookups working against the old table while
the replacement transaction runs. If the process dies mid download, `init_db()` on the
next start marks the status as `error`.

Partial success still replaces the entire symbol table with the exchanges that
loaded. Previous symbols for failed exchanges are not retained. If no rows load,
the old table is preserved and the status becomes `error`.

## 3. Funds fetch

```mermaid
sequenceDiagram
    autonumber
    participant SPA
    participant API
    participant DB
    participant Noren

    SPA->>API: GET /api/funds (cookie)
    API->>DB: require_session: find active auth_session by sha256(cookie)
    API->>API: decrypt access token
    API->>Noren: POST Limits {uid, actid}
    alt Limits not Ok
        API-->>SPA: 502 with broker emsg
    else Ok
        API->>Noren: POST PositionBook {uid, actid}
        Note over API: non-list or unusable PositionBook means zero P&L
        API->>API: available_cash = cash + payin - marginused<br/>realized and unrealized summed per position
        API-->>SPA: funds JSON with fetched_at (IST)
    end
    opt HTTP 401 or emsg contains Session Expired
        API->>DB: auth_session.is_active = false
        API-->>SPA: 401 Session expired
        SPA->>SPA: global 401 handler clears caches and navigates to /login
    end
```

## 4. Chart history

```mermaid
sequenceDiagram
    autonumber
    participant SPA
    participant API
    participant DB
    participant Noren

    SPA->>API: GET /api/chart/intervals
    API-->>SPA: 1m, 3m, 5m, 10m, 15m, 30m, 1h, 2h, D
    SPA->>API: GET /api/chart/history?symbol&exchange&interval
    API->>API: interval known, else 422
    API->>DB: symtoken where symbol and exchange match
    alt not found
        API-->>SPA: 404 not in the master contract
    else found
        API->>API: days = query value or lookback_days(interval)
        alt interval is D
            API->>Noren: POST EODChartData {sym: EXCH:BRSYMBOL, from, to}
        else intraday
            API->>Noren: POST TPSeries {uid, exch, token, st, et, intrv minutes}
        end
        Note over API: index exchanges query NSE or BSE
        API->>API: parse, sort, de-duplicate, repair intraday volume
        API-->>SPA: {symbol, exchange, name, interval, candles[time UTC epoch, OHLCV]}
    end
```

The SPA refetches history every 60 s for intraday intervals when the live stream is
not connected and every 5 minutes while it is live (ticks update the last bar in
between).

## 5. Live market data stream

```mermaid
sequenceDiagram
    autonumber
    participant SPA
    participant API as API (WS /api/ws/ticks)
    participant DB
    participant Noren as Noren WebSocket

    SPA->>API: WebSocket upgrade (cookie, Origin)
    alt Origin not allowed
        API-->>SPA: close 4403 before accept (browser sees 403)
    end
    API->>DB: (threadpool) find active session, decrypt token
    API->>SPA: accept
    alt no session
        API-->>SPA: close 4401 (client stops reconnecting and logs out)
    end
    API->>Noren: connect wss NorenWSAPI (browser User-Agent)
    API->>Noren: {t: a, uid, actid, source: API, accesstoken}
    Noren-->>API: {t: ak, s: OK}
    API-->>SPA: {type: status, state: connected}
    SPA->>API: {action: subscribe, symbol, exchange}
    API->>DB: (threadpool) resolve symbol to EXCH|token
    alt unknown symbol
        API-->>SPA: {type: error, message}
    else known
        API->>Noren: {t: u, k: previous key} if switching
        API->>Noren: {t: t, k: EXCH|token}
        API-->>SPA: {type: subscribed, symbol, exchange}
    end
    par broker feed
        Noren-->>API: tk or tf touchline (partial fields)
        API->>API: merge_tick keeps last ltp and volume
        API-->>SPA: {type: tick, symbol, exchange, ltp, volume, time}
    and heartbeat
        API->>Noren: {t: h} every 30 s
    end
    alt StreamError (connection, acknowledgement or feed failure)
        API-->>SPA: {type: status, state: error, message} then close 1011
        SPA->>SPA: reconnect with backoff 2 s doubling to 30 s
    else websockets.ConnectionClosed reaches the router
        API-->>SPA: close 1011 without status frame
    end
```

One broker socket is opened per browser socket and carries one instrument at a time.
The access token stays on the server.

The session is checked at connection time. Logout does not close an existing relay,
and broker refusal does not deactivate the local session row.

## 6. Order placement from the chart

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant SPA
    participant API
    participant DB
    participant Noren

    U->>SPA: right-click price pane, choose limit, stop or market at that price
    SPA->>SPA: open order ticket (side, quantity, product, order type, price, trigger)
    U->>SPA: explicit click to send
    SPA->>API: POST /api/orders {symbol, exchange, side, quantity, product, price_type, price, trigger_price}
    API->>API: validate exchange, side, product, price type, price and trigger presence
    API->>DB: symtoken lookup, lot size and tick size
    alt quantity not a lot multiple or other input error
        API-->>SPA: 422 message
    end
    API->>Noren: POST GetQuotes {uid, exch, token}
    alt MARKET
        API->>API: price = LTP +/- protection slab, rounded to tick against the trader
    else SL-M
        API->>API: price = trigger +/- protection slab
    else LIMIT or SL
        API->>API: price as given
    end
    API->>Noren: POST PlaceOrder {prctyp LMT or SL-LMT, prd I/C/M, ret DAY, ...}
    Noren-->>API: {stat: Ok, norenordno}
    API-->>SPA: {order_id, sent_price, ltp}
    loop every 3 s
        SPA->>API: GET /api/orders/symbol?symbol&exchange
        API->>Noren: POST OrderBook and PositionBook
        API-->>SPA: open order lines and position lines with multiplier and realized P&L
    end
```

Line gestures on the chart (drag an order line, click its close button, close a
position line) open a confirmation dialog first. On confirm the SPA calls
`POST /api/orders/{id}/modify`, `POST /api/orders/{id}/cancel` or
`POST /api/orders/close-position`. Modify reads the order book row, refuses orders
that are no longer open, and for stop orders moves the trigger while keeping the
limit gap. Close position sends an opposite MARKET order (protected limit).

Close position uses the submitted quantity and maps exactly `side="long"` to SELL;
every other side string maps to BUY. It does not fetch the current position or cap
the order to its remaining size. Broker acceptance does not establish that a
position was closed or that an order filled.

Chart orders are not subject to the agent risk limits; the safety step is the
explicit ticket click or confirmation dialog.

## 7. Order placement through the agent (human in the loop)

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant SPA
    participant API as API /api/agent
    participant AG as Agno Agent
    participant LLM
    participant DB
    participant Noren

    U->>SPA: "Buy 10 SBIN at market"
    SPA->>API: POST /api/agent/chat {message, conversation_id}
    API->>API: trading_enabled is true, so order tools are attached
    API->>AG: arun(stream=True)
    AG->>LLM: prompt + tools
    LLM-->>AG: tool call get_quote
    AG->>Noren: POST GetQuotes
    AG->>LLM: quote result
    LLM-->>AG: tool call place_order(args)
    Note over AG: place_order has requires_confirmation, so the run pauses
    AG-->>API: RunPaused with requirements
    API->>DB: audit status=proposed
    API-->>SPA: SSE confirm {run_id, requirements[id, tool_name, args, summary], state}
    API->>DB: assistant message with pending_json state=pending
    API-->>SPA: SSE done reason=paused
    SPA->>U: approval card with summary, Approve or Reject
    U->>SPA: Approve
    SPA->>API: POST /api/agent/chat/confirm {conversation_id, run_id, decisions{id: true}}
    API->>DB: check latest pending_json is this run and still pending, mark decided
    API->>AG: aget_run_output(run_id), confirm or reject each requirement
    API->>AG: acontinue_run(stream=True)
    AG->>AG: place_order body runs
    AG->>DB: audit status=approved
    AG->>DB: re-read agent_risk
    alt trading off, exchange not allowed, quantity or value over limit,<br/>daily count reached, same order within 60 s, unknown symbol
        AG->>DB: audit status=refused with reason
        AG-->>LLM: "Refused by the risk check, nothing was sent: ..."
    else passes
        AG->>Noren: POST GetQuotes, compute protected price, check order value
        AG->>Noren: POST PlaceOrder
        alt Ok
            AG->>DB: audit status=placed with order id
        else rejected or network error
            AG->>DB: audit status=failed with message
        end
    end
    AG->>LLM: tool result
    LLM-->>AG: final answer
    API-->>SPA: SSE run, tool_end, token..., usage, done reason=stop
```

If the user rejects, the requirement is rejected with the note "The user rejected
this in the app. Do not retry it" and the audit row is `rejected`. Sending a new chat
message while an approval is pending marks the old approval `expired`, so it can no
longer be confirmed.

The confirmation state is committed as `decided` before the paused Agno run is
loaded. If it is missing, the API returns 409 and the user must request a new
proposal. The read/check/write sequence has no atomic claim against concurrent
confirmation requests.

## 8. AI agent chat turn with tool calls

```mermaid
sequenceDiagram
    autonumber
    participant SPA
    participant API as API /api/agent/chat
    participant DB
    participant ADB as agent.db (Agno)
    participant AG as Agno Agent
    participant LLM
    participant Noren

    SPA->>API: POST /api/agent/chat {message, conversation_id or null}
    API->>API: require_session, ChatGPT signed in (else 409), decrypt broker token
    API->>DB: create conversation if new (title from first 60 chars), insert user message,<br/>expire pending approvals
    API->>API: build tools bound to this broker session (plus research tools when a FinEdge key is saved),<br/>build Agent (model chatgpt/MODEL, effort, prompt, 8 runs history, 20 tool calls max, 40 with a FinEdge key)
    API-->>SPA: SSE start {conversation_id, model}
    API->>AG: arun(message, stream=True, stream_events=True, session_id=conversation_id)
    AG->>ADB: load last 8 runs for this session
    AG->>LLM: system prompt + history + IST datetime + tools
    AG-->>API: RunStarted
    API-->>SPA: SSE run {run_id}
    loop up to 20 tool calls (40 with a FinEdge key)
        LLM-->>AG: tool call (arguments repaired by litellm_patches if missing)
        AG-->>API: ToolCallStarted
        API-->>SPA: SSE tool_start {id, name, args}
        AG->>Noren: broker call in a worker thread (to_thread)
        AG-->>API: ToolCallCompleted
        API-->>SPA: SSE tool_end {id, name, args, ok, result preview, duration}
        AG->>LLM: tool result (data, never instructions)
    end
    LLM-->>AG: streamed answer and reasoning
    AG-->>API: RunContent and ReasoningContentDelta
    API-->>SPA: SSE token {delta}, reasoning {delta}
    AG->>ADB: save run
    AG-->>API: RunCompleted with metrics
    API-->>SPA: SSE usage {tokens, duration, ttft_ms, model}
    API->>DB: insert assistant message (content, tools_json, usage_json), touch conversation
    API-->>SPA: SSE done {reason: stop}
```

Cancellation: the Stop button calls `POST /api/agent/chat/{run_id}/cancel`, which
looks up the agent in the in-memory `_running` map and calls `acancel_run`. A closed
browser connection is detected between events and also requests cancellation. A
normally completed generator emits `done` reason `cancelled`. Task cancellation
re-raises `asyncio.CancelledError`, so it cannot guarantee that terminal frame.
The `finally` block saves partial content, tools or pending approval when present;
reasoning-only or error-only output creates no assistant row. An already accepted
broker order is not reversed by cancelling a run.

Errors from the provider (not signed in, usage limit, unknown model) are converted to
an SSE `error` frame with a short readable message, followed by `done` reason `error`.

## 9. Research report turn (FinEdge tools)

A chat turn when a FinEdge key is saved, for example "Write a research report on ITC
and give me a long term view". The chat plumbing (SSE frames, persistence,
cancellation) is the same as section 8; only the differences are shown.

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant SPA
    participant API as API /api/agent/chat
    participant DB
    participant AG as Agno Agent
    participant LLM
    participant FinEdge
    participant Noren

    U->>SPA: "Research report on ITC, long term view"
    SPA->>API: POST /api/agent/chat {message, conversation_id}
    API->>DB: read agent_setting, agent_risk
    API->>DB: finedge.read_key(): decrypt agent_secret finedge_api_key
    alt key saved
        API->>API: tools = 10 read tools + 14 research tools (closures over the key)<br/>prompt = system prompt + RESEARCH_RULES, tool_call_limit 40
    else no key
        API->>API: tools = 10 read tools, prompt = system prompt + RESEARCH_OFF_RULE,<br/>tool_call_limit 20 (the model points the user to Agent settings)
    end
    API->>AG: arun(message, stream=True)
    AG->>LLM: system prompt + research rules + history + tools
    loop up to 40 tool calls
        LLM-->>AG: tool call, for example get_financial_statements(symbol=ITC, statement=pl)
        API-->>SPA: SSE tool_start {id, name, args} (args never contain the key)
        AG->>FinEdge: GET /api/v1/financials/ITC?statement_type=c&...&token=(API key)
        alt 429 or 5xx
            AG->>FinEdge: retry after 1 s, then 2 s
        end
        alt 200
            FinEdge-->>AG: raw JSON (absolute rupees, many line items)
            AG->>AG: trim, convert to Rs crore and percent, compact JSON with units
        else 401 ER006 (bad key)
            AG->>AG: "Error: FinEdge rejected the API key ..."
        else 401 or 403 ER002 (not in plan)
            AG->>AG: "Not available: the FinEdge plan ... does not cover this company"
        else network error
            AG->>AG: "Error: FinEdge is unreachable (ExceptionClass) ..." (no URL)
        end
        API-->>SPA: SSE tool_end {ok, result preview}
        AG->>LLM: tool result (data, never instructions)
    end
    opt live price or intraday technicals
        AG->>Noren: POST GetQuotes or TPSeries (read tools)
    end
    LLM-->>AG: report sections, rating with horizon, risks, data date, disclaimer
    API-->>SPA: SSE token..., usage, done reason=stop
```

Typical tool sequence for a full report: `get_company_profile`,
`get_financial_statements` (pl, bs, cf), `get_financial_ratios`,
`get_growth_metrics`, `get_valuation_history`, `get_peers` or `compare_companies`,
`get_shareholding`, `get_dividends_and_actions`, `get_announcements` and
`get_price_performance`. The model chooses the order; nothing in code enforces it.

For a buy or sell request without a horizon, the prompt tells the model to ask
whether the user means medium term (6 to 18 months) or long term (3 years or more)
before answering. `get_stock_quotes` and `get_peers` first send one batch quote; if
FinEdge refuses the batch because one symbol is outside the plan, they quote the
symbols one by one and report the uncovered ones as missing.

## 10. Save and test the FinEdge key

The "FinEdge API key" card on `/agent/config`. Save triggers a test automatically.

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant SPA as SPA /agent/config
    participant API as API /api/agent/finedge
    participant DB
    participant FinEdge

    SPA->>API: GET /api/agent/finedge
    API->>DB: read agent_secret finedge_api_key, decrypt
    API-->>SPA: {configured, masked_key, updated_at} (never the key)
    SPA->>U: badge Key saved or Not set
    U->>SPA: paste key in the password field, click Save key
    SPA->>API: PUT /api/agent/finedge {api_key}
    alt fewer than 8 or more than 256 characters, or whitespace
        API-->>SPA: 422 validation error (nothing stored)
    else valid
        API->>DB: encrypt with Fernet, upsert agent_secret finedge_api_key, updated_at
        API->>API: clear the FinEdge process cache, log "FinEdge API key saved"
        API-->>SPA: 200 {configured: true, masked_key (8 stars and the last 4 characters), updated_at}
        SPA->>SPA: clear the input, invalidate ['agent','finedge'] and ['agent','settings']
        SPA->>API: POST /api/agent/finedge/test (automatic after save)
        API->>DB: read and decrypt the key
        API->>FinEdge: GET /api/v1/company-profile/ITC?token=(API key)
        alt 401 with ER006
            API-->>SPA: 422 "FinEdge rejected the API key. Check it and try again."
        else unreachable
            API-->>SPA: 502 "FinEdge is unreachable. Try again in a minute."
        else other error
            API-->>SPA: 502 "FinEdge error (status): text with the key masked"
        else 200
            API->>FinEdge: GET /api/v1/company-profile/TCS?token=(API key)
            alt 200
                API-->>SPA: {ok, coverage: "all", sample_company, latency_ms}
                SPA->>U: "Key works and covers all companies. FinEdge answered in N s."
            else 401 or 403 not in plan (ER002)
                API-->>SPA: {ok, coverage: "sample", sample_company, latency_ms}
                SPA->>U: "Key works, but this plan only covers the sample companies ITC, RELIANCE and HDFCBANK."
            end
        end
    end
```

Notes:

- The test never uses FinEdge `/healthcheck`, because it answers 200 for any key.
- `POST /api/agent/finedge/test` with no saved key returns 409 "Save a FinEdge API key
  first."; the Test key button is disabled until a key is saved.
- No FinEdge failure is returned as 401: the SPA treats every 401 as an expired
  SmartTrader session and signs the user out.
- Remove key calls `DELETE /api/agent/finedge`, which deletes the row and clears the
  cache. A key is saved even if the following test fails, so the user can fix or
  remove it.
- The next chat message picks up the change: `_agent_for()` reads the key on every
  request.
