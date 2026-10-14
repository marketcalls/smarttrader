# Core tables (smarttrader.db)

The four tables from the original design: broker credentials, login
sessions, the symbol master and its download status. All are defined in
`backend/app/models.py` and created by `backend/app/db.py:init_db`. See
[README.md](README.md) for conventions that apply to every table (timestamps,
booleans, ORM-side defaults, no foreign keys).

Column tables use these headings:

* Type: the SQLite type generated from the models (SQLAlchemy `String` becomes VARCHAR,
  `Text` TEXT, `Float` FLOAT, `DateTime` DATETIME, `Boolean` BOOLEAN).
* Null: `NO` means `NOT NULL` in the DDL.
* Default: the ORM-side default, applied by SQLAlchemy only. There are no
  database defaults.

For tables without a Default column, no ORM default is declared: nullable
fields may be NULL, required fields must be supplied, and integer primary
keys use SQLite rowid generation. Indexes appear in DDL or the Key column;
there are no additional indexes. The access tables below identify readers
and writers; a row insertion or replacement supplies all its fields unless
the operation explicitly lists a subset.

---

## broker_config

The TradeSmart app credentials entered on the login page. Single row, `id = 1`.
Model: `models.py:BrokerConfig`.

```sql
CREATE TABLE broker_config (
	id INTEGER NOT NULL,
	api_key TEXT,
	api_secret_enc TEXT,
	redirect_url TEXT,
	updated_at DATETIME,
	PRIMARY KEY (id)
)
```

| Column | Type | Null | Default | Key | Meaning |
| --- | --- | --- | --- | --- | --- |
| `id` | INTEGER | NO | none (code always uses 1) | PK | Singleton id. |
| `api_key` | TEXT | yes | | | TradeSmart API key (the OAuth `client_id`). Plaintext: it is not a secret and is returned by `GET /api/config` to pre-fill the form. |
| `api_secret_enc` | TEXT | yes | | | TradeSmart API secret, **Fernet encrypted** with `crypto.encrypt`. Never returned to the browser; `GET /api/config` only reports `has_secret`. |
| `redirect_url` | TEXT | yes | | | OAuth redirect URL registered with TradeSmart (normally `http://127.0.0.1:8000/tradesmart/callback`). Validated as an http or https URL with a host. |
| `updated_at` | DATETIME | yes | | | Naive UTC time of the last save. |

Indexes: primary key only. Constraints: none beyond the PK.

Code that touches it:

| Operation | File:function | Detail |
| --- | --- | --- |
| Read | `routers/auth.py:get_config` | Returns `api_key`, `redirect_url`, `has_secret = bool(api_secret_enc)`. |
| Insert or update | `routers/auth.py:login` | Creates `BrokerConfig(id=1)` if missing. Always overwrites `api_key`, `redirect_url`, `updated_at`. Overwrites `api_secret_enc` only when a non-empty secret is posted (empty keeps the stored one). 400 if no secret is posted and none is stored. |
| Read and decrypt | `routers/auth.py:tradesmart_callback` | Decrypts `api_secret_enc` to compute the GenAcsTok checksum `sha256(api_key + api_secret + code)`. If decryption fails (for example `secret.key` changed) the user is sent back to the login page with an error. |

Lifecycle: created on the first Login press, updated on every Login press,
never deleted by the application.

---

## auth_session

One row per successful TradeSmart login. The browser holds a random cookie
`st_session`; the database holds only its SHA-256. Model:
`models.py:AuthSession`.

```sql
CREATE TABLE auth_session (
	id INTEGER NOT NULL,
	session_hash TEXT NOT NULL,
	user_id TEXT,
	user_name TEXT,
	access_token_enc TEXT,
	created_at DATETIME,
	is_active BOOLEAN NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (session_hash)
)
-- implicit index: sqlite_autoindex_auth_session_1 on (session_hash)
```

| Column | Type | Null | Default | Key | Meaning |
| --- | --- | --- | --- | --- | --- |
| `id` | INTEGER | NO | rowid | PK | Surrogate id. |
| `session_hash` | TEXT | NO | | UNIQUE | Lowercase hex SHA-256 of the cookie value (`deps.py:hash_session_value`). The raw cookie (`secrets.token_urlsafe(32)`, 43 characters) is never stored. Hashed, not encrypted: it cannot be reversed and does not depend on `secret.key`. |
| `user_id` | TEXT | yes | | | TradeSmart account id from GenAcsTok (`actid`, else `uid`, else `USERID`). Sent as `uid` and `actid` in every broker call; also passed to Agno as `user_id`. |
| `user_name` | TEXT | yes | | | Display name (`uname`) from GenAcsTok. |
| `access_token_enc` | TEXT | yes | | | Broker bearer token (`access_token`, fallback `susertoken`), **Fernet encrypted**. Decrypted per request; never sent to the browser. |
| `created_at` | DATETIME | yes | | | Naive UTC login time. Shown as `login_time` (IST) by `GET /api/auth/session`. |
| `is_active` | BOOLEAN | NO | `True` | | 1 while the session is usable. Lookups only match active rows. |

Indexes: PK, and the automatic unique index on `session_hash` that serves
every cookie lookup.

Code that touches it:

| Operation | File:function | Detail |
| --- | --- | --- |
| Insert | `routers/auth.py:tradesmart_callback` | First `UPDATE auth_session SET is_active = 0 WHERE is_active = 1` (only one active session at a time), then insert the new row with the encrypted token. Both in one commit. |
| Read (lookup) | `deps.py:find_active_session` | `WHERE session_hash = sha256(cookie) AND is_active = 1`. Used by `deps.py:require_session` (every protected route), `routers/auth.py:get_session`, `routers/auth.py:logout`, `routers/stream.py:_session_credentials`. |
| Read and decrypt token | `routers/funds.py:get_funds`, `routers/chart.py:chart_history`, `routers/orders.py:_creds`, `routers/agent.py:_credentials`, `routers/stream.py:_session_credentials` | `crypto.decrypt(access_token_enc)`. |
| Deactivate | `routers/auth.py:logout` | Sets `is_active = 0` for the caller's session. |
| Deactivate on broker expiry | `routers/funds.py:_expire`, `routers/orders.py:_creds` and `_call`, `routers/chart.py:chart_history` | When the token cannot be decrypted, `user_id` is empty, or TradeSmart answers "Session Expired" (`SessionExpiredError`), the row is set inactive and the client gets 401. |

Not deactivated: the agent tools (`agent/tools.py`, `agent/trading.py`) and the
tick WebSocket (`routers/stream.py`) report an expired broker session to the
model or the socket but leave `is_active` unchanged.

Lifecycle: inserted on each successful OAuth callback; previous active rows
are flipped to inactive at the same moment; rows are deactivated by logout or
broker expiry; **rows are never deleted**, so inactive rows (with their
encrypted tokens) accumulate.

---

## symtoken

The symbol master (master contract): every tradable instrument on NSE, BSE,
NFO, BFO, CDS and MCX, plus index pseudo-exchanges, in OpenAlgo symbol
conventions. Coverage depends on which exchange downloads succeed.
Model: `models.py:SymToken`. Loaded by
`broker/master_contract.py`; the download and parsing rules are in
[files-and-formats.md](files-and-formats.md#master-contract-files).

```sql
CREATE TABLE symtoken (
	id INTEGER NOT NULL,
	symbol VARCHAR NOT NULL,
	brsymbol VARCHAR NOT NULL,
	name VARCHAR,
	exchange VARCHAR,
	brexchange VARCHAR,
	token VARCHAR,
	expiry VARCHAR,
	strike FLOAT,
	lotsize INTEGER,
	instrumenttype VARCHAR,
	tick_size FLOAT,
	PRIMARY KEY (id)
)
CREATE INDEX idx_symtoken_symbol_exchange ON symtoken (symbol, exchange)
CREATE INDEX idx_symtoken_token ON symtoken (token)
CREATE INDEX idx_symtoken_brsymbol ON symtoken (brsymbol)
```

| Column | Type | Null | Key | Meaning and values |
| --- | --- | --- | --- | --- |
| `id` | INTEGER | NO | PK | Rowid. Restarts at 1 on every full reload, so it is not a stable identifier. Nothing references it. |
| `symbol` | VARCHAR | NO | idx (with `exchange`) | SmartTrader / OpenAlgo symbol, the key users and the agent type. Equities: trading symbol without the `-EQ` or `-BE` series suffix (`RELIANCE`, `BAJAJ-AUTO`, `M&M`). NSE indices: normalized and aliased (`NIFTY`, `BANKNIFTY`, `FINNIFTY`, `NIFTYNXT50`, `MIDCPNIFTY`). BSE: the trading symbol as is. Derivatives: `name + expiry without dashes + [strike] + FUT/CE/PE`, for example `NIFTY28OCT2625000CE`, `CRUDEOIL19OCT26FUT`. |
| `brsymbol` | VARCHAR | NO | idx | Broker trading symbol (`TradingSymbol` column, `tsym` in Noren). Sent in order payloads and EOD chart requests. |
| `name` | VARCHAR | yes | | Underlying or company name (`Symbol` column). For BFO it is the leading letters of the trading symbol. |
| `exchange` | VARCHAR | yes | idx (second column) | SmartTrader exchange: `NSE`, `NSE_INDEX`, `BSE`, `BSE_INDEX`, `NFO`, `BFO`, `CDS`, `MCX`. Nullable in the DDL but always set by the parsers. |
| `brexchange` | VARCHAR | yes | | Broker exchange. Every parser sets it equal to `exchange` (including `NSE_INDEX` and `BSE_INDEX`). Callers map the index pseudo-exchanges to `NSE` or `BSE` with `broker/history.py:data_exchange` before calling TradeSmart. |
| `token` | VARCHAR | yes | idx | Broker instrument token (string). Used for quotes (`GetQuotes`), intraday history (`TPSeries`) and the tick stream key `EXCH\|token`. Rows with a missing token are treated as "not in the symbol master" by every lookup. |
| `expiry` | VARCHAR | yes | | Derivatives: `DD-MMM-YY` uppercase (`28-OCT-26`). Equities and indices: empty string `''`. `NULL` if a derivative's expiry could not be parsed. |
| `strike` | FLOAT | yes | | Option strike. `-1` for equities, indices and when the file value is blank or unparsable. Futures keep the file's placeholder (for example `-0.01` on NFO, `0` on MCX, `-1e-07` on CDS), so treat `strike <= 0` as "no strike" (the agent's `search_symbols` does exactly that). Stored as REAL even when integral. |
| `lotsize` | INTEGER | yes | | Lot size from the file; `0` when unparsable. UI order placement through `broker/order_service.py:place` requires F&O and MCX quantities to be a multiple of it when above 1. Agent order tools do not apply this check. |
| `instrumenttype` | VARCHAR | yes | | `EQ`, `FUT`, `CE`, `PE`, `INDEX`; other NSE series are kept as is (`SG`, `GS`, `SM`, `N0`, `MF`, ...); BFO rows whose symbol ends in none of FUT/CE/PE get `UNKNOWN`. Drives the market price protection slab in `broker/orders.py:protection_pct`. |
| `tick_size` | FLOAT | yes | | Tick size in rupees as published (no division by 100). `NULL` when unparsable; code falls back to `0.05`. |

Constraints: none besides the PK. There is **no unique constraint** on
`(token, exchange)` or `(symbol, exchange)`. Uniqueness of `(token, exchange)`
is enforced in Python before insert (first occurrence wins). `(symbol,
exchange)` can collide after symbol normalization (see
[risks-and-discrepancies.md](risks-and-discrepancies.md)).

Index usage:

* `idx_symtoken_symbol_exchange` serves every exact lookup
  `WHERE symbol = ? AND exchange = ? LIMIT 1`.
* `idx_symtoken_token` and `idx_symtoken_brsymbol` exist (they mirror
  OpenAlgo and SPEC.md) but no current query filters on `token` or on
  `brsymbol` by equality.
* Searches use `LIKE '%term%'` on `symbol`, `brsymbol` and `name`, which
  cannot use any index and scan the table.

Code that touches it:

| Operation | File:function | Detail |
| --- | --- | --- |
| Replace all | `broker/master_contract.py:_replace_symtoken` (called by `_run_download`) | One transaction: `DELETE FROM symtoken`, then executemany `INSERT` in batches of 10,000 (`INSERT_BATCH`). Readers keep seeing the old table until commit (WAL). |
| Search | `routers/master.py:search_symbols` (`GET /api/symbols/search`) | Up to 5 whitespace-separated terms, each must match `symbol`, `brsymbol` or `name` with an escaped `LIKE`; optional exact `exchange`; ranked exact symbol, then prefix, then other; then by symbol length; limit 50. |
| Search (agent) | `agent/tools.py:build_tools.search_symbols` | Up to 4 terms, unescaped `LIKE`, optional exchange, ordered by symbol, limit 15. |
| Exact lookup | `routers/chart.py:chart_history`, `routers/stream.py:_lookup`, `broker/order_service.py:instrument`, `agent/tools.py:_instrument`, `agent/trading.py:_instrument` | `WHERE symbol = ? AND exchange = ? LIMIT 1`, no `ORDER BY`. |

Lifecycle: empty until the first successful download. Fully replaced after
each successful download: automatically after every login
(`routers/auth.py:tradesmart_callback`) and on demand
(`POST /api/master-contract/download`). A download in which no exchange
loads leaves the existing rows untouched. A partial success replaces the whole
table with successful exchanges only, removing old rows for failed exchanges.
Individual rows are never updated.

---

## master_contract_status

Progress and result of the latest master contract download. Single row,
`id = 1`. Model: `models.py:MasterContractStatus`.

```sql
CREATE TABLE master_contract_status (
	id INTEGER NOT NULL,
	status VARCHAR NOT NULL,
	message TEXT,
	total_records INTEGER NOT NULL,
	exchanges_json TEXT,
	started_at DATETIME,
	finished_at DATETIME,
	PRIMARY KEY (id)
)
```

| Column | Type | Null | Default | Key | Meaning |
| --- | --- | --- | --- | --- | --- |
| `id` | INTEGER | NO | none (always 1) | PK | Singleton id. |
| `status` | VARCHAR | NO | `'idle'` | | `idle` (never downloaded), `downloading`, `success`, `error`. Not constrained in the DDL. |
| `message` | TEXT | yes | | | Human readable progress or result: `Queued`, `Starting download`, `Downloading NFO`, `Processing NFO`, `Saving N records`, `Loaded N records in X s (failed: BSE)`, `No exchange could be loaded (...)`, `Download failed: ...`, `Previous download was interrupted`. Error messages are cut to 500 characters. |
| `total_records` | INTEGER | NO | `0` | | Rows written to `symtoken` by the last successful load. |
| `exchanges_json` | TEXT | yes | | | JSON object, symtoken `exchange` to row count, for example `{"NSE": 100, "BSE": 80, "BSE_INDEX": 2}` (illustrative counts). Seeded as `'{}'`. |
| `started_at` | DATETIME | yes | | | Naive UTC start of the latest download. |
| `finished_at` | DATETIME | yes | | | Naive UTC end; set to `NULL` when a new download starts. |

Code that touches it:

| Operation | File:function | Detail |
| --- | --- | --- |
| Insert (seed) | `db.py:init_db` | At startup if missing: `status='idle'`, `total_records=0`, `exchanges_json='{}'`. |
| Recover | `db.py:init_db` | At startup, `downloading` becomes `error` with `Previous download was interrupted` and `finished_at = now`. |
| Update | `broker/master_contract.py:_set_status` | `UPDATE ... WHERE id = 1` with keyword values, each in its own short transaction. Called by `start_download_in_background` (`downloading`, `Queued`, `started_at`, `finished_at = NULL`), `_run_download` (start, `Saving`, `success` with `total_records`, `exchanges_json`, `finished_at`; or `error`) and `_fetch_and_parse` (per exchange `Downloading X`, `Processing X`). |
| Read | `routers/master.py:master_status` (`GET /api/master-contract/status`) | Parses `exchanges_json` (bad JSON becomes `{}`), converts times to IST. |
| Read | `agent/tools.py:build_tools.get_master_contract_status` | Same data for the agent. |

Lifecycle: created once at first startup, updated by every download, never
deleted. On `error`, `total_records` and `exchanges_json` keep the values of
the last successful status update (they are not reset). A total download
failure or failed replacement transaction preserves old symbols. The symbol
replacement and success status update are separate transactions, so an error
or crash between them can leave new symbols with old counts.

Concurrency: only one download runs per process
(`master_contract._download_lock`, a non-blocking `threading.Lock`). A second
request gets 409. The lock is per process, so two backend processes on the
same data folder could both download.
