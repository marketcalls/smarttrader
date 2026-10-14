@platform
Feature: Platform behaviour: health, static frontend serving and security at rest
  As the operator of a single user SmartTrader install
  I want the backend to serve the built UI, report health and keep secrets out of the browser, logs and plain text storage
  So that the app is safe to run on a local machine

  Code: backend/app/main.py, backend/app/config.py, backend/app/crypto.py, backend/app/db.py, backend/app/deps.py

  Background:
    Given the SmartTrader backend is running at "http://127.0.0.1:8000"

  @smoke @req-NFR-10
  Scenario: Health check
    When the client sends GET "/api/health"
    Then the response status is 200
    And the response body is {"status": "ok"}

  @req-UX-5 @req-NFR-9
  Scenario Outline: The built frontend is served with an SPA fallback
    Given "frontend/dist/index.html" and "frontend/dist/assets/index-abc123.js" exist
    When the browser sends GET "<path>"
    Then the response is <result>

    Examples:
      | path                       | result                                  |
      | /                          | the contents of "index.html"            |
      | /dashboard                 | the contents of "index.html"            |
      | /trading                   | the contents of "index.html"            |
      | /agent/config              | the contents of "index.html"            |
      | /assets/index-abc123.js    | that JavaScript file                    |
      | /../backend/data/secret.key | the contents of "index.html", never a file outside "frontend/dist" |

  @negative @req-UX-5 @req-NFR-9
  Scenario Outline: API and callback paths never fall back to the UI
    Given "frontend/dist/index.html" exists
    When the browser sends GET "<path>"
    Then the response status is 404
    And the response field "detail" is "Not Found"

    Examples:
      | path                  |
      | /api                  |
      | /api/unknown          |
      | /tradesmart/unknown   |

  @negative @req-UX-5 @req-NFR-9
  Scenario: Without a frontend build only the API is served
    Given "frontend/dist/index.html" does not exist
    When the browser sends GET "/dashboard"
    Then the response status is 404
    And the response field "detail" is "Frontend not built. Run npm run build in frontend/."

  @req-UX-5 @req-NFR-9
  Scenario Outline: CORS allows only the Vite dev server origins with credentials
    When a browser preflight request arrives with Origin "<origin>"
    Then the CORS allow origin header is <allowed>

    Examples:
      | origin                 | allowed                  |
      | http://127.0.0.1:5173  | "http://127.0.0.1:5173"  |
      | http://localhost:5173  | "http://localhost:5173"  |
      | https://evil.example   | absent                   |

  @req-NFR-3 @req-NFR-4
  Scenario: Secrets are encrypted at rest with a generated key
    Given "data/secret.key" does not exist
    When the backend starts
    Then a Fernet key is written to "data/secret.key" with file mode 0600 where the OS supports it
    When broker credentials, a broker session and ChatGPT tokens are subsequently saved
    Then those secrets are encrypted before storage
    And "broker_config.api_secret_enc", "auth_session.access_token_enc" and "agent_secret.value_enc" hold Fernet ciphertext
    And "auth_session.session_hash" holds only the SHA-256 of the cookie value, never the raw cookie

  @req-NFR-6
  Scenario: The OAuth authorization code is redacted from the access log
    When the browser is redirected to GET "/tradesmart/callback?code=<AUTH_CODE>&state=x"
    Then the uvicorn access log line contains "code=[redacted]"
    And the access log does not contain "<AUTH_CODE>"

  @req-NFR-8
  Scenario: The database is ready at startup
    Given the application database is empty
    When the backend starts
    Then all application ORM tables exist in "data/smarttrader.db" with WAL journal mode
    And a "master_contract_status" row with id 1 and status "idle" exists

  @req-NFR-8
  Scenario: An older database gains the pending approval column
    Given the application database has an older "agent_message" table without "pending_json"
    When the backend starts
    Then the missing "agent_message.pending_json" column is added
