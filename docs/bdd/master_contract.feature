@master_contract
Feature: Master contract (symbol master) download, status and symbol search
  As a logged in SmartTrader user
  I want the TradeSmart symbol master downloaded automatically and searchable
  So that charts, live quotes, orders and the agent can resolve symbols to broker tokens

  Code: backend/app/broker/master_contract.py, backend/app/routers/master.py,
        backend/app/routers/auth.py (auto start), backend/app/db.py (startup recovery)

  Background:
    Given the SmartTrader backend is running
    And an active session with a valid "st_session" cookie
    And the TradeSmart symbol file host "https://v2api.tradesmartonline.in/<EXCH>_symbols.txt.zip" is replaced by a test double

  # ---------------------------------------------------------------------------
  # Automatic and manual download
  # ---------------------------------------------------------------------------

  @smoke @req-MC-1
  Scenario: The download starts automatically after a successful login
    Given no master contract download is running
    When a successful OAuth callback completes
    Then a background thread named "master-contract-download" is started
    And GET "/api/master-contract/status" immediately returns "status" "downloading" and "message" "Queued"
    And "started_at" is set and "finished_at" is null

  @smoke @req-MC-2
  Scenario: A full download of all six exchanges succeeds
    Given the symbol files for "NSE", "BSE", "NFO", "CDS", "MCX" and "BFO" are valid zip archives
    When the master contract download runs to completion
    Then the files are requested in the order NSE, BSE, NFO, CDS, MCX, BFO with a browser "User-Agent" header
    And GET "/api/master-contract/status" returns status 200 with:
      | field         | value                                                |
      | status        | success                                              |
      | message       | matches "Loaded <N> records in <seconds> s"          |
      | total_records | the number of unique rows stored in "symtoken"       |
      | exchanges     | an object with a count per exchange, for example NSE, NSE_INDEX, BSE, BSE_INDEX, NFO, CDS, MCX, BFO |
      | started_at    | an ISO 8601 timestamp with offset "+05:30"           |
      | finished_at   | an ISO 8601 timestamp with offset "+05:30"           |
    And the temporary work directory under "data/tmp" is removed

  @req-MC-2
  Scenario: Progress is reported while the download runs
    When the master contract download runs
    Then the status "message" passes through "Starting download", "Downloading NSE", "Processing NSE" and so on for each exchange
    And before the table swap the message is "Saving <N> records"

  @negative @req-MC-4 @req-NFR-8
  Scenario: One failing exchange is skipped and the rest are loaded
    Given the "CDS" symbol file request answers HTTP 404
    And the other five exchanges answer valid archives
    When the master contract download runs to completion
    Then the status is "success"
    And the status message ends with "(failed: CDS)"
    And "exchanges" has no "CDS" key
    And symbols from NSE, BSE, NFO, MCX and BFO are searchable

  @negative @req-MC-4 @req-NFR-8
  Scenario Outline: A bad archive for one exchange is skipped
    Given the "<exchange>" archive <problem>
    When the master contract download runs to completion
    Then the status is "success" and the message ends with "(failed: <exchange>)"

    Examples:
      | exchange | problem                                                       |
      | NFO      | does not contain "NFO_symbols.txt" and has more than one file |
      | MCX      | contains a symbols file larger than 512 MB                    |
      | BFO      | is not a valid zip file                                       |

  @negative @req-MC-4 @req-NFR-8
  Scenario: When no exchange loads the status is error and the previous symbols are kept
    Given "symtoken" already holds symbols from an earlier successful download
    And every symbol file request fails with a network error
    When the master contract download runs to completion
    Then the status is "error"
    And the status message starts with "No exchange could be loaded ("
    And the status message is at most 500 characters long
    And "symtoken" still holds the symbols from the earlier download

  @req-MC-4 @req-NFR-8
  Scenario: The symbol table is swapped in one transaction
    Given "symtoken" holds symbols from an earlier download
    When a new download saves its rows
    Then the old rows are deleted and the new rows inserted in batches of 10000 inside one transaction
    And a concurrent search never sees an empty or half filled table

  @req-MC-5
  Scenario: Duplicate token and exchange pairs keep the first occurrence
    Given two parsed rows share token "2885" on exchange "NSE"
    When the rows are saved
    Then only the first row for token "2885" on "NSE" is stored

  @smoke @req-MC-3
  Scenario: A manual download can be started from the dashboard
    Given no master contract download is running
    When the client sends POST "/api/master-contract/download"
    Then the response status is 202
    And the response body is {"status": "downloading"}

  @negative @req-MC-3
  Scenario: A second download is refused while one is running
    Given a master contract download is running
    When the client sends POST "/api/master-contract/download"
    Then the response status is 409
    And the response field "detail" is "Master contract download already in progress"

  @negative @req-MC-4 @req-NFR-8
  Scenario: A download interrupted by a process restart is marked as failed at startup
    Given the "master_contract_status" row has status "downloading"
    When the backend starts and initialises the database
    Then the status is "error"
    And the status message is "Previous download was interrupted"
    And "finished_at" is set

  @req-MC-2
  Scenario: A fresh database reports an idle master contract
    Given the database was just created
    When the client sends GET "/api/master-contract/status"
    Then the response status is 200
    And the response field "status" is "idle"
    And the response field "total_records" is 0
    And the response field "exchanges" is {}

  # ---------------------------------------------------------------------------
  # Parsing rules (OpenAlgo symbol conventions)
  # ---------------------------------------------------------------------------

  @req-MC-5
  Scenario Outline: Broker trading symbols are mapped to SmartTrader symbols
    Given a "<exchange_file>" symbol file row with TradingSymbol "<brsymbol>" and the columns <columns>
    When the file is parsed
    Then the stored row has symbol "<symbol>", exchange "<exchange>" and instrumenttype "<type>"

    Examples:
      | exchange_file | brsymbol                | columns                                                  | symbol                  | exchange  | type  |
      | NSE           | RELIANCE-EQ             | Instrument EQ, Token 2885                                | RELIANCE                | NSE       | EQ    |
      | NSE           | BAJAJ-AUTO-EQ           | Instrument EQ                                            | BAJAJ-AUTO              | NSE       | EQ    |
      | NSE           | M&M-EQ                  | Instrument EQ                                            | M&M                     | NSE       | EQ    |
      | NSE           | SOMEBE-BE               | Instrument BE                                            | SOMEBE                  | NSE       | EQ    |
      | NSE           | NIFTY INDEX             | Instrument INDEX, Symbol NIFTY 50, Token 26000           | NIFTY                   | NSE_INDEX | INDEX |
      | NSE           | NIFTY BANK              | Instrument INDEX, Token 26009                            | BANKNIFTY               | NSE_INDEX | INDEX |
      | BSE           | RELIANCE                | Instrument A, Token 500325                               | RELIANCE                | BSE       | EQ    |
      | NFO           | NIFTY23NOV26F           | OptionType XX, Expiry 23-NOV-2026                        | NIFTY23NOV26FUT         | NFO       | FUT   |
      | NFO           | NIFTY09NOV26C26000      | OptionType CE, StrikePrice 26000, Expiry 09-NOV-2026     | NIFTY09NOV2626000CE     | NFO       | CE    |
      | NFO           | ABC29DEC26P12.5         | OptionType PE, StrikePrice 12.5, Expiry 29-DEC-2026      | ABC29DEC2612.5PE        | NFO       | PE    |
      | BFO           | ETERNAL26OCT375PE       | Strike 375, Expiry 29-OCT-2026                           | ETERNAL29OCT26375PE     | BFO       | PE    |
      | BFO           | ETERNAL26OCTFUT         | OptionType XX, Expiry 29-OCT-2026                        | ETERNAL29OCT26FUT       | BFO       | FUT   |
      | MCX           | CRUDEOIL19OCT26         | Instrument FUTCOM, OptionType XX, Expiry 19-OCT-2026     | CRUDEOIL19OCT26FUT      | MCX       | FUT   |
      | MCX           | CRUDEOILM16DEC26P5300   | Instrument OPTFUT, OptionType PE, StrikePrice 5300       | CRUDEOILM16DEC265300PE  | MCX       | PE    |
      | CDS           | USDINR13NOV26F          | Instrument FUTCUR, OptionType XX, Token 13298            | USDINR13NOV26FUT        | CDS       | FUT   |
      | CDS           | USDINR29DEC26C82.5      | Instrument OPTCUR, OptionType CE, StrikePrice 82.5       | USDINR29DEC2682.5CE     | CDS       | CE    |

  @req-MC-5
  Scenario: The BSE file adds the SENSEX and BANKEX indices
    When a BSE symbol file is parsed
    Then the rows include symbol "SENSEX" with token "1" and symbol "BANKEX" with token "12" on exchange "BSE_INDEX"
    And both have instrumenttype "INDEX", lotsize 1 and tick_size 0.05

  @req-MC-5
  Scenario: Dummy, blank and incomplete rows are skipped
    Given a CDS symbol file with a row whose Token is "1"
    And an NSE symbol file with a row that has an empty Token or TradingSymbol and a blank line
    When the files are parsed
    Then CDS rows with a token of 100 or less are not stored
    And rows without a Token or TradingSymbol are not stored

  @req-MC-5
  Scenario Outline: Expiry and strike formatting
    When expiry "<raw_expiry>" and strike "<raw_strike>" are formatted
    Then the expiry is "<expiry>" and the strike is <strike>

    Examples:
      | raw_expiry  | raw_strike | expiry    | strike |
      | 29-DEC-2026 | 26000      | 29-DEC-26 | 26000  |
      | 29-Dec-2026 | 26000.0    | 29-DEC-26 | 26000  |
      | 29-DEC-2026 | 82.5       | 29-DEC-26 | 82.5   |
      |             |            |           | -1     |
      | 2026-12-29  | abc        |           | -1     |

  # ---------------------------------------------------------------------------
  # Symbol search (GET /api/symbols/search)
  # ---------------------------------------------------------------------------

  @smoke @req-MC-6
  Scenario: Searching by symbol returns matching instruments with all fields
    Given the symbol master contains:
      | symbol    | brsymbol    | name             | exchange | token | lotsize | instrumenttype | tick_size |
      | SBIN      | SBIN-EQ     | SBIN             | NSE      | 3045  | 1       | EQ             | 0.05      |
      | SBICARD   | SBICARD-EQ  | SBICARD          | NSE      | 17971 | 1       | EQ             | 0.05      |
      | SBIN      | SBIN        | SBIN             | BSE      | 500112| 1       | EQ             | 0.05      |
    When the client sends GET "/api/symbols/search?q=sbin"
    Then the response status is 200
    And the first result has "symbol" "SBIN"
    And every result has the fields "symbol", "brsymbol", "name", "exchange", "token", "expiry", "strike", "lotsize", "instrumenttype" and "tick_size"

  @req-MC-6
  Scenario: Results are ranked exact match first, then prefix, then contains, shorter symbols first
    Given the symbol master contains symbols "SBIN", "SBICARD", "ESBIN" on "NSE"
    When the client sends GET "/api/symbols/search?q=SBI"
    Then the result symbols are in the order "SBIN", "SBICARD", "ESBIN"

  @req-MC-6
  Scenario: Every search term must match the symbol, broker symbol or name
    Given the symbol master contains "NIFTY09NOV2626000CE" and "NIFTY23NOV26FUT" on "NFO"
    When the client sends GET "/api/symbols/search?q=nifty 09nov"
    Then the results contain "NIFTY09NOV2626000CE"
    And the results do not contain "NIFTY23NOV26FUT"

  @req-MC-6
  Scenario: The exchange filter is case insensitive
    Given the symbol master contains "SBIN" on "NSE" and on "BSE"
    When the client sends GET "/api/symbols/search?q=SBIN&exchange=bse"
    Then every result has "exchange" "BSE"

  @req-MC-6
  Scenario: At most 50 results are returned
    Given the symbol master contains 200 symbols that contain "NIFTY"
    When the client sends GET "/api/symbols/search?q=NIFTY"
    Then the response contains exactly 50 results

  @req-MC-6
  Scenario: LIKE wildcards in the query are matched literally
    Given the symbol master contains "SBIN" on "NSE"
    When the client sends GET "/api/symbols/search?q=%25%25"
    Then the response status is 200
    And the response is an empty list

  @req-MC-6
  Scenario: Searching an empty master contract returns an empty list
    Given the symbol master is empty
    When the client sends GET "/api/symbols/search?q=SBIN"
    Then the response status is 200
    And the response is an empty list

  @negative @req-MC-6
  Scenario Outline: Invalid search queries are rejected
    When the client sends GET "/api/symbols/search<query>"
    Then the response status is 422
    And the response field "detail" <detail>

    Examples:
      | query                                  | detail                                         |
      |                                        | is a list of validation errors naming "q"      |
      | ?q=S                                   | is a list of validation errors naming "q"      |
      | ?q=S%20%20                             | is "Query must be at least 2 characters"       |
      | ?q=<65 characters>                     | is a list of validation errors naming "q"      |
      | ?q=SBIN&exchange=<17 characters>       | is a list of validation errors naming "exchange" |
