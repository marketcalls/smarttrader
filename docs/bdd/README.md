# SmartTrader BDD specifications

Gherkin specifications of SmartTrader as built. The backend code in `backend/app` is the
source of truth; `SPEC.md` and the pytest suite in `backend/tests` were used to confirm
intent. Where the code and `SPEC.md` disagree, the feature files follow the code and the
difference is listed under Coverage gaps and observations.

These files are documentation first. They are written so they can be bound to step
definitions later (for example with pytest-bdd against FastAPI's `TestClient` and the
test doubles already used in `backend/tests`), but no step definitions exist yet.

Verification on 2026-10-14: the existing backend suite passed 94 cases from 86 test
functions using `uv run --offline --no-sync python -m pytest -q` with
`LITELLM_LOCAL_MODEL_COST_MAP=True`. This does not execute these Gherkin specifications.

## Files

| File | Area | Scenarios | Main code |
| --- | --- | --- | --- |
| `authentication.feature` | Broker config save, OAuth login redirect, callback success and failure, session, logout, protected routes, broker session expiry | 24 | `routers/auth.py`, `deps.py`, `broker/tradesmart.py`, `crypto.py`, `config.py` |
| `master_contract.feature` | Auto download after login, manual download, status, parsing conventions, symbol search | 24 | `broker/master_contract.py`, `routers/master.py`, `db.py` |
| `funds.feature` | Funds and P&L from Limits and PositionBook, errors, dashboard formatting | 11 | `routers/funds.py`, `broker/tradesmart.py` |
| `chart.feature` | Intervals, history candles, look back windows, errors | 16 | `routers/chart.py`, `broker/history.py` |
| `stream.feature` | Tick WebSocket: origin and session checks, subscribe, switch (unsubscribe), relay, heartbeat, broker failures, frontend reconnect | 20 | `routers/stream.py`, `broker/stream.py`, frontend bundle |
| `orders.feature` | Place, modify, cancel, close position, order and position lines, validation, broker rejection | 31 | `routers/orders.py`, `broker/order_service.py`, `broker/orders.py` |
| `agent.feature` | Agent settings, ChatGPT sign-in, streaming chat, tools, provider errors, conversations, risk limits, order approval and audit | 60 | `routers/agent.py`, `agent/*.py` |
| `platform.feature` | Health, SPA fallback, CORS, encryption at rest, log redaction, startup | 9 | `main.py`, `config.py`, `crypto.py`, `db.py` |
| `research.feature` | FinEdge API key save, test and remove, research tools, statements, ratios, quotes, peers, comparisons, filings, research reports and buy or sell research views | 29 | `routers/agent.py`, `agent/finedge.py`, `agent/research.py`, `agent/builder.py` |
| Total | | 224 | |

A Scenario Outline counts as one scenario in these numbers; its Examples rows are the
individual cases.

## How the features are organised

* One feature file per functional area. Each file starts with a short user story, a
  `Code:` line naming the backend modules it describes, and a `Background` that sets up
  the running backend, an active SmartTrader session where needed, the symbol master
  rows the scenarios use, and test doubles for TradeSmart and the LLM provider.
* Scenarios are grouped with comment banners in the order a user meets them: setup,
  happy path, variations, then failures.
* Steps name real HTTP methods, paths, JSON field names, status codes and `detail`
  strings exactly as the code produces them. Broker calls are named by their Noren
  endpoint (`GenAcsTok`, `Limits`, `PositionBook`, `TPSeries`, `EODChartData`,
  `GetQuotes`, `PlaceOrder`, `ModifyOrder`, `CancelOrder`, `OrderBook`, `TradeBook`,
  `Holdings`) and by the `jData` fields sent.
* FastAPI request validation failures are written as "a list of validation errors naming
  `<field>`" because FastAPI returns `detail` as a list in that case, while handler errors
  return `detail` as a string.
* Frontend behaviour (`@ux`) is taken from the built bundle in `frontend/dist/assets`
  because the frontend source is not in the repository. These scenarios are the least
  precise and are not testable with the backend suite.

### Test data and placeholders

No real credentials, tokens or user ids appear in these files. The values used are
placeholders or the fake values already used in `backend/tests`:

| Placeholder | Meaning |
| --- | --- |
| `DEMO_KEY`, `DEMO_SECRET` | TradeSmart app api key and secret |
| `<ACCESS_TOKEN>` | Broker access token returned by GenAcsTok |
| `<AUTH_CODE>` | OAuth authorization code |
| `AB1234`, `Test User` | Broker user id and name (test fixture values) |
| `<MODEL>` | Any entry of `GET /api/agent/settings` `models` |
| `<conversation_id>` | Id returned in the chat `start` frame |
| `<broker rejection reason>` | Any `emsg` text TradeSmart returns; the backend passes it through verbatim |
| `me@example.com` | ChatGPT account email in the ID token test double |
| `fe-test-key-12345678` | FinEdge API key test fixture from `tests/test_research.py` (masked as `********5678`) |
| `<FINEDGE_API_KEY>` | A real FinEdge API key; never written into these files |

## Tagging conventions

| Tag | Meaning |
| --- | --- |
| `@auth`, `@master_contract`, `@funds`, `@chart`, `@stream`, `@orders`, `@agent`, `@platform`, `@research` | Area tag, set once at Feature level so every scenario inherits it |
| `@smoke` | Candidate happy path for a future BDD smoke suite; `--tags @smoke` is not currently an executable repository command |
| `@negative` | Validation errors, broker rejections, expired sessions, network failures and other failure paths |
| `@ux` | Frontend behaviour read from the built bundle (no source available) |
| `@req-<AREA>-<N>` | Requirement id from `docs/prd/README.md`. Prefixes: `AUTH`, `MC`, `DASH`, `CHART`, `ORD`, `AGENT`, `UX`, `NFR`. One scenario may carry several ids, for example `@req-AUTH-7 @req-DASH-4` for a broker session expiry seen from the funds route |

Every `@req-*` tag is a user story or non-functional requirement id defined in the PRD.
When a PRD story is added, changed or removed, update the tags here to match. Live quotes
are product scope of the chart, so `stream.feature` scenarios carry `CHART-4` (see live
prices), plus `NFR-5` for the origin check. FinEdge research belongs to the agent area in the PRD
(`AGENT-15` to `AGENT-20`, `NFR-14`) but has its own `research.feature` file and `@research` tag.

### PRD requirements without a scenario

These PRD ids have no BDD scenario yet. Most are frontend behaviour (no source available
to test against) or are marked Not implemented in the PRD.

| Kind | PRD ids |
| --- | --- |
| Frontend only | AUTH-8, MC-7, DASH-1, DASH-5, CHART-1, CHART-5, CHART-6, CHART-7, CHART-8, CHART-9, AGENT-7, UX-1, UX-2, UX-3, UX-4 |
| Not implemented | MC-8, DASH-6, ORD-11, NFR-13 |
| Non-functional, not expressed as behaviour | NFR-1, NFR-2, NFR-7, NFR-11, NFR-12 |

## Traceability

Code paths are relative to `backend/app/`. Test references are
`<file in backend/tests>::<test function>`. "partial" means the test exercises only part
of the scenario, uses a unit test where the scenario promises route behavior, or covers
only some Examples rows. Unit-only scenarios can be fully covered by unit tests.
"not covered" means no existing pytest exercises it. Coverage is a manual mapping of
test assertions to documented behavior, not a measured line or branch coverage report.

### authentication.feature

| Scenario | Tags | Code | Existing pytest |
| --- | --- | --- | --- |
| Reading the configuration before anything is saved | AUTH-1 | `routers/auth.py` get_config | not covered |
| Saving the configuration returns the TradeSmart authorize URL | AUTH-1 | `routers/auth.py` login, `broker/tradesmart.py` build_authorize_url, `crypto.py` | partial: test_checksum.py::test_authorize_url_encodes_client_id |
| The saved secret is never returned to the browser | AUTH-1, NFR-3, NFR-4 | `routers/auth.py` get_config | not covered |
| A blank secret keeps the stored secret | AUTH-1 | `routers/auth.py` login | not covered |
| Leading and trailing spaces are trimmed before saving | AUTH-1 | `routers/auth.py` login | not covered |
| Invalid configuration is rejected and nothing is saved | AUTH-1 | `routers/auth.py` login, _is_http_url | not covered |
| Missing required JSON fields fail request validation | AUTH-1 | `routers/auth.py` LoginRequest | not covered |
| The client id in the authorize URL is URL encoded | AUTH-2 | `broker/tradesmart.py` build_authorize_url | test_checksum.py::test_authorize_url_encodes_client_id |
| Successful callback creates a session, sets the cookie and starts the master contract download | AUTH-3 | `routers/auth.py` tradesmart_callback, `broker/tradesmart.py` generate_access_token, `deps.py` | partial: test_callback.py::test_success_sets_cookie_creates_session_and_starts_download (GenAcsTok is stubbed; request headers and exchange payload are not exercised) |
| Token and user id fallbacks in the GenAcsTok answer | AUTH-3 | `broker/tradesmart.py` generate_access_token | not covered |
| The GenAcsTok checksum is SHA-256 of api_key, api_secret and code in that order | AUTH-3 | `broker/tradesmart.py` make_checksum | test_checksum.py::test_checksum_matches_docs_example, ::test_checksum_order_is_key_secret_code, ::test_checksum_is_lowercase_hex |
| A new login deactivates the previous session | AUTH-3 | `routers/auth.py` tradesmart_callback | partial: test_callback.py::test_success_sets_cookie_creates_session_and_starts_download (active row count checked; old-cookie session endpoint not asserted) |
| Where the callback redirects depends on how the frontend is served | AUTH-3, UX-5 | `config.py` frontend_url, `routers/auth.py` _login_error | test_callback.py::test_no_code_redirects_to_dev_login, ::test_env_override_for_dev_frontend, ::test_relative_redirect_when_dist_exists |
| A failed callback redirects to the login page with a readable error and creates no session | AUTH-4 | `routers/auth.py` tradesmart_callback, `broker/tradesmart.py` generate_access_token, _parse_json | partial: test_callback.py::test_no_code_redirects_to_dev_login, ::test_bogus_code_redirects_with_broker_message (2 of 13 rows) |
| A very long broker error is cut to 300 characters in the redirect | AUTH-4 | `routers/auth.py` _login_error | not covered |
| A whitespace only code counts as no code | AUTH-4 | `routers/auth.py` tradesmart_callback | not covered |
| A login while a master contract download is already running does not start a second one | MC-1 | `routers/auth.py`, `broker/master_contract.py` start_download_in_background | not covered |
| The session endpoint reports the logged in user | AUTH-5 | `routers/auth.py` get_session, `config.py` to_ist_iso | not covered |
| The session endpoint never returns 401, it reports unauthenticated | AUTH-5 | `routers/auth.py` get_session, `deps.py` find_active_session | not covered |
| Logging out deactivates the session and deletes the cookie | AUTH-6 | `routers/auth.py` logout | not covered |
| Logging out without a session is harmless | AUTH-6 | `routers/auth.py` logout | not covered |
| Protected endpoints reject requests without an active session | AUTH-5 | `deps.py` require_session, all routers | partial: test_history.py::test_history_route_requires_session (chart rows only) |
| An expired broker session deactivates the SmartTrader session | AUTH-7 | `broker/tradesmart.py` post_authenticated, `routers/funds.py`, `routers/chart.py`, `routers/orders.py` | partial: test_funds.py::test_fetch_funds_session_expired, test_history.py::test_expired_session_is_reported (client level; route deactivation not covered) |
| A stored access token that can no longer be decrypted ends the session | AUTH-7 | `crypto.py` decrypt, `routers/funds.py` _expire | not covered |

### master_contract.feature

| Scenario | Tags | Code | Existing pytest |
| --- | --- | --- | --- |
| The download starts automatically after a successful login | MC-1 | `routers/auth.py`, `broker/master_contract.py` start_download_in_background | partial: test_callback.py::test_success_sets_cookie_creates_session_and_starts_download (start only, not the status) |
| A full download of all six exchanges succeeds | MC-2 | `broker/master_contract.py` _run_download, `routers/master.py` master_status | not covered |
| Progress is reported while the download runs | MC-2 | `broker/master_contract.py` _fetch_and_parse, _set_status | not covered |
| One failing exchange is skipped and the rest are loaded | MC-4, NFR-8 | `broker/master_contract.py` _run_download | not covered |
| A bad archive for one exchange is skipped | MC-4, NFR-8 | `broker/master_contract.py` _extract_symbols_file | not covered |
| When no exchange loads the status is error and the previous symbols are kept | MC-4, NFR-8 | `broker/master_contract.py` _run_download | not covered |
| The symbol table is swapped in one transaction | MC-4, NFR-8 | `broker/master_contract.py` _replace_symtoken, `db.py` WAL | not covered |
| Duplicate token and exchange pairs keep the first occurrence | MC-5 | `broker/master_contract.py` _run_download | not covered |
| A manual download can be started from the dashboard | MC-3 | `routers/master.py` master_download | not covered |
| A second download is refused while one is running | MC-3 | `routers/master.py` master_download | not covered |
| A download interrupted by a process restart is marked as failed at startup | MC-4, NFR-8 | `db.py` init_db | not covered |
| A fresh database reports an idle master contract | MC-2 | `db.py` init_db, `routers/master.py` master_status | not covered |
| Broker trading symbols are mapped to SmartTrader symbols | MC-5 | `broker/master_contract.py` parse_nse, parse_bse, parse_nfo, parse_bfo, parse_cds, parse_mcx | test_master_contract.py::test_parse_nse, ::test_parse_bse_adds_indices, ::test_parse_nfo, ::test_parse_bfo_strike_column_and_trailing_comma, ::test_parse_cds_filters_small_tokens_and_maps_options, ::test_parse_mcx |
| The BSE file adds the SENSEX and BANKEX indices | MC-5 | `broker/master_contract.py` parse_bse, BSE_INDEX_ROWS | test_master_contract.py::test_parse_bse_adds_indices |
| Dummy, blank and incomplete rows are skipped | MC-5 | `broker/master_contract.py` parsers | test_master_contract.py::test_parse_cds_filters_small_tokens_and_maps_options, ::test_blank_rows_are_skipped |
| Expiry and strike formatting | MC-5 | `broker/master_contract.py` format_expiry, handle_strike | test_master_contract.py::test_helpers |
| Searching by symbol returns matching instruments with all fields | MC-6 | `routers/master.py` search_symbols | not covered |
| Results are ranked exact match first, then prefix, then contains, shorter symbols first | MC-6 | `routers/master.py` search_symbols | not covered |
| Every search term must match the symbol, broker symbol or name | MC-6 | `routers/master.py` search_symbols | not covered |
| The exchange filter is case insensitive | MC-6 | `routers/master.py` search_symbols | not covered |
| At most 50 results are returned | MC-6 | `routers/master.py` SEARCH_LIMIT | not covered |
| LIKE wildcards in the query are matched literally | MC-6 | `routers/master.py` _like_escape | not covered |
| Searching an empty master contract returns an empty list | MC-6 | `routers/master.py` search_symbols | not covered |
| Invalid search queries are rejected | MC-6 | `routers/master.py` search_symbols | not covered |

### funds.feature

| Scenario | Tags | Code | Existing pytest |
| --- | --- | --- | --- |
| Funds are computed from Limits and PositionBook | DASH-2 | `routers/funds.py`, `broker/tradesmart.py` compute_funds, fetch_funds | partial: test_funds.py::test_compute_funds_basic (unit, not route) |
| Limits and PositionBook are called with the Noren request shape | DASH-2 | `broker/tradesmart.py` fetch_funds, post_authenticated | test_funds.py::test_fetch_funds_sends_jdata_and_bearer |
| Unrealized P&L falls back to (lp - netavgprc) x netqty x prcftr when urmtom is zero | DASH-2 | `broker/tradesmart.py` position_pnl | test_funds.py::test_unrealized_fallback_when_urmtom_zero, ::test_unrealized_fallback_defaults_prcftr_to_one, ::test_no_fallback_when_flat |
| A PositionBook without positions means zero P&L, not an error | DASH-2 | `broker/tradesmart.py` fetch_funds, compute_funds | test_funds.py::test_non_list_positionbook_means_zero_pnl, ::test_fetch_funds_with_no_positions, ::test_fetch_funds_positionbook_non_json_means_zero_pnl |
| Missing and unparsable numbers count as zero | DASH-2 | `broker/tradesmart.py` to_float | test_funds.py::test_missing_and_garbage_fields_count_as_zero |
| Broker errors on Limits become 502 with the broker message | DASH-4 | `routers/funds.py`, `broker/tradesmart.py` fetch_funds | partial: test_funds.py::test_fetch_funds_limits_error_raises_broker_error, ::test_fetch_funds_limits_non_json_raises_broker_error (502 mapping not covered) |
| Network failure to TradeSmart | DASH-4 | `routers/funds.py` | not covered |
| An expired broker session on either call ends the SmartTrader session | AUTH-7, DASH-4 | `routers/funds.py` _expire, `broker/tradesmart.py` | partial: test_funds.py::test_fetch_funds_session_expired (client level) |
| Funds require a SmartTrader session | AUTH-5 | `deps.py` require_session | not covered |
| The dashboard shows funds as Indian rupees and can be refreshed | DASH-2, DASH-3 | frontend bundle `index-*.js` | not covered (no frontend tests) |
| A 401 from any API call sends the user back to the login page | AUTH-5 | frontend bundle `index-*.js` | not covered (no frontend tests) |

### chart.feature

| Scenario | Tags | Code | Existing pytest |
| --- | --- | --- | --- |
| The supported intervals are listed | CHART-2 | `routers/chart.py` chart_intervals, `broker/history.py` INTERVALS | partial: test_history.py::test_history_route_looks_up_the_symbol_and_returns_candles (last entry only) |
| Intraday history is fetched from TPSeries with the instrument token | CHART-3 | `routers/chart.py` chart_history, `broker/history.py` fetch_history | test_history.py::test_history_route_looks_up_the_symbol_and_returns_candles, ::test_intraday_request_uses_tpseries_with_token_and_minutes |
| Daily history is fetched from EODChartData with EXCH:BRSYMBOL | CHART-3 | `broker/history.py` fetch_history, parse_candles | partial: test_history.py::test_daily_request_uses_eodchartdata_symbol, ::test_parse_daily_uses_ssboe_and_accepts_json_strings (broker/parser tests only; daily HTTP route not exercised) |
| The default look back window depends on the interval | CHART-3 | `broker/history.py` lookback_days | partial: test_history.py::test_lookback_and_exchange_helpers (helper checks 1m, 5m, 15m, 30m, 1h and D; HTTP mapping and remaining intervals not covered) |
| An explicit days parameter overrides the default window | CHART-3 | `routers/chart.py` chart_history | not covered |
| Index pseudo exchanges fetch data from their parent cash exchange | CHART-3 | `broker/history.py` data_exchange | partial: test_history.py::test_lookback_and_exchange_helpers, ::test_intraday_request_uses_tpseries_with_token_and_minutes (exchange helper and broker client only; route rows not all exercised) |
| Candles are sorted, de-duplicated and empty rows are dropped | CHART-3 | `broker/history.py` parse_candles | partial: test_history.py::test_parse_intraday_sorts_dedupes_and_skips_empty_rows (parser only; HTTP behavior not exercised) |
| Intraday volume glitches near the close are clamped | CHART-3 | `broker/history.py` _repair_intraday_volume | partial: test_history.py::test_closing_volume_glitch_is_clamped (parser only; HTTP behavior not exercised) |
| No data from the broker is an empty chart, not an error (for example a holiday or a closed market) | CHART-3 | `broker/history.py` parse_candles | partial: test_history.py::test_parse_no_data_is_empty_and_other_errors_raise (parse level) |
| Unsupported intervals are rejected before any broker call | CHART-2 | `routers/chart.py` chart_history | partial: test_history.py::test_history_route_rejects_unknown_symbol_and_interval (7m, status only) |
| A symbol that is not in the master contract returns 404 | CHART-3 | `routers/chart.py` chart_history | partial: test_history.py::test_history_route_rejects_unknown_symbol_and_interval (status only) |
| A master contract row without a token is treated as missing | CHART-3 | `routers/chart.py` chart_history | not covered |
| Query parameter validation | CHART-3 | `routers/chart.py` Query constraints | not covered |
| Broker errors become 502 with a readable detail | CHART-3 | `routers/chart.py`, `broker/history.py` parse_candles | partial: test_history.py::test_parse_no_data_is_empty_and_other_errors_raise (BrokerError raised; 502 mapping not covered) |
| An expired broker session on history ends the SmartTrader session | CHART-3, AUTH-7 | `routers/chart.py`, `broker/tradesmart.py` post_authenticated | partial: test_history.py::test_expired_session_is_reported (client level) |
| Chart endpoints require a SmartTrader session | CHART-3 | `deps.py` require_session | test_history.py::test_history_route_requires_session |

### stream.feature

| Scenario | Tags | Code | Existing pytest |
| --- | --- | --- | --- |
| Only this app's origins may open the tick socket | NFR-5, CHART-4 | `routers/stream.py` origin_allowed, ticks | partial: test_stream.py::test_origin_check, ::test_ws_rejects_foreign_origin_and_missing_session (localhost origin and close reasons are not asserted) |
| A socket without an active session is accepted and then closed with 4401 | CHART-4 | `routers/stream.py` ticks, _session_credentials | partial: test_stream.py::test_ws_rejects_foreign_origin_and_missing_session (no cookie row only) |
| Connecting authenticates the broker stream with the stored token | CHART-4 | `broker/stream.py` relay_ticks | test_stream.py::test_relay_connects_subscribes_and_forwards_only_its_instrument |
| The broker acknowledgement status is matched case insensitively | CHART-4 | `broker/stream.py` relay_ticks | partial: test_stream.py::test_relay_connects_subscribes_and_forwards_only_its_instrument ("OK" only) |
| Subscribing to an instrument relays its ticks | CHART-4 | `broker/stream.py` relay_ticks from_browser, from_broker | partial: test_stream.py::test_relay_connects_subscribes_and_forwards_only_its_instrument (relay covered; subscribed frame and all tick fields are not asserted) |
| Feeds for other instruments are not forwarded | CHART-4 | `broker/stream.py` from_broker | test_stream.py::test_relay_connects_subscribes_and_forwards_only_its_instrument |
| Partial touchline feeds are merged with the last known values | CHART-4 | `broker/stream.py` merge_tick | test_stream.py::test_merge_tick_keeps_fields_missing_from_partial_feeds |
| Index instruments subscribe on the parent exchange key | CHART-4 | `routers/stream.py` _lookup, `broker/history.py` data_exchange | not covered |
| Switching instruments unsubscribes the previous one | CHART-4 | `broker/stream.py` from_browser | not covered |
| Re-subscribing the same instrument does not unsubscribe it | CHART-4 | `broker/stream.py` from_browser | not covered |
| Closing the browser socket is the only way to unsubscribe completely | CHART-4 | `broker/stream.py` relay_ticks, `routers/stream.py` | not covered |
| Messages other than subscribe are ignored | CHART-4 | `broker/stream.py` from_browser | not covered |
| Subscribing to an unknown symbol reports an error and keeps the socket open | CHART-4 | `broker/stream.py` from_browser | not covered |
| The broker socket is kept alive with a heartbeat | CHART-4 | `broker/stream.py` heartbeat | not covered |
| Broker stream failures are reported to the browser and the socket closes with 1011 | CHART-4 | `broker/stream.py` relay_ticks, `routers/stream.py` ticks | partial: test_stream.py::test_relay_reports_refused_session (StreamError raised; frame and close code not covered) |
| The broker closing the stream mid session is reported | CHART-4 | `broker/stream.py` from_broker, `routers/stream.py` | not covered |
| Non JSON broker frames are skipped | CHART-4 | `broker/stream.py` from_broker | not covered |
| The chart reconnects with exponential back off | CHART-4 | frontend bundle `TradingPage-*.js` | not covered (no frontend tests) |
| A 4401 close stops reconnecting | CHART-4 | frontend bundle `TradingPage-*.js` | not covered (no frontend tests) |
| Switching symbols reuses the open socket | CHART-4 | frontend bundle `TradingPage-*.js` | not covered (no frontend tests) |

### orders.feature

| Scenario | Tags | Code | Existing pytest |
| --- | --- | --- | --- |
| Placing a limit order | ORD-1 | `routers/orders.py` place, `broker/order_service.py` place, `broker/orders.py` place_payload | partial: test_orders.py::test_place_payload_maps_fields_and_encodes_ampersand (payload only, not route) |
| A market order is sent as a protected limit order | ORD-3 | `broker/order_service.py` place, `broker/orders.py` protected_price | partial: test_order_service.py::test_market_order_goes_out_as_protected_limit (service level) |
| Market price protection slabs and tick rounding against the trader | ORD-3 | `broker/orders.py` protection_pct, round_to_tick, protected_price | partial: test_orders.py::test_market_price_protection_slabs_and_rounding (4 of 7 rows) |
| Stop orders go out as SL-LMT with the trigger | ORD-1 | `broker/order_service.py` place, `broker/orders.py` place_payload | partial: test_orders.py::test_place_payload_maps_fields_and_encodes_ampersand (SL only; SL-M not covered) |
| Product codes are mapped to Noren codes | ORD-1 | `broker/orders.py` PRODUCTS | partial: test_orders.py::test_place_payload_maps_fields_and_encodes_ampersand (MIS, CNC) |
| Symbols with an ampersand are URL encoded for PlaceOrder | ORD-1 | `broker/orders.py` place_payload | test_orders.py::test_place_payload_maps_fields_and_encodes_ampersand |
| Futures quantities must be a multiple of the lot size | ORD-4 | `broker/order_service.py` place | not covered (only the negative case) |
| Orders that cannot be valid are rejected with 422 and nothing is sent | ORD-4 | `broker/order_service.py` place, `routers/orders.py` _call | partial: test_order_service.py::test_validation_messages (3 of 10 rows, service level) |
| A market order without a live price is refused | ORD-4 | `broker/order_service.py` place | not covered |
| Request schema validation rejects bad numbers before any broker call | ORD-4 | `routers/orders.py` PlaceBody | not covered |
| An order rejected by TradeSmart returns 502 with the broker message | ORD-10 | `broker/orders.py` _order_number, `routers/orders.py` _call | not covered |
| A failing quote request blocks the order | ORD-10 | `broker/order_service.py` ltp | not covered |
| Network failure while placing an order | ORD-10 | `routers/orders.py` _call | not covered |
| An expired broker session while trading ends the SmartTrader session | ORD-10, AUTH-7 | `routers/orders.py` _call | not covered |
| Open orders and the net position for one instrument | ORD-5, ORD-8 | `routers/orders.py` symbol_state, `broker/order_service.py` symbol_state, _order_line | partial: test_order_service.py::test_symbol_state_lists_open_orders_and_position_with_pnl_inputs (service only; HTTP response and every response field are not asserted) |
| A partly filled order shows only its remaining quantity | ORD-5, ORD-8 | `broker/order_service.py` _order_line | not covered |
| Flat positions, other instruments and closed orders are left out | ORD-5, ORD-8 | `broker/order_service.py` symbol_state | partial: test_order_service.py::test_symbol_state_lists_open_orders_and_position_with_pnl_inputs (closed order only) |
| An empty order book or position book is not an error | ORD-5, ORD-8 | `broker/order_service.py` symbol_state | not covered |
| Order lines for an unknown symbol | ORD-5, ORD-8 | `broker/order_service.py` instrument | not covered |
| Dragging a limit order line changes its price and keeps its quantity | ORD-6 | `routers/orders.py` modify, `broker/order_service.py` modify, `broker/orders.py` modify_order | partial: test_order_service.py::test_limit_modify_and_completed_order_guard (service only; HTTP response not exercised) |
| Dragging a stop order line moves the trigger and keeps the limit gap | ORD-6 | `broker/order_service.py` modify | partial: test_order_service.py::test_dragging_a_stop_line_moves_trigger_and_keeps_the_gap (service only; HTTP response not exercised) |
| An explicit trigger price and quantity are used as given | ORD-6 | `broker/order_service.py` modify | not covered |
| Orders that cannot be modified | ORD-6 | `broker/order_service.py` modify, `broker/orders.py` find_order | partial: test_order_service.py::test_limit_modify_and_completed_order_guard (complete order only) |
| Modify body validation | ORD-6 | `routers/orders.py` ModifyBody | not covered |
| A modification rejected by TradeSmart | ORD-6 | `broker/orders.py` modify_order, _order_number | not covered |
| Cancelling an open order | ORD-7 | `routers/orders.py` cancel, `broker/orders.py` cancel_order | not covered |
| A cancellation rejected by TradeSmart | ORD-7 | `broker/orders.py` cancel_order | not covered |
| Closing a position sends an opposite protected market order | ORD-9 | `routers/orders.py` close_position | not covered |
| Close position trusts the client side and quantity | ORD-9 | `routers/orders.py` close_position | not covered |
| Close position body validation | ORD-9 | `routers/orders.py` ClosePositionBody, `broker/order_service.py` place | not covered |
| Every chart order action is confirmed before it is sent | ORD-2, ORD-6, ORD-7 | frontend bundle `TradingPage-*.js`, SPEC.md | not covered (no frontend tests) |

### agent.feature

| Scenario | Tags | Code | Existing pytest |
| --- | --- | --- | --- |
| Default settings are created on first use | AGENT-2 | `routers/agent.py` _settings, get_settings, `agent/builder.py` available_models | partial: test_agent.py::test_settings_roundtrip_and_validation |
| Saving settings trims the instructions | AGENT-2 | `routers/agent.py` put_settings | test_agent.py::test_settings_roundtrip_and_validation |
| Empty reasoning effort and empty instructions are stored as null | AGENT-2 | `routers/agent.py` put_settings | not covered |
| Invalid settings are rejected | AGENT-2 | `routers/agent.py` put_settings, SettingsBody | partial: test_agent.py::test_settings_roundtrip_and_validation (unknown model, status only) |
| The status endpoint combines sign-in state, settings and the trading switch | AGENT-1, AGENT-8 | `routers/agent.py` status | not covered |
| Starting the ChatGPT sign-in returns a device code | AGENT-1 | `routers/agent.py` chatgpt_login_start, `agent/chatgpt_auth.py` start_login | not covered |
| Polling the sign-in flow | AGENT-1 | `agent/chatgpt_auth.py` current_flow, _run_flow | not covered |
| No sign-in flow in progress | AGENT-1 | `routers/agent.py` _flow_dict | not covered |
| The device code request fails | AGENT-1 | `routers/agent.py` chatgpt_login_start | not covered |
| Cancelling the flow and signing out | AGENT-1 | `routers/agent.py` chatgpt_login_cancel, chatgpt_logout | not covered |
| A cancelled sign-in worker can save tokens after sign-out | AGENT-1 | `agent/chatgpt_auth.py` cancel_login, _run_flow, write_tokens | not covered |
| ChatGPT tokens are stored encrypted and the status reads the ID token claims | AGENT-1, NFR-3, NFR-4 | `agent/chatgpt_auth.py` write_tokens, account_status | partial: test_agent.py::test_tokens_are_encrypted_and_status_reads_the_id_token (account_status helper only; GET /api/agent/status not exercised) |
| The model library never starts an interactive console login | AGENT-1, NFR-3 | `agent/chatgpt_auth.py` install | test_agent.py::test_litellm_authenticator_uses_the_table_and_never_blocks |
| Testing the configured model | AGENT-3 | `routers/agent.py` test_model, _provider_message | not covered |
| Chat requires a ChatGPT sign-in | AGENT-4 | `routers/agent.py` _credentials | partial: test_agent.py::test_chat_requires_chatgpt_sign_in (409 asserted; exact detail not asserted) |
| Chat with a broker token that cannot be decrypted | AGENT-4 | `routers/agent.py` _credentials | not covered |
| Chat message validation | AGENT-4 | `routers/agent.py` ChatBody | not covered |
| A chat turn streams frames and saves the transcript | AGENT-4, AGENT-6 | `routers/agent.py` chat, _stream_run | partial: test_agent.py::test_chat_streams_frames_and_saves_the_transcript (fake agent events and transcript covered; SSE headers and every usage field not asserted) |
| A follow up message continues the same conversation | AGENT-4, AGENT-6 | `routers/agent.py` chat, `agent/builder.py` build_agent | not covered |
| The conversation title is the first 60 characters of the first message | AGENT-4, AGENT-6 | `routers/agent.py` chat | partial: test_agent.py::test_chat_streams_frames_and_saves_the_transcript (short title only) |
| Whitespace-only chat text passes raw length validation | AGENT-4, AGENT-6 | `routers/agent.py` ChatBody, chat | not covered |
| An unknown conversation id creates a new conversation | AGENT-4, AGENT-6 | `routers/agent.py` chat | not covered |
| Without trading enabled the agent gets only the ten read tools and a read only rule | AGENT-5, AGENT-13 | `routers/agent.py` _agent_for, `agent/tools.py` build_tools, `agent/builder.py` | partial: test_agent.py::test_chat_streams_frames_and_saves_the_transcript (first two tool names) |
| Tool failures are returned to the model as readable text | AGENT-5, AGENT-13 | `agent/tools.py` guarded and each tool | not covered |
| Empty broker data is reported plainly, for example when the market is closed | AGENT-5, AGENT-13 | `agent/tools.py` get_history, get_technicals, get_positions, get_order_book, get_trade_book, get_holdings | not covered |
| Indicator math used by get_technicals | AGENT-5, AGENT-13 | `agent/tools.py` _sma, _ema, _rsi, _atr | test_agent.py::test_indicator_math |
| Tool arguments that arrive only in the done events are recovered | AGENT-4 | `agent/litellm_patches.py` | test_agent.py::test_tool_arguments_sent_only_in_done_events_are_recovered, ::test_streamed_arguments_are_left_alone |
| LLM provider errors end the stream with a readable message | AGENT-14 | `routers/agent.py` _provider_message, _stream_run | not covered |
| A provider error message is cut to 400 characters | AGENT-14 | `routers/agent.py` _provider_message | not covered |
| A running turn can be cancelled | AGENT-4 | `routers/agent.py` cancel_chat, _running | not covered |
| Cancelling an unknown run | AGENT-4 | `routers/agent.py` cancel_chat | not covered |
| Closing the browser request cancels the run | AGENT-4 | `routers/agent.py` _stream_run | not covered |
| Task cancellation persists output without guaranteeing a done frame | AGENT-4 | `routers/agent.py` _stream_run | not covered |
| Renaming and deleting a conversation | AGENT-6 | `routers/agent.py` rename_conversation, delete_conversation | partial: test_agent.py::test_chat_streams_frames_and_saves_the_transcript (delete only) |
| Whitespace-only rename produces an empty title | AGENT-6 | `routers/agent.py` RenameBody, rename_conversation | not covered |
| Transcript deletion succeeds when Agno cleanup fails | AGENT-6 | `routers/agent.py` delete_conversation | not covered |
| A later broker login can read the installation's earlier transcripts and audit | AGENT-6, AGENT-12 | `routers/agent.py` list_conversations, get_conversation, order_audit | not covered |
| Conversation not found or invalid title | AGENT-6 | `routers/agent.py` get_conversation, rename_conversation, delete_conversation | not covered |
| Risk settings default to trading off with conservative limits | AGENT-8, AGENT-9 | `routers/agent.py` get_risk, `agent/trading.py` risk_settings | not covered |
| Allowed exchanges are stored in canonical order without duplicates | AGENT-8, AGENT-9 | `agent/trading.py` save_risk_settings | not covered |
| Invalid risk settings are rejected | AGENT-8, AGENT-9 | `routers/agent.py` put_risk, RiskBody | not covered |
| Every order tool requires confirmation | AGENT-10 | `agent/trading.py` build_order_tools | test_orders.py::test_order_tools_require_confirmation |
| A proposed order pauses the run for approval | AGENT-10 | `routers/agent.py` _stream_run (RunPaused), `agent/trading.py` order_summary, audit | partial: test_orders.py::test_chat_pauses_for_approval_then_resumes (fake agent; summary prefix and pause state covered, trading-on configuration not exercised) |
| The user approves or rejects the proposed order | AGENT-10 | `routers/agent.py` confirm | partial: test_orders.py::test_chat_pauses_for_approval_then_resumes (both parameters) (both decisions exercised with a fake agent; approved tool body and broker mutation are not executed) |
| A decision missing for a requirement counts as a rejection | AGENT-10 | `routers/agent.py` confirm | not covered |
| An approval can only be used while it is pending | AGENT-10 | `routers/agent.py` confirm, chat (expiry of pending) | partial: test_orders.py::test_chat_pauses_for_approval_then_resumes (already decided row only) |
| Confirm body validation | AGENT-10 | `routers/agent.py` ConfirmBody | not covered |
| The risk check runs again inside the approved tool and refuses without sending anything | AGENT-11 | `agent/trading.py` place_order, refuse | partial: test_orders.py::test_risk_check_refuses_and_sends_nothing (5 of 12 rows) |
| Trading switched off between proposal and approval still blocks the order | AGENT-11 | `agent/trading.py` place_order | partial: test_orders.py::test_risk_check_refuses_and_sends_nothing (tool body with trading off) |
| An approved market order is sent as a protected limit and audited | AGENT-11 | `agent/trading.py` place_order | partial: test_orders.py::test_approved_market_order_is_sent_as_protected_limit_and_duplicates_are_blocked (tool invocation covered; payload mapping, all audit fields and note are not asserted) |
| The same order within 60 seconds is refused as a duplicate | AGENT-11, AGENT-9 | `agent/trading.py` _recent_duplicate | partial: test_orders.py::test_approved_market_order_is_sent_as_protected_limit_and_duplicates_are_blocked (immediate duplicate covered; 60-second time boundary not tested) |
| The daily agent order limit is enforced per IST day | AGENT-11, AGENT-9 | `agent/trading.py` _placed_today | partial: test_orders.py::test_approved_market_order_is_sent_as_protected_limit_and_duplicates_are_blocked (same-day count covered; IST midnight boundary not tested) |
| Broker failures inside the approved order tool are audited and reported | AGENT-11 | `agent/trading.py` broker_call | not covered |
| A market order without a live price is refused by the agent | AGENT-11 | `agent/trading.py` place_order | not covered |
| The agent's modify and cancel tools | AGENT-10, AGENT-11 | `agent/trading.py` modify_order, cancel_order | not covered |
| Agent placement does not enforce lot-size multiples | AGENT-11 | `agent/trading.py` place_order | not covered |
| Agent modification relies on the broker for status exchange and trigger validation | AGENT-11 | `agent/trading.py` modify_order | not covered |
| The approval card summary for modify and cancel | AGENT-10, AGENT-11 | `agent/trading.py` order_summary | partial: test_orders.py::test_chat_pauses_for_approval_then_resumes (place summary prefix only) |
| With trading on the agent gets the order tools and the trading rule | AGENT-8, AGENT-13 | `routers/agent.py` _agent_for, `agent/builder.py` TRADING_RULE | not covered |
| The order audit lists the latest 100 rows newest first | AGENT-12 | `routers/agent.py` order_audit | not covered |

### platform.feature

| Scenario | Tags | Code | Existing pytest |
| --- | --- | --- | --- |
| Health check | NFR-10 | `main.py` health | not covered |
| The built frontend is served with an SPA fallback | UX-5, NFR-9 | `main.py` spa_fallback | not covered |
| API and callback paths never fall back to the UI | UX-5, NFR-9 | `main.py` spa_fallback | not covered |
| Without a frontend build only the API is served | UX-5, NFR-9 | `main.py` spa_fallback | not covered |
| CORS allows only the Vite dev server origins with credentials | UX-5, NFR-9 | `main.py` CORSMiddleware, `config.py` CORS_ORIGINS | not covered |
| Secrets are encrypted at rest with a generated key | NFR-3, NFR-4 | `crypto.py`, `deps.py` hash_session_value | partial: test_callback.py::test_success_sets_cookie_creates_session_and_starts_download (token and session hash), test_agent.py::test_tokens_are_encrypted_and_status_reads_the_id_token |
| The OAuth authorization code is redacted from the access log | NFR-6 | `main.py` _RedactAuthCode | not covered |
| The database is ready at startup | NFR-8 | `db.py` init_db, _add_missing_columns | not covered |
| An older database gains the pending approval column | NFR-8 | `db.py` _add_missing_columns | not covered |

### research.feature

| Scenario | Tags | Code | Existing pytest |
| --- | --- | --- | --- |
| Saving a FinEdge API key stores it encrypted and returns only a mask | AGENT-15, NFR-14 | `routers/agent.py` finedge_status, finedge_save, `agent/finedge.py` write_key, key_status, mask | partial: test_research.py::test_key_is_encrypted_and_never_returned (updated_at format, cache clear and log line not asserted) |
| A malformed key is rejected with 422 and nothing is stored | AGENT-15 | `routers/agent.py` FinEdgeKeyBody | partial: test_research.py::test_invalid_keys_are_refused (all 3 rows; status and nothing stored asserted, validation error body not asserted) |
| The saved key is never returned by any endpoint | AGENT-15, NFR-14 | `routers/agent.py` finedge_status, get_settings, `agent/finedge.py` key_status | partial: test_research.py::test_key_is_encrypted_and_never_returned (PUT body and settings checked; GET /api/agent/finedge after saving and the settings mask not asserted) |
| Removing the key | AGENT-15 | `routers/agent.py` finedge_remove, `agent/finedge.py` clear_key, clear_cache | partial: test_research.py::test_key_is_encrypted_and_never_returned (response and deleted key asserted; cache clear and log line not asserted) |
| The key is masked in FinEdge errors and never leaks through network failures | NFR-14 | `agent/finedge.py` get | test_research.py::test_get_sends_the_token_and_masks_it_in_errors, ::test_unreachable_error_does_not_leak_the_key |
| The FinEdge client retries busy answers and encodes symbols for the URL path | AGENT-16 | `agent/finedge.py` get, path_symbol, RETRY_STATUSES | partial: test_research.py::test_get_sends_the_token_and_masks_it_in_errors (429 only; 5xx retries and delays not tested) |
| Testing the key reports whether the plan covers all companies or only the sample | AGENT-15, AGENT-20 | `routers/agent.py` finedge_test, `agent/finedge.py` test_key | partial: test_research.py::test_test_key_reports_plan_coverage (both rows, client level), ::test_key_test_route_maps_finedge_answers (route with a stubbed sample result; UI message not covered) |
| Testing without a saved key returns 409 | AGENT-15 | `routers/agent.py` finedge_test | partial: test_research.py::test_key_test_route_maps_finedge_answers (status only) |
| FinEdge failures during the key test are mapped without a 401 | AGENT-15 | `routers/agent.py` _finedge_failure, `agent/finedge.py` FinEdgeError.invalid_key | partial: test_research.py::test_key_test_route_maps_finedge_answers, ::test_test_key_raises_for_a_wrong_key (ER006 row only; network and 500 rows not covered) |
| The settings card saves, then tests the key, and can remove it | AGENT-15 | frontend bundle `AgentConfigPage-*.js` | not covered (no frontend tests) |
| Research tools are listed and dimmed until a key is saved | AGENT-15, AGENT-16 | frontend bundle `AgentConfigPage-*.js` | not covered (no frontend tests) |
| The agent gets the research tools and rules only when a key is saved | AGENT-16 | `routers/agent.py` _agent_for, `agent/builder.py` build_agent, RESEARCH_RULES, RESEARCH_OFF_RULE | partial: test_research.py::test_agent_gets_research_tools_only_with_a_key (tool names and research flag; prompt text and tool call limit not asserted) |
| Financial statements are in Rs crore, oldest period first, with growth | AGENT-16 | `agent/research.py` get_financial_statements | test_research.py::test_statements_are_in_crore_oldest_first_with_growth |
| Consolidated statements fall back to standalone | AGENT-16 | `agent/research.py` statements | test_research.py::test_statements_fall_back_to_standalone |
| Statement arguments are validated before any FinEdge call | AGENT-16 | `agent/research.py` get_financial_statements, STATEMENT_PERIODS | test_research.py::test_statement_arguments_are_validated (message prefix only) |
| Ratios are converted to percent and leverage stays in times | AGENT-16 | `agent/research.py` get_financial_ratios, _pct | test_research.py::test_ratios_are_converted_to_percent |
| Tool failures are returned to the model as readable text | AGENT-16, AGENT-20 | `agent/research.py` guarded | partial: test_research.py::test_tool_errors_are_readable (outside plan and rejected key rows; 404 and network rows not covered) |
| Company search puts exact matches first and accepts BSE codes | AGENT-16 | `agent/research.py` search_companies, symbol_master | test_research.py::test_search_puts_exact_matches_first |
| The company profile trims the description and links the latest annual report | AGENT-16 | `agent/research.py` get_company_profile | test_research.py::test_profile_trims_the_description (length bound, not the exact cut) |
| Quotes fall back to one by one when a symbol is outside the plan | AGENT-20 | `agent/research.py` quotes, get_stock_quotes | test_research.py::test_quotes_fall_back_to_one_by_one_when_a_symbol_is_outside_the_plan |
| Peers leave out the company and are ranked by market cap | AGENT-18 | `agent/research.py` get_peers | test_research.py::test_peers_drop_the_company_and_rank_by_market_cap |
| Comparing companies marks uncovered companies and mixed accounting bases | AGENT-18, AGENT-20 | `agent/research.py` compare_companies | test_research.py::test_compare_companies_marks_uncovered_companies (note presence asserted, not its text) |
| A comparison needs 2 to 6 companies | AGENT-18 | `agent/research.py` compare_companies, MAX_COMPARE | partial: test_research.py::test_compare_companies_marks_uncovered_companies (one symbol row, prefix only) |
| Announcements skip routine filings and are sorted newest first | AGENT-16 | `agent/research.py` get_announcements, NOISE | test_research.py::test_announcements_skip_noise_and_sort_newest_first |
| A research report follows the fixed structure and has no rating unless asked | AGENT-17 | `agent/builder.py` RESEARCH_RULES | not covered (prompt rule, model behaviour) |
| A comparison answer is one table followed by who leads | AGENT-18 | `agent/builder.py` RESEARCH_RULES | not covered (prompt rule, model behaviour) |
| A recommendation request without a horizon makes the agent ask for one | AGENT-19 | `agent/builder.py` RESEARCH_RULES | not covered (prompt rule, model behaviour) |
| A buy or sell research view states the rating and horizon and ends with the disclaimer | AGENT-19 | `agent/builder.py` RESEARCH_RULES | not covered (prompt rule, model behaviour) |
| On a sample-only plan the agent names the missing data and carries on | AGENT-20 | `agent/builder.py` RESEARCH_RULES, `agent/research.py` guarded | not covered (prompt rule, model behaviour; the tool side is covered above) |

## Coverage summary

| Feature | Scenarios | Fully covered | Partial | Not covered |
| --- | --- | --- | --- | --- |
| authentication | 24 | 3 | 6 | 15 |
| master_contract | 24 | 4 | 1 | 19 |
| funds | 11 | 4 | 3 | 4 |
| chart | 16 | 2 | 11 | 3 |
| stream | 20 | 3 | 5 | 12 |
| orders | 31 | 1 | 11 | 19 |
| agent | 60 | 5 | 17 | 38 |
| platform | 9 | 0 | 1 | 8 |
| research | 29 | 11 | 11 | 7 |
| Total | 224 | 33 | 66 | 125 |

## Coverage gaps and observations

Gaps in the existing pytest suite, most important first:

1. Route level error mapping is mostly untested. The 401 "Session expired, please log in
   again" with session deactivation, the 502 "Could not reach TradeSmart" on network
   failure and the 502 with the broker `emsg` are only tested at broker client level for
   funds and history, and not at all for orders.
2. The chart trading routes are untested over HTTP: `POST /api/orders`, cancel,
   close-position, schema validation (quantity 0, negative prices), broker rejection
   (`TradeSmart: <emsg>`), and the GetQuotes failure path. Only the service functions are
   tested.
3. Master contract orchestration is untested: the status lifecycle (`Queued`,
   `Downloading <EXCH>`, `success` message), one exchange failing, all exchanges failing
   while keeping the old table, the 409 on a second download, duplicate handling, and the
   startup recovery of an interrupted download. Parsers are well covered.
4. Symbol search (`GET /api/symbols/search`) has no tests: ranking, multi term AND, the
   exchange filter, the 50 row limit, LIKE escaping and the 422 rules.
5. Config save and validation (`POST /api/auth/login`), `GET /api/config`,
   `GET /api/auth/session` and `POST /api/auth/logout` have no tests, nor do most callback
   failure branches (no token, no user id, non JSON, network error, 300 character cut).
6. Tick stream: switching instruments (unsubscribe `t=u`), unknown symbol error frame,
   heartbeat, the `status: error` frame with close code 1011 on broker failure, and
   index key mapping are untested.
7. Agent: the ChatGPT device flow endpoints, `POST /api/agent/test`, provider error
   mapping, run cancellation, risk settings endpoints, the audit endpoint, the
   modify_order and cancel_order tools, and broker failures inside order tools are
   untested. Pending approval expiry on a new message is untested. The approval test's
   fake agent records decisions but never executes an approved order tool, so it does
   not prove the full approval-to-broker path. Cancellation races, whitespace input,
   cross-account transcript visibility and Agno cleanup failure are also untested.
8. No frontend tests at all, and the frontend source is not in the repository. All
   `@ux` scenarios (rupee formatting, 401 redirect, WebSocket reconnect with 2 s to 30 s
   back off, order confirmations) are taken from the built bundle and need an end to end
   suite (for example Playwright) once the source is recovered.
9. Platform behaviour (SPA fallback and its path traversal guard, CORS, health, access log
   redaction, schema migration at startup) is untested.
10. FinEdge research: the tools are tested against a canned FinEdge double, but the research
    prompt rules (report structure, comparison table, recommendation only when asked, the
    horizon question, the Buy to Sell rating scale and the closing not-investment-advice
    line) cannot be checked by pytest and need transcript review or an LLM evaluation. The
    key test's 502 mappings, 5xx retries, the cache clear on save and remove, and the
    settings card in the bundle are also untested.

Behaviour observations found while writing the specs (worth a decision, not necessarily
bugs):

* Market hours are not handled anywhere. Outside market hours, orders are still sent
  and broker rejections surface as 502 `TradeSmart: <emsg>`. History is empty only when
  the broker reports no data; a closed market alone does not determine the response.
  The agent's empty-history message says "The market may be closed for this range."
* There is no explicit unsubscribe message on the tick socket. The browser unsubscribes
  only by subscribing to another instrument or closing the socket.
* A refused broker stream (`ak` not ok) reports "Your session may have expired" but does
  not deactivate the SmartTrader session, unlike the REST routes.
* The agent's `_credentials` returns 401 for an undecryptable broker token without
  deactivating the session, unlike funds, chart and orders.
* Agent token cancellation only clears the active flow reference. A worker can still
  complete and store tokens after cancellation, replacement by a newer flow, or logout.
* Logout deactivates the local session but does not cancel an already running agent
  turn or established tick socket. Agent transcripts, settings and audit rows are
  global to the installation and remain accessible after a different broker login.
* Raw request lengths are checked before trimming: whitespace-only chat messages and
  conversation titles can become empty. An unknown chat conversation id creates a new
  conversation. Transcript deletion ignores Agno cleanup failures.
* Agent placement lacks the chart path's lot-multiple check. Agent modification does
  not check existing order status, allowed exchange, lot multiples or a positive SL
  trigger. Daily and duplicate guards count only placement and are not atomic with
  broker submission. These are current gaps, not desired trading guarantees.
* A disconnect observed while consuming events requests run cancellation. A task-level
  CancelledError persists accumulated output but is re-raised before the final done
  yield, so that frame is not guaranteed after cancellation.
* `POST /api/orders/close-position` does not validate `side`: anything other than
  `long` is treated as a short position and sends a BUY.
* `POST /api/orders/{order_id}/cancel` does not check the order status first; a cancel
  of a completed order goes to the broker and comes back as a 502 rejection.
* Chart orders are sent with `remarks` "smarttrader-agent", the same as agent orders, and
  chart orders have no risk limits or audit trail (also noted in the PRD as ORD-11, not
  implemented).
* In `POST /api/agent/chat/confirm` the pending approval is marked `decided` before the
  paused run is looked up, so if the Agno run is missing (409 "The paused run was not
  found") the approval cannot be retried.
* SPEC.md says a foreign WebSocket Origin is refused with 403. The code closes before
  accept with code 4403; a browser sees this as an HTTP 403 handshake failure while the
  Starlette test client reports close code 4403.
* `GET /api/master-contract/status` requires a session, so the login page cannot show
  download progress before login (by design, noted for the PRD).
* `PUT /api/agent/finedge` stores any well formed key without asking FinEdge. The settings
  page runs the test right after saving, so a wrong key is saved first and then reported
  as rejected (PRD open question Q9).
* FinEdge takes the API key as a `token` query parameter. The client never logs URLs and
  masks the key in errors, but the key is in every request URL (PRD risk R12).
