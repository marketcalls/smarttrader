@chart
Feature: Historical candles for the trading chart
  As a logged in SmartTrader user
  I want OHLCV candles for any instrument in the symbol master and in any broker timeframe
  So that the chart can draw history before live ticks arrive

  Code: backend/app/routers/chart.py, backend/app/broker/history.py

  Background:
    Given the SmartTrader backend is running
    And an active session for user_id "AB1234" with a valid broker access token
    And the symbol master contains:
      | symbol | brsymbol | name     | exchange  | brexchange | token |
      | NIFTY  | Nifty 50 | NIFTY 50 | NSE_INDEX | NSE_INDEX  | 26000 |
      | SBIN   | SBIN-EQ  | SBIN     | NSE       | NSE        | 3045  |
      | SENSEX | SENSEX   | SENSEX   | BSE_INDEX | BSE_INDEX  | 1     |
    And the TradeSmart REST API is replaced by a test double

  @smoke @req-CHART-2
  Scenario: The supported intervals are listed
    When the client sends GET "/api/chart/intervals"
    Then the response status is 200
    And the response body is:
      """
      {"intervals": ["1m", "3m", "5m", "10m", "15m", "30m", "1h", "2h", "D"]}
      """

  @smoke @req-CHART-3
  Scenario: Intraday history is fetched from TPSeries with the instrument token
    Given TPSeries answers:
      """
      [{"stat": "Ok", "time": "09-10-2026 09:15:00", "into": "100", "inth": "101", "intl": "99", "intc": "101", "intv": "70"}]
      """
    When the client sends GET "/api/chart/history?symbol=nifty&exchange=NSE_INDEX&interval=15m"
    Then the response status is 200
    And the response field "symbol" is "NIFTY"
    And the response field "exchange" is "NSE_INDEX"
    And the response field "name" is "NIFTY 50"
    And the response field "interval" is "15m"
    And the response field "candles" is:
      """
      [{"time": 1791517500, "open": 100.0, "high": 101.0, "low": 99.0, "close": 101.0, "volume": 70}]
      """
    And TPSeries received jData with "uid" "AB1234", "exch" "NSE", "token" "26000" and "intrv" "15"
    And the "st" value is midnight IST 60 days before now and "et" is now, both as epoch seconds strings

  @req-CHART-3
  Scenario: Daily history is fetched from EODChartData with EXCH:BRSYMBOL
    Given EODChartData answers:
      """
      ["{\"ssboe\": \"1760000000\", \"into\": \"1\", \"inth\": \"2\", \"intl\": \"0.5\", \"intc\": \"1.5\", \"intv\": \"9\"}"]
      """
    When the client sends GET "/api/chart/history?symbol=SBIN&exchange=NSE&interval=D"
    Then the response status is 200
    And EODChartData received jData with "sym" "NSE:SBIN-EQ" and "from" and "to" as epoch seconds strings
    And the response field "candles" is:
      """
      [{"time": 1760000000, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 9}]
      """

  @req-CHART-3
  Scenario Outline: The default look back window depends on the interval
    When the client sends GET "/api/chart/history?symbol=SBIN&exchange=NSE&interval=<interval>"
    Then the history request covers <days> days ending now
    And the broker endpoint is "<endpoint>" with "intrv" "<intrv>"

    Examples:
      | interval | days | endpoint     | intrv |
      | 1m       | 7    | TPSeries     | 1     |
      | 3m       | 30   | TPSeries     | 3     |
      | 5m       | 30   | TPSeries     | 5     |
      | 10m      | 60   | TPSeries     | 10    |
      | 15m      | 60   | TPSeries     | 15    |
      | 30m      | 120  | TPSeries     | 30    |
      | 1h       | 180  | TPSeries     | 60    |
      | 2h       | 180  | TPSeries     | 120   |
      | D        | 1095 | EODChartData | n/a   |

  @req-CHART-3
  Scenario Outline: An explicit days parameter overrides the default window
    When the client sends GET "/api/chart/history?symbol=SBIN&exchange=NSE&interval=5m&days=<days>"
    Then the response status is 200
    And the history request covers <days> days ending now

    Examples:
      | days |
      | 1    |
      | 1825 |

  @req-CHART-3
  Scenario Outline: Index pseudo exchanges fetch data from their parent cash exchange
    When the client sends GET "/api/chart/history?symbol=<symbol>&exchange=<exchange>&interval=5m"
    Then TPSeries received jData with "exch" "<data_exchange>"

    Examples:
      | symbol | exchange  | data_exchange |
      | NIFTY  | NSE_INDEX | NSE           |
      | SENSEX | BSE_INDEX | BSE           |
      | SBIN   | NSE       | NSE           |

  @req-CHART-3
  Scenario: Candles are sorted, de-duplicated and empty rows are dropped
    Given TPSeries answers rows for 09:16, 09:15, an all zero row for 09:17 and a duplicate of 09:15 on 09-10-2026 IST
    When the client sends GET "/api/chart/history?symbol=SBIN&exchange=NSE&interval=1m"
    Then the response contains 2 candles in ascending time order
    And the first candle time is the epoch of 09-10-2026 09:15:00 IST
    And there is no candle for 09:17

  @req-CHART-3
  Scenario: Intraday volume glitches near the close are clamped
    Given TPSeries answers bars where "intv" goes negative and then jumps to roughly the whole day's volume while the cumulative "v" resets
    When the client sends GET "/api/chart/history?symbol=SBIN&exchange=NSE&interval=1m"
    Then no candle has a negative volume
    And each bar's volume is at most the rise of "v" over its running maximum for that IST day

  @req-CHART-3
  Scenario: No data from the broker is an empty chart, not an error (for example a holiday or a closed market)
    Given TPSeries answers {"stat": "Not_Ok", "emsg": "Error Occurred : 5 \"no data\""}
    When the client sends GET "/api/chart/history?symbol=SBIN&exchange=NSE&interval=5m"
    Then the response status is 200
    And the response field "candles" is []

  @negative @req-CHART-2
  Scenario Outline: Unsupported intervals are rejected before any broker call
    When the client sends GET "/api/chart/history?symbol=NIFTY&exchange=NSE_INDEX&interval=<interval>"
    Then the response status is 422
    And the response field "detail" is "Unsupported interval. Supported: 1m, 3m, 5m, 10m, 15m, 30m, 1h, 2h, D"
    And no request was sent to TradeSmart

    Examples:
      | interval |
      | 7m       |
      | 1d       |
      | 1H       |
      | 4h       |

  @negative @req-CHART-3
  Scenario Outline: A symbol that is not in the master contract returns 404
    When the client sends GET "/api/chart/history?symbol=<symbol>&exchange=<exchange>&interval=5m"
    Then the response status is 404
    And the response field "detail" is "<detail>"
    And no request was sent to TradeSmart

    Examples:
      | symbol | exchange | detail                                                                            |
      | NOPE   | NSE      | NOPE on NSE is not in the master contract. Download it or pick another symbol.   |
      | SBIN   | BSE      | SBIN on BSE is not in the master contract. Download it or pick another symbol.   |
      | NIFTY  | NSE      | NIFTY on NSE is not in the master contract. Download it or pick another symbol.  |

  @negative @req-CHART-3
  Scenario: A master contract row without a token is treated as missing
    Given the symbol master contains "BROKEN" on "NSE" with an empty token
    When the client sends GET "/api/chart/history?symbol=BROKEN&exchange=NSE&interval=5m"
    Then the response status is 404

  @negative @req-CHART-3
  Scenario Outline: Query parameter validation
    When the client sends GET "/api/chart/history?<query>"
    Then the response status is 422
    And the response field "detail" is a list of validation errors naming "<field>"

    Examples:
      | query                                                    | field    |
      | exchange=NSE&interval=5m                                 | symbol   |
      | symbol=SBIN&interval=5m                                  | exchange |
      | symbol=SBIN&exchange=N&interval=5m                       | exchange |
      | symbol=SBIN&exchange=NSE                                 | interval |
      | symbol=SBIN&exchange=NSE&interval=5m&days=0              | days     |
      | symbol=SBIN&exchange=NSE&interval=5m&days=1826           | days     |
      | symbol=SBIN&exchange=NSE&interval=5m&days=abc            | days     |

  @negative @req-CHART-3
  Scenario Outline: Broker errors become 502 with a readable detail
    Given TPSeries answers <answer>
    When the client sends GET "/api/chart/history?symbol=SBIN&exchange=NSE&interval=5m"
    Then the response status is 502
    And the response field "detail" is "<detail>"
    And the session stays active

    Examples:
      | answer                                               | detail                                          |
      | {"stat": "Not_Ok", "emsg": "Invalid token"}          | Invalid token                                   |
      | {"stat": "Ok"}                                       | Unexpected history response from TradeSmart     |
      | HTTP 502 with an HTML body                           | Unexpected response from TradeSmart (HTTP 502)  |
      | a connect timeout                                    | Could not reach TradeSmart                      |

  @negative @req-CHART-3 @req-AUTH-7
  Scenario Outline: An expired broker session on history ends the SmartTrader session
    Given <endpoint> answers <answer>
    When the client sends GET "/api/chart/history?symbol=SBIN&exchange=NSE&interval=<interval>"
    Then the response status is 401
    And the response field "detail" is "Session expired, please log in again"
    And the "auth_session" row is no longer active

    Examples:
      | endpoint     | interval | answer                                                              |
      | TPSeries     | 5m       | HTTP 401                                                            |
      | EODChartData | D        | {"stat": "Not_Ok", "emsg": "Session Expired :  Invalid Acct Id "}   |

  @negative @req-CHART-3
  Scenario Outline: Chart endpoints require a SmartTrader session
    Given no "st_session" cookie is sent
    When the client sends GET "<path>"
    Then the response status is 401
    And the response field "detail" is "Not authenticated"

    Examples:
      | path                                                           |
      | /api/chart/intervals                                           |
      | /api/chart/history?symbol=NIFTY&exchange=NSE_INDEX&interval=5m |
