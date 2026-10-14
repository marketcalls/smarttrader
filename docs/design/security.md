# Security Design

SmartTrader is a single-user application meant to run on the user's own machine on
`http://127.0.0.1:8000`. Its security model assumes that the loopback interface and
the local user account are trusted, and that the browser is the only client. This
document describes the controls in the code and the threats they do and do not cover.

## 1. Assets

| Asset | Where it lives | Sensitivity |
| --- | --- | --- |
| TradeSmart API secret | `broker_config.api_secret_enc` (Fernet) | High: with the API key it signs the token exchange |
| TradeSmart access token | `auth_session.access_token_enc` (Fernet), process memory while used | Critical: full trading access for the day |
| ChatGPT OAuth tokens (access, refresh, id) | `agent_secret` row `chatgpt_oauth` (Fernet) | High: access to the user's ChatGPT subscription |
| FinEdge API key | `agent_secret` row `finedge_api_key` (Fernet), process memory inside research tool closures for one run | Medium: use of the user's paid FinEdge data plan and its rate limits; no trading or account access |
| Fernet key | `backend/data/secret.key` | Critical: decrypts all of the above |
| Session cookie `st_session` | Browser cookie jar; only its SHA-256 is stored | High while active |
| Account data (funds, positions, orders, holdings) | Fetched live; copies in agent transcripts and `agent.db` | Personal financial data |
| API key, redirect URL, broker user id and name | Plain text in `broker_config` and `auth_session` | Low to medium |

## 2. Secret storage and encryption

- `crypto.py` uses Fernet (AES-128-CBC with HMAC-SHA256, from `cryptography` 50.0.2).
- The key is generated on first start and written to `data/secret.key` with
  `O_CREAT | O_EXCL` and mode 0600 (the mode is effective on POSIX; on Windows the
  file inherits the folder ACL). Exclusive creation avoids overwriting an existing
  key, and the process-local lock serializes normal callers in this single-worker app.
- One `Fernet` instance is cached under a lock.
- `decrypt()` returns `None` for missing ciphertext or Fernet `InvalidToken` under
  a valid loaded key. It does not catch malformed key files or every decoding error.
  The key is cached until process restart. Replacing it with another valid key makes
  old ciphertext unreadable after restart. `/api/config` still reports `has_secret`
  from ciphertext presence, so the user must explicitly re-enter the broker secret;
  the callback cannot repair it. There is no key rotation.
- No `.env` files and no secrets in `config.py`. The only environment variables are
  `SMARTTRADER_FRONTEND_URL` (not secret) and `CHATGPT_TOKEN_DIR` (a path). The
  FinEdge API key is not read from `.env` or the environment either; it is entered
  only on the Agent settings page and stored encrypted (section 4.1).
- `backend/.gitignore` excludes `data/`.

Limitation: the key and the encrypted values sit in the same folder. Encryption at
rest protects against a leak of the database file alone (for example sharing
`smarttrader.db`), not against someone who can read the whole `data` folder or a
backup of it.

## 3. Session management

- On a successful OAuth callback the server creates `secrets.token_urlsafe(32)` (256
  bits) as the cookie value and stores only `sha256(cookie)` in `auth_session`.
- Cookie attributes: `HttpOnly`, `SameSite=Lax`, `Path=/`, host-only (no `Domain`), no
  `Max-Age` (browser session cookie), `Secure=False` so it works on plain HTTP
  loopback.
- Only one active session: a new login sets all other rows inactive.
- Protected HTTP requests use `require_session` (hash lookup with `is_active = true`).
  There is no server-side idle or absolute timeout. Logout and a new broker login
  deactivate sessions. Funds, chart history and order routes also deactivate the
  session on missing credentials or broker expiry; agent tools and tick streams do
  not. `/api/auth/session` only checks the local row, not broker token validity.
- Logout deactivates the row and deletes the cookie. It does not revoke the broker
  token, close existing WebSockets or cancel agent runs already holding credentials.
  WebSocket authentication happens at connection time, not before every tick.
- Inactive rows are never purged; they keep the encrypted (expired) broker token.

Conversations, settings, ChatGPT tokens, risk limits and audit rows belong to the
local installation, not an authenticated broker account. Agent routes do not filter
them by `user_id`. Signing in with a different broker account exposes the same local
history and settings. This is a single-user design, not account isolation.

## 4. Broker token and FinEdge key handling

- The token is obtained server side from `GenAcsTok` using
  `checksum = sha256(api_key + api_secret + code)`; the secret never leaves the server.
- It is encrypted before storage and decrypted only for the duration of a request,
  inside agent tool closures for one run, or inside the tick relay for the lifetime
  of a WebSocket.
- It is sent only to TradeSmart: as a Bearer header on REST calls and in the `t=a`
  connect message on the Noren WebSocket. The browser never receives it; the tick
  relay forwards only symbol, LTP, volume and time.
- The token does not enter the LLM prompt or tool arguments (tools are closures).
- Application logging does not deliberately include broker tokens. The access-log
  filter redacts literal `code=` parameters and httpx logging is turned down to
  WARNING. This is not a general secret scrubber: an encoded key such as `c%6fde`
  bypasses the regex, and upstream exception text is not centrally sanitized.

### 4.1 FinEdge API key

| Control | Implementation (`agent/finedge.py`, `routers/agent.py`) |
| --- | --- |
| Entry | Only through `PUT /api/agent/finedge` from the Agent settings page (password input, `autoComplete=off`). Body `{api_key}` must be 8 to 256 characters with no whitespace. Never in `.env`, environment variables, files or the repository. |
| Storage | `crypto.encrypt()` into `agent_secret.value_enc`, row `finedge_api_key`, with `updated_at`. Same Fernet key and same limitation as the other secrets (section 2). |
| Read back | Never. `GET /api/agent/finedge`, the `finedge` field of `GET /api/agent/settings` and the PUT and DELETE responses return only `configured`, `masked_key` (eight `*` plus the last 4 characters; eight `*` only when the key has 8 characters or fewer) and `updated_at`. The browser cannot recover the key; replacing it means pasting a new one. |
| Use | `finedge.read_key()` decrypts it per request. `research.build_research_tools(key)` makes closures over it, so it is not in the system prompt, tool arguments, SSE frames, `agent_message` or `agent.db`. |
| On the wire | HTTPS to `https://data.finedgeapi.com`. FinEdge accepts the key only as a `token` query parameter, so every request URL contains it. |
| Logging | URLs are never logged: the `httpx` logger is at WARNING, and the code logs only "FinEdge API key saved" or "removed" and, for unexpected tool failures, the tool name and exception class. |
| Error text | For a non-200 answer, the message is the status code plus at most 200 characters of FinEdge's text with the key replaced by `***`. A connection error becomes "FinEdge is unreachable (ExceptionClass)" with the httpx exception (which can contain the URL) dropped. These masked messages are the only FinEdge text that reaches the browser, the model or the transcript. |
| Status codes | A rejected key is answered with 422, other FinEdge failures with 502, a missing key with 409. Never 401, because the SPA logs the user out on any 401. |
| Removal | `DELETE /api/agent/finedge` deletes the row and clears the in-process FinEdge cache. The key is not revoked at FinEdge; the user does that in their FinEdge account if needed. |

Residual exposure: anyone who can read `data/` can decrypt the key, as with the other
secrets. Raising the `httpx` log level to INFO or DEBUG for troubleshooting would
write the key into the console log. The masking relies on an exact string replace,
so FinEdge echoing an encoded form of the key would not be masked (not observed).

## 5. CSRF, CORS and Origin checks

| Control | Implementation |
| --- | --- |
| CORS | `CORSMiddleware` with `allow_origins = ["http://127.0.0.1:5173", "http://localhost:5173"]`, `allow_credentials=True`, all methods and headers. Same-origin requests on port 8000 do not need CORS. |
| JSON bodies | All state-changing endpoints with a body use Pydantic models, so FastAPI requires a JSON content type. A cross-origin JSON POST is not a "simple" request and triggers a preflight that only the two dev origins pass. |
| SameSite=Lax | The cookie is not sent on cross-site subrequests or cross-site POSTs from other sites. |
| WebSocket Origin | `origin_allowed()` accepts a configured CORS origin or an Origin whose parsed `netloc` equals the `Host` header, including the port. Missing/other origins close with 4403 before accept. The fallback comparison does not validate the scheme. |
| SPA fallback | Serves only files that resolve inside `frontend/dist` (`is_relative_to`), never `api/` or `tradesmart/` paths. |

There is no CSRF token. Residual exposure:

- "Same site" ignores ports, so pages served from any other port on `127.0.0.1` (for
  example another local dev server) are same-site, and the browser sends `st_session`
  with their requests. Such a page can trigger body-less POSTs without a preflight:
  `/api/orders/{id}/cancel`, `/api/master-contract/download`, `/api/auth/logout`,
  `/api/agent/chatgpt/login`, `/api/agent/chatgpt/logout`, `/api/agent/test`,
  `/api/agent/finedge/test`, `/api/agent/chat/{run_id}/cancel`. It cannot read
  responses. `/api/agent/finedge/test` only spends two FinEdge calls; saving or
  removing the FinEdge key needs a JSON body or a DELETE, which trigger a preflight. The most harmful is
  cancelling an open order, which needs a valid order id.
- The two dev origins are allowed with credentials even in single-port production
  mode. Any process that serves a page on port 5173 of the same machine can read every
  API response and send JSON requests, including `POST /api/orders` (real orders, with
  no server-side risk limits) and `PUT /api/agent/risk`.
- The OAuth authorize URL carries no `state` parameter, so the callback cannot tell a
  login it started from one it did not (login CSRF). Practical impact is low because a
  forged code must be valid for this API key and the exchange needs the server's
  secret; a failed exchange does not touch existing sessions.
- `/api/config` and `/api/auth/login` are unauthenticated by design (they are needed
  before login). Any local client can read the API key and redirect URL, and overwrite
  the stored broker configuration.

## 6. Transport

Plain HTTP on loopback. There is no TLS, no HSTS and the cookie is not `Secure`. This
is acceptable only while uvicorn binds to `127.0.0.1` as documented. Nothing in the
code prevents `--host 0.0.0.0`; doing so would expose unauthenticated configuration
endpoints and send the session cookie in clear text over the network.

No security headers are set (no Content-Security-Policy, X-Frame-Options or
Referrer-Policy). Framing by other sites is harmless for authenticated actions
because a third-party frame does not receive the Lax cookie, but a same-site page on
another port could frame the app.

## 7. Agent order safety

See [agent.md](agent.md) section 5 for the full list. Summary:

1. Off by default: new chats attach order tools only when trading is enabled.
   Confirmation requests also attach them to resume pending calls; each tool checks
   the current trading switch before sending an order.
2. Human approval per order: every order tool call pauses the run; the UI shows the
   real arguments and a summary; only an explicit approval of that tool call id lets
   the tool body run. Unanswered decisions count as rejections; a new message expires
   pending approvals; only the latest pending approval for the run can be confirmed.
3. Server-side re-check after approval: `place_order` checks trading, exchange,
   quantity, value, daily count, duplicates and the symbol master. `modify_order`
   checks trading, price type, quantity, price and value plus order existence, but
   not open status, allowed exchange, lot multiples or a positive SL trigger.
   `cancel_order` checks the trading switch only.
4. Market price protection: MARKET and SL-M go out as limit orders priced off the LTP
   or trigger with slab buffers, rounded against the trader.
5. Audit: proposals and explicit decisions are recorded, with `approved` written
   before risk evaluation and `refused`, `failed` or `placed` afterward on handled
   paths. Expiring an approval via a new message does not add a rejection row.
   Conversion, database or other unhandled failures need not produce a terminal
   audit row. `placed` records broker acceptance, not a confirmed fill.
6. Prompt injection resistance: rule 1 of the system prompt, structured tool results,
   approval UI shows the actual arguments, and a tool call budget per run (20, or 40
   when a FinEdge key is saved). FinEdge filing descriptions and company profiles
   are third-party text and are treated as data under rule 1; research tools are read
   only and cannot reach the broker.

Chart orders (`/api/orders`) are a separate path with input validation and price
protection but no risk limits; their safety step is the UI ticket click or
confirmation dialog, which the API itself does not enforce.

The chart close-position route trusts the submitted side and quantity. It does not
read the current position before placing the opposite order, so it is not a
reduce-only operation and can open or reverse exposure. ChatGPT sign-out also has a
separate limitation: an already-running device-login thread can save tokens after
sign-out because `_run_flow()` writes them before checking flow identity. See R6 in
[risks.md](risks.md).

## 8. Third-party data flows

- TradeSmart: credentials, token, orders and market data (required).
- OpenAI ChatGPT backend through LiteLLM: every agent turn sends the conversation
  history (last 8 runs), the system prompt, the user's extra instructions and tool
  results, which include funds, positions, holdings, orders and trades. Users should
  treat agent conversations as shared with OpenAI under their ChatGPT plan terms.
- OpenAI device-code OAuth endpoints for sign-in.
- Cloudflare in front of TradeSmart sees a fixed desktop Chrome User-Agent.
- FinEdge Financials API (`data.finedgeapi.com`, optional, only when a key is saved):
  each research tool call sends the API key (as the `token` query parameter), the
  company symbol or BSE code, statement and period choices, date ranges and, for
  the key test, the fixed symbols ITC and TCS. FinEdge therefore learns which
  companies the user researches and when, together with the server's public IP. No
  broker credentials, account data, positions, orders or chat text are sent to
  FinEdge.
- FinEdge results flow on to OpenAI: research tool results are part of the agent
  turn, so the fundamentals, quotes and filings returned by FinEdge are sent to the
  ChatGPT backend as tool results, as above. The key itself is not.

## 9. Threat notes

| Threat | Likelihood | Impact | Current mitigation | Gap or recommendation |
| --- | --- | --- | --- | --- |
| Theft of `data/` folder or backup | Low | Critical (token, secrets) | Fernet at rest | Key beside data; consider OS keyring (Windows DPAPI) for the key |
| Local web page on port 5173 abusing CORS | Low to medium on a developer machine | High (orders, data) | none beyond loopback | Include dev origins only in dev mode (env flag) |
| Same-site page on another port sending body-less POSTs | Low | Medium (cancel orders, logout) | SameSite=Lax blocks other sites only | Require a custom header or CSRF token on all state-changing routes |
| Exposure by binding to a non-loopback interface | Low | Critical | README says 127.0.0.1 | Refuse to start on non-loopback hosts or require TLS and a password |
| Prompt injection via tool data or symbol names | Medium | Medium (bad proposals) | Rule 1, approval gate, risk re-check | Keep approvals mandatory; show value with multiplier |
| Agent proposes oversized MCX or F&O order | Medium | High | Max value check | Value ignores `prcftr` and lot size; include multiplier and lot checks |
| Session never times out | Medium | Medium | Broker token expiry | Add idle and absolute timeouts; purge inactive rows |
| Supply chain (agno, litellm with `>=` constraints) | Medium | High (code runs with access to tokens) | `uv.lock` pins exact versions | Pin in `pyproject.toml` and review upgrades; monkey patches hide behaviour changes |
| Malicious or oversized symbol zip | Low | Medium | Fixed extract name, 512 MB member cap, HTTPS source | Acceptable |
| Path traversal through SPA fallback | Low | Medium | `resolve()` plus `is_relative_to(dist)` | Acceptable |
| Sensitive data in logs | Low | Low | Code redaction, httpx WARNING | User id is logged at login and logout |
| FinEdge key leaked through a logged URL | Low | Medium (use of the paid plan) | httpx logger at WARNING, errors built from status and masked text, no URL in connection errors | Keep httpx at WARNING; FinEdge only supports the key as a query parameter |
| FinEdge key read from the browser | Low | Medium | Never returned, only masked with the last 4 characters | Acceptable |
| Misleading research output taken as advice | Medium | Medium to High (financial loss) | Prompt requires a rating with horizon, risks, data date and a not-advice disclaimer | Prompt-only; see [risks.md](risks.md) R10 |
| Unencrypted transcripts | Medium | Medium | Local disk only | `agent_message` and `agent.db` hold account data in plain text |
