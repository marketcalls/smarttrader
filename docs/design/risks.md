# Risks, Technical Debt and Recommendations

Findings from reading the code, the build output and the local environment. Each item
lists the evidence, the impact and a recommendation. Severity: High, Medium, Low.

## 1. Summary of top risks

| # | Risk | Severity |
| --- | --- | --- |
| R1 | Frontend source is missing; only the compiled bundle exists | High |
| R2 | Dev CORS origins always trusted with credentials, and no CSRF token | High |
| R3 | Monkey patches of private LiteLLM internals with loose version constraints | High |
| R4 | Agent risk check gaps (contract multiplier, lot size, modify exchange, non-atomic counters); chart orders have no limits | Medium to High |
| R5 | Single-process in-memory state, no migrations, fragile local environment (broken venv launcher) | Medium |
| R6 | Cancelled or superseded ChatGPT sign-in can still save tokens | High |
| R7 | Price serialization changes approved prices; close-position trusts submitted exposure | High |
| R8 | No durable order idempotency or reconciliation for uncertain broker outcomes | High |
| R9 | Incomplete upstream data can appear as zero exposure or a successful master refresh | High |
| R10 | FinEdge research: undocumented response fields, Basic plan coverage, key in the query string, recommendations that are not advice | Medium |
| R11 | Agent settings chunk is a hand patch of the minified build: lost on rebuild, stale in browser caches | Medium |

## 2. Detailed findings

### R1. Missing frontend source (High)

- Evidence: `frontend/src` has only empty folders (`components/agent`, `chart`,
  `layout`, `ui`, `lib`). `frontend/index.html` loads `/src/main.tsx`, which does not
  exist. `frontend/dist` was built on 2026-10-09.
- Impact: the UI cannot be changed, fixed, type checked or rebuilt. `npm run build`
  cannot succeed without the source, and the Vite config uses `emptyOutDir: true`, so
  any build tooling change that let a build complete would replace the only working
  UI. Any backend API change risks breaking the frozen bundle. Security fixes in the
  UI (for example CSRF headers) are impossible.
- Recommendation: recover the source from version control, a backup or the original
  author. Until then, back up `frontend/dist`, do not run `npm run build`, and treat
  the API used by the bundle (listed in [frontend.md](frontend.md)) as frozen. As a
  last resort, the bundle could be de-minified, but that would not recover the
  original component structure.
- Update: the FinEdge key card was added by editing the minified settings chunk
  directly, which is the only way to change the UI today. See R11.

### R2. CORS and CSRF exposure (High)

- Evidence: `config.CORS_ORIGINS` (`127.0.0.1:5173`, `localhost:5173`) is applied with
  `allow_credentials=True` regardless of mode. No CSRF token or custom header check.
  Body-less POSTs (`/api/orders/{id}/cancel`, `/api/master-contract/download`,
  `/api/auth/logout`, agent sign-in and cancel routes) are simple requests.
- Impact: any page served on port 5173 can read account data and place real orders
  through `POST /api/orders`; any same-site page (127.0.0.1 on any port) can cancel
  orders blindly. See [security.md](security.md) section 5.
- Recommendation: enable the dev origins only when an environment flag is set; require
  a custom header (for example `X-SmartTrader: 1`) or a double-submit CSRF token on all
  non-GET routes; consider `SameSite=Strict`.

### R3. Fragile LiteLLM and Agno integration (High)

- Evidence: `chatgpt_auth.install()` replaces five private `Authenticator` methods and
  calls private ones (`_request_device_code`, `_poll_for_authorization_code`,
  `_exchange_code_for_tokens`, `_build_auth_record`, `_decode_jwt_claims`).
  `litellm_patches` wraps a private stream iterator method and relies on
  `_sequential_tool_call_index` and `_tool_call_index_map`. `pyproject.toml` uses
  `agno>=3.1.2` and `litellm>=1.104.2` (only `uv.lock` pins them).
- Impact: an upgrade (for example `uv lock --upgrade`) can silently break sign-in, make
  a request block on the interactive device login (if the override no longer applies),
  or make every tool call fail with missing arguments. The patch logs a warning and
  steps aside, so failures would be visible only in logs and as tool errors.
- Recommendation: pin exact versions in `pyproject.toml` for `agno` and `litellm`; keep
  the existing tests (`test_litellm_authenticator_uses_the_table_and_never_blocks`,
  `test_tool_arguments_sent_only_in_done_events_are_recovered`) as upgrade gates; add a
  startup self-check that reports whether both patches are active via
  `/api/agent/status`.

### R4. Order safety gaps (Medium to High)

- Evidence (`agent/trading.py`):
  - `place_order` value check is `send_price x quantity`; it ignores the contract
    multiplier (`prcftr`) used elsewhere for P&L, so MCX and some derivative orders are
    undervalued against `max_order_value`.
  - `place_order` does not validate lot-size multiples (the chart path does); the
    broker rejects them, which is safe but wastes an approval.
  - `modify_order` does not check that the order's exchange is allowed and does not
    look up a live price.
  - `_placed_today()` and `_recent_duplicate()` read the audit table without a lock;
    two approvals processed at the same moment could both pass.
  - The daily limit counts only `place_order` rows, so modifications and cancels are
    unlimited.
- Evidence (`routers/orders.py`): chart orders accept quantities up to 1,000,000 with no
  value limit; the confirmation is a UI-only step.
- Impact: the guardrails are weaker than the settings page suggests, mainly for F&O and
  MCX.
- Recommendation: compute notional as `price x quantity x multiplier` (from the quote
  or position data); reuse `order_service` validation (lot size, tradable exchange)
  inside the agent tools; check allowed exchanges on modify; serialize the risk check
  and placement with a process lock; optionally apply a separate, higher chart order
  limit.

### R5. Process model, persistence and environment (Medium)

- In-memory state: `_download_lock`, `_running` (agent runs), the ChatGPT `_flow` and
  the cached Fernet object are process local. Multiple uvicorn workers would break
  409 detection, run cancellation and sign-in polling. Document and enforce one worker.
- No graceful shutdown: the lifespan does nothing after `yield`; daemon threads are
  killed. `init_db()` repairs an interrupted master download on the next start, but an
  agent run killed mid stream loses its transcript.
- Migrations: only `create_all` plus an ad-hoc `_ADDED_COLUMNS` map. Renames, type
  changes or index changes need manual work. Consider Alembic once the schema settles.
- Broken venv launcher: `backend/.venv/Scripts/uvicorn.exe` has its interpreter path
  baked in and still points at `D:\AI Bootcamp 2026 Fintech\Day05\smarttrader\backend\.venv\Scripts\python.exe`
  (the venv was copied from the Day05 folder). The root README now uses
  `uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`, which avoids
  that launcher. A clean environment recreated from the lockfile still needs to be
  verified before release.
- `secret.key` lives next to the database; see [security.md](security.md).

### R6. Stale ChatGPT sign-in writes (High)

- Evidence: `chatgpt_auth._run_flow()` calls `write_tokens()` before checking
  `if _flow is flow`. `cancel_login()` clears only the visible flow reference.
- Impact: an old poller can overwrite credentials after cancellation, a newer login
  or logout. The identity check protects the displayed status, not stored tokens.
- Recommendation: fence token writes with a login generation checked atomically
  with persistence, invalidate that generation on logout, and stop superseded
  pollers. Verify with a delayed old flow completing after logout or a new sign-in.

### R7. Order price and position correctness (High)

- Evidence: `broker/orders.py` formats price and trigger with `:g`, which defaults
  to six significant digits. An intended price `25234.55` becomes `25234.5`.
  `routers/orders.close_position()` maps exactly `long` to SELL and every other
  string to BUY, forwarding quantity without fetching the current position.
- Impact: a wire price can differ from the approved price; a typo, stale position
  or oversized close request can increase or reverse exposure. Positive infinity
  also passes the current chart price models; broker acceptance is not established.
- Recommendation: finite decimal values with tick-preserving serialization, side
  enums, current-position validation and a durable close intent. Use shared domain
  validation for chart and agent paths, including lot size, allowed exchange,
  modification state and contract multiplier.

### R8. Uncertain order outcomes and approval recovery (High)

- Evidence: broker calls have no durable intent/idempotency key. A network error
  becomes 502 or an agent `failed` result even if the broker accepted the request
  before its response was lost. Duplicate checks count only recorded `placed` rows.
  `confirm()` also commits `decided` before checking whether the paused run exists.
- Impact: manual retries can duplicate exposure; an audit write failure can hide an
  accepted order; a missing paused run consumes an approval without executing it.
- Recommendation: reserve and persist an order intent before submission, represent
  ambiguous outcomes as `unknown`, reconcile with the broker and prevent blind
  resend. Atomically reserve daily quota across concurrent tools. Make approval
  recovery idempotent. The current single event-loop serializes the synchronous
  pre-await confirmation block; a database-level claim is needed before moving
  that work to threads or supporting multiple workers.

### R9. Misleading data fallbacks (High)

- Evidence: unusable PositionBook data can become zero P&L in `fetch_funds()`;
  non-list order/position books become empty arrays in `symbol_state()`.
  Partial master success replaces the whole table and drops previous contracts
  from failed exchanges. Symbol publication and status updates commit separately.
- Impact: unavailable data can look like no exposure, and a transient exchange
  download failure can remove previously usable contracts.
- Recommendation: distinguish empty from unavailable, expose completeness and age,
  retain validated prior exchange snapshots, and publish a version with its status
  atomically. Reject ambiguous `(symbol, exchange)` lookups; BFO normalization can
  collapse underlying names containing digits (see the schema risk document).

### R10. FinEdge research data and recommendations (Medium)

- Evidence (`agent/finedge.py`, `agent/research.py`, `agent/builder.py`):
  - FinEdge response fields (`revenueFromOperations`, `profitLossForPeriod`,
    `current_price`, `high52`, `columns` and `rows` in shareholding, and so on) and
    error codes are not formally documented. The code was written against live
    responses: `ER006` means a bad key, `ER002` means data outside the plan, and
    `/healthcheck` answers 200 for any key. Missing fields become `null`, and a
    renamed field would silently drop a figure rather than fail.
  - The free Basic plan covers only ITC, RELIANCE and HDFCBANK. Other companies are
    refused as not in plan; one uncovered symbol makes FinEdge refuse a whole batch
    quote, so the tools fall back to one quote per symbol (more calls, slower).
    The key test reports `sample` coverage from a single probe (TCS); it does not
    prove which other datasets the plan includes.
  - FinEdge only accepts the key as a `token` query parameter. It stays out of logs
    only while the `httpx` logger is at WARNING and while errors are built from the
    status code and masked FinEdge text.
  - Recommendations (Buy, Accumulate, Hold, Reduce or Sell with a medium term or
    long term horizon) are produced by the model under prompt rules only. Nothing in
    code checks the numbers, the rating, the horizon or the disclaimer. Prices from
    FinEdge are delayed 15 to 30 minutes, and statements lag the reporting cycle.
    The output is a research view for education, not investment advice, and the
    app is not a SEBI registered adviser.
  - The key is not configured through `.env`: it is entered only on the Agent
    settings page and stored Fernet-encrypted in `agent_secret`. Setting a
    `FINEDGE_API_KEY` or similar environment variable has no effect.
- Impact: a FinEdge schema change can produce reports with missing or wrong figures
  without an error; a Basic plan user sees most companies as "Not available"; a user
  may act on a model-written rating as if it were advice; raising the httpx log
  level would write the key to the console.
- Recommendation: add a contract test against a recorded FinEdge sample and a live
  smoke check behind a flag; surface plan coverage in the agent status; keep the
  disclaimer and the data date in every recommendation (consider a code-level check
  or a fixed footer added by the UI); never raise the httpx log level in production;
  ask FinEdge for header-based authentication.

### R11. Hand-patched settings chunk (Medium)

- Evidence: `frontend/dist/assets/AgentConfigPage-C4TKen9_.js` was edited in place to
  add the FinEdge API key card and the research tools rows (it imports the main
  chunk's fetch wrapper as `Qe` and uses the query key `["agent","finedge"]`). The
  file name, which is a content hash, was kept so that `index-*.js` still loads it.
  `frontend/src` is missing, so the change exists nowhere in source.
- Impact:
  - Any rebuild from recovered source (or restoring the original chunk) removes the
    card and the research tools list; the backend keeps working, but the user can no
    longer save, test or remove a FinEdge key from the UI.
  - Because the hashed file name did not change, a browser that had cached the old
    chunk could keep showing the settings page without the card. This happened in
    practice. `spa_fallback` in `main.py` now sends `Cache-Control: no-cache` with
    every frontend file, so the browser revalidates with the ETag (a cheap 304 when
    unchanged) and picks up a patched chunk. A copy cached before that change still
    needs one hard reload (Ctrl+F5).
  - A minified hand patch has no type checking or tests; a mistake shows up only in
    the browser.
- Recommendation: when the source is recovered, re-implement the card and the research
  tools rows in `src` before building, and use the API in [../api/agent.md](../api/agent.md)
  as the contract. Until then, keep the patched file under version control; the
  original chunk can be restored from git if the patch must be reverted. Tell users
  to hard reload `/agent/config` once after the update.

## 3. Other technical debt

| Item | Evidence | Recommendation |
| --- | --- | --- |
| Stale docstring | `agent/tools.py` says "SmartTrader has no order routes" | Update the module docstring |
| Misleading order tag | `orders.place_payload()` sets `remarks=smarttrader-agent` for chart orders too | Pass the source (`chart` or `agent`) into the payload |
| No HTTP connection reuse | `post_authenticated()` creates a new `httpx.Client` per call (except funds and master download) | Share one client per process or per request with keep-alive |
| Blocking DB writes on the event loop | `_stream_run` writes transcripts and audit rows synchronously inside the async generator | Move to `run_in_threadpool` if streams get heavier |
| Agent tool session expiry | Agent tools return an error string but do not deactivate `auth_session` | Deactivate on `SessionExpiredError` as REST routes do |
| Unbounded tables | Inactive `auth_session` rows, `agent_message`, `agent_order_audit` and `agent.db` grow forever | Add retention or a cleanup action |
| No session timeout | Sessions end only on logout, new login or broker expiry | Add idle and absolute timeouts |
| Unencrypted transcripts | `agent_message` and `agent.db` store account data in plain text | Encrypt content or offer a purge |
| `search_symbols` tool LIKE escaping | Agent tool builds `%term%` without escaping `%` and `_` (the REST search escapes) | Reuse the REST search helper |
| `vite preview` cannot reach the API | Preview on port 4173 has no proxy and is not a CORS origin | Add a preview proxy or document that preview is unsupported |
| History `days` unused by the UI | Client never sends `days` | Either expose it or drop it |
| Frontend tests | None in the repository | Add once the source is recovered |
| One broker socket per browser socket | Each chart tab opens its own Noren WebSocket | Share one broker connection and fan out if several tabs are common |
| Hard-coded IST offset | `_placed_today()` adds a fixed +05:30 | Acceptable (India has no DST), but use `config.IST` for consistency |
| Default model name | `DEFAULT_MODEL = "gpt-5.5"` must exist in LiteLLM's model list | Already falls back to the first listed model |
| Logging and operations | Console logs have no request correlation, metrics or alert pipeline; health always returns `ok` | Add redacted structured events, order-intent correlation, readiness and actionable alerts |
| Narrow OAuth log redaction | `_RedactAuthCode` matches literal `code=` only; an encoded query key bypasses it | Parse/redact query parameters before logging and test encoding variants |
| Backup and recovery | No automated consistent backup or restore drill for both databases and their key | Define recovery objectives, protect backups and test restoration |
| Release verification | No CI workflow, backend lint/type gate or frontend tests in the supplied checkout | Build and test from a clean locked environment; make safety regression tests release gates |

## 4. Spec discrepancies (consolidated)

| Area | SPEC.md | Code (source of truth) |
| --- | --- | --- |
| Backend layout | Omits `broker/history.py`, `broker/stream.py`, `broker/orders.py`, `broker/order_service.py`, `agent/trading.py`, `agent/litellm_patches.py` | These modules exist and carry charts, ticks, orders and agent trading |
| Frontend routes | `/`, `/login`, `/dashboard` (agent section adds `/agent`, `/agent/config`) | Build also has `/trading` |
| Open order statuses | `OPEN`, `TRIGGER_PENDING` | Adds `PENDING` |
| WS foreign Origin | Refused with 403 | Close code 4403 before accept (observed as HTTP 403) |
| Agent risk check | Lists six checks for orders | All six apply to `place_order` only; `modify_order` and `cancel_order` check fewer |
| Agent status and settings payloads | Fewer fields | Extra fields (`provider`, `instructions`, `trading_enabled`, `order_tools`, `reasoning_efforts`) |
| Run command | Original setup used `uv run uvicorn ...` | Root README now uses `uv run python -m uvicorn ...` for this checkout |
| Frontend source | Described as React source | Only the build exists; the settings chunk carries a hand patch (R11) |
| Fundamental research | Not in the spec | FinEdge research tools, key card and routes (R10, R11) |

## 5. Recommended next steps (priority order)

1. Recover `frontend/src` and protect `frontend/dist` from an accidental rebuild.
2. Make dev CORS origins opt-in and add a CSRF header check on all non-GET routes.
3. Pin `agno` and `litellm` exactly; surface patch health in `/api/agent/status`.
4. Fix agent risk math (multiplier, lot size), check exchanges on modify, and lock the
   risk check plus placement.
5. Verify a freshly created virtual environment and enforce the documented
   single-worker requirement. The README launch command has been corrected.
6. Add session timeouts and data retention for sessions, transcripts and audit rows.
7. Fix stale login writes, wire-price precision and close-position validation.
8. Introduce durable order intents, reconciliation, safe read retries and explicit
   degraded-data states before treating this as reliable live-trading software.
9. Port the FinEdge card into recovered frontend source (R11) and add a FinEdge
   contract test and a plan coverage indicator (R10).
