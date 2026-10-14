@auth
Feature: Broker configuration, TradeSmart OAuth login, session and logout
  As the single SmartTrader user
  I want to save my TradeSmart app credentials and log in through the TradeSmart OAuth portal
  So that the backend holds a broker session for me without the secret or token ever reaching the browser

  Code: backend/app/routers/auth.py, backend/app/deps.py, backend/app/broker/tradesmart.py,
        backend/app/crypto.py, backend/app/config.py, backend/app/main.py

  Background:
    Given the SmartTrader backend is running at "http://127.0.0.1:8000" with an empty database
    And the TradeSmart REST API is replaced by a test double
    And no frontend build exists in "frontend/dist"

  # ---------------------------------------------------------------------------
  # Saving the broker configuration (GET /api/config, POST /api/auth/login)
  # ---------------------------------------------------------------------------

  @smoke @req-AUTH-1
  Scenario: Reading the configuration before anything is saved
    When the client sends GET "/api/config"
    Then the response status is 200
    And the response body is:
      """
      {"api_key": null, "redirect_url": null, "has_secret": false}
      """

  @smoke @req-AUTH-1
  Scenario: Saving the configuration returns the TradeSmart authorize URL
    When the client sends POST "/api/auth/login" with body:
      """
      {"api_key": "DEMO_KEY", "api_secret": "DEMO_SECRET", "redirect_url": "http://127.0.0.1:8000/tradesmart/callback"}
      """
    Then the response status is 200
    And the response body is:
      """
      {"authorize_url": "https://v2api.tradesmartonline.in/OAuthlogin/authorize/oauth?client_id=DEMO_KEY"}
      """
    And the "broker_config" row with id 1 has api_key "DEMO_KEY" and redirect_url "http://127.0.0.1:8000/tradesmart/callback"
    And the stored "api_secret_enc" is not equal to "DEMO_SECRET"
    And the stored "api_secret_enc" decrypts with the Fernet key in "data/secret.key" to "DEMO_SECRET"

  @req-AUTH-1 @req-NFR-3 @req-NFR-4
  Scenario: The saved secret is never returned to the browser
    Given the configuration was saved with api_key "DEMO_KEY" and api_secret "DEMO_SECRET"
    When the client sends GET "/api/config"
    Then the response status is 200
    And the response field "api_key" is "DEMO_KEY"
    And the response field "has_secret" is true
    And the response body does not contain "DEMO_SECRET"
    And the response body has no field "api_secret"

  @req-AUTH-1
  Scenario Outline: A blank secret keeps the stored secret
    Given the configuration was saved with api_key "DEMO_KEY" and api_secret "DEMO_SECRET"
    When the client sends POST "/api/auth/login" with api_key "NEW_KEY", api_secret <secret> and redirect_url "http://127.0.0.1:8000/tradesmart/callback"
    Then the response status is 200
    And the stored api_key is "NEW_KEY"
    And the stored secret still decrypts to "DEMO_SECRET"

    Examples:
      | secret |
      | null   |
      | ""     |
      | "   "  |

  @req-AUTH-1
  Scenario: Leading and trailing spaces are trimmed before saving
    When the client sends POST "/api/auth/login" with api_key "  DEMO_KEY  ", api_secret " DEMO_SECRET " and redirect_url " http://127.0.0.1:8000/tradesmart/callback "
    Then the response status is 200
    And the stored api_key is "DEMO_KEY"
    And the stored redirect_url is "http://127.0.0.1:8000/tradesmart/callback"
    And the stored secret decrypts to "DEMO_SECRET"

  @negative @req-AUTH-1
  Scenario Outline: Invalid configuration is rejected and nothing is saved
    Given <precondition>
    When the client sends POST "/api/auth/login" with api_key <api_key>, api_secret <api_secret> and redirect_url <redirect_url>
    Then the response status is <status>
    And the response field "detail" is <detail>

    Examples:
      | precondition                 | api_key    | api_secret    | redirect_url                                  | status | detail                                        |
      | no configuration is stored   | "   "      | "DEMO_SECRET" | "http://127.0.0.1:8000/tradesmart/callback"   | 400    | "API key is required"                         |
      | no configuration is stored   | "DEMO_KEY" | "DEMO_SECRET" | "ftp://127.0.0.1/tradesmart/callback"         | 400    | "Redirect URL must be an http or https URL"   |
      | no configuration is stored   | "DEMO_KEY" | "DEMO_SECRET" | "127.0.0.1:8000/tradesmart/callback"          | 400    | "Redirect URL must be an http or https URL"   |
      | no configuration is stored   | "DEMO_KEY" | "DEMO_SECRET" | "http://"                                     | 400    | "Redirect URL must be an http or https URL"   |
      | no configuration is stored   | "DEMO_KEY" | ""            | "http://127.0.0.1:8000/tradesmart/callback"   | 400    | "API secret is required"                      |
      | no configuration is stored   | "DEMO_KEY" | null          | "http://127.0.0.1:8000/tradesmart/callback"   | 400    | "API secret is required"                      |

  @negative @req-AUTH-1
  Scenario Outline: Missing required JSON fields fail request validation
    When the client sends POST "/api/auth/login" with body <body>
    Then the response status is 422
    And the response field "detail" is a list of validation errors naming "<field>"

    Examples:
      | body                                                                     | field        |
      | {"api_secret": "DEMO_SECRET", "redirect_url": "http://127.0.0.1:8000/x"} | api_key      |
      | {"api_key": "DEMO_KEY", "api_secret": "DEMO_SECRET"}                     | redirect_url |

  @req-AUTH-2
  Scenario Outline: The client id in the authorize URL is URL encoded
    When the authorize URL is built for api_key "<api_key>"
    Then it is "https://v2api.tradesmartonline.in/OAuthlogin/authorize/oauth?client_id=<encoded>"

    Examples:
      | api_key | encoded   |
      | ABC123  | ABC123    |
      | A B&C   | A+B%26C   |

  # ---------------------------------------------------------------------------
  # OAuth callback (GET /tradesmart/callback)
  # ---------------------------------------------------------------------------

  @smoke @req-AUTH-3
  Scenario: Successful callback creates a session, sets the cookie and starts the master contract download
    Given the configuration was saved with api_key "DEMO_KEY" and api_secret "DEMO_SECRET"
    And GenAcsTok answers {"stat": "Ok", "access_token": "<ACCESS_TOKEN>", "actid": "AB1234", "uname": "Test User"}
    When the browser is redirected to GET "/tradesmart/callback?code=good"
    Then GenAcsTok received one POST with Content-Type "text/plain" and no Authorization header
    And the GenAcsTok body is "jData=" followed by {"code": "good", "checksum": SHA-256("DEMO_KEY" + "DEMO_SECRET" + "good")}
    And the response status is 302
    And the "Location" header is "http://127.0.0.1:5173/dashboard"
    And the response sets cookie "st_session" with attributes "HttpOnly", "SameSite=Lax" and "Path=/"
    And the cookie has no "Secure" attribute and no "Domain" attribute
    And exactly one active "auth_session" row exists for user_id "AB1234" and user_name "Test User"
    And its "session_hash" is the SHA-256 hex of the cookie value
    And its "access_token_enc" is not equal to "<ACCESS_TOKEN>" and decrypts to "<ACCESS_TOKEN>"
    And a master contract download was started in the background

  @req-AUTH-3
  Scenario Outline: Token and user id fallbacks in the GenAcsTok answer
    Given the configuration was saved with api_key "DEMO_KEY" and api_secret "DEMO_SECRET"
    And GenAcsTok answers <answer>
    When the browser is redirected to GET "/tradesmart/callback?code=good"
    Then the response status is 302
    And the "Location" header ends with "/dashboard"
    And the active session has user_id "<user_id>"

    Examples:
      | answer                                                                  | user_id |
      | {"stat": "Ok", "access_token": "<ACCESS_TOKEN>", "actid": "AB1234"}     | AB1234  |
      | {"stat": "Ok", "susertoken": "<ACCESS_TOKEN>", "uid": "AB1234"}         | AB1234  |
      | {"stat": "Ok", "access_token": "<ACCESS_TOKEN>", "USERID": "AB1234"}    | AB1234  |

  @req-AUTH-3
  Scenario Outline: The GenAcsTok checksum is SHA-256 of api_key, api_secret and code in that order
    When the checksum is computed for api_key "<key>", api_secret "<secret>" and code "<code>"
    Then the checksum is the 64 character lowercase hex string "<checksum>"

    Examples:
      | key | secret | code   | checksum                                                         |
      | ABC | 123    | x1y2z3 | 7b482d7b380a3067eaba4c9c909b19253c4fa0edb5833e246401b8497c99a9c3 |

  @req-AUTH-3
  Scenario: A new login deactivates the previous session
    Given a successful callback created session cookie "COOKIE_1"
    When a second successful callback with code "again" completes
    Then exactly one "auth_session" row is active
    And the new "st_session" cookie value differs from "COOKIE_1"
    And GET "/api/auth/session" with cookie "COOKIE_1" returns "authenticated" false

  @req-AUTH-3 @req-UX-5
  Scenario Outline: Where the callback redirects depends on how the frontend is served
    Given <frontend_setup>
    When the browser is redirected to GET "/tradesmart/callback" without a code
    Then the response status is 302
    And the "Location" header starts with "<prefix>"

    Examples:
      | frontend_setup                                                                       | prefix                               |
      | no frontend build exists and SMARTTRADER_FRONTEND_URL is not set                     | http://127.0.0.1:5173/login?error=   |
      | no frontend build exists and SMARTTRADER_FRONTEND_URL is "http://localhost:5174/"    | http://localhost:5174/login?error=   |
      | "frontend/dist/index.html" exists                                                    | /login?error=                        |

  @negative @req-AUTH-4
  Scenario Outline: A failed callback redirects to the login page with a readable error and creates no session
    Given <setup>
    When the browser is redirected to GET "<callback>"
    Then the response status is 302
    And the "Location" header starts with "http://127.0.0.1:5173/login?error="
    And the URL decoded "error" query parameter is "<error>"
    And the response does not set a cookie
    And no "auth_session" row is active

    Examples:
      | setup                                                                                        | callback                                         | error                                                                                     |
      | the configuration was saved                                                                  | /tradesmart/callback                             | TradeSmart did not return an authorization code                                           |
      | the configuration was saved                                                                  | /tradesmart/callback?error_description=Denied    | TradeSmart did not return an authorization code: Denied                                   |
      | the configuration was saved                                                                  | /tradesmart/callback?emsg=Invalid%20App          | TradeSmart did not return an authorization code: Invalid App                              |
      | no configuration is stored                                                                   | /tradesmart/callback?code=good                   | API key and secret are not configured. Save them and log in again.                        |
      | the stored secret cannot be decrypted because "data/secret.key" changed                      | /tradesmart/callback?code=good                   | API key and secret are not configured. Save them and log in again.                        |
      | GenAcsTok answers {"stat": "Not_Ok", "emsg": "Error Occurred : 5 \"no data\""}               | /tradesmart/callback?code=bogus                  | TradeSmart login failed: Error Occurred : 5 "no data"                                     |
      | GenAcsTok answers {"stat": "Not_Ok"}                                                         | /tradesmart/callback?code=bogus                  | TradeSmart login failed: Authentication failed                                            |
      | GenAcsTok answers {"stat": "Ok", "actid": "AB1234"}                                          | /tradesmart/callback?code=good                   | TradeSmart login failed: Login succeeded but TradeSmart returned no access token          |
      | GenAcsTok answers {"stat": "Ok", "access_token": "<ACCESS_TOKEN>"}                           | /tradesmart/callback?code=good                   | TradeSmart login failed: Login succeeded but TradeSmart returned no user id               |
      | GenAcsTok answers HTTP 503 with an HTML body                                                 | /tradesmart/callback?code=good                   | TradeSmart login failed: Unexpected response from TradeSmart (HTTP 503)                   |
      | GenAcsTok answers a JSON list instead of an object                                           | /tradesmart/callback?code=good                   | TradeSmart login failed: Unexpected response from TradeSmart                              |
      | the network to TradeSmart fails with a connect timeout                                       | /tradesmart/callback?code=good                   | Could not reach TradeSmart. Please try again.                                             |
      | the token exchange raises an unexpected internal error                                       | /tradesmart/callback?code=good                   | Unexpected error during login                                                             |

  @negative @req-AUTH-4
  Scenario: A very long broker error is cut to 300 characters in the redirect
    Given the configuration was saved
    And GenAcsTok answers {"stat": "Not_Ok", "emsg": "<a 500 character message>"}
    When the browser is redirected to GET "/tradesmart/callback?code=bogus"
    Then the URL decoded "error" query parameter is at most 300 characters long

  @negative @req-AUTH-4
  Scenario: A whitespace only code counts as no code
    Given the configuration was saved
    When the browser is redirected to GET "/tradesmart/callback?code=%20%20"
    Then the URL decoded "error" query parameter is "TradeSmart did not return an authorization code"
    And GenAcsTok was not called

  @req-MC-1
  Scenario: A login while a master contract download is already running does not start a second one
    Given a master contract download is running
    When a successful callback with code "good" completes
    Then the response status is 302
    And no second master contract download thread is started

  # ---------------------------------------------------------------------------
  # Session (GET /api/auth/session)
  # ---------------------------------------------------------------------------

  @smoke @req-AUTH-5
  Scenario: The session endpoint reports the logged in user
    Given a successful callback created an active session for user_id "AB1234" and user_name "Test User"
    When the client sends GET "/api/auth/session" with that "st_session" cookie
    Then the response status is 200
    And the response field "authenticated" is true
    And the response field "user_id" is "AB1234"
    And the response field "user_name" is "Test User"
    And the response field "login_time" is an ISO 8601 timestamp with offset "+05:30"
    And the response body does not contain the broker access token

  @negative @req-AUTH-5
  Scenario Outline: The session endpoint never returns 401, it reports unauthenticated
    Given <cookie_state>
    When the client sends GET "/api/auth/session"
    Then the response status is 200
    And the response body is:
      """
      {"authenticated": false, "user_id": null, "user_name": null, "login_time": null}
      """

    Examples:
      | cookie_state                                               |
      | no "st_session" cookie is sent                             |
      | the "st_session" cookie is "unknown-value"                 |
      | the "st_session" cookie belongs to a deactivated session   |

  # ---------------------------------------------------------------------------
  # Logout (POST /api/auth/logout)
  # ---------------------------------------------------------------------------

  @smoke @req-AUTH-6
  Scenario: Logging out deactivates the session and deletes the cookie
    Given an active session with a valid "st_session" cookie
    When the client sends POST "/api/auth/logout"
    Then the response status is 200
    And the response body is {"status": "ok"}
    And the response deletes cookie "st_session" on Path "/"
    And the "auth_session" row is no longer active
    And GET "/api/funds" with the old cookie returns 401 with detail "Not authenticated"

  @req-AUTH-6
  Scenario: Logging out without a session is harmless
    Given no "st_session" cookie is sent
    When the client sends POST "/api/auth/logout"
    Then the response status is 200
    And the response body is {"status": "ok"}

  # ---------------------------------------------------------------------------
  # Protected endpoints and broker session expiry (cross-cutting)
  # ---------------------------------------------------------------------------

  @negative @req-AUTH-5
  Scenario Outline: Protected endpoints reject requests without an active session
    Given no "st_session" cookie is sent
    When the client sends <method> "<path>"
    Then the response status is 401
    And the response field "detail" is "Not authenticated"

    Examples:
      | method | path                                                           |
      | GET    | /api/funds                                                     |
      | GET    | /api/master-contract/status                                    |
      | POST   | /api/master-contract/download                                  |
      | GET    | /api/symbols/search?q=SBIN                                     |
      | GET    | /api/chart/intervals                                           |
      | GET    | /api/chart/history?symbol=NIFTY&exchange=NSE_INDEX&interval=5m |
      | GET    | /api/orders/symbol?symbol=SBIN&exchange=NSE                    |
      | POST   | /api/orders                                                    |
      | POST   | /api/orders/11/modify                                          |
      | POST   | /api/orders/11/cancel                                          |
      | POST   | /api/orders/close-position                                     |
      | GET    | /api/agent/status                                              |
      | GET    | /api/agent/settings                                            |
      | POST   | /api/agent/chat                                                |
      | GET    | /api/agent/risk                                                |

  @negative @req-AUTH-7
  Scenario Outline: An expired broker session deactivates the SmartTrader session
    Given an active session with a valid "st_session" cookie
    And the TradeSmart "<endpoint>" call answers <broker_answer>
    When the client sends GET "<path>"
    Then the response status is 401
    And the response field "detail" is "Session expired, please log in again"
    And the "auth_session" row is no longer active
    And GET "/api/auth/session" returns "authenticated" false

    Examples:
      | path                                                       | endpoint  | broker_answer                                                       |
      | /api/funds                                                 | Limits    | HTTP 401                                                            |
      | /api/funds                                                 | Limits    | {"stat": "Not_Ok", "emsg": "Session Expired :  Invalid Acct Id "}   |
      | /api/chart/history?symbol=SBIN&exchange=NSE&interval=5m    | TPSeries  | HTTP 401                                                            |
      | /api/orders/symbol?symbol=SBIN&exchange=NSE                | OrderBook | {"stat": "Not_Ok", "emsg": "Session Expired :  Invalid Acct Id "}   |

  @negative @req-AUTH-7
  Scenario: A stored access token that can no longer be decrypted ends the session
    Given an active session whose "access_token_enc" was encrypted with a different "secret.key"
    When the client sends GET "/api/funds"
    Then the response status is 401
    And the response field "detail" is "Session expired, please log in again"
    And the "auth_session" row is no longer active
    And no request was sent to TradeSmart
