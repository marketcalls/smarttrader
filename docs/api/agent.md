# AI agent

Source: `backend/app/routers/agent.py`, `backend/app/agent/builder.py`, `backend/app/agent/tools.py`,
`backend/app/agent/trading.py`, `backend/app/agent/chatgpt_auth.py`.
Tests: `backend/tests/test_agent.py`, `backend/tests/test_orders.py`.

Back to the [API index](README.md).

The agent is an Agno `Agent` driving a ChatGPT-subscription model through LiteLLM's `chatgpt/<model>`
provider. It reads market and account data from TradeSmart with the logged-in user's session and, when
trading is switched on, can propose orders that the user must approve in the chat.

Every route under `/api/agent` requires a SmartTrader session (router-level `require_session`), so all
of them can return `401 {"detail": "Not authenticated"}`. This is separate from the ChatGPT sign-in,
which is required only for `/test`, `/chat` and `/chat/confirm` (409 otherwise).

Settings, ChatGPT tokens, risk limits, transcripts and audit rows are shared application state.
Conversation and audit routes do not filter by broker user id; a later broker login can access the
previous account's saved conversations. Handled endpoint errors are listed below; unhandled setup,
storage or provider failures can return HTTP 500 under the [common error contract](README.md#error-format).

---

## Settings and status

### GET /api/agent/status

ChatGPT sign-in state plus the current agent settings.

- Auth required: yes
- Request: none

Response `200`:

```json
{
  "signed_in": true,
  "email": "trader@example.com",
  "plan": "plus",
  "provider": "chatgpt",
  "model": "gpt-5.5",
  "reasoning_effort": "medium",
  "instructions": "",
  "trading_enabled": false
}
```

| Field | Type | Notes |
| --- | --- | --- |
| `signed_in` | boolean | true when stored tokens can be decrypted and contain a refresh token; does not validate that token with the provider |
| `email` | string or null | From the stored ID token claims |
| `plan` | string or null | `chatgpt_plan_type` claim |
| `provider` | string | Always `chatgpt` |
| `model` | string | Model id without the `chatgpt/` prefix |
| `reasoning_effort` | string or null | `low`, `medium`, `high` or null |
| `instructions` | string | Extra user instructions, empty string when none |
| `trading_enabled` | boolean | Agent trading switch from the risk settings |

Errors: 401 only.

Side effects: creates the `agent_setting` row (id 1) with defaults on first use (model `gpt-5.5` if
LiteLLM knows it, else the first available model; effort `medium`) and the `agent_risk` row (id 1) with
its defaults. No tokens are ever returned.

### GET /api/agent/settings

Settings plus the choices the settings page needs.

- Auth required: yes

Response `200`:

```json
{
  "model": "gpt-5.5",
  "reasoning_effort": "medium",
  "instructions": "",
  "models": ["gpt-5.5", "gpt-5.4", "gpt-5.3-codex", "gpt-5"],
  "reasoning_efforts": ["low", "medium", "high"],
  "tools": [
    {"name": "search_symbols", "description": "Find instruments by symbol or name"},
    {"name": "get_quote", "description": "Live quote with change, volume, circuits and 52 week range"},
    {"name": "get_history", "description": "Recent OHLCV candles in any broker timeframe"},
    {"name": "get_technicals", "description": "RSI, SMA, EMA, ATR and the recent range"},
    {"name": "get_funds", "description": "Cash, collateral, margin and P&L"},
    {"name": "get_positions", "description": "Today's positions"},
    {"name": "get_holdings", "description": "Demat holdings"},
    {"name": "get_order_book", "description": "Today's orders"},
    {"name": "get_trade_book", "description": "Today's trades"},
    {"name": "get_master_contract_status", "description": "Symbol master status"}
  ],
  "order_tools": [
    {"name": "place_order", "description": "Place an order after your approval"},
    {"name": "modify_order", "description": "Modify an open order after your approval"},
    {"name": "cancel_order", "description": "Cancel an open order after your approval"}
  ],
  "trading_enabled": false
}
```

`models` lists every `chatgpt/*` key in LiteLLM's model cost map with the prefix removed, sorted in
reverse string order. The exact list depends on the installed LiteLLM version; the values above are
illustrative.

Errors: 401 only. Side effects: same row creation as `/status`.

### PUT /api/agent/settings

- Auth required: yes

Request body (`SettingsBody`):

| Field | Type | Required | Constraints | Notes |
| --- | --- | --- | --- | --- |
| `model` | string | yes | length 1 to 80 | Must be one of `models` from GET settings |
| `reasoning_effort` | string or null | no | `low`, `medium`, `high`, empty or null | Empty or null means the model default (no `reasoning_effort` sent) |
| `instructions` | string or null | no | max 4000 characters | Trimmed; empty becomes null. Appended to the system prompt as "Additional instructions from the user's settings" |

Example:

```json
{"model": "gpt-5.5", "reasoning_effort": "high", "instructions": "Keep answers short."}
```

Response `200`:

```json
{"model": "gpt-5.5", "reasoning_effort": "high", "instructions": "Keep answers short."}
```

Errors:

| Status | `detail` | When |
| --- | --- | --- |
| 422 | `Unknown model` | `model` not in the available list |
| 422 | `Reasoning effort must be low, medium or high` | Any other non-empty effort |
| 422 | validation list | Missing `model` or length limits |

Side effects: updates `agent_setting` (id 1) and `updated_at`. Applies to the next turn.

---

## ChatGPT sign-in (device-code flow)

The model provider is signed in with a ChatGPT subscription through OpenAI's device-code OAuth flow
(the one used by the Codex CLI, via LiteLLM). The tokens are stored Fernet-encrypted in the
`agent_secret` table (row `chatgpt_oauth`) and never reach the browser. Only one flow exists at a time,
held in process memory.

Flow object returned by the login endpoints:

| Field | Type | Notes |
| --- | --- | --- |
| `state` | string | `none`, `pending`, `complete`, `error` or `expired` |
| `user_code` | string or null | The code the user types on the verification page; only while `pending` |
| `verification_url` | string | `https://auth.openai.com/codex/device` |
| `expires_in` | integer | Seconds left of the 15 minute window, never negative |
| `error` | string or null | Readable reason for `error` or `expired` |

When there is no flow the object is just `{"state": "none"}`.

### POST /api/agent/chatgpt/login

Requests a new device code and starts waiting for approval.

- Auth required: yes
- Request: none

Response `200`:

```json
{
  "state": "pending",
  "user_code": "ABCD-12EF",
  "verification_url": "https://auth.openai.com/codex/device",
  "expires_in": 900,
  "error": null
}
```

Errors:

| Status | `detail` | When |
| --- | --- | --- |
| 502 | `Could not start ChatGPT sign-in. Try again in a minute.` | The device code request to OpenAI failed |

Side effects:

- Outbound request to OpenAI's device-code endpoint (through LiteLLM).
- Replaces any previous flow in memory.
- Starts a daemon thread (`chatgpt-login`) that polls OpenAI until the code is approved, then exchanges
  it for tokens and writes them encrypted to `agent_secret`. On success the flow state becomes
  `complete`; on failure `error` or `expired`.

### GET /api/agent/chatgpt/login

Polls the current flow. A `pending` flow past its 15 minute window is reported as `expired` with
`error` `The code expired before it was approved.`

Response `200` after approval:

```json
{
  "state": "complete",
  "user_code": null,
  "verification_url": "https://auth.openai.com/codex/device",
  "expires_in": 612,
  "error": null
}
```

Errors: 401 only. Side effects: polling changes a stale pending flow to `expired` in memory.

### DELETE /api/agent/chatgpt/login

Forgets the current flow.

Response `200`:

```json
{"state": "none"}
```

Side effects: clears the in-memory flow. The polling thread is not stopped (see notes).

### POST /api/agent/chatgpt/logout

Signs the agent out of ChatGPT.

Response `200`:

```json
{"signed_in": false}
```

Side effects: clears the in-memory flow and deletes the `agent_secret` row `chatgpt_oauth`. No call to
OpenAI. Existing polling threads and model runs are not stopped. An old flow that later completes
can write tokens again after logout.

### POST /api/agent/test

Runs one short completion (`Reply with exactly: OK`) with the configured model, effort `low`, no tools
and no history, to check that the model works.

- Auth required: yes
- Request: none

Response `200`:

```json
{"ok": true, "model": "gpt-5.5", "reply": "OK", "latency_ms": 1840}
```

`reply` is truncated to 200 characters.

Errors:

| Status | `detail` | When |
| --- | --- | --- |
| 409 | `Sign in with ChatGPT first.` | No stored ChatGPT tokens |
| 502 | provider message | The model call failed. Known causes are rewritten to: `ChatGPT sign-in is missing or expired. Open Agent settings and sign in again.`, `ChatGPT usage limit reached for this plan. Try again later or pick another model.`, `This model is not available on your ChatGPT plan. Pick another model in Agent settings.`; anything else is the provider text truncated to 400 characters |

Side effects: one outbound model request to the ChatGPT backend (`https://chatgpt.com/backend-api/codex/responses`
through LiteLLM). LiteLLM may refresh and re-store the tokens. Nothing is saved in the conversation
tables.

---

## Conversations

Conversation object:

| Field | Type | Notes |
| --- | --- | --- |
| `id` | string | 32 hex characters (`uuid4().hex`) |
| `title` | string | First 60 characters of the first message (whitespace collapsed), or the renamed title |
| `model` | string or null | Model used for the latest turn |
| `created_at` | string | IST ISO 8601 |
| `updated_at` | string | IST ISO 8601 |

### GET /api/agent/conversations

Up to 200 conversations, most recently updated first.

Response `200`:

```json
[
  {
    "id": "3f9a1c0e8b7d4e2fa1b2c3d4e5f60718",
    "title": "Quote SBIN",
    "model": "gpt-5.5",
    "created_at": "2026-10-14T10:20:11+05:30",
    "updated_at": "2026-10-14T10:20:19+05:30"
  }
]
```

Errors: 401 only. Side effects: none.

### GET /api/agent/conversations/{conversation_id}

The conversation with its UI transcript (`agent_message` rows in insertion order).

Response `200`:

```json
{
  "id": "3f9a1c0e8b7d4e2fa1b2c3d4e5f60718",
  "title": "Quote SBIN",
  "model": "gpt-5.5",
  "created_at": "2026-10-14T10:20:11+05:30",
  "updated_at": "2026-10-14T10:20:19+05:30",
  "messages": [
    {
      "id": 41,
      "role": "user",
      "content": "Quote SBIN",
      "tools": [],
      "usage": null,
      "pending": null,
      "created_at": "2026-10-14T10:20:11+05:30"
    },
    {
      "id": 42,
      "role": "assistant",
      "content": "SBIN is at 950.50.",
      "tools": [
        {
          "id": "call_1",
          "name": "get_quote",
          "args": {"symbol": "SBIN", "exchange": "NSE"},
          "ok": true,
          "result": "{\"symbol\": \"SBIN\", \"exchange\": \"NSE\", \"ltp\": 950.5}",
          "duration": 0.25
        }
      ],
      "usage": {
        "input_tokens": 1830,
        "output_tokens": 42,
        "total_tokens": 1872,
        "reasoning_tokens": 0,
        "duration": 3.1,
        "ttft_ms": 2400,
        "model": "gpt-5.5"
      },
      "pending": null,
      "created_at": "2026-10-14T10:20:19+05:30"
    }
  ]
}
```

| Message field | Type | Notes |
| --- | --- | --- |
| `id` | integer | |
| `role` | string | `user` or `assistant` |
| `content` | string | Full text |
| `tools` | array | Tool calls of an assistant turn, same shape as the `tool_end` frame |
| `usage` | object or null | Same shape as the `usage` frame |
| `pending` | object or null | Set when the turn paused for order approval: `{"run_id", "requirements", "state"}` where `state` is `pending`, `decided` (then also `decisions`) or `expired` |
| `created_at` | string | IST ISO 8601 |

Errors:

| Status | `detail` | When |
| --- | --- | --- |
| 404 | `Conversation not found` | Unknown id |

### PATCH /api/agent/conversations/{conversation_id}

Renames a conversation.

Request body (`RenameBody`):

| Field | Type | Required | Constraints |
| --- | --- | --- | --- |
| `title` | string | yes | length 1 to 120, trimmed before saving |

Response `200`: the updated conversation object.

Example request: `{"title": "SBIN watchlist"}`. The response has the same fields as the conversation
object above, with `title` changed to `SBIN watchlist` and both timestamps unchanged.

Errors: `404 Conversation not found`, `422` validation list.
Side effects: updates `agent_conversation.title`. `updated_at` is not changed.

### DELETE /api/agent/conversations/{conversation_id}

Deletes a conversation and its transcript.

Response `200` (also for an unknown id):

```json
{"status": "ok"}
```

Side effects: deletes the `agent_message` rows and the `agent_conversation` row, and deletes the Agno
session with the same id from `backend/data/agent.db` (failures there are only logged).
`agent_order_audit` rows are kept.
Deletion does not cancel a running turn; that turn can subsequently write an assistant message for
the deleted conversation id.

---

## Chat (Server-Sent Events)

### Stream format

`POST /api/agent/chat` and `POST /api/agent/chat/confirm` answer `200` with
`Content-Type: text/event-stream`, `Cache-Control: no-cache` and `X-Accel-Buffering: no`. Each frame is
one SSE `data:` line holding a JSON object with a `type` field, followed by a blank line. There are no
`event:` or `id:` lines.

```
data: {"type": "start", "conversation_id": "3f9a1c0e8b7d4e2fa1b2c3d4e5f60718", "model": "gpt-5.5"}

data: {"type": "run", "run_id": "a1b2c3"}

```

Because these are POST requests, a browser `EventSource` cannot be used; the frontend reads the
response body as a stream and splits on blank lines.

Frame types:

| `type` | Fields | When |
| --- | --- | --- |
| `start` | `conversation_id` (string), `model` (string) | First frame of `/chat` only |
| `run` | `run_id` (string or null) | Run started or continued. First frame of `/chat/confirm` |
| `token` | `delta` (string) | A chunk of answer text. If the model streamed no text, the final content is sent as one `token` at completion |
| `reasoning` | `delta` (string) | A chunk of reasoning text, when the model exposes it |
| `tool_start` | `id`, `name`, `args` (object) | A tool call began |
| `tool_end` | `id`, `name`, `args`, `ok` (boolean), `result` (string), `duration` (number or null, seconds) | A tool call finished. `ok` is false on a tool error or when the result starts with `Error`, `Refused`, `Order not placed` or `Not `. `result` is truncated to 4000 characters plus ` ...` |
| `confirm` | `run_id`, `requirements` (array), `state` (`pending`) | The run paused for order approval. Each requirement: `id` (tool call id), `tool_name`, `args`, `summary` (one human-readable line) |
| `usage` | `input_tokens`, `output_tokens`, `total_tokens`, `reasoning_tokens`, `duration` (seconds), `ttft_ms` (time to first token, ms, or null), `model` | Run completed with metrics |
| `error` | `message` (string) | The run failed; same message rewriting as `/test` |
| `done` | `reason`: `stop`, `paused`, `error` or `cancelled` | Terminal frame when the generator completes normally; not guaranteed after disconnect or task cancellation |

Example `confirm` frame:

```json
{
  "type": "confirm",
  "run_id": "a1b2c3",
  "requirements": [
    {
      "id": "call-1",
      "tool_name": "place_order",
      "args": {"symbol": "SBIN", "exchange": "NSE", "side": "BUY", "quantity": 1, "product": "MIS", "price_type": "MARKET"},
      "summary": "BUY 1 SBIN on NSE at market (sent as a protected limit), MIS, MARKET"
    }
  ],
  "state": "pending"
}
```

Handled failures in the event loop arrive as an `error` frame followed by `done`, with HTTP status
200. The loop checks for disconnect on each incoming event and requests cancellation when a run id
is known. An `asyncio.CancelledError` is re-raised after cleanup, so that path does not emit `done`.
Persistence failures during cleanup can also terminate the stream without its terminal frame.
No heartbeat, SSE event id or automatic replay protocol is implemented.

At the end of every stream the assistant turn is saved as an `agent_message` row (content, tool calls,
usage, pending approval) when it has any content, tool call or pending approval, and the conversation's
`updated_at` is bumped.
Reasoning deltas are not stored in this UI transcript. A tool interrupted before completion can be
saved with `ok`, `result` and `duration` still null. A turn containing only an error or usage metrics
is not saved as an assistant message.

### POST /api/agent/chat

Sends a user message and streams the reply.

- Auth required: yes

Request body (`ChatBody`):

| Field | Type | Required | Constraints | Notes |
| --- | --- | --- | --- | --- |
| `message` | string | yes | length 1 to 16 000 before trimming | Trimmed before use; an all-whitespace input passes validation and becomes an empty saved message |
| `conversation_id` | string or null | no | | Continue this conversation. Null, or an id that does not exist, starts a new conversation with a new id |

Example:

```json
{"message": "What is SBIN trading at?", "conversation_id": null}
```

Example stream:

```
data: {"type": "start", "conversation_id": "3f9a1c0e8b7d4e2fa1b2c3d4e5f60718", "model": "gpt-5.5"}

data: {"type": "run", "run_id": "r1"}

data: {"type": "tool_start", "id": "c1", "name": "get_quote", "args": {"symbol": "SBIN", "exchange": "NSE"}}

data: {"type": "tool_end", "id": "c1", "name": "get_quote", "args": {"symbol": "SBIN", "exchange": "NSE"}, "ok": true, "result": "{\"ltp\": 950.5}", "duration": 0.25}

data: {"type": "token", "delta": "SBIN is at "}

data: {"type": "token", "delta": "950.50."}

data: {"type": "usage", "input_tokens": 10, "output_tokens": 5, "total_tokens": 15, "reasoning_tokens": 0, "duration": 1.5, "ttft_ms": 830, "model": "gpt-5.5"}

data: {"type": "done", "reason": "stop"}

```

Errors before the stream starts:

| Status | `detail` | When |
| --- | --- | --- |
| 401 | `Not authenticated` | No SmartTrader session |
| 401 | `Session expired, please log in again` | Stored broker token missing or undecryptable (the session is not deactivated here) |
| 409 | `Sign in with ChatGPT in Agent settings first.` | No ChatGPT tokens |
| 422 | validation list | Empty or too long `message` |

Side effects:

- Creates the `agent_conversation` row when needed; sets its `model` and `updated_at`.
- Inserts the user `agent_message` row.
- Marks every still-`pending` approval in this conversation as `expired` (a new question abandons it).
- Builds the agent: system prompt (read-only or trading rules), extra instructions, IST date, the last 8
  runs of Agno history from `backend/data/agent.db`, at most 20 tool calls per turn. The 10 read tools
  are always present; the 3 order tools are added only when `trading_enabled` is true.
- Model requests go to the ChatGPT backend through LiteLLM. Tool calls go to TradeSmart with the user's
  session (see [broker-integration.md](broker-integration.md#calls-made-by-the-ai-agent-tools)).
- Agno writes its own session and run records to `backend/data/agent.db`.
- On a pause for approval: one `agent_order_audit` row with status `proposed` per requirement, and the
  saved assistant message carries `pending` with state `pending`.
- The run is registered in an in-memory map so `/chat/{run_id}/cancel` can stop it.

### POST /api/agent/chat/confirm

Resumes a run that paused for order approval, with the user's decision on each proposed order, and
streams the rest of the run in the same frame format (first frame `run`, no `start`).

- Auth required: yes

Request body (`ConfirmBody`):

| Field | Type | Required | Constraints | Notes |
| --- | --- | --- | --- | --- |
| `conversation_id` | string | yes | length 1 to 64 | |
| `run_id` | string | yes | length 1 to 64 | From the `confirm` frame |
| `decisions` | object (string to boolean) | yes | | Tool call id to approve (true) or reject (false). A requirement missing from the map counts as rejected |

Example:

```json
{
  "conversation_id": "3f9a1c0e8b7d4e2fa1b2c3d4e5f60718",
  "run_id": "a1b2c3",
  "decisions": {"call-1": true}
}
```

Example stream after approval:

```
data: {"type": "run", "run_id": "a1b2c3"}

data: {"type": "tool_end", "id": "call-1", "name": "place_order", "args": {"symbol": "SBIN", "exchange": "NSE", "side": "BUY", "quantity": 1, "product": "MIS", "price_type": "MARKET"}, "ok": true, "result": "{\"status\": \"placed\", \"order_id\": \"26101400012351\", \"sent_price\": 955.35, \"estimated_value\": 955.35, \"note\": \"Check get_order_book for the fill. Sent as a limit at 955.35 (market price protection on LTP 950.55).\"}", "duration": 0.9}

data: {"type": "token", "delta": "Order 26101400012351 placed."}

data: {"type": "done", "reason": "stop"}

```

Errors before the stream starts:

| Status | `detail` | When |
| --- | --- | --- |
| 401 | as for `/chat` | |
| 409 | `Sign in with ChatGPT in Agent settings first.` | No ChatGPT tokens |
| 409 | `This approval is no longer pending.` | The latest message with a pending approval in this conversation has another `run_id`, or its state is `decided` or `expired` (each approval can be used once) |
| 409 | `The paused run was not found. Ask again to propose the order.` | Agno has no paused run with this id |
| 422 | validation list | |

Side effects:

- Sets the stored `pending` state to `decided` with the `decisions` map (committed before the run is
  resumed).
- For each rejected requirement: an `agent_order_audit` row with status `rejected`, and the tool gets a
  rejection note telling the model not to retry.
- For each approved requirement the tool body first writes `approved`, then re-reads risk settings.
  A failed check adds `refused`; broker acceptance or failure adds `placed` or `failed`. Only the
  checks implemented for that particular tool apply. See [Agent order tools](#agent-order-tools).
- The agent is built with the order tools present even if trading was switched off in the meantime; the
  tool body then refuses with `trading is switched off in Agent settings`.

### POST /api/agent/chat/{run_id}/cancel

Stops a running turn.

- Auth required: yes
- Path parameter: `run_id` (string)
- Request: none

Response `200`:

```json
{"cancelled": true}
```

`cancelled` is false when no run with that id is registered in this process, including a run already
paused for approval. True means a cancellation request was sent to Agno, not that cancellation or
broker rollback has completed. A `RunCancelled` event results in `done` reason `cancelled` when the
stream completes normally. This endpoint does not reject or expire a pending approval.

Side effects: asks Agno to cancel the run.

---

## Risk settings and audit

### GET /api/agent/risk

- Auth required: yes

Response `200` (defaults shown):

```json
{
  "trading_enabled": false,
  "max_order_value": 50000.0,
  "max_quantity": 500,
  "max_orders_per_day": 20,
  "allowed_exchanges": ["NSE", "BSE"],
  "exchanges": ["NSE", "BSE", "NFO", "BFO", "CDS", "MCX"]
}
```

| Field | Type | Notes |
| --- | --- | --- |
| `trading_enabled` | boolean | When false a new chat has no order tools; confirmation resumes include them but their bodies refuse execution |
| `max_order_value` | number | INR, checked against estimated value |
| `max_quantity` | integer | Per order |
| `max_orders_per_day` | integer | Counts `place_order` audit rows with status `placed` since IST midnight; modify/cancel rows do not count |
| `allowed_exchanges` | array of string | Exchanges the agent may trade |
| `exchanges` | array of string | All exchanges that may be allowed |

Side effects: creates the `agent_risk` row with defaults on first use.

### PUT /api/agent/risk

- Auth required: yes

Request body (`RiskBody`), all fields required:

| Field | Type | Constraints |
| --- | --- | --- |
| `trading_enabled` | boolean | |
| `max_order_value` | number | above 0, at most 10 000 000 |
| `max_quantity` | integer | 1 to 1 000 000 |
| `max_orders_per_day` | integer | 1 to 1000 |
| `allowed_exchanges` | array of string | each one of `NSE`, `BSE`, `NFO`, `BFO`, `CDS`, `MCX` (case sensitive); may be empty |

Example:

```json
{
  "trading_enabled": true,
  "max_order_value": 25000,
  "max_quantity": 100,
  "max_orders_per_day": 10,
  "allowed_exchanges": ["NSE"]
}
```

Response `200`: the saved settings in the GET shape. `allowed_exchanges` is de-duplicated and returned in
the canonical order.

Errors:

| Status | `detail` | When |
| --- | --- | --- |
| 422 | `Unknown exchange: <list>` | An exchange outside the six above (for example `NSE_INDEX`) |
| 422 | validation list | Missing field or a limit out of range |

Side effects: updates `agent_risk` (id 1). Takes effect for the next turn's tool list and immediately for
the risk re-check inside order tools.

### GET /api/agent/orders/audit

Last 100 agent order audit rows, newest first.

Response `200`:

```json
[
  {
    "id": 18,
    "created_at": "2026-10-14T10:31:05+05:30",
    "conversation_id": "3f9a1c0e8b7d4e2fa1b2c3d4e5f60718",
    "tool": "place_order",
    "args": {"symbol": "SBIN", "exchange": "NSE", "side": "BUY", "quantity": 1, "product": "MIS", "price_type": "MARKET", "price": 0.0, "trigger_price": 0.0},
    "status": "placed",
    "order_id": "26101400012351",
    "message": "sent as MARKET 955.35"
  }
]
```

| `status` | Written when |
| --- | --- |
| `proposed` | The run paused with this order (args as the model sent them) |
| `rejected` | The user rejected it in `/chat/confirm` |
| `approved` | The tool body started after approval (args normalized) |
| `refused` | The risk re-check refused it; `message` holds the reason |
| `failed` | TradeSmart rejected it or could not be reached; `message` holds the reason |
| `placed` | TradeSmart accepted it; `order_id` is set (for modify and cancel too) |

The `run_id` column is stored but not returned. Side effects: none.

---

## Agent order tools

Not HTTP endpoints, but they explain what `/chat/confirm` does after approval. All three are Agno tools
with `requires_confirmation=True`.

`place_order(symbol, exchange, side, quantity, product="MIS", price_type="MARKET", price=0, trigger_price=0)`
checks, in order, and refuses on the first failure:

1. trading is switched on;
2. side is `BUY` or `SELL`; product is `MIS`, `CNC` or `NRML`; price type is `MARKET`, `LIMIT`, `SL` or `SL-M`;
3. exchange is in `allowed_exchanges`;
4. quantity is 1 to `max_quantity`;
5. `LIMIT` and `SL` have a price above 0; `SL` and `SL-M` have a trigger above 0;
6. fewer than `max_orders_per_day` agent orders placed since IST midnight;
7. no identical placed order (same symbol, exchange, side, quantity) in the last 60 seconds;
8. the symbol is in the master contract;
9. `GetQuotes` completes without an exception; MARKET additionally needs LTP above 0;
10. estimated value `(sent price or LTP) x quantity` is at most `max_order_value`.

Then it sends `PlaceOrder` with the same market price protection as the chart (see
[orders.md](orders.md#market-price-protection)). Unlike the chart path, it does not check the lot size
multiple.

`modify_order(order_id, quantity, price_type, price, trigger_price=0)`: trading on, price type `LIMIT` or
`SL`, quantity 1 to `max_quantity`, price above 0, `price x quantity` within `max_order_value`, order present
in today's order book; then `ModifyOrder`. It does not check open status, allowed exchanges, lot-size
multiples, tick alignment or a positive SL trigger. Daily and duplicate placement limits are not
checked for modifications.

`cancel_order(order_id)`: trading on; then `CancelOrder`. It does not fetch the order book or apply
exchange, quantity, value, daily or duplicate placement limits.

For placement, a quote body with `stat: "Not_Ok"` is not explicitly rejected in this tool path.
Without `lp`, MARKET is refused, but LIMIT, SL and SL-M can continue using the requested or protected
trigger price. The chart placement service explicitly rejects such a quote response.

### Read tool contracts

These are model tools, not public HTTP endpoints. Results are JSON strings or readable text, carried
in `tool_end.result` (subject to the 4000-character preview limit). Broker numeric fields are not
uniformly normalized across the tools.

| Tool and arguments | Result and bounds |
| --- | --- |
| `search_symbols(query, exchange="")` | Up to 15 rows ordered by symbol. First 4 whitespace-separated terms must each match symbol, name or broker symbol. `%` and `_` remain SQL LIKE wildcards, unlike HTTP search. Rows contain `symbol`, `exchange`, `name`, `type`, `expiry`, `strike`, `lot_size`; blank expiry and nonpositive strike become null. |
| `get_quote(symbol, exchange)` | Symbol/name, LTP, OHLC, previous close, change and percent change, volume, average price, open interest, circuits, 52-week range, lot/tick size and feed time. Lot/tick size and feed time retain broker values. Missing previous close makes change fields null. |
| `get_history(symbol, exchange, interval="D", bars=30)` | `bars` is clamped to 1 to 200. Daily fetch window is `max(bars * 2, 60)` days; intraday uses chart defaults. Returns summary plus compact candle arrays with `columns = ["time_epoch", "open", "high", "low", "close", "volume"]`. No candles returns a readable text result. |
| `get_technicals(symbol, exchange, interval="D")` | Fetches 400 days for daily, chart defaults otherwise. Requires at least 15 candles. Returns `bars_used`, `last_close`, `rsi14`, `sma20`, `sma50`, `sma200`, `ema20`, `atr14`, `high_20`, `low_20`. Indicators without enough history are null; the recent range uses up to 20 candles. |
| `get_funds()` | Same computed payload as `GET /api/funds`. |
| `get_positions()` | First 50 dictionary rows, including closed positions, with selected raw broker fields such as `netqty`, `rpnl`, `urmtom`. Empty means `No positions today.` |
| `get_holdings()` | Processes the first 50 broker rows. Uses the first `exch_tsym` listing, quantity `holdqty + btstqty`, uploaded average price and used quantity. Empty means `No holdings.` |
| `get_order_book()` | First 50 dictionary rows with order ids, symbols, side, price type, quantity/prices, status, fills, rejection reason and exchange time. Values retain broker types. Empty means `No orders today.` |
| `get_trade_book()` | First 50 dictionary rows with order id, symbol, exchange, side, product, fill quantity, price and time. Empty means `No trades today.` |
| `get_master_contract_status()` | Status, message, total count and exchange counts, without timestamps. A missing row returns only `{"status":"idle"}`. |

The shared wrapper turns broker expiry, broker errors and other exceptions into text for the model;
it does not deactivate the SmartTrader session. Neither list truncation nor tool preview truncation
provides pagination. The first 50 rows are not necessarily the newest; no local ordering is applied.

---

## Notes and possible issues

- `POST /chat/confirm` marks the approval `decided` before checking that Agno still has the paused run. If
  the run is missing or no longer paused, the request fails with 409 and the approval cannot be
  retried. Restart alone does not prove the run is lost: the lookup uses persisted Agno history.
- `DELETE /chatgpt/login`, logout and starting a new flow only drop or replace the in-memory reference.
  The old polling thread keeps running and can store its tokens even after cancellation, logout or a
  newer login. The identity check in `_run_flow` protects only the displayed flow state, not token writes.
- `PATCH /conversations/{id}` validates the length before trimming, so a title of only spaces is saved as an
  empty string.
- `DELETE /conversations/{id}` returns 200 for unknown ids.
- When a tool sees an expired TradeSmart session it returns an error text to the model; the SmartTrader
  session is not deactivated (unlike the funds, chart and orders routers).
- The run registry used by `/chat/{run_id}/cancel` is per process; with more than one worker a cancel can
  land on a process that does not hold the run.
- Placement count and duplicate checks read completed `placed` audit rows before submitting to the
  broker, without reserving a slot. Concurrent approvals can both pass. A timeout is logged as
  `failed` even if the broker accepted the order; it therefore does not block a retry as a duplicate.
  There is no durable idempotency key or unknown-outcome reconciliation process.
