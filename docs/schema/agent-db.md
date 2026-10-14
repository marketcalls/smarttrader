# agent.db (Agno session store)

`backend/data/agent.db` holds the agent's own memory: the sessions and runs
that Agno replays into the model's context (last 8 runs per conversation).
It is owned entirely by the Agno library (agno 3.1.2 in `uv.lock`); SmartTrader
never runs SQL against it directly. The visible transcript is kept separately
in `smarttrader.db` (`agent_message`, see [agent-tables.md](agent-tables.md)).

## Ownership and access

| Aspect | Detail |
| --- | --- |
| Opened by | `agent/builder.py:agent_db`, which returns one cached `agno.db.sqlite.SqliteDb(db_file=data/agent.db)` per process (`lru_cache(maxsize=1)`). |
| Used by | `agent/builder.py:build_agent` passes it as `Agent(db=...)` with `session_id = agent_conversation.id`, `user_id = auth_session.user_id`, `add_history_to_context=True`, `num_history_runs=8`. |
| Written | By Agno during `Agent.arun` (`routers/agent.py:chat`) and `Agent.acontinue_run` (`routers/agent.py:confirm`): it upserts the session and stores each run, including paused runs waiting for order approval. |
| Read | By Agno to build history for each new run; explicitly by `routers/agent.py:confirm` through `agent.aget_run_output(run_id, session_id=...)` to load the paused run before resuming it. |
| Deleted | `routers/agent.py:delete_conversation` calls `agent_db().delete_session(conversation_id)`, which deletes the session row, its runs (explicit delete plus `ON DELETE CASCADE`) and any compaction records. Failures are logged and ignored. |
| Not written | `routers/agent.py:test_model` sets `agent.db = None` before its one-off completion, so model tests leave no session. |
| Pragmas | Set by Agno on every connection: `foreign_keys = ON`, `journal_mode = WAL`; `synchronous` stays at the default `FULL`. |
| Creation and migration | Tables are created on first use by Agno and newly created tables are stamped in `agno_schema_versions`. Agno provides `MigrationManager.up` for upgrades, but SmartTrader and the SQLite adapter do not invoke it automatically. SmartTrader's `init_db` does not touch this file. |
| Encryption | None. Run payloads are stored in plaintext JSON. |

Agno can also create memory, metrics, compaction and other tables; the three
below describe the session persistence used by this application. Their
presence in a deployed database depends on prior use. This source audit did
not inspect runtime files. No column below declares a database or client
default; Agno supplies the required values on writes. Indexes are listed in
the DDL. SmartTrader accesses these records through Agno rather than reading
or writing individual columns itself.

## agno_sessions

One row per conversation. `session_id` equals `agent_conversation.id`.

```sql
CREATE TABLE agno_sessions (
	session_id VARCHAR NOT NULL,
	session_type VARCHAR NOT NULL,
	agent_id VARCHAR,
	team_id VARCHAR,
	workflow_id VARCHAR,
	user_id VARCHAR,
	session_data JSON,
	agent_data JSON,
	team_data JSON,
	workflow_data JSON,
	metadata JSON,
	summary JSON,
	created_at BIGINT NOT NULL,
	updated_at BIGINT,
	PRIMARY KEY (session_id)
)
CREATE INDEX idx_agno_sessions_created_at ON agno_sessions (created_at)
CREATE INDEX idx_agno_sessions_session_type ON agno_sessions (session_type)
-- implicit: sqlite_autoindex_agno_sessions_1 on (session_id)
```

| Column | Type | Null | Meaning |
| --- | --- | --- | --- |
| `session_id` | VARCHAR | NO, PK | Conversation id from SmartTrader (`uuid4().hex`). |
| `session_type` | VARCHAR | NO, idx | Agno session kind; `agent` for SmartTrader. |
| `agent_id` | VARCHAR | yes | Agno agent id (derived by Agno from the agent name `SmartTrader Agent`). |
| `team_id`, `workflow_id` | VARCHAR | yes | Unused by SmartTrader (no teams or workflows). |
| `user_id` | VARCHAR | yes | TradeSmart account id of the logged-in user. |
| `session_data` | JSON | yes | Agno session state (for example session metrics). |
| `agent_data` | JSON | yes | Snapshot of the agent configuration (name, model) as Agno records it. |
| `team_data`, `workflow_data` | JSON | yes | Unused. |
| `metadata` | JSON | yes | Agno metadata; SmartTrader sets none. |
| `summary` | JSON | yes | Session summary; SmartTrader does not enable summaries. |
| `created_at` | BIGINT | NO, idx | Epoch seconds. |
| `updated_at` | BIGINT | yes | Epoch seconds. |

## agno_runs

One row per agent run (one user turn, including a resumed run after
approval). `run_data` holds the full run output: input, model messages, tool
calls with full results, metrics and, for paused runs, the requirements that
await confirmation.

```sql
CREATE TABLE agno_runs (
	run_id VARCHAR NOT NULL,
	session_id VARCHAR NOT NULL,
	run_type VARCHAR NOT NULL,
	agent_id VARCHAR,
	team_id VARCHAR,
	workflow_id VARCHAR,
	user_id VARCHAR,
	parent_run_id VARCHAR,
	status VARCHAR,
	run_index BIGINT,
	run_data JSON NOT NULL,
	created_at BIGINT NOT NULL,
	updated_at BIGINT,
	PRIMARY KEY (run_id),
	FOREIGN KEY(session_id) REFERENCES agno_sessions (session_id) ON DELETE CASCADE
)
CREATE INDEX idx_agno_runs_agent_id ON agno_runs (agent_id)
CREATE INDEX idx_agno_runs_created_at ON agno_runs (created_at)
CREATE INDEX idx_agno_runs_run_type ON agno_runs (run_type)
CREATE INDEX idx_agno_runs_session_id ON agno_runs (session_id)
CREATE INDEX idx_agno_runs_session_id_run_index ON agno_runs (session_id, run_index)
CREATE INDEX idx_agno_runs_status ON agno_runs (status)
CREATE INDEX idx_agno_runs_team_id ON agno_runs (team_id)
CREATE INDEX idx_agno_runs_user_id ON agno_runs (user_id)
CREATE INDEX idx_agno_runs_workflow_id ON agno_runs (workflow_id)
-- implicit: sqlite_autoindex_agno_runs_1 on (run_id)
```

| Column | Type | Null | Meaning |
| --- | --- | --- | --- |
| `run_id` | VARCHAR | NO, PK | Agno run id. Sent to the browser in the `run` and `confirm` SSE frames, stored in `agent_message.pending_json.run_id` and `agent_order_audit.run_id`. |
| `session_id` | VARCHAR | NO, FK | `agno_sessions.session_id`, `ON DELETE CASCADE` (enforced because Agno turns `foreign_keys` on). |
| `run_type` | VARCHAR | NO | Run kind (agent run). |
| `agent_id`, `team_id`, `workflow_id` | VARCHAR | yes | As in `agno_sessions`. |
| `user_id` | VARCHAR | yes | TradeSmart account id. |
| `parent_run_id` | VARCHAR | yes | Unused by SmartTrader. |
| `status` | VARCHAR | yes | Agno run status (for example running, paused, completed, cancelled, error). |
| `run_index` | BIGINT | yes | Position of the run in its session; with `session_id` it serves "latest N runs" for history. |
| `run_data` | JSON | NO | Full serialized run output. Contains tool results verbatim (quotes, funds, positions, holdings, order book), unlike `agent_message.tools_json`, which keeps previews of at most 4,000 characters. |
| `created_at` | BIGINT | NO | Epoch seconds; indexed. |
| `updated_at` | BIGINT | yes | Epoch seconds. |

## agno_schema_versions

Agno's own migration bookkeeping.

```sql
CREATE TABLE agno_schema_versions (
	table_name VARCHAR NOT NULL,
	version VARCHAR NOT NULL,
	created_at VARCHAR NOT NULL,
	updated_at VARCHAR,
	PRIMARY KEY (table_name)
)
CREATE INDEX idx_agno_schema_versions_created_at ON agno_schema_versions (created_at)
```

| Column | Type | Null | Meaning |
| --- | --- | --- | --- |
| `table_name` | VARCHAR | NO, PK | Name of a table Agno created or migrated. |
| `version` | VARCHAR | NO | Schema version recorded for that table. |
| `created_at` | VARCHAR | NO, idx | Naive local ISO timestamp from Agno's `datetime.now().isoformat()` when the version row was created. |
| `updated_at` | VARCHAR | yes | Naive local ISO timestamp of its latest version write. |

`SqliteDb._create_table` writes the version through `upsert_schema_version`;
`MigrationManager` reads it through `get_latest_schema_version` when explicitly
invoked. Newly created session and run tables are stamped `3.0.0` by the
installed Agno 3.1.2 source. This is a schema version, not the package version.

## Lifecycle summary

| Event | agent.db effect |
| --- | --- |
| First chat message in a new conversation | Session row created, first run stored. |
| Every later message | Session updated, a new run stored. |
| Run pauses for order approval | Run stored with paused status and its requirements. |
| `POST /api/agent/chat/confirm` | Paused run loaded, continued, and stored again with the outcome. |
| Conversation deleted | Session, runs and compactions deleted. |
| ChatGPT sign-out, TradeSmart logout | No effect; history remains. |
