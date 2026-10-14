# Agent tables (smarttrader.db)

Six tables that support the AI agent. They live in `smarttrader.db` next to
the core tables and are created by the same `create_all` in
`backend/app/db.py:init_db`. The model's own memory is separate, in
`agent.db` (see [agent-db.md](agent-db.md)). Models are in
`backend/app/models.py`. Conventions (timestamps, booleans, ORM-side defaults,
no foreign keys) are in [README.md](README.md#conventions-that-apply-to-every-smarttrader-table).

All six tables are global to the installation. Conversations, messages and
audits have no account ownership field; signing in to another TradeSmart
account does not partition them. For tables without a Default column, no
ORM default is declared. Nullable columns may be NULL, required values are
supplied by the listed writer, and integer primary keys use rowid generation.
Only indexes shown in the DDL exist; `agent_setting` and `agent_risk` have
their primary key only. Insert/upsert operations below write all fields
unless a subset is explicitly named.

---

## agent_setting

Which ChatGPT model the agent uses and any extra instructions. Single row,
`id = 1`. Model: `models.py:AgentSetting`.

```sql
CREATE TABLE agent_setting (
	id INTEGER NOT NULL,
	model VARCHAR NOT NULL,
	reasoning_effort VARCHAR,
	instructions TEXT,
	updated_at DATETIME,
	PRIMARY KEY (id)
)
```

| Column | Type | Null | Default | Key | Meaning |
| --- | --- | --- | --- | --- | --- |
| `id` | INTEGER | NO | none (always 1) | PK | Singleton id. |
| `model` | VARCHAR | NO | first use: `gpt-5.5` (`builder.DEFAULT_MODEL`) if listed; otherwise the first listed model, or `gpt-5.5` if the list is empty | | Model name without the `chatgpt/` provider prefix. Must be one of `builder.available_models()` (LiteLLM `model_cost` keys starting `chatgpt/`) when saved; max 80 characters. |
| `reasoning_effort` | VARCHAR | yes | first use: `medium` | | `low`, `medium`, `high`, or `NULL` (model default). Passed as `request_params.reasoning_effort` only when it is one of the three values. |
| `instructions` | TEXT | yes | first use: `NULL` | | Extra user instructions appended to the system prompt. Trimmed; empty becomes `NULL`; max 4,000 characters. Plaintext. |
| `updated_at` | DATETIME | yes | | | Naive UTC time of the last save. |

Code that touches it:

| Operation | File:function | Detail |
| --- | --- | --- |
| Insert (lazy) | `routers/agent.py:_settings` | Creates the row with defaults on first read. With `commit=False` it only flushes, for use inside a caller's transaction (`chat`, `_agent_for`). |
| Read | `routers/agent.py:status`, `get_settings`, `test_model`, `chat`, `_agent_for` | Model and effort for building the agent; model name also copied onto `agent_conversation.model`. |
| Update | `routers/agent.py:put_settings` (`PUT /api/agent/settings`) | Validates model and effort (422 otherwise), stores trimmed instructions, sets `updated_at`. |

Lifecycle: created on first agent use, updated from the settings page, never
deleted.

---

## agent_secret

Encrypted credentials for the agent's model provider. Today one row:
`name = 'chatgpt_oauth'`, the ChatGPT subscription OAuth tokens. Model:
`models.py:AgentSecret`.

```sql
CREATE TABLE agent_secret (
	name VARCHAR NOT NULL,
	value_enc TEXT NOT NULL,
	updated_at DATETIME,
	PRIMARY KEY (name)
)
-- implicit index: sqlite_autoindex_agent_secret_1 on (name)
```

| Column | Type | Null | Key | Meaning |
| --- | --- | --- | --- | --- |
| `name` | VARCHAR | NO | PK | Secret name. Only `chatgpt_oauth` (`chatgpt_auth.SECRET_NAME`) is used. |
| `value_enc` | TEXT | NO | | **Fernet encrypted** JSON. For `chatgpt_oauth` it is the LiteLLM ChatGPT auth record: `access_token`, `refresh_token`, `id_token`, `expires_at`, `account_id` (and any other keys LiteLLM's authenticator chooses to persist). |
| `updated_at` | DATETIME | yes | | Naive UTC time of the last write (sign-in or token refresh). |

How it is wired: LiteLLM's `chatgpt/` provider normally keeps these tokens in
a plain JSON file. `agent/chatgpt_auth.py:install` replaces the
authenticator's `_read_auth_file` and `_write_auth_file` with
`read_tokens` and `write_tokens`, so every read and every refresh goes through
this table, encrypted. Its token directory function is made a no-op.

Code that touches it:

| Operation | File:function | Detail |
| --- | --- | --- |
| Upsert | `agent/chatgpt_auth.py:write_tokens` | Called by `_run_flow` after the device-code sign-in succeeds, and by LiteLLM whenever it refreshes tokens. Encrypts `json.dumps(data)`. |
| Read | `agent/chatgpt_auth.py:read_tokens` | Decrypts and parses; returns `None` if missing, undecryptable or not a JSON object. Used by LiteLLM and by `account_status`. |
| Read (derived) | `agent/chatgpt_auth.py:account_status` | Signed in when a `refresh_token` exists. Decodes the `id_token` claims for `email` and the plan (`https://api.openai.com/auth` claim, `chatgpt_plan_type`). Tokens are never returned to the API. |
| Delete | `agent/chatgpt_auth.py:clear_tokens` | `POST /api/agent/chatgpt/logout`. |

Lifecycle: created at first ChatGPT sign-in, rewritten on every token refresh
or new sign-in, deleted on ChatGPT sign-out. `cancel_login` clears only the
in-memory flow reference. An existing `_run_flow` worker writes tokens before
checking whether its flow is still current, so a cancelled or superseded flow
can recreate or overwrite this row after logout or a newer sign-in.

---

## agent_conversation

One row per chat thread shown in the agent sidebar. Model:
`models.py:AgentConversation`.

```sql
CREATE TABLE agent_conversation (
	id VARCHAR NOT NULL,
	title TEXT NOT NULL,
	model VARCHAR,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id)
)
-- implicit index: sqlite_autoindex_agent_conversation_1 on (id)
```

| Column | Type | Null | Key | Meaning |
| --- | --- | --- | --- | --- |
| `id` | VARCHAR | NO | PK | `uuid.uuid4().hex` (32 lowercase hex characters). Also used as the Agno `session_id` in `agent.db`, which is how the visible transcript and the model memory are paired. |
| `title` | TEXT | NO | | First user message with whitespace collapsed, cut to 60 characters (`New chat` if empty). Rename input is validated at 1 to 120 characters before trimming; whitespace-only input therefore stores an empty title. |
| `model` | VARCHAR | yes | | Model in `agent_setting` at the time of the latest user message. |
| `created_at` | DATETIME | NO | | Naive UTC creation time. |
| `updated_at` | DATETIME | NO | | Naive UTC time of the latest user or assistant message. Sort key of the conversation list. |

There is no index on `updated_at`; the list query sorts the whole table
(acceptable at current volumes).

Code that touches it:

| Operation | File:function | Detail |
| --- | --- | --- |
| Insert | `routers/agent.py:chat` (`POST /api/agent/chat`) | When `conversation_id` is null **or does not exist**, a new row with a fresh id is created (the client's unknown id is not reused). |
| Update | `routers/agent.py:chat` | `updated_at`, `model` on every user message. |
| Update | `routers/agent.py:_stream_run` | `updated_at` when the assistant turn is saved. |
| Update | `routers/agent.py:rename_conversation` (`PATCH`) | `title`. |
| Read | `routers/agent.py:list_conversations` | `ORDER BY updated_at DESC LIMIT 200`. |
| Read | `routers/agent.py:get_conversation` | One row plus its messages. |
| Delete | `routers/agent.py:delete_conversation` (`DELETE`) | Deletes its `agent_message` rows, then the conversation, commits, then calls `agent_db().delete_session(id)` to remove the Agno session and runs. `agent_order_audit` rows are kept. |

---

## agent_message

The transcript as the UI shows it: user turns, assistant text, tool calls with
result previews, token usage, and order approval state. The model's context
is not read from here; it comes from Agno's `agent.db`. Model:
`models.py:AgentMessage`.

```sql
CREATE TABLE agent_message (
	id INTEGER NOT NULL,
	conversation_id VARCHAR NOT NULL,
	role VARCHAR NOT NULL,
	content TEXT NOT NULL,
	tools_json TEXT,
	usage_json TEXT,
	pending_json TEXT,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id)
)
CREATE INDEX ix_agent_message_conversation_id ON agent_message (conversation_id)
```

This is fresh-table column order. On older databases `pending_json` is last
because `db.py:_add_missing_columns` adds it with `ALTER TABLE`; existing rows
then receive NULL in that column.

| Column | Type | Null | Default | Key | Meaning |
| --- | --- | --- | --- | --- | --- |
| `id` | INTEGER | NO | rowid | PK | Insertion order; the transcript is read `ORDER BY id`. |
| `conversation_id` | VARCHAR | NO | | idx | `agent_conversation.id`. Logical reference only, no FK. |
| `role` | VARCHAR | NO | | | `user` or `assistant`. |
| `content` | TEXT | NO | `''` | | User text (validated at 1 to 16,000 characters before trimming, so whitespace-only input stores empty text) or concatenated streamed assistant text (may be empty for tool-only or paused turns). Markdown. |
| `tools_json` | TEXT | yes | | | Assistant rows: JSON array of tool calls, see below. `NULL` when no tool ran. |
| `usage_json` | TEXT | yes | | | Assistant rows: JSON object with run metrics, see below. `NULL` unless `RunCompleted` supplied metrics. |
| `pending_json` | TEXT | yes | | | Assistant rows of a run paused for order approval: JSON object, see below. |
| `created_at` | DATETIME | NO | | | Naive UTC. |

`tools_json` element shape (built in `routers/agent.py:_stream_run`):

```json
{"id": "<tool_call_id>", "name": "get_quote", "args": {"symbol": "SBIN", "exchange": "NSE"},
 "ok": true, "result": "<tool result, cut to 4,000 chars plus ' ...'>", "duration": 0.42}
```

`ok` is false when the tool errored or its text starts with `Error`,
`Refused`, `Order not placed` or `Not `. A started tool with no completion
event keeps `ok`, `result` and `duration` as NULL. Reasoning deltas and error
frames are sent over SSE but are not saved in this transcript.

`usage_json` shape:

```json
{"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "reasoning_tokens": 0,
 "duration": 1.23, "ttft_ms": 850, "model": "gpt-5.5"}
```

`pending_json` shape and states:

```json
{"run_id": "<agno run id>",
 "requirements": [{"id": "<tool_call_id>", "tool_name": "place_order",
                   "args": {"symbol": "SBIN", "exchange": "NSE", "side": "BUY", "quantity": 1},
                   "summary": "BUY 1 SBIN on NSE at market (sent as a protected limit), MIS, MARKET"}],
 "state": "decided",
 "decisions": {"<tool_call_id>": true}}
```

* `pending`: written when the run pauses (`RunPaused` event).
* `expired`: a new user message in the same conversation abandons any
  approval still pending (`routers/agent.py:chat`).
* `decided`: set by `routers/agent.py:confirm` before loading or resuming the
  Agno run; `decisions` is added only in this state (the example above).
  Missing decision ids default to false; extra ids are ignored. Failure to
  load the paused run returns 409 but does not restore `pending`.

Code that touches it:

| Operation | File:function | Detail |
| --- | --- | --- |
| Insert (user) | `routers/agent.py:chat` | In the same transaction that creates or touches the conversation. |
| Insert (assistant) | `routers/agent.py:_stream_run` (in `finally`) | Only when there is text, at least one tool call, or a pause. Saved even when the stream ends in error or is cancelled. |
| Update | `routers/agent.py:chat` | Every row of the conversation with `pending_json.state = pending` becomes `expired`. |
| Update | `routers/agent.py:confirm` | Latest row with `pending_json` must be `pending` for the given `run_id` (else 409); becomes `decided` with the decisions. |
| Read | `routers/agent.py:get_conversation` | `WHERE conversation_id = ? ORDER BY id`, parses the JSON columns. |
| Delete | `routers/agent.py:delete_conversation` | `DELETE WHERE conversation_id = ?`. |

Lifecycle: append-only during a chat except for the `pending_json` state
changes; removed only with its conversation. Plaintext: tool results can
contain funds, positions, holdings and order book data.

---

## agent_risk

Whether the agent may trade, and the limits checked inside every order tool
after the user approves. Single row, `id = 1`. Model: `models.py:AgentRisk`.

```sql
CREATE TABLE agent_risk (
	id INTEGER NOT NULL,
	trading_enabled BOOLEAN NOT NULL,
	max_order_value FLOAT NOT NULL,
	max_quantity INTEGER NOT NULL,
	max_orders_per_day INTEGER NOT NULL,
	allowed_exchanges TEXT NOT NULL,
	updated_at DATETIME,
	PRIMARY KEY (id)
)
```

| Column | Type | Null | Default | Allowed on save | Meaning |
| --- | --- | --- | --- | --- | --- |
| `id` | INTEGER | NO | none (always 1) | | Singleton id. |
| `trading_enabled` | BOOLEAN | NO | `False` | bool | Master switch. Off omits order tools from new chats. Confirmation rebuilds them even when off so their execution-time check can refuse. Each tool reads the switch once near entry; changing it later does not cancel an in-flight broker call. |
| `max_order_value` | FLOAT | NO | `50000.0` | above 0, at most 10,000,000 | Max estimated value (send price or LTP times quantity) of one order, INR. |
| `max_quantity` | INTEGER | NO | `500` | 1 to 1,000,000 | Max quantity of one order. |
| `max_orders_per_day` | INTEGER | NO | `20` | 1 to 1,000 | Max `place_order` audits with status `placed` since IST midnight. |
| `allowed_exchanges` | TEXT | NO | `'NSE,BSE'` | subset of NSE, BSE, NFO, BFO, CDS, MCX | Comma-separated list, stored in canonical order `NSE,BSE,NFO,BFO,CDS,MCX`. Empty string blocks new placements. Modification and cancellation do not check this list. |
| `updated_at` | DATETIME | yes | | | Naive UTC time of the last save. |

Code that touches it:

| Operation | File:function | Detail |
| --- | --- | --- |
| Insert (lazy) | `agent/trading.py:risk_settings` | Creates the row with the defaults above on first read. |
| Read | `agent/trading.py:risk_settings` | Called by `routers/agent.py:status`, `get_settings`, `_agent_for`, `get_risk`, and at the start of each order tool body (`place_order`, `modify_order`, `cancel_order`). |
| Update | `agent/trading.py:save_risk_settings` via `routers/agent.py:put_risk` (`PUT /api/agent/risk`) | Unknown exchanges are rejected with 422 by the router. |

Lifecycle: created on first read, updated from the settings page, never
deleted.

Enforcement differs by tool: placement checks every configured limit plus a
60-second duplicate window; modification checks the master switch, quantity
and estimated value; cancellation checks only the master switch. Chart order
routes do not use these settings.

---

## agent_order_audit

Append-only log of every order the agent proposed, the user's decision, risk
refusals and the broker's answer. Model: `models.py:AgentOrderAudit`.

```sql
CREATE TABLE agent_order_audit (
	id INTEGER NOT NULL,
	created_at DATETIME NOT NULL,
	conversation_id VARCHAR,
	run_id VARCHAR,
	tool VARCHAR NOT NULL,
	args_json TEXT NOT NULL,
	status VARCHAR NOT NULL,
	order_id VARCHAR,
	message TEXT,
	PRIMARY KEY (id)
)
CREATE INDEX ix_agent_order_audit_conversation_id ON agent_order_audit (conversation_id)
```

| Column | Type | Null | Key | Meaning |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | NO | PK | Insertion order. |
| `created_at` | DATETIME | NO | | Naive UTC. Used for the daily count and the duplicate window. |
| `conversation_id` | VARCHAR | yes | idx | `agent_conversation.id` (logical). Kept after the conversation is deleted. |
| `run_id` | VARCHAR | yes | | Agno run id (`agno_runs.run_id` in `agent.db`, logical). |
| `tool` | VARCHAR | NO | | `place_order`, `modify_order` or `cancel_order`. |
| `args_json` | TEXT | NO | | JSON of the tool arguments. For `proposed` and `rejected` rows these are the raw model arguments; for the other statuses the normalized arguments (uppercased strings, typed numbers), for example `{"symbol": "SBIN", "exchange": "NSE", "side": "BUY", "quantity": 1, "product": "MIS", "price_type": "MARKET", "price": 0.0, "trigger_price": 0.0}`. |
| `status` | VARCHAR | NO | | One of `proposed`, `approved`, `rejected`, `refused`, `placed`, `failed` (see below). Not constrained in the DDL. |
| `order_id` | VARCHAR | yes | | Noren order number, set on `placed` rows. |
| `message` | TEXT | yes | | Refusal reason, broker error text, or for a placed order `sent as <PRICE_TYPE> <price>`. |

Status values and who writes them (all through `agent/trading.py:audit`, one
row per call, each in its own transaction):

| Status | Written by | When |
| --- | --- | --- |
| `proposed` | `routers/agent.py:_stream_run` | The run paused for confirmation; one row per tool call awaiting approval. |
| `rejected` | `routers/agent.py:confirm` | The user declined that tool call. |
| `approved` | `agent/trading.py` tool bodies | First line of the tool body, which only runs after the user approved. |
| `refused` | `agent/trading.py:build_order_tools.refuse` | A risk or validation check failed after approval; nothing was sent. |
| `failed` | `agent/trading.py:build_order_tools.broker_call` | The broker call raised (session expired, broker error, network). |
| `placed` | `agent/trading.py` tool bodies | The broker accepted. Used for a new order, a modification and a cancel request alike; filter on `tool` to tell them apart. |

A typical approved order produces `proposed`, `approved`, `placed` (three
rows); a declined one `proposed`, `rejected`.

Code that reads it:

| File:function | Query |
| --- | --- |
| `agent/trading.py:_placed_today` | `COUNT(*) WHERE status = 'placed' AND tool = 'place_order' AND created_at >= <IST midnight in UTC>`. Enforces `max_orders_per_day`. |
| `agent/trading.py:_recent_duplicate` | `args_json WHERE tool = 'place_order' AND status = 'placed' AND created_at >= now - 60 s`, then compares `symbol`, `exchange`, `side`, `quantity` in Python. |
| `routers/agent.py:order_audit` (`GET /api/agent/orders/audit`) | `ORDER BY id DESC LIMIT 100`. |

There is no index on `created_at` or `status`; the two risk queries scan the
table (cheap at current volumes, grows linearly).

Lifecycle: insert only. Rows are never updated or deleted by the application,
including when the related conversation is deleted.
