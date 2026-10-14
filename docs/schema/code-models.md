# In-code data models

SmartTrader has three kinds of data model in code:

1. SQLAlchemy 2 ORM classes in `backend/app/models.py`, one per table.
2. Pydantic `BaseModel` request bodies declared inside the routers.
3. Dataclasses for values passed between broker, stream and agent code.

`backend/app/models.py` contains only SQLAlchemy models (declarative
`Mapped[...]` / `mapped_column`), not Pydantic models. API responses are plain
`dict`s built in the routers; there are no Pydantic response models.

## 1. SQLAlchemy ORM models (`backend/app/models.py`)

All classes derive from `models.Base(DeclarativeBase)`; `Base.metadata` is
what `db.py:init_db` passes to `create_all`. An optional hint
(`Mapped[str | None]`) gives a nullable column; a non-optional hint gives NOT
NULL; primary-key declarations also provide NOT NULL without an explicit
`nullable=False` argument.

| Class | Table | Database | Primary key | Notes |
| --- | --- | --- | --- | --- |
| `BrokerConfig` | `broker_config` | smarttrader.db | `id` (always 1) | `api_secret_enc` encrypted |
| `AuthSession` | `auth_session` | smarttrader.db | `id` | `session_hash` unique; `access_token_enc` encrypted; `is_active` default True |
| `SymToken` | `symtoken` | smarttrader.db | `id` | `__table_args__` declares the three indexes |
| `MasterContractStatus` | `master_contract_status` | smarttrader.db | `id` (always 1) | `status` default `idle`, `total_records` default 0 |
| `AgentSetting` | `agent_setting` | smarttrader.db | `id` (always 1) | |
| `AgentSecret` | `agent_secret` | smarttrader.db | `name` | `value_enc` encrypted; rows `chatgpt_oauth` and `finedge_api_key` |
| `AgentConversation` | `agent_conversation` | smarttrader.db | `id` (uuid hex) | |
| `AgentMessage` | `agent_message` | smarttrader.db | `id` | `conversation_id` has `index=True`; `content` default `''` |
| `AgentRisk` | `agent_risk` | smarttrader.db | `id` (always 1) | Defaults: off, 50,000, 500, 20, `NSE,BSE` |
| `AgentOrderAudit` | `agent_order_audit` | smarttrader.db | `id` | `conversation_id` has `index=True` |

Field-level mapping (Python type to column):

| Class.field | Python type | Column |
| --- | --- | --- |
| `BrokerConfig.id` | `int` | INTEGER PK |
| `BrokerConfig.api_key`, `api_secret_enc`, `redirect_url` | `str \| None` | TEXT |
| `BrokerConfig.updated_at` | `datetime \| None` | DATETIME |
| `AuthSession.id` | `int` | INTEGER PK (autoincrement flag, plain rowid) |
| `AuthSession.session_hash` | `str` | TEXT NOT NULL UNIQUE |
| `AuthSession.user_id`, `user_name`, `access_token_enc` | `str \| None` | TEXT |
| `AuthSession.created_at` | `datetime \| None` | DATETIME |
| `AuthSession.is_active` | `bool` | BOOLEAN NOT NULL, default True |
| `SymToken.id`, `AgentMessage.id`, `AgentOrderAudit.id` | `int` | INTEGER PK (autoincrement flag, plain rowid) |
| `MasterContractStatus.id`, `AgentSetting.id`, `AgentRisk.id` | `int` | INTEGER PK (code supplies 1) |
| `SymToken.symbol`, `brsymbol` | `str` | VARCHAR NOT NULL |
| `SymToken.name`, `exchange`, `brexchange`, `token`, `expiry`, `instrumenttype` | `str \| None` | VARCHAR |
| `SymToken.strike`, `tick_size` | `float \| None` | FLOAT |
| `SymToken.lotsize` | `int \| None` | INTEGER |
| `MasterContractStatus.status` | `str` | VARCHAR NOT NULL, default `idle` |
| `MasterContractStatus.message`, `exchanges_json` | `str \| None` | TEXT |
| `MasterContractStatus.total_records` | `int` | INTEGER NOT NULL, default 0 |
| `MasterContractStatus.started_at`, `finished_at` | `datetime \| None` | DATETIME |
| `AgentSetting.model` | `str` | VARCHAR NOT NULL |
| `AgentSetting.reasoning_effort` | `str \| None` | VARCHAR |
| `AgentSetting.instructions` | `str \| None` | TEXT |
| `AgentSetting.updated_at`, `AgentSecret.updated_at`, `AgentRisk.updated_at` | `datetime \| None` | DATETIME |
| `AgentSecret.name` | `str` | VARCHAR PK |
| `AgentSecret.value_enc` | `str` | TEXT NOT NULL |
| `AgentConversation.id` | `str` | VARCHAR PK |
| `AgentConversation.title` | `str` | TEXT NOT NULL |
| `AgentConversation.model` | `str \| None` | VARCHAR |
| `AgentConversation.created_at`, `updated_at` | `datetime` | DATETIME NOT NULL |
| `AgentMessage.conversation_id`, `role` | `str` | VARCHAR NOT NULL |
| `AgentMessage.content` | `str` | TEXT NOT NULL, default `''` |
| `AgentMessage.tools_json`, `usage_json`, `pending_json` | `str \| None` | TEXT |
| `AgentMessage.created_at` | `datetime` | DATETIME NOT NULL |
| `AgentRisk.trading_enabled` | `bool` | BOOLEAN NOT NULL, default False |
| `AgentRisk.max_order_value` | `float` | FLOAT NOT NULL, default 50000.0 |
| `AgentRisk.max_quantity`, `max_orders_per_day` | `int` | INTEGER NOT NULL, defaults 500 and 20 |
| `AgentRisk.allowed_exchanges` | `str` | TEXT NOT NULL, default `NSE,BSE` |
| `AgentOrderAudit.created_at` | `datetime` | DATETIME NOT NULL |
| `AgentOrderAudit.conversation_id`, `run_id`, `order_id` | `str \| None` | VARCHAR |
| `AgentOrderAudit.tool`, `status` | `str` | VARCHAR NOT NULL |
| `AgentOrderAudit.args_json` | `str` | TEXT NOT NULL |
| `AgentOrderAudit.message` | `str \| None` | TEXT |

No `relationship()` is declared between models; joins are not used. Code
looks rows up by key (`db.get(Model, pk)`) or with explicit `select()`
statements.

The `agent.db` tables have no SmartTrader model; their schema is defined in
Agno (`agno/db/sqlite/schemas.py`).

## 2. Pydantic request bodies (routers)

These validate input before it reaches a table or the broker. Field limits
here are the effective column limits, since the DDL has no length checks.

| Model | File | Fields and validation | Feeds |
| --- | --- | --- | --- |
| `LoginRequest` | `routers/auth.py` | `api_key: str`, `api_secret: str \| None = None`, `redirect_url: str` (router checks non-empty key and http(s) URL) | `broker_config` |
| `SettingsBody` | `routers/agent.py` | `model: str` (1 to 80), `reasoning_effort: str \| None = None`, `instructions: str \| None = None` (max 4,000); router checks model catalogue and effort enum | `agent_setting` |
| `FinEdgeKeyBody` | `routers/agent.py` | `api_key: str` (8 to 256 characters, `pattern=r"^\S+$"` so no whitespace anywhere; not trimmed). Any violation is a 422 validation list | `agent_secret` row `finedge_api_key` (encrypted by `finedge.write_key`) |
| `RenameBody` | `routers/agent.py` | `title: str` (1 to 120 before trimming); whitespace-only title stores empty text | `agent_conversation.title` |
| `ChatBody` | `routers/agent.py` | `message: str` (1 to 16,000 before trimming); whitespace-only input stores empty text; `conversation_id: str \| None = None` (no length bound) | `agent_conversation`, `agent_message` |
| `ConfirmBody` | `routers/agent.py` | `conversation_id: str` (1 to 64), `run_id: str` (1 to 64), `decisions: dict[str, bool]` (tool_call_id to approve) | `agent_message.pending_json`, `agent_order_audit` |
| `RiskBody` | `routers/agent.py` | `trading_enabled: bool`, `max_order_value: float` (above 0, at most 10,000,000), `max_quantity: int` (1 to 1,000,000), `max_orders_per_day: int` (1 to 1,000), `allowed_exchanges: list[str]` (router rejects unknown names) | `agent_risk` |
| `PlaceBody` | `routers/orders.py` | `symbol` (1 to 64), `exchange` (2 to 16), `side`, `quantity` (1 to 1,000,000), `product = "MIS"`, `price_type = "LIMIT"`, `price >= 0`, `trigger_price >= 0` | Broker only (looked up in `symtoken`) |
| `ModifyBody` | `routers/orders.py` | `price > 0`, `quantity: int \| None` (1 to 1,000,000), `trigger_price: float \| None` (above 0) | Broker only |
| `ClosePositionBody` | `routers/orders.py` | `symbol` (1 to 64), `exchange` (2 to 16), `product: str`, `side: str` (no enum validation), `quantity` (1 to 1,000,000). Router maps exactly `long` to SELL and every other side to BUY. | Broker only |

Chart and order placement from the UI are not stored locally; only agent
orders are audited.

## 3. Dataclasses and other value types

| Type | File | Fields | Role |
| --- | --- | --- | --- |
| `LoginResult` (frozen) | `broker/tradesmart.py` | `access_token`, `user_id`, `user_name` | Result of GenAcsTok; becomes an `auth_session` row (token encrypted). |
| `OrderRequest` (frozen) | `broker/orders.py` | `exchange`, `brsymbol`, `side`, `quantity`, `product`, `price_type`, `price`, `trigger_price = 0.0` | Validated order built from a `symtoken` row, mapped to a Noren `PlaceOrder` payload (`PRODUCTS`: CNC to C, NRML to M, MIS to I; `PRICE_TYPES`: MARKET and LIMIT to LMT, SL and SL-M to SL-LMT). |
| `Placed` (frozen) | `broker/order_service.py` | `order_id`, `sent_price`, `ltp` | Return value of a UI order placement. Not stored. |
| `Instrument` | `broker/stream.py` | `symbol`, `exchange`, `key` (`EXCH\|token`) | Built from a `symtoken` row by `routers/stream.py:_lookup` for tick subscriptions. |
| `TickState` | `broker/stream.py` | `ltp`, `volume` | Running state of one touchline subscription. |
| `FinEdgeError` (Exception) | `agent/finedge.py` | `status_code` (HTTP status, 0 when unreachable), `message` (never contains the key); properties `invalid_key` (401 with `ER006`) and `not_in_plan` (401 or 403 otherwise) | Raised by the FinEdge client; mapped to 422 or 502 by the key test route and to text by the research tools. Not stored. |
| `LoginFlow` | `agent/chatgpt_auth.py` | `flow_id`, `user_code`, `verification_url`, `expires_at`, `state` (`pending`, `complete`, `error`, `expired`), `error` | In-memory ChatGPT device-code sign-in; on success the tokens go to `agent_secret`. |
| `Row` (type alias) | `broker/master_contract.py` | `dict[str, Any]` with the 11 `symtoken` columns except `id` | Parser output, bulk-inserted into `symtoken`. |
| `BrowserChannel` (Protocol) | `broker/stream.py` | `send_json`, `receive_json` | Abstraction over the browser WebSocket. |

## JSON documents stored in TEXT columns

| Column | Shape documented in |
| --- | --- |
| `master_contract_status.exchanges_json` | [core-tables.md](core-tables.md#master_contract_status) |
| `agent_secret.value_enc` (row `chatgpt_oauth`, after decryption; `finedge_api_key` holds a plain string) | [agent-tables.md](agent-tables.md#agent_secret) |
| `agent_message.tools_json`, `usage_json`, `pending_json` | [agent-tables.md](agent-tables.md#agent_message) |
| `agent_order_audit.args_json` | [agent-tables.md](agent-tables.md#agent_order_audit) |
| `agno_sessions.*_data`, `agno_runs.run_data` | Agno internal, see [agent-db.md](agent-db.md) |
