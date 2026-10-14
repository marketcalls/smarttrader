@orders
Feature: Chart trading: place, modify and cancel orders, close positions and show order and position lines
  As a logged in SmartTrader user trading from the chart
  I want to send orders that TradeSmart accepts from the API
  So that limit, stop and market orders reach the exchange safely and my open orders and positions show on the chart

  TradeSmart refuses market type orders sent through the API, so MARKET goes out as a
  limit (LMT) priced from the live LTP plus a market price protection buffer, and SL-M
  goes out as SL-LMT priced from the trigger. The UI confirms every action before sending.

  Code: backend/app/routers/orders.py, backend/app/broker/order_service.py, backend/app/broker/orders.py

  Background:
    Given the SmartTrader backend is running
    And an active session for user_id "AB1234" with a valid broker access token
    And the symbol master contains:
      | symbol             | brsymbol        | exchange  | brexchange | token  | lotsize | tick_size | instrumenttype |
      | SBIN               | SBIN-EQ         | NSE       | NSE        | 3045   | 1       | 0.05      | EQ             |
      | M&M                | M&M-EQ          | NSE       | NSE        | 2031   | 1       | 0.05      | EQ             |
      | CRUDEOIL19OCT26FUT | CRUDEOIL19OCT26 | MCX       | MCX        | 569900 | 100     | 1.0       | FUT            |
      | NIFTY              | Nifty 50        | NSE_INDEX | NSE_INDEX  | 26000  | 1       | 0.05      | INDEX          |
    And the TradeSmart REST API is replaced by a test double
    And GetQuotes for "SBIN" answers {"stat": "Ok", "lp": "959.10"}
    And PlaceOrder answers {"stat": "Ok", "norenordno": "99"}

  # ---------------------------------------------------------------------------
  # Place (POST /api/orders)
  # ---------------------------------------------------------------------------

  @smoke @req-ORD-1
  Scenario: Placing a limit order
    When the client sends POST "/api/orders" with body:
      """
      {"symbol": "SBIN", "exchange": "NSE", "side": "BUY", "quantity": 5, "product": "MIS", "price_type": "LIMIT", "price": 940}
      """
    Then the response status is 200
    And the response body is {"order_id": "99", "sent_price": 940.0, "ltp": 959.1}
    And PlaceOrder received jData:
      | field       | value             |
      | uid         | AB1234            |
      | actid       | AB1234            |
      | exch        | NSE               |
      | tsym        | SBIN-EQ           |
      | qty         | 5                 |
      | prc         | 940               |
      | dscqty      | 0                 |
      | prd         | I                 |
      | trantype    | B                 |
      | prctyp      | LMT               |
      | ret         | DAY               |
      | ordersource | API               |
      | remarks     | smarttrader-agent |
    And the PlaceOrder jData has no "trgprc" field

  @smoke @req-ORD-3
  Scenario: A market order is sent as a protected limit order
    When the client sends POST "/api/orders" with body:
      """
      {"symbol": "sbin", "exchange": "nse", "side": "buy", "quantity": 2, "product": "mis", "price_type": "market"}
      """
    Then the response status is 200
    And the response body is {"order_id": "99", "sent_price": 963.9, "ltp": 959.1}
    And PlaceOrder received jData with "prctyp" "LMT", "prc" "963.9" and "qty" "2"

  @req-ORD-3
  Scenario Outline: Market price protection slabs and tick rounding against the trader
    When a protected price is computed for reference <reference>, side <side>, instrument type <type> and tick <tick>
    Then the protected price is <protected>

    Examples:
      | reference | side | type | tick | protected | rule                                  |
      | 959.1     | BUY  | EQ   | 0.05 | 963.9     | 0.5 percent at 500 and above, round up |
      | 959.1     | SELL | EQ   | 0.05 | 954.3     | 0.5 percent, round down               |
      | 80.0      | BUY  | EQ   | 0.05 | 81.6      | 2 percent below 100                   |
      | 250.0     | BUY  | FUT  | 0.05 | 252.5     | 1 percent below 500                   |
      | 8.0       | BUY  | CE   | 0.05 | 8.4       | options: 5 percent below 10           |
      | 50.0      | SELL | PE   | 0.05 | 48.5      | options: 3 percent below 100          |
      | 8801.0    | BUY  | FUT  | 1.0  | 8846.0    | tick of 1 rounds up                   |

  @req-ORD-1
  Scenario Outline: Stop orders go out as SL-LMT with the trigger
    When the client sends POST "/api/orders" with body:
      """
      {"symbol": "SBIN", "exchange": "NSE", "side": "<side>", "quantity": 1, "product": "CNC", "price_type": "<price_type>", "price": <price>, "trigger_price": 950}
      """
    Then the response status is 200
    And the response field "sent_price" is <sent_price>
    And PlaceOrder received jData with "prctyp" "SL-LMT", "trgprc" "950", "prd" "C" and "prc" "<prc>"

    Examples:
      | side | price_type | price | sent_price | prc    |
      | SELL | SL         | 949   | 949.0      | 949    |
      | BUY  | SL-M       | 0     | 954.75     | 954.75 |
      | SELL | SL-M       | 0     | 945.25     | 945.25 |

  @req-ORD-1
  Scenario Outline: Product codes are mapped to Noren codes
    When the client places a LIMIT order for "SBIN" on "NSE" with product "<product>" and price 940
    Then PlaceOrder received jData with "prd" "<prd>"

    Examples:
      | product | prd |
      | MIS     | I   |
      | CNC     | C   |
      | NRML    | M   |

  @req-ORD-1
  Scenario: Symbols with an ampersand are URL encoded for PlaceOrder
    Given GetQuotes for "M&M" answers {"stat": "Ok", "lp": "1500.00"}
    When the client places a LIMIT BUY order for 2 "M&M" on "NSE" at 1500.5
    Then PlaceOrder received jData with "tsym" "M%26M-EQ"

  @req-ORD-4
  Scenario: Futures quantities must be a multiple of the lot size
    Given GetQuotes for "CRUDEOIL19OCT26FUT" answers {"stat": "Ok", "lp": "8801.00"}
    When the client places a LIMIT BUY order for 200 "CRUDEOIL19OCT26FUT" on "MCX" with product "NRML" at 8800
    Then the response status is 200
    And PlaceOrder received jData with "qty" "200" and "prd" "M"

  @negative @req-ORD-4
  Scenario Outline: Orders that cannot be valid are rejected with 422 and nothing is sent
    When the client sends POST "/api/orders" with body <body>
    Then the response status is 422
    And the response field "detail" is "<detail>"
    And PlaceOrder was not called

    Examples:
      | body                                                                                                                          | detail                                                 |
      | {"symbol": "NIFTY", "exchange": "NSE_INDEX", "side": "BUY", "quantity": 1, "price_type": "MARKET"}                            | Orders cannot be placed on NSE_INDEX                   |
      | {"symbol": "SBIN", "exchange": "NSE", "side": "HOLD", "quantity": 1, "price_type": "MARKET"}                                  | Side must be BUY or SELL                               |
      | {"symbol": "SBIN", "exchange": "NSE", "side": "BUY", "quantity": 1, "product": "BO", "price_type": "MARKET"}                  | Product must be MIS, CNC or NRML                       |
      | {"symbol": "SBIN", "exchange": "NSE", "side": "BUY", "quantity": 1, "price_type": "STOP"}                                     | Price type must be MARKET, LIMIT, SL or SL-M           |
      | {"symbol": "SBIN", "exchange": "NSE", "side": "BUY", "quantity": 1, "price_type": "LIMIT"}                                    | A LIMIT or SL order needs a price                      |
      | {"symbol": "SBIN", "exchange": "NSE", "side": "BUY", "quantity": 1}                                                           | A LIMIT or SL order needs a price                      |
      | {"symbol": "SBIN", "exchange": "NSE", "side": "BUY", "quantity": 1, "price_type": "SL", "price": 950}                         | An SL or SL-M order needs a trigger price              |
      | {"symbol": "SBIN", "exchange": "NSE", "side": "BUY", "quantity": 1, "price_type": "SL-M"}                                     | An SL or SL-M order needs a trigger price              |
      | {"symbol": "NOPE", "exchange": "NSE", "side": "BUY", "quantity": 1, "price_type": "LIMIT", "price": 10}                        | NOPE on NSE is not in the symbol master                |
      | {"symbol": "CRUDEOIL19OCT26FUT", "exchange": "MCX", "side": "BUY", "quantity": 150, "product": "NRML", "price_type": "LIMIT", "price": 8800} | Quantity must be a multiple of the lot size 100 |

  @negative @req-ORD-4
  Scenario: A market order without a live price is refused
    Given GetQuotes for "SBIN" answers {"stat": "Ok", "lp": "0"}
    When the client sends POST "/api/orders" with a MARKET BUY order for 1 "SBIN" on "NSE"
    Then the response status is 422
    And the response field "detail" is "No live price to protect a market order; use a limit order"
    And PlaceOrder was not called

  @negative @req-ORD-4
  Scenario Outline: Request schema validation rejects bad numbers before any broker call
    When the client sends POST "/api/orders" with body <body>
    Then the response status is 422
    And the response field "detail" is a list of validation errors naming "<field>"
    And no request was sent to TradeSmart

    Examples:
      | body                                                                                                    | field         |
      | {"symbol": "SBIN", "exchange": "NSE", "side": "BUY", "quantity": 0, "price_type": "MARKET"}             | quantity      |
      | {"symbol": "SBIN", "exchange": "NSE", "side": "BUY", "quantity": -5, "price_type": "MARKET"}            | quantity      |
      | {"symbol": "SBIN", "exchange": "NSE", "side": "BUY", "quantity": 1000001, "price_type": "MARKET"}       | quantity      |
      | {"symbol": "SBIN", "exchange": "NSE", "side": "BUY", "quantity": 1, "price": -1}                        | price         |
      | {"symbol": "SBIN", "exchange": "NSE", "side": "BUY", "quantity": 1, "price_type": "SL", "price": 1, "trigger_price": -1} | trigger_price |
      | {"symbol": "", "exchange": "NSE", "side": "BUY", "quantity": 1}                                         | symbol        |
      | {"symbol": "SBIN", "exchange": "N", "side": "BUY", "quantity": 1}                                       | exchange      |
      | {"symbol": "SBIN", "exchange": "NSE", "quantity": 1}                                                    | side          |

  @negative @req-ORD-10
  Scenario Outline: An order rejected by TradeSmart returns 502 with the broker message
    Given PlaceOrder answers <answer>
    When the client places a LIMIT BUY order for 1 "SBIN" on "NSE" at 940
    Then the response status is 502
    And the response field "detail" is "<detail>"
    And the session stays active

    Examples:
      | answer                                                              | detail                                                    |
      | {"stat": "Not_Ok", "emsg": "<broker rejection reason>"}             | TradeSmart: <broker rejection reason>                     |
      | {"stat": "Not_Ok", "emsg": "Market is closed"}                      | TradeSmart: Market is closed                              |
      | {"stat": "Ok"}                                                      | TradeSmart: The order was rejected by TradeSmart          |
      | HTTP 500 with an HTML body                                          | TradeSmart: Unexpected response from TradeSmart (HTTP 500) |

  @negative @req-ORD-10
  Scenario: A failing quote request blocks the order
    Given GetQuotes for "SBIN" answers {"stat": "Not_Ok", "emsg": "Invalid Token"}
    When the client places a LIMIT BUY order for 1 "SBIN" on "NSE" at 940
    Then the response status is 502
    And the response field "detail" is "TradeSmart: Invalid Token"
    And PlaceOrder was not called

  @negative @req-ORD-10
  Scenario: Network failure while placing an order
    Given the connection to "v2api.tradesmartonline.in" times out
    When the client places a LIMIT BUY order for 1 "SBIN" on "NSE" at 940
    Then the response status is 502
    And the response field "detail" is "Could not reach TradeSmart"

  @negative @req-ORD-10 @req-AUTH-7
  Scenario Outline: An expired broker session while trading ends the SmartTrader session
    Given <endpoint> answers <answer>
    When the client places a LIMIT BUY order for 1 "SBIN" on "NSE" at 940
    Then the response status is 401
    And the response field "detail" is "Session expired, please log in again"
    And the "auth_session" row is no longer active

    Examples:
      | endpoint   | answer                                                              |
      | GetQuotes  | HTTP 401                                                            |
      | PlaceOrder | {"stat": "Not_Ok", "emsg": "Session Expired :  Invalid Acct Id "}   |

  # ---------------------------------------------------------------------------
  # Order and position lines (GET /api/orders/symbol)
  # ---------------------------------------------------------------------------

  @smoke @req-ORD-5 @req-ORD-8
  Scenario: Open orders and the net position for one instrument
    Given OrderBook answers:
      | norenordno | tsym    | exch | trantype | qty | fillshares | prc    | trgprc | prctyp | status          | s_prdt_ali |
      | 11         | SBIN-EQ | NSE  | B        | 5   | 0          | 940.00 |        | LMT    | OPEN            | MIS        |
      | 12         | SBIN-EQ | NSE  | S        | 5   |            | 930.00 | 931.00 | SL-LMT | TRIGGER_PENDING |            |
      | 13         | SBIN-EQ | NSE  | B        | 1   |            | 950    |        | LMT    | COMPLETE        |            |
    And PositionBook answers:
      | tsym    | exch | prd | netqty | netavgprc | lp     | rpnl   | urmtom | prcftr   |
      | SBIN-EQ | NSE  | I   | -10    | 960.00    | 959.10 | 120.00 | 9.00   | 1.000000 |
    When the client sends GET "/api/orders/symbol?symbol=SBIN&exchange=NSE"
    Then the response status is 200
    And the response fields are "symbol" "SBIN", "exchange" "NSE", "lot_size" 1 and "tick_size" 0.05
    And the "orders" are:
      | order_id | side | quantity | price | trigger_price | line_price | type       | price_type | product | status          |
      | 11       | BUY  | 5        | 940.0 | null          | 940.0      | limit      | LMT        | MIS     | OPEN            |
      | 12       | SELL | 5        | 930.0 | 931.0         | 931.0      | stop_limit | SL-LMT     | null    | TRIGGER_PENDING |
    And the "positions" are:
      | id        | product | side  | quantity | net_quantity | avg_price | ltp   | multiplier | realized | unrealized |
      | I:SBIN-EQ | MIS     | short | 10       | -10          | 960.0     | 959.1 | 1.0        | 120.0    | 9.0        |

  @req-ORD-5 @req-ORD-8
  Scenario: A partly filled order shows only its remaining quantity
    Given OrderBook has order "21" for "SBIN-EQ" on "NSE" with "qty" "10", "fillshares" "4" and status "OPEN"
    When the client sends GET "/api/orders/symbol?symbol=SBIN&exchange=NSE"
    Then order "21" has "quantity" 6

  @req-ORD-5 @req-ORD-8
  Scenario: Flat positions, other instruments and closed orders are left out
    Given PositionBook has a row for "SBIN-EQ" on "NSE" with "netqty" "0"
    And OrderBook and PositionBook have rows for "RELIANCE-EQ" on "NSE"
    And OrderBook has order "13" for "SBIN-EQ" with status "COMPLETE"
    When the client sends GET "/api/orders/symbol?symbol=SBIN&exchange=NSE"
    Then "positions" is empty
    And "orders" contains no "RELIANCE-EQ" order and no order "13"

  @req-ORD-5 @req-ORD-8
  Scenario: An empty order book or position book is not an error
    Given OrderBook answers {"stat": "Not_Ok", "emsg": "Error Occurred : 5 \"no data\""}
    And PositionBook answers {"stat": "Not_Ok", "emsg": "Error Occurred : 5 \"no data\""}
    When the client sends GET "/api/orders/symbol?symbol=SBIN&exchange=NSE"
    Then the response status is 200
    And "orders" is empty and "positions" is empty

  @negative @req-ORD-5 @req-ORD-8
  Scenario: Order lines for an unknown symbol
    When the client sends GET "/api/orders/symbol?symbol=NOPE&exchange=NSE"
    Then the response status is 422
    And the response field "detail" is "NOPE on NSE is not in the symbol master"

  # ---------------------------------------------------------------------------
  # Modify (POST /api/orders/{order_id}/modify)
  # ---------------------------------------------------------------------------

  @smoke @req-ORD-6
  Scenario: Dragging a limit order line changes its price and keeps its quantity
    Given the order book from the "Open orders and the net position" scenario
    And ModifyOrder answers {"stat": "Ok", "norenordno": "11"}
    When the client sends POST "/api/orders/11/modify" with body {"price": 945.5}
    Then the response status is 200
    And the response body is {"order_id": "11", "status": "modified"}
    And ModifyOrder received jData with "norenordno" "11", "exch" "NSE", "tsym" "SBIN-EQ", "qty" "5", "prc" "945.5", "prctyp" "LMT" and "ret" "DAY"
    And the ModifyOrder jData has no "trgprc" field

  @req-ORD-6
  Scenario: Dragging a stop order line moves the trigger and keeps the limit gap
    Given the order book from the "Open orders and the net position" scenario
    And ModifyOrder answers {"stat": "Ok", "norenordno": "12"}
    When the client sends POST "/api/orders/12/modify" with body {"price": 935.0}
    Then the response status is 200
    And ModifyOrder received jData with "trgprc" "935", "prc" "934" and "prctyp" "SL-LMT"

  @req-ORD-6
  Scenario: An explicit trigger price and quantity are used as given
    Given the order book from the "Open orders and the net position" scenario
    And ModifyOrder answers {"stat": "Ok", "norenordno": "12"}
    When the client sends POST "/api/orders/12/modify" with body {"price": 930.0, "trigger_price": 932.0, "quantity": 8}
    Then ModifyOrder received jData with "trgprc" "932", "prc" "930", "qty" "8" and "prctyp" "SL-LMT"

  @negative @req-ORD-6
  Scenario Outline: Orders that cannot be modified
    Given the order book from the "Open orders and the net position" scenario
    When the client sends POST "/api/orders/<order_id>/modify" with body {"price": 951}
    Then the response status is 422
    And the response field "detail" is "<detail>"
    And ModifyOrder was not called

    Examples:
      | order_id | detail                                         |
      | 13       | Order 13 is complete and cannot be modified    |
      | 77       | Order 77 is not in today's order book          |

  @negative @req-ORD-6
  Scenario Outline: Modify body validation
    When the client sends POST "/api/orders/11/modify" with body <body>
    Then the response status is 422
    And the response field "detail" is a list of validation errors naming "<field>"

    Examples:
      | body                                  | field         |
      | {}                                    | price         |
      | {"price": 0}                          | price         |
      | {"price": 940, "quantity": 0}         | quantity      |
      | {"price": 940, "trigger_price": 0}    | trigger_price |

  @negative @req-ORD-6
  Scenario Outline: A modification rejected by TradeSmart
    Given the order book from the "Open orders and the net position" scenario
    And ModifyOrder answers <answer>
    When the client sends POST "/api/orders/11/modify" with body {"price": 945.5}
    Then the response status is 502
    And the response field "detail" is "<detail>"

    Examples:
      | answer                                                | detail                                                |
      | {"stat": "Not_Ok", "emsg": "<broker rejection reason>"} | TradeSmart: <broker rejection reason>               |
      | {"stat": "Ok"}                                        | TradeSmart: The modification was rejected by TradeSmart |

  # ---------------------------------------------------------------------------
  # Cancel (POST /api/orders/{order_id}/cancel)
  # ---------------------------------------------------------------------------

  @smoke @req-ORD-7
  Scenario: Cancelling an open order
    Given CancelOrder answers {"stat": "Ok", "result": "11"}
    When the client sends POST "/api/orders/11/cancel"
    Then the response status is 200
    And the response body is {"order_id": "11", "status": "cancel requested"}
    And CancelOrder received jData {"uid": "AB1234", "norenordno": "11"}

  @negative @req-ORD-7
  Scenario Outline: A cancellation rejected by TradeSmart
    Given CancelOrder answers <answer>
    When the client sends POST "/api/orders/13/cancel"
    Then the response status is 502
    And the response field "detail" is "<detail>"

    Examples:
      | answer                                                  | detail                                                  |
      | {"stat": "Not_Ok", "emsg": "<broker rejection reason>"} | TradeSmart: <broker rejection reason>                   |
      | {"stat": "Not_Ok"}                                      | TradeSmart: The cancellation was rejected by TradeSmart |

  # ---------------------------------------------------------------------------
  # Close position (POST /api/orders/close-position)
  # ---------------------------------------------------------------------------

  @smoke @req-ORD-9
  Scenario Outline: Closing a position sends an opposite protected market order
    When the client sends POST "/api/orders/close-position" with body:
      """
      {"symbol": "SBIN", "exchange": "NSE", "product": "MIS", "side": "<position_side>", "quantity": 10}
      """
    Then the response status is 200
    And the response body is {"order_id": "99", "sent_price": <sent_price>}
    And PlaceOrder received jData with "trantype" "<trantype>", "qty" "10", "prctyp" "LMT" and "prd" "I"

    Examples:
      | position_side | trantype | sent_price |
      | long          | S        | 954.3      |
      | short         | B        | 963.9      |

  @negative @req-ORD-9
  Scenario: Close position trusts the client side and quantity
    Given the account has no current position in SBIN
    When the client sends POST "/api/orders/close-position" with body {"symbol": "SBIN", "exchange": "NSE", "product": "MIS", "side": "LONG", "quantity": 10}
    Then the response status is 200
    And PlaceOrder receives a BUY for 10 units because the side is not exactly "long"
    And PositionBook was not called to validate the supplied position

  @negative @req-ORD-9
  Scenario Outline: Close position body validation
    When the client sends POST "/api/orders/close-position" with body <body>
    Then the response status is 422

    Examples:
      | body                                                                                   |
      | {"symbol": "SBIN", "exchange": "NSE", "product": "MIS", "side": "long", "quantity": 0} |
      | {"symbol": "SBIN", "exchange": "NSE", "side": "long", "quantity": 10}                  |
      | {"symbol": "SBIN", "exchange": "NSE", "product": "XYZ", "side": "long", "quantity": 10} |

  # ---------------------------------------------------------------------------
  # Frontend confirmation (from the built bundle and SPEC.md)
  # ---------------------------------------------------------------------------

  @ux @req-ORD-2 @req-ORD-6 @req-ORD-7
  Scenario: Every chart order action is confirmed before it is sent
    Given the user is on "/trading" with "SBIN" on "NSE"
    When the user right clicks the price pane and picks a limit, stop or market order
    Then an order ticket opens and nothing is sent until the user clicks its submit button
    When the user drags an order line or presses its close button
    Then a confirm dialog opens and the modify or cancel request is sent only after confirmation
