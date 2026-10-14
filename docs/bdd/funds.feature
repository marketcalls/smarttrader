@funds
Feature: Funds and margins on the dashboard
  As a logged in SmartTrader user
  I want to see my cash, collateral, margin used and today's P&L
  So that I know what I can trade with

  Code: backend/app/routers/funds.py, backend/app/broker/tradesmart.py (fetch_funds, compute_funds, position_pnl)
  Frontend (bundle only): Dashboard funds cards in frontend/dist/assets/index-*.js

  Background:
    Given the SmartTrader backend is running
    And an active session for user_id "AB1234" with a valid broker access token
    And the TradeSmart REST API is replaced by a test double

  @smoke @req-DASH-2
  Scenario: Funds are computed from Limits and PositionBook
    Given Limits answers:
      """
      {"stat": "Ok", "cash": "100000.50", "payin": "2500.00", "payout": "1000.00",
       "brkcollamt": "50000.25", "marginused": "12000.00"}
      """
    And PositionBook answers:
      """
      [{"rpnl": "150.50", "urmtom": "-20.25", "netqty": "10"},
       {"rpnl": "-50", "urmtom": "30", "netqty": "0"}]
      """
    When the client sends GET "/api/funds"
    Then the response status is 200
    And the response contains:
      | field           | value     |
      | available_cash  | 90500.50  |
      | collateral      | 50000.25  |
      | m2m_realized    | 100.50    |
      | m2m_unrealized  | 9.75      |
      | utilised_debits | 12000.00  |
      | cash            | 100000.50 |
      | payin           | 2500.00   |
      | payout          | 1000.00   |
    And the response field "fetched_at" is an ISO 8601 timestamp with offset "+05:30"
    And every amount is rounded to 2 decimals

  @req-DASH-2
  Scenario: Limits and PositionBook are called with the Noren request shape
    When the client sends GET "/api/funds"
    Then TradeSmart received POST "/NorenWClientAPIv2/Limits" and POST "/NorenWClientAPIv2/PositionBook"
    And each request has Content-Type "text/plain" and header "Authorization: Bearer <ACCESS_TOKEN>"
    And each body is "jData=" followed by {"uid": "AB1234", "actid": "AB1234"}

  @req-DASH-2
  Scenario Outline: Unrealized P&L falls back to (lp - netavgprc) x netqty x prcftr when urmtom is zero
    Given PositionBook answers one row <row>
    And Limits answers {"stat": "Ok"}
    When the client sends GET "/api/funds"
    Then the response field "m2m_unrealized" is <unrealized>

    Examples:
      | row                                                                                         | unrealized |
      | {"urmtom": "0", "rpnl": "0", "netqty": "-50", "lp": "98", "netavgprc": "100", "prcftr": "1.5"} | 150.00     |
      | {"netqty": "2", "lp": "110", "netavgprc": "100"}                                            | 20.00      |
      | {"urmtom": "0", "netqty": "0", "lp": "110", "netavgprc": "100"}                             | 0.00       |

  @req-DASH-2
  Scenario Outline: A PositionBook without positions means zero P&L, not an error
    Given Limits answers {"stat": "Ok", "cash": "1000"}
    And PositionBook answers <positionbook>
    When the client sends GET "/api/funds"
    Then the response status is 200
    And the response field "m2m_realized" is 0.0
    And the response field "m2m_unrealized" is 0.0
    And the response field "cash" is 1000.0

    Examples:
      | positionbook                                                      |
      | {"stat": "Not_Ok", "emsg": "Error Occurred : 5 \"no data\""}      |
      | HTTP 200 with a non JSON body                                     |

  @req-DASH-2
  Scenario: Missing and unparsable numbers count as zero
    Given Limits answers {"stat": "Ok", "cash": "", "payin": null, "marginused": "abc"}
    And PositionBook answers [{"rpnl": "nan"}]
    When the client sends GET "/api/funds"
    Then the response status is 200
    And "available_cash", "collateral", "m2m_unrealized", "m2m_realized", "utilised_debits", "cash", "payin" and "payout" are all 0.0

  @negative @req-DASH-4
  Scenario Outline: Broker errors on Limits become 502 with the broker message
    Given Limits answers <limits>
    When the client sends GET "/api/funds"
    Then the response status is 502
    And the response field "detail" is "<detail>"
    And the session stays active

    Examples:
      | limits                                          | detail                                              |
      | {"stat": "Not_Ok", "emsg": "Invalid Input"}     | Invalid Input                                       |
      | {"stat": "Not_Ok"}                              | Limits request failed                               |
      | HTTP 500 with an HTML body                      | Unexpected response from TradeSmart (HTTP 500)      |

  @negative @req-DASH-4
  Scenario: Network failure to TradeSmart
    Given the connection to "v2api.tradesmartonline.in" times out
    When the client sends GET "/api/funds"
    Then the response status is 502
    And the response field "detail" is "Could not reach TradeSmart"
    And the session stays active

  @negative @req-AUTH-7 @req-DASH-4
  Scenario Outline: An expired broker session on either call ends the SmartTrader session
    Given <call> answers <answer>
    When the client sends GET "/api/funds"
    Then the response status is 401
    And the response field "detail" is "Session expired, please log in again"
    And the "auth_session" row is no longer active

    Examples:
      | call         | answer                                                              |
      | Limits       | HTTP 401                                                            |
      | Limits       | {"stat": "Not_Ok", "emsg": "Session Expired :  Invalid Acct Id "}   |
      | PositionBook | HTTP 401                                                            |
      | PositionBook | {"stat": "Not_Ok", "emsg": "Session Expired :  Invalid Acct Id "}   |

  @negative @req-AUTH-5
  Scenario: Funds require a SmartTrader session
    Given no "st_session" cookie is sent
    When the client sends GET "/api/funds"
    Then the response status is 401
    And the response field "detail" is "Not authenticated"
    And no request was sent to TradeSmart

  @ux @req-DASH-2 @req-DASH-3
  Scenario: The dashboard shows funds as Indian rupees and can be refreshed
    Given the user is on "/dashboard"
    When the funds load
    Then each amount is formatted with Intl.NumberFormat "en-IN", currency "INR" and 2 fraction digits
    And the cards show Available Cash, Collateral, Realized M2M, Unrealized M2M, Utilised Margin, Cash, Payin and Payout
    And the last fetched time is shown
    When the user presses Refresh
    Then GET "/api/funds" is sent again

  @ux @negative @req-AUTH-5
  Scenario: A 401 from any API call sends the user back to the login page
    Given the user is on "/dashboard"
    When GET "/api/funds" answers 401
    Then the browser navigates to "/login"
