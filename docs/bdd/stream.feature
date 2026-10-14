@stream
Feature: Live quotes over the tick WebSocket
  As a logged in SmartTrader user watching a chart
  I want live last traded price and volume for the selected instrument
  So that the last candle and position P&L update in real time without exposing my broker token

  The backend accepts the browser socket at /api/ws/ticks, opens its own socket to
  wss://v2api.tradesmartonline.in/NorenWSAPI/ with the stored access token, subscribes
  touchline for one instrument at a time and forwards only price and volume.

  Code: backend/app/routers/stream.py, backend/app/broker/stream.py
  Frontend (bundle only): reconnect logic in frontend/dist/assets/TradingPage-*.js

  Background:
    Given the SmartTrader backend is running at host "127.0.0.1:8000"
    And the symbol master contains:
      | symbol             | brsymbol        | exchange  | brexchange | token  |
      | CRUDEOIL19OCT26FUT | CRUDEOIL19OCT26 | MCX       | MCX        | 569900 |
      | SBIN               | SBIN-EQ         | NSE       | NSE        | 3045   |
      | NIFTY              | Nifty 50        | NSE_INDEX | NSE_INDEX  | 26000  |
    And the TradeSmart WebSocket is replaced by a test double

  # ---------------------------------------------------------------------------
  # Handshake: origin and session
  # ---------------------------------------------------------------------------

  @negative @req-NFR-5 @req-CHART-4
  Scenario Outline: Only this app's origins may open the tick socket
    When a WebSocket client connects to "/api/ws/ticks" with Origin <origin> and Host "127.0.0.1:8000"
    Then the origin is <result>

    Examples:
      | origin                     | result                                                                      |
      | "http://127.0.0.1:5173"    | allowed                                                                     |
      | "http://localhost:5173"    | allowed                                                                     |
      | "http://127.0.0.1:8000"    | allowed                                                                     |
      | "https://evil.example"     | refused before accept with close code 4403 and reason "Origin not allowed"  |
      | no Origin header           | refused before accept with close code 4403 and reason "Origin not allowed"  |

  @negative @req-CHART-4
  Scenario Outline: A socket without an active session is accepted and then closed with 4401
    Given <session_state>
    When a WebSocket client connects to "/api/ws/ticks" with Origin "http://127.0.0.1:5173"
    Then the socket is accepted
    And the socket is closed with code 4401 and reason "Not authenticated"
    And no connection is opened to the TradeSmart stream

    Examples:
      | session_state                                                  |
      | no "st_session" cookie is sent                                 |
      | the "st_session" cookie belongs to a deactivated session       |
      | the active session's access token cannot be decrypted          |

  # ---------------------------------------------------------------------------
  # Connect, subscribe and relay
  # ---------------------------------------------------------------------------

  @smoke @req-CHART-4
  Scenario: Connecting authenticates the broker stream with the stored token
    Given an active session for user_id "AB1234" with broker access token "<ACCESS_TOKEN>"
    And the broker stream acknowledges with {"t": "ak", "s": "OK", "uid": "AB1234"}
    When a WebSocket client connects to "/api/ws/ticks" with Origin "http://127.0.0.1:5173" and the session cookie
    Then the backend sends the broker {"t": "a", "uid": "AB1234", "actid": "AB1234", "source": "API", "accesstoken": "<ACCESS_TOKEN>"}
    And the browser receives {"type": "status", "state": "connected"}
    And no message sent to the browser contains "<ACCESS_TOKEN>"

  @req-CHART-4
  Scenario Outline: The broker acknowledgement status is matched case insensitively
    Given an active session for user_id "AB1234"
    And the broker stream acknowledges with {"t": "ak", "s": "<s>"}
    When a WebSocket client connects with a valid origin and session
    Then the browser receives {"type": "status", "state": "connected"}

    Examples:
      | s  |
      | OK |
      | Ok |

  @smoke @req-CHART-4
  Scenario: Subscribing to an instrument relays its ticks
    Given a connected tick socket for user_id "AB1234"
    When the browser sends {"action": "subscribe", "symbol": "crudeoil19oct26fut", "exchange": "mcx"}
    Then the backend sends the broker {"t": "t", "k": "MCX|569900"}
    And the browser receives {"type": "subscribed", "symbol": "CRUDEOIL19OCT26FUT", "exchange": "MCX"}
    When the broker sends {"t": "tk", "e": "MCX", "tk": "569900", "lp": "8801.00", "v": "21951"}
    Then the browser receives a tick {"type": "tick", "symbol": "CRUDEOIL19OCT26FUT", "exchange": "MCX", "ltp": 8801.0, "volume": 21951.0}
    And the tick "time" is the backend clock in epoch seconds, not the broker trade time

  @req-CHART-4
  Scenario: Feeds for other instruments are not forwarded
    Given a tick socket subscribed to "CRUDEOIL19OCT26FUT" on "MCX"
    When the broker sends {"t": "tf", "e": "MCX", "tk": "111", "lp": "1.00"}
    And the broker sends {"t": "tf", "e": "MCX", "tk": "569900", "lp": "8802.00"}
    Then the browser receives exactly one new tick with "ltp" 8802.0

  @req-CHART-4
  Scenario: Partial touchline feeds are merged with the last known values
    Given a tick socket subscribed to "CRUDEOIL19OCT26FUT" on "MCX" with no ticks yet
    When the broker sends the feeds in order:
      | feed                                                       |
      | {"t": "tf", "e": "MCX", "tk": "569900", "v": "10"}         |
      | {"t": "tk", "e": "MCX", "tk": "569900", "lp": "8801.00", "v": "21951"} |
      | {"t": "tf", "e": "MCX", "tk": "569900", "v": "21954"}      |
      | {"t": "tf", "e": "MCX", "tk": "569900", "lp": "0"}         |
    Then the forwarded ticks are:
      | ltp    | volume  |
      | 8801.0 | 21951.0 |
      | 8801.0 | 21954.0 |
      | 8801.0 | 21954.0 |
    And the first feed produced no tick because no price was known yet
    And a zero or missing "lp" never replaces the last price

  @req-CHART-4
  Scenario: Index instruments subscribe on the parent exchange key
    Given a connected tick socket
    When the browser sends {"action": "subscribe", "symbol": "NIFTY", "exchange": "NSE_INDEX"}
    Then the backend sends the broker {"t": "t", "k": "NSE|26000"}
    And the browser receives {"type": "subscribed", "symbol": "NIFTY", "exchange": "NSE_INDEX"}

  @req-CHART-4
  Scenario: Switching instruments unsubscribes the previous one
    Given a tick socket subscribed to "CRUDEOIL19OCT26FUT" on "MCX"
    When the browser sends {"action": "subscribe", "symbol": "SBIN", "exchange": "NSE"}
    Then the backend sends the broker {"t": "u", "k": "MCX|569900"} before {"t": "t", "k": "NSE|3045"}
    And the tick state is reset so no CRUDEOIL price is reported for SBIN
    And later feeds for "MCX|569900" are not forwarded

  @req-CHART-4
  Scenario: Re-subscribing the same instrument does not unsubscribe it
    Given a tick socket subscribed to "SBIN" on "NSE"
    When the browser sends {"action": "subscribe", "symbol": "SBIN", "exchange": "NSE"}
    Then the backend does not send {"t": "u", "k": "NSE|3045"}
    And the backend sends {"t": "t", "k": "NSE|3045"} again

  @req-CHART-4
  Scenario: Closing the browser socket is the only way to unsubscribe completely
    Given a tick socket subscribed to "SBIN" on "NSE"
    When the browser closes the socket
    Then the backend closes its TradeSmart stream connection
    And the heartbeat and relay tasks are cancelled

  @req-CHART-4
  Scenario Outline: Messages other than subscribe are ignored
    Given a tick socket subscribed to "SBIN" on "NSE"
    When the browser sends <message>
    Then nothing is sent to the broker
    And the browser receives no reply

    Examples:
      | message                                                    |
      | {"action": "unsubscribe", "symbol": "SBIN", "exchange": "NSE"} |
      | {"action": "ping"}                                         |
      | ["subscribe", "SBIN"]                                      |

  @negative @req-CHART-4
  Scenario: Subscribing to an unknown symbol reports an error and keeps the socket open
    Given a connected tick socket
    When the browser sends {"action": "subscribe", "symbol": "NOPE", "exchange": "NSE"}
    Then the browser receives {"type": "error", "message": "NOPE on NSE is not in the master contract"}
    And nothing is sent to the broker
    And the socket stays open

  @req-CHART-4
  Scenario: The broker socket is kept alive with a heartbeat
    Given a connected tick socket
    When 30 seconds pass
    Then the backend sends the broker {"t": "h"}

  # ---------------------------------------------------------------------------
  # Broker failures
  # ---------------------------------------------------------------------------

  @negative @req-CHART-4
  Scenario Outline: Broker stream failures are reported to the browser and the socket closes with 1011
    Given an active session for user_id "AB1234"
    And <broker_problem>
    When a WebSocket client connects with a valid origin and session
    Then the browser receives {"type": "status", "state": "error", "message": "<message>"}
    And the socket is closed with code 1011

    Examples:
      | broker_problem                                                           | message                                                                              |
      | the broker stream acknowledges with {"t": "ak", "s": "Not_Ok"}           | TradeSmart refused the stream. Your session may have expired; log in again.         |
      | the broker stream host refuses the TCP connection                        | Could not reach the TradeSmart stream (ConnectionRefusedError)                      |
      | the broker stream does not answer the connect request within 15 seconds  | The TradeSmart stream did not acknowledge the connection                             |
      | the broker stream answers the connect request with invalid JSON          | The TradeSmart stream did not acknowledge the connection                             |

  @negative @req-CHART-4
  Scenario: The broker closing the stream mid session is reported
    Given a tick socket subscribed to "SBIN" on "NSE"
    When the broker closes its WebSocket
    Then the browser receives {"type": "status", "state": "error", "message": "The TradeSmart stream closed"}
    And the socket is closed with code 1011

  @negative @req-CHART-4
  Scenario: Non JSON broker frames are skipped
    Given a tick socket subscribed to "SBIN" on "NSE"
    When the broker sends the text "not json"
    Then no tick is forwarded
    And the socket stays open

  # ---------------------------------------------------------------------------
  # Frontend reconnect behaviour (from the built bundle)
  # ---------------------------------------------------------------------------

  @ux @req-CHART-4
  Scenario: The chart reconnects with exponential back off
    Given the trading page is open with "SBIN" on "NSE" selected
    When the tick socket closes with a code other than 4401
    Then the live indicator shows "connecting"
    And the page reconnects to "ws://<host>/api/ws/ticks" after 2 seconds
    And each further failure doubles the delay up to a maximum of 30 seconds
    When a reconnect succeeds and the server sends {"type": "status", "state": "connected"}
    Then the delay is reset to 2 seconds and the indicator shows "live"
    And the page re-sends {"action": "subscribe", "symbol": "SBIN", "exchange": "NSE"} on open

  @ux @negative @req-CHART-4
  Scenario: A 4401 close stops reconnecting
    Given the trading page is open
    When the tick socket closes with code 4401
    Then the live indicator shows "error"
    And the page does not try to reconnect
    And the session expiry handler runs

  @ux @req-CHART-4
  Scenario: Switching symbols reuses the open socket
    Given the trading page has an open tick socket
    When the user selects "CRUDEOIL19OCT26FUT" on "MCX"
    Then the page sends {"action": "subscribe", "symbol": "CRUDEOIL19OCT26FUT", "exchange": "MCX"} on the same socket
