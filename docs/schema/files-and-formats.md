# Files, formats and caches outside SQL

Everything SmartTrader keeps on disk lives in `backend/data/` (git-ignored).
This page covers the files that are not tables: the encryption key, the
master contract downloads and their parsing rules, temporary and SQLite
side files, and the in-memory state that acts as a cache.

## backend/data layout

| Path | Created by | Content | Lifetime |
| --- | --- | --- | --- |
| `smarttrader.db` | `db.py:init_db` (`create_all`) | Main SQLite database, see [core-tables.md](core-tables.md) and [agent-tables.md](agent-tables.md) | Permanent |
| `smarttrader.db-wal`, `smarttrader.db-shm` | SQLite (WAL mode) | Write-ahead log and shared-memory index | Present while any connection is open; checkpointed by SQLite |
| `agent.db` (+ `-wal`, `-shm`) | Agno `SqliteDb` | Agent session store, see [agent-db.md](agent-db.md) | Permanent |
| `secret.key` | `crypto.py:_load_or_create_key` | Fernet key (44 bytes) | Permanent; losing it makes encrypted columns unreadable |
| `tmp/` | `config.ensure_data_dirs` | Parent of per-download working folders | Permanent, normally empty |
| `tmp/master_<random>/` | `master_contract.py:_run_download` (`tempfile.mkdtemp`) | `<EXCH>.zip` and extracted `<EXCH>_symbols.txt` | Cleanup attempted in `finally` with `ignore_errors=True`; a killed process or filesystem cleanup failure can leave files behind |
| `chatgpt/` | would be LiteLLM's token folder | `chatgpt_auth.py` defaults `CHATGPT_TOKEN_DIR` to `data/chatgpt` if unset, then replaces LiteLLM's directory creation and file I/O, so tokens go to `agent_secret` instead | Not created by the patched flow; existing files are not removed |

The application does not load a `.env` or configuration file: credentials
live in SQLite, constants in `backend/app/config.py`. Redirect selection can
read the `SMARTTRADER_FRONTEND_URL` environment variable.

## Encryption at rest

Module: `backend/app/crypto.py`. Library: `cryptography.fernet`.

### secret.key

* Location: `backend/data/secret.key` (`config.SECRET_KEY_PATH`).
* Format: one Fernet key, the URL-safe base64 encoding of 32 random bytes
  (44 ASCII characters, no newline). The first 16 bytes are the HMAC-SHA256
  signing key, the last 16 the AES-128 encryption key.
* Creation: `crypto.py:_load_or_create_key` on first use (forced at startup by
  `main.py:lifespan` calling `get_fernet()`). It is written with
  `os.open(O_WRONLY | O_CREAT | O_EXCL, 0o600)`, so two processes racing at
  first start cannot overwrite each other's key (the loser reads the winner's
  file). The `0o600` mode only restricts access on POSIX systems; on Windows
  the file inherits the folder ACL.
  Creation is exclusive but publication is not atomic: a second process can
  read before the first finishes writing, and a crash can leave an empty or
  incomplete file. There is no retry, file synchronization or recovery step.
* Loading: read once, whitespace stripped, wrapped in a process-wide
  `Fernet` instance (`crypto._fernet`, guarded by a lock).
* Rotation: none. There is no `MultiFernet` and no re-encryption routine.

Never copy the key into documentation, logs or tickets.

### Encrypted values

| Table.column | Plaintext | Written by | Read by |
| --- | --- | --- | --- |
| `broker_config.api_secret_enc` | TradeSmart API secret | `routers/auth.py:login` | `routers/auth.py:tradesmart_callback` |
| `auth_session.access_token_enc` | TradeSmart bearer token | `routers/auth.py:tradesmart_callback` | funds, chart, orders, stream and agent routers |
| `agent_secret.value_enc` | JSON with ChatGPT OAuth tokens | `agent/chatgpt_auth.py:write_tokens` | `agent/chatgpt_auth.py:read_tokens` |

`crypto.encrypt(text)` returns `Fernet.encrypt(text.encode('utf-8'))` as an
ASCII string: a Fernet token (version byte `0x80`, 8-byte timestamp, 16-byte
IV, AES-128-CBC ciphertext, 32-byte HMAC-SHA256), URL-safe base64. Every call
uses a fresh IV, so equal plaintexts encrypt differently. The token's
timestamp is not checked on decrypt (no TTL).

`crypto.decrypt(token)` returns `None` for an empty value or when the token
does not verify (for example after `secret.key` was replaced), and logs
"Stored value could not be decrypted". The callers treat `None` as "not
configured" or "session expired":

* missing API secret: the OAuth callback sends the user back to the login
  page to enter it again;
* missing access token: funds, chart and order routers deactivate the
  `auth_session` row and answer 401; the agent returns 401 without changing
  the row, and the tick socket closes with 4401 without changing it;
* missing ChatGPT tokens: the agent reports "not signed in".

Only `InvalidToken` is caught. A malformed key file can make `get_fernet`
raise during startup; decryption does not silently replace an invalid key.

Hashed, not encrypted: `auth_session.session_hash` (SHA-256 hex of the cookie,
`deps.py:hash_session_value`). It does not depend on `secret.key`.

Stored in plaintext: `broker_config.api_key`, `auth_session.user_id` and
`user_name`, all agent settings, conversations, messages (including tool
result previews with account data), the order audit, and everything in
`agent.db`.

## Master contract files

Module: `backend/app/broker/master_contract.py` (a csv-module port of
OpenAlgo's `master_contract_db.py`). Output: the `symtoken` table and the
`master_contract_status` row (see [core-tables.md](core-tables.md#symtoken)).

### Download

| Item | Value |
| --- | --- |
| URL | `https://v2api.tradesmartonline.in/<EXCH>_symbols.txt.zip` (`symbols_url`) |
| Exchanges, in processing order | `NSE`, `BSE`, `NFO`, `CDS`, `MCX`, `BFO` (`EXCHANGES`) |
| Auth | None (public files) |
| HTTP client | `httpx.Client` with a desktop Chrome `User-Agent` (`config.USER_AGENT`, needed because the host is behind Cloudflare), `follow_redirects=True`, connect timeout 15 s, read timeout 120 s |
| Transfer | Streamed to `tmp/master_<random>/<EXCH>.zip` in 256 KiB chunks; any status other than 200 fails that exchange |
| Archive handling | Opens the zip, picks the member named `<EXCH>_symbols.txt` (case-insensitive), or the only file if there is exactly one. Refuses members larger than 512 MiB (`MAX_EXTRACTED_BYTES`). Extracts to a fixed file name in the work folder (archive paths are not trusted). |
| Text decoding | UTF-8 with optional BOM (`utf-8-sig`), undecodable bytes replaced, read with `csv.DictReader` |
| Failure policy | Each exchange is independent: a failure is logged, recorded in the final message (`failed: BSE`), and the others continue. Any non-empty combined result replaces the whole table, including removal of older rows from failed exchanges. No rows, replacement failure or another outer exception sets `error`. |
| Trigger | After every successful login (`routers/auth.py:tradesmart_callback`), and `POST /api/master-contract/download`; also `download_blocking()` for scripts. One download at a time per process. |

### File format

Each file is a comma-separated text file with a header row. Every line,
including the header, ends with a trailing comma, which gives an extra empty
column that the parser ignores. Headers as seen in the test fixtures
(`backend/tests/test_master_contract.py`):

| Exchange | Header |
| --- | --- |
| NSE | `Exchange,Token,LotSize,Symbol,TradingSymbol,Instrument,TickSize,` |
| BSE | `Exchange,Token,LotSize,Symbol,TradingSymbol,Instrument,TickSize,` |
| NFO | `Exchange,Token,LotSize,Symbol,TradingSymbol,Expiry,Instrument,OptionType,StrikePrice,TickSize,` |
| BFO | `Exchange,Token,LotSize,Symbol,TradingSymbol,Expiry,Instrument,OptionType,Strike,TickSize,` (strike column is `Strike`, not `StrikePrice`) |
| CDS | `Exchange,Token,LotSize,Precision,Multiplier,Symbol,TradingSymbol,Expiry,Instrument,OptionType,StrikePrice,TickSize,` |
| MCX | `Exchange,Token,LotSize,GNGD,Symbol,TradingSymbol,Expiry,Instrument,OptionType,StrikePrice,TickSize,PriceUnits,TradeUnits,DeliveryUnits,` |

Field formats in the files: `Expiry` is `DD-MMM-YYYY` (for example
`23-NOV-2026`), `OptionType` is `CE`, `PE` or `XX` (future or none),
`TickSize` is in rupees, `StrikePrice` is a decimal (futures carry a
placeholder such as `-0.01`).

Columns read: `Token`, `TradingSymbol`, `Symbol`, `LotSize`, `TickSize`,
`Instrument`, `Expiry`, `OptionType`, `StrikePrice` or `Strike`. The rest
(`Exchange`, `Precision`, `Multiplier`, `GNGD`, units) is ignored.

### Parsing rules (file to symtoken)

Common to all exchanges:

* Rows with an empty `Token` or `TradingSymbol` are skipped.
* `brsymbol` = `TradingSymbol`; `token` = `Token`; `exchange` = `brexchange`.
* `lotsize` = `int(float(LotSize))`, `0` if unparsable.
* `tick_size` = `float(TickSize)`, `NULL` if unparsable. No division by 100
  (OpenAlgo divides NSE and BSE ticks; the live TradeSmart files are already
  in rupees).
* Derivatives (`_derivative_row`): `expiry` = `Expiry` reformatted to
  `DD-MMM-YY` uppercase (`format_expiry`, `NULL` if invalid); `strike` =
  `handle_strike` (blank or invalid becomes `-1`, integral values become int);
  `symbol` = `format_derivative_symbol`: `name + expiry without dashes + FUT`
  for futures, `name + expiry without dashes + strike + CE/PE` for options
  (`NIFTY09NOV2626000CE`, `USDINR29DEC2682.5CE`).

Per exchange:

| Exchange | `exchange` value | `symbol` | `instrumenttype` | Notes |
| --- | --- | --- | --- | --- |
| NSE | `NSE`, or `NSE_INDEX` when `Instrument = INDEX` | Trading symbol with `-EQ` or `-BE` removed. Indices: uppercased, spaces and dashes removed, then exact aliases: `NIFTY50` and `NIFTYINDEX` to `NIFTY`; `NIFTYBANK` to `BANKNIFTY`; `NIFTYFIN`, `NIFTYFINSERVICE`, `NIFTYFINANCIALSERVICES` to `FINNIFTY`; `NIFTYNEXT50` to `NIFTYNXT50`; `NIFTYMIDSELECT`, `NIFTYMIDCAPSELECT` to `MIDCPNIFTY`. Other names are kept normalized. | `EQ` for series EQ and BE; otherwise the `Instrument` value as is (`INDEX`, `SG`, `GS`, ...) | `name` = `Symbol`; `expiry` = `''`; `strike` = `-1`. |
| BSE | `BSE` | Trading symbol unchanged | Always `EQ` | Two index rows are appended in code: `SENSEX` (token `1`) and `BANKEX` (token `12`) on `BSE_INDEX`, tick 0.05. |
| NFO | `NFO` | Derivative rule | `FUT` when `OptionType = XX`, else `CE` / `PE` | `name` = `Symbol`. |
| CDS | `CDS` | Derivative rule | `FUT` when `OptionType = XX`; `Instrument` value otherwise, with `OPTCUR` replaced by the `OptionType` | Rows with token `<= 100` (dummy and index rows) and non-numeric tokens are skipped. |
| MCX | `MCX` | Derivative rule | `FUT` when `OptionType = XX`; `Instrument` value otherwise, with `OPTFUT` replaced by the `OptionType` | |
| BFO | `BFO` | Derivative rule, with non-integral option strikes rounded to two decimal places and trailing zeros removed | From the trading symbol suffix: `FUT`, `CE`, `PE`, else `UNKNOWN` | `name` = leading letters of the trading symbol (regex `[A-Za-z]+`), or the complete trading symbol if it has no leading letters. Uses `Strike` if that header exists, otherwise `StrikePrice`. |

After all exchanges: rows are de-duplicated on `(token, exchange)` keeping the
first occurrence, per-exchange counts are computed for
`master_contract_status.exchanges_json`, and the whole `symtoken` table is
replaced in one transaction (delete all, insert in batches of 10,000).

Counts depend on the downloaded files and successful exchanges. The subsequent
status update commits separately from the symbol replacement, so status
counts are not transactionally coupled to the table contents.

## SQLite side files

Both databases run in WAL mode, so `-wal` and `-shm` files sit next to them
while the app (or any reader) has them open. The full `symtoken` replacement
is one large transaction and can substantially grow `smarttrader.db-wal`.
No `journal_size_limit` or explicit checkpoint is configured by the app;
SQLite controls checkpointing, reuse and eventual side-file removal. For a
backup, stop all writers and close database connections before copying, or
use a consistent SQLite backup mechanism. Copying a live `.db` and `-wal`
in separate filesystem operations does not guarantee a consistent snapshot.
Preserve both databases and their matching `secret.key` together.

## In-memory state and caches

REST broker reads fetch funds, quotes, history, positions, holdings and order
books afresh. Tick subscriptions retain the latest changed fields, and agent
history can retain prior tool results on disk; those results are not a cache
used to serve later broker reads. The
following process-local state exists and is lost on restart:

| Object | Location | What it holds | Notes |
| --- | --- | --- | --- |
| `_fernet` | `crypto.py` | The loaded Fernet instance | Created once per process. |
| `agent_db()` | `agent/builder.py` (`lru_cache(maxsize=1)`) | The single Agno `SqliteDb` | Agno also keeps an internal per-instance cache of deserialized history runs. |
| `_flow`, `_counter`, `_lock` | `agent/chatgpt_auth.py` | Current visible ChatGPT device-code sign-in (`LoginFlow`), incrementing flow id and state lock | Visible flow expires after 15 minutes. Starting another flow replaces the reference but does not stop older worker threads; cancelling clears only the reference. Workers can still write tokens. |
| `_running` | `routers/agent.py` | `run_id` to running `Agent`, so `POST /api/agent/chat/{run_id}/cancel` can stop it | Entries removed when the stream ends. |
| `_download_lock` | `broker/master_contract.py` | Whether a master contract download is running | Per process only. |
| `TickState` | `broker/stream.py` | Last LTP and volume per WebSocket subscription | Touchline updates carry only changed fields, so the state is merged per message. |
| `litellm.model_cost` | LiteLLM | Model catalogue used by `builder.available_models` | Comes with the installed LiteLLM version. |
