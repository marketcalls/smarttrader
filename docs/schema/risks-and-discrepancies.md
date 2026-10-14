# Schema risks and code vs SPEC.md discrepancies

Observations from reading `backend/app` and installed Agno source on
2026-10-14. Runtime databases were not inspected. The code is the source of
truth; the other pages document what it does.

## Code vs SPEC.md

| # | SPEC.md says | Code does | Impact |
| --- | --- | --- | --- |
| 1 | "Data storage" lists four tables (`broker_config`, `auth_session`, `symtoken`, `master_contract_status`). | `smarttrader.db` has ten tables; the six `agent_*` tables are also there. | Data storage section is incomplete. |
| 2 | The AI agent section's "Tables" line names `agent_setting`, `agent_secret`, `agent_conversation`, `agent_message`. | Also `agent_risk` and `agent_order_audit` (both described in prose elsewhere in SPEC.md, but not in the tables list or in Data storage). | Incomplete list. |
| 3 | Layout: `data/` holds `smarttrader.db, secret.key, tmp/`. | `data/agent.db` (Agno) also lives there (it is mentioned in SPEC.md's agent section only). The root README now includes it. | Backups and clean-up instructions following the original layout miss `agent.db`. |
| 4 | SPEC.md Data storage describes encryption of the API secret and access token. | The key also encrypts `agent_secret.value_enc` (ChatGPT OAuth tokens), as SPEC.md's agent section notes. The root README now includes this. | Losing the key makes both broker and ChatGPT credentials unreadable after restart. |
| 5 | `symtoken` columns listed without types except `strike REAL`, `lotsize INTEGER`; `master_contract_status.status` as TEXT. | String columns are VARCHAR, `strike` and `tick_size` FLOAT, `status` VARCHAR. | None in practice (same SQLite affinity). |
| 6 | Master contract exchanges listed as NSE, BSE, NFO, BFO, CDS, MCX. | Processing order is NSE, BSE, NFO, CDS, MCX, BFO (`EXCHANGES`). | Only affects which row wins a `(token, exchange)` duplicate, which cannot collide across exchanges. None in practice. |
| 7 | "If the broker answers Session Expired ... deactivate the session and return 401." | Done in the funds, chart and orders routers. The agent tools and the tick WebSocket report the expiry but leave `auth_session.is_active = 1`. | The UI may still show a logged-in state until another route deactivates the row. |
| 8 | Not specified. | Startup turns a stale `master_contract_status.status = 'downloading'` into `error` ("Previous download was interrupted"). | Undocumented in SPEC.md but intended. |
| 9 | Not specified. | Schema evolution uses `create_all` plus a hand-kept `_ADDED_COLUMNS` registry (`agent_message.pending_json`). | Undocumented in SPEC.md. |
| 10 | Not specified. | `master_contract_status` keeps previous `total_records` and `exchanges_json` after an `error` run. Replacement and status writes are separate commits. | Usually describes old data, but a crash or error after replacement can leave counts behind the symbol table. |

## Schema risks

### Data integrity

1. **`(symbol, exchange)` is not unique in `symtoken`, and lookups take an
   arbitrary row.** Every exact lookup (`chart_history`, `stream._lookup`,
   `order_service.instrument`, `agent/tools._instrument`,
   `agent/trading._instrument`) uses `WHERE symbol = ? AND exchange = ?
   LIMIT 1` without `ORDER BY`. In particular,
   `master_contract.py:_bfo_name` takes only the leading letters of the
   trading symbol. Synthetic names `SENSEX26OCTFUT` and `SENSEX5026OCTFUT`
   with the same expiry both normalize to the `SENSEX` underlying. Distinct
   tokens survive deduplication and can produce the same lookup key.
   An order or chart can then resolve to the wrong contract. The parsers
   only reject empty tokens/symbols (plus CDS token filtering), so other
   malformed but non-empty names can also enter the table.
   A unique index on `(symbol, exchange)` would make the reload fail rather
   than silently pick one; fixing `_bfo_name` is the root fix.
2. **No foreign keys in `smarttrader.db`.** `agent_message.conversation_id`
   and `agent_order_audit.conversation_id` are kept consistent only by
   `routers/agent.py:delete_conversation`. Audit rows intentionally outlive
   their conversation and then point at nothing.
3. **Cross-database link without a transaction.** A conversation is deleted
   in `smarttrader.db`, committed, then `agent.db` is cleaned. If the second
   step fails it is only logged, leaving an orphan Agno session. A crash
   between the transcript insert and Agno's run write can also leave the two
   out of step.
   Deletion does not cancel an active run: `_stream_run` can later append an
   orphan `agent_message` and Agno can store history again after the deletion.
4. **No CHECK constraints.** `status` columns (`master_contract_status`,
   `agent_order_audit`), `agent_message.role`, `agent_setting.reasoning_effort`
   and booleans accept any value at the database level; validation exists
   only in Python.
5. **Defaults are ORM-side only.** Inserts that bypass SQLAlchemy (manual SQL,
   another tool) must supply `is_active`, `status`, `total_records`, the
   `agent_risk` limits and `agent_message.content`.
6. **`symtoken.id` is not stable.** It restarts at 1 on every reload (rowid
   without `AUTOINCREMENT`). Nothing references it today; nothing should.
7. **Unknown `conversation_id` creates a new conversation.** `POST
   /api/agent/chat` with an id that does not exist silently starts a new
   thread with a new id instead of returning 404.
8. **Risk checks are not atomic.** `_placed_today` and `_recent_duplicate`
   read `agent_order_audit` before the broker call and the `placed` row is
   written after it. Two approvals processed at the same moment could both
   pass the daily limit or the 60-second duplicate check.

   **Approval and result persistence are also separate.** `confirm` commits
   `pending_json.state = decided` before loading the paused Agno run. A missing
   run or failure while building the agent leaves the approval consumed.
   There is no database-level conditional claim. The current single event-loop
   serializes the synchronous block before its first await; parallel workers or
   moving that block to threads would permit concurrent callers to read pending.
   A broker call can also succeed before a subsequent audit insert fails,
   leaving the local audit without the broker outcome.

   **Download completeness is not preserved on partial success.** Any non-empty
   combined result replaces all symbols, dropping previous rows for failed
   exchanges. The separate success-status commit can fail after new symbols
   are already stored, leaving stale counts or an interrupted status.

### Security and privacy

9. **The key sits next to the data.** `secret.key` is in the same folder as
   the databases. Anyone who can copy `backend/data/` can decrypt the API
   secret, the broker token and the ChatGPT tokens. There is no key rotation.
10. **Plaintext account data.** `agent_message.tools_json` (previews up to
    4,000 characters) and `agno_runs.run_data` (full tool results) store
    funds, positions, holdings and order books unencrypted.
11. **Sessions are never purged.** `auth_session` rows are only deactivated.
    Inactive rows keep their encrypted access tokens forever and the table
    grows by one row per login.
12. **Agent tool search does not escape LIKE wildcards.**
    `agent/tools.py:search_symbols` builds `%term%` without escaping `%` and
    `_` (the HTTP search in `routers/master.py` does escape them). Only
    affects match quality, not safety (values are bound parameters).

    **Agent data is not scoped to an account.** Settings, provider credentials,
    risk limits, conversations, messages and audits are global. Conversation
    and audit routes filter by id or chronology, not the logged-in user.
    Agno receives a broker `user_id`, but this does not add ownership checks
    to SmartTrader's tables or prevent an existing conversation being reused
    after another account logs in.

    **ChatGPT sign-out does not stop token writers.** `_run_flow` stores tokens
    before checking whether its flow is still current. A cancelled or older
    worker can recreate `agent_secret.chatgpt_oauth` after logout or overwrite
    a newer login. In-flight provider token refreshes are not cancelled either.
    Similarly, deactivating `auth_session` does not revoke broker credentials
    already captured by an active agent run or tick socket.

### Operations and performance

13. **WAL growth.** Replacing the entire `symtoken` table in one transaction
    can substantially grow `smarttrader.db-wal`. No `journal_size_limit` or
    explicit checkpoint is configured by the app. SQLite manages checkpointing
    and reuse; file size is workload-dependent, not a fixed schema property.
14. **Unused indexes.** `idx_symtoken_token` and `idx_symtoken_brsymbol` are
    maintained on every reload but no query filters on those columns by
    equality.
15. **Full scans.** Symbol search (`LIKE '%term%'`), the conversation list
    (`ORDER BY updated_at`) and the two risk queries on `agent_order_audit`
    (`status`, `tool`, `created_at`) have no supporting index. Fine at
    current sizes; the audit scan grows with every agent order.
16. **Limited migration path.** Only "add a nullable column" is automated.
    New indexes on existing tables, type changes, renames and constraints
    need a separate migration, and SmartTrader does not maintain a schema
    version. Agno supplies migration tooling for `agent.db`, but neither
    SmartTrader nor its SQLite adapter automatically invokes an upgrade.
17. **Per-process download lock.** Two backend processes sharing one data
    folder could run two master contract downloads at once. SQLite would
    serialize the writes (busy timeout 30 s), but status messages would
    interleave. A newly started process also changes any `downloading` status
    to `error`, even if another process is still downloading. Lazy singleton
    creation and the check-then-ALTER column migration likewise have no
    cross-process coordination.
18. **Naive UTC timestamps.** All SmartTrader `DATETIME` values are naive UTC
    strings and are compared as strings by SQLite. Any writer that stores
    local time or a different format would break the daily order limit and
    the sort orders.
19. **No managed backup, restore or retention workflow.** The application has
    no consistent backup operation for the two databases and their encryption
    key, no restore verification, and no age-based purge for sessions, audit
    rows or agent runs. `synchronous=NORMAL` also permits loss of the latest
    committed main-database transactions after a power failure. A deployment
    needs explicit recovery and retention policies rather than treating a
    copy of one live SQLite file as a complete backup.
20. **Encryption-key publication can race.** `crypto._load_or_create_key`
    creates an exclusive file before writing its bytes. Another process can
    read that file while empty, or a crash can leave it incomplete. Key loading
    then fails without an automatic repair path. Exclusivity prevents
    overwriting, but does not ensure readers see a complete key.
