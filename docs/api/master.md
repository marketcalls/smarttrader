# Master contract and symbol search

Source: `backend/app/routers/master.py`, `backend/app/broker/master_contract.py`.
Tests: `backend/tests/test_master_contract.py`.

Back to the [API index](README.md).

Every route in this router requires a session (router-level `require_session` dependency).

---

## GET /api/master-contract/status

State of the latest symbol master download.

- Auth required: yes
- Request: none

Response `200` (finished):

```json
{
  "status": "success",
  "message": "Loaded 171234 records in 38.6 s",
  "total_records": 171234,
  "exchanges": {
    "NSE": 2954,
    "NSE_INDEX": 128,
    "BSE": 12510,
    "BSE_INDEX": 2,
    "NFO": 98340,
    "CDS": 9821,
    "MCX": 6110,
    "BFO": 41369
  },
  "started_at": "2026-10-14T09:12:42+05:30",
  "finished_at": "2026-10-14T09:13:21+05:30"
}
```

Response `200` (in progress):

```json
{
  "status": "downloading",
  "message": "Downloading NFO",
  "total_records": 0,
  "exchanges": {},
  "started_at": "2026-10-14T09:12:42+05:30",
  "finished_at": null
}
```

| Field | Type | Notes |
| --- | --- | --- |
| `status` | string | `idle`, `downloading`, `success` or `error` |
| `message` | string or null | Progress or result text. Progress values: `Queued`, `Starting download`, `Downloading <EXCH>`, `Processing <EXCH>`, `Saving <n> records`. Results: `Loaded <n> records in <s> s` (with ` (failed: <EXCH>, ...)` when some exchanges failed), `No exchange could be loaded (<details>)`, `Download failed: <error>`, `Previous download was interrupted` |
| `total_records` | integer | Rows in `symtoken` after the last successful download (the previous value stays while downloading) |
| `exchanges` | object | Exchange code to row count, from the last successful download |
| `started_at` | string or null | IST ISO 8601 |
| `finished_at` | string or null | IST ISO 8601; null while downloading |

If the status row is missing (it is created at startup, so this should not happen) the response is
`{"status": "idle", "message": null, "total_records": 0, "exchanges": {}, "started_at": null, "finished_at": null}`.

The frontend polls this endpoint every 2 seconds while `status` is `downloading`.

Errors: `401 Not authenticated`.
Side effects: none.

---

## POST /api/master-contract/download

Starts a fresh download of the symbol master in a background thread. The same download also starts
automatically after every successful login.

- Auth required: yes
- Request: none

Response `202`:

```json
{"status": "downloading"}
```

Errors:

| Status | `detail` | When |
| --- | --- | --- |
| 401 | `Not authenticated` | No active session |
| 409 | `Master contract download already in progress` | The process-wide download lock is held |

Side effects:

- Synchronously sets `master_contract_status` to `downloading`, message `Queued`, `started_at = now`,
  `finished_at = null`, so an immediate status poll already sees the new state.
- Background thread (`master-contract-download`):
  1. For each of `NSE`, `BSE`, `NFO`, `CDS`, `MCX`, `BFO` in that order: downloads
     `https://v2api.tradesmartonline.in/<EXCH>_symbols.txt.zip` to a temp folder under
     `backend/data/tmp/`, extracts `<EXCH>_symbols.txt` (max 512 MB), and parses it with the `csv`
     module (see [broker-integration.md](broker-integration.md#symbol-master-files)).
  2. One failing exchange is logged and skipped.
  3. Drops duplicate `(token, exchange)` pairs (first one wins).
  4. If no rows were parsed: status `error`; the existing `symtoken` table is left untouched.
  5. Otherwise replaces the whole `symtoken` table inside one transaction (delete, then inserts in
     batches of 10 000), and sets status `success` with counts.
     If only some exchanges succeeded, old rows for failed exchanges are removed too; `success`
     can therefore represent a partial download. The message lists the failed exchanges.
  6. Removes the temp folder.
- The lock is in-process memory. On startup a status left at `downloading` by a crashed process is
  rewritten to `error` / `Previous download was interrupted`.
- On total failure, the prior `total_records` and `exchanges` remain in the status row, alongside the
  new error message and timestamps. Table replacement and the final success-status write are separate
  transactions, so a failure between them can leave the new symbol table with stale counts.

---

## GET /api/symbols/search

Searches the local symbol master (`symtoken` table). No broker call.

- Auth required: yes

Query parameters:

| Name | Type | Required | Constraints | Notes |
| --- | --- | --- | --- | --- |
| `q` | string | yes | length 2 to 64 | Trimmed, upper-cased and split on whitespace. Up to the first 5 terms are used; every term must match (AND) as a substring of `symbol`, `brsymbol` or `name` (OR). `%`, `_` and `\` are escaped. |
| `exchange` | string | no | max length 16 | Exact match on `exchange` after trim and upper-case. Blank means all. |

Ordering: exact `symbol` match on the concatenated terms first, then symbols starting with the first
term, then the rest; ties by symbol length, then symbol. At most 50 rows.

Example: `GET /api/symbols/search?q=sbin&exchange=NSE`

Response `200`:

```json
[
  {
    "symbol": "SBIN",
    "brsymbol": "SBIN-EQ",
    "name": "SBIN",
    "exchange": "NSE",
    "token": "3045",
    "expiry": "",
    "strike": -1.0,
    "lotsize": 1,
    "instrumenttype": "EQ",
    "tick_size": 0.05
  }
]
```

Derivative example row:

```json
{
  "symbol": "NIFTY28OCT2625000CE",
  "brsymbol": "NIFTY28OCT26C25000",
  "name": "NIFTY",
  "exchange": "NFO",
  "token": "45211",
  "expiry": "28-OCT-26",
  "strike": 25000.0,
  "lotsize": 65,
  "instrumenttype": "CE",
  "tick_size": 0.05
}
```

| Field | Type | Notes |
| --- | --- | --- |
| `symbol` | string | OpenAlgo-style symbol used everywhere in the app |
| `brsymbol` | string | TradeSmart trading symbol (`tsym`) |
| `name` | string or null | Underlying or company short name |
| `exchange` | string or null | `NSE`, `BSE`, `NFO`, `BFO`, `CDS`, `MCX`, `NSE_INDEX`, `BSE_INDEX` |
| `token` | string or null | Broker instrument token |
| `expiry` | string or null | `DD-MMM-YY` upper case for derivatives, empty string otherwise |
| `strike` | number or null | `-1` for non-options |
| `lotsize` | integer or null | |
| `instrumenttype` | string or null | `EQ`, `FUT`, `CE`, `PE`, `INDEX`, or another NSE series as published |
| `tick_size` | number or null | In rupees |

An empty array means no match (or the master contract has not been downloaded yet).

Errors:

| Status | `detail` | When |
| --- | --- | --- |
| 401 | `Not authenticated` | No active session |
| 422 | validation list | `q` missing, shorter than 2 or longer than 64 characters, or `exchange` longer than 16 |
| 422 | `Query must be at least 2 characters` | `q` has fewer than 2 non-space characters (for example `"a "`) |

Side effects: none.
