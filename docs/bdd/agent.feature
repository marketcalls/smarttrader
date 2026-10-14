@agent
Feature: AI trading agent: settings, ChatGPT sign-in, streaming chat, tools and order safety
  As a logged in SmartTrader user
  I want a chat assistant that reads my market data and account through my TradeSmart session
  And that can only trade when I switch trading on, approve each order and stay inside my risk limits
  So that I get help without any order going out by accident

  Code: backend/app/routers/agent.py, backend/app/agent/builder.py, backend/app/agent/tools.py,
        backend/app/agent/trading.py, backend/app/agent/chatgpt_auth.py, backend/app/agent/litellm_patches.py

  Background:
    Given the SmartTrader backend is running
    And an active session for user_id "AB1234" with a valid broker access token
    And the symbol master contains "SBIN" on "NSE" with brsymbol "SBIN-EQ", token "3045", tick_size 0.05, lotsize 1 and instrumenttype "EQ"
    And GetQuotes for "SBIN" answers {"stat": "Ok", "lp": "959.10"}
    And the LLM provider and the TradeSmart REST API are replaced by test doubles

  # ---------------------------------------------------------------------------
  # Agent settings and status
  # ---------------------------------------------------------------------------

  @smoke @req-AGENT-2
  Scenario: Default settings are created on first use
    Given no "agent_setting" row exists
    When the client sends GET "/api/agent/settings"
    Then the response status is 200
    And the response field "model" is "gpt-5.5" when that model is in "models", else the first available model, or "gpt-5.5" if the list is empty
    And the response field "reasoning_effort" is "medium"
    And the response field "instructions" is ""
    And the response field "reasoning_efforts" is ["low", "medium", "high"]
    And the response field "tools" lists "search_symbols", "get_quote", "get_history", "get_technicals", "get_funds", "get_positions", "get_holdings", "get_order_book", "get_trade_book" and "get_master_contract_status"
    And the response field "order_tools" lists "place_order", "modify_order" and "cancel_order"
    And the response field "trading_enabled" is false

  @req-AGENT-2
  Scenario: Saving settings trims the instructions
    Given "<MODEL>" is one of the available models
    When the client sends PUT "/api/agent/settings" with body {"model": "<MODEL>", "reasoning_effort": "high", "instructions": " short "}
    Then the response status is 200
    And the response body is {"model": "<MODEL>", "reasoning_effort": "high", "instructions": "short"}

  @req-AGENT-2
  Scenario Outline: Empty reasoning effort and empty instructions are stored as null
    When the client sends PUT "/api/agent/settings" with body {"model": "<MODEL>", "reasoning_effort": <effort>, "instructions": <instructions>}
    Then the response status is 200
    And the response field "reasoning_effort" is null
    And the response field "instructions" is ""

    Examples:
      | effort | instructions |
      | null   | null         |
      | ""     | "   "        |

  @negative @req-AGENT-2
  Scenario Outline: Invalid settings are rejected
    When the client sends PUT "/api/agent/settings" with body <body>
    Then the response status is 422
    And the response field "detail" <detail>

    Examples:
      | body                                                              | detail                                                   |
      | {"model": "nope"}                                                 | is "Unknown model"                                       |
      | {"model": "<MODEL>", "reasoning_effort": "extreme"}               | is "Reasoning effort must be low, medium or high"        |
      | {"model": ""}                                                     | is a list of validation errors naming "model"            |
      | {"model": "<MODEL>", "instructions": "<4001 characters>"}         | is a list of validation errors naming "instructions"     |

  @req-AGENT-1 @req-AGENT-8
  Scenario: The status endpoint combines sign-in state, settings and the trading switch
    When the client sends GET "/api/agent/status"
    Then the response status is 200
    And the response has the fields "signed_in", "email", "plan", "provider", "model", "reasoning_effort", "instructions" and "trading_enabled"
    And the response field "provider" is "chatgpt"
    And the response contains no OAuth token

  # ---------------------------------------------------------------------------
  # ChatGPT device code sign-in
  # ---------------------------------------------------------------------------

  @smoke @req-AGENT-1
  Scenario: Starting the ChatGPT sign-in returns a device code
    When the client sends POST "/api/agent/chatgpt/login"
    Then the response status is 200
    And the response field "state" is "pending"
    And the response has a "user_code", a "verification_url" and a positive "expires_in"
    And the response field "error" is null

  @req-AGENT-1
  Scenario Outline: Polling the sign-in flow
    Given a ChatGPT sign-in flow that <flow_state>
    When the client sends GET "/api/agent/chatgpt/login"
    Then the response field "state" is "<state>"
    And the response field "user_code" is <user_code>
    And the response field "error" is <error>

    Examples:
      | flow_state                                    | state    | user_code         | error                                         |
      | is waiting for approval                       | pending  | the device code   | null                                          |
      | was approved and the tokens were saved        | complete | null              | null                                          |
      | passed its expiry time without approval       | expired  | null              | "The code expired before it was approved."    |
      | failed with an unexpected ValueError          | error    | null              | "Sign-in failed (ValueError)"                 |

  @req-AGENT-1
  Scenario: No sign-in flow in progress
    Given no ChatGPT sign-in flow was started
    When the client sends GET "/api/agent/chatgpt/login"
    Then the response body is {"state": "none"}

  @negative @req-AGENT-1
  Scenario: The device code request fails
    Given the ChatGPT device code endpoint is unreachable
    When the client sends POST "/api/agent/chatgpt/login"
    Then the response status is 502
    And the response field "detail" is "Could not start ChatGPT sign-in. Try again in a minute."

  @req-AGENT-1
  Scenario: Cancelling the flow and signing out
    Given a ChatGPT sign-in flow is pending
    When the client sends DELETE "/api/agent/chatgpt/login"
    Then the response body is {"state": "none"}
    When the client sends POST "/api/agent/chatgpt/logout"
    Then the response body is {"signed_in": false}
    And the stored ChatGPT tokens are deleted at the time of the logout response

  @negative @req-AGENT-1
  Scenario: A cancelled sign-in worker can save tokens after sign-out
    Given a ChatGPT sign-in worker is waiting for device approval
    When the client sends POST "/api/agent/chatgpt/logout"
    And that worker subsequently receives approval and completes its token exchange
    Then the worker writes the returned tokens to "agent_secret" despite no longer being the current flow
    And the account can become signed in again
    # Current implementation gap: cancel_login clears _flow but does not stop the worker.

  @req-AGENT-1 @req-NFR-3 @req-NFR-4
  Scenario: ChatGPT tokens are stored encrypted and the status reads the ID token claims
    When ChatGPT tokens with an ID token for email "me@example.com" and plan "plus" are saved
    Then the "agent_secret" row value does not contain "refresh_token"
    And GET "/api/agent/status" returns "signed_in" true, "email" "me@example.com" and "plan" "plus"
    When the tokens are cleared
    Then GET "/api/agent/status" returns "signed_in" false

  @req-AGENT-1 @req-NFR-3
  Scenario: The model library never starts an interactive console login
    Given no ChatGPT tokens are stored
    When the LiteLLM ChatGPT authenticator asks for an access token
    Then it raises an access token error instead of blocking for a console login

  @req-AGENT-3
  Scenario Outline: Testing the configured model
    Given ChatGPT sign-in state is <signed_in>
    And the model <behaviour>
    When the client sends POST "/api/agent/test"
    Then the response status is <status>
    And <outcome>

    Examples:
      | signed_in  | behaviour                     | status | outcome                                                                                       |
      | signed out | is not called                 | 409    | the response field "detail" is "Sign in with ChatGPT first."                                  |
      | signed in  | replies "OK"                  | 200    | the response has "ok" true, "model", "reply" "OK" and an integer "latency_ms"                  |
      | signed in  | raises "429 rate limit"       | 502    | the response field "detail" is "ChatGPT usage limit reached for this plan. Try again later or pick another model." |

  # ---------------------------------------------------------------------------
  # Chat (POST /api/agent/chat, Server-Sent Events)
  # ---------------------------------------------------------------------------

  @negative @req-AGENT-4
  Scenario: Chat requires a ChatGPT sign-in
    Given ChatGPT sign-in state is signed out
    When the client sends POST "/api/agent/chat" with body {"message": "hi"}
    Then the response status is 409
    And the response field "detail" is "Sign in with ChatGPT in Agent settings first."

  @negative @req-AGENT-4
  Scenario: Chat with a broker token that cannot be decrypted
    Given ChatGPT sign-in state is signed in
    And the active session's access token cannot be decrypted
    When the client sends POST "/api/agent/chat" with body {"message": "hi"}
    Then the response status is 401
    And the response field "detail" is "Session expired, please log in again"
    And the local "auth_session" row remains active

  @negative @req-AGENT-4
  Scenario Outline: Chat message validation
    Given ChatGPT sign-in state is signed in
    When the client sends POST "/api/agent/chat" with body <body>
    Then the response status is 422

    Examples:
      | body                                  |
      | {"message": ""}                       |
      | {"message": "<16001 characters>"}     |
      | {"conversation_id": "abc"}            |

  @smoke @req-AGENT-4 @req-AGENT-6
  Scenario: A chat turn streams frames and saves the transcript
    Given ChatGPT sign-in state is signed in
    And the model calls tool "get_quote" with {"symbol": "SBIN", "exchange": "NSE"} which returns {"ltp": 950.5} in 0.25 seconds
    And the model then answers "SBIN is at " and "950.50."
    When the client sends POST "/api/agent/chat" with body {"message": "Quote SBIN"}
    Then the response status is 200 with media type "text/event-stream"
    And the response has headers "Cache-Control: no-cache" and "X-Accel-Buffering: no"
    And the frame types in order are "start", "run", "tool_start", "tool_end", "token", "token", "usage", "done"
    And the "start" frame has a new "conversation_id" and the "model"
    And the "tool_end" frame has "ok" true and "duration" 0.25
    And the "usage" frame has "input_tokens", "output_tokens", "total_tokens", "reasoning_tokens", "duration", "ttft_ms" and "model"
    And the "done" frame has "reason" "stop"
    And GET "/api/agent/conversations/<conversation_id>" returns title "Quote SBIN" and messages with roles "user" and "assistant"
    And the assistant message has content "SBIN is at 950.50." and one tool named "get_quote"
    And GET "/api/agent/conversations" lists that conversation first

  @req-AGENT-4 @req-AGENT-6
  Scenario: A follow up message continues the same conversation
    Given ChatGPT sign-in state is signed in
    And a conversation "<conversation_id>" exists
    When the client sends POST "/api/agent/chat" with body {"message": "And its RSI?", "conversation_id": "<conversation_id>"}
    Then the "start" frame has "conversation_id" "<conversation_id>"
    And the agent session id is "<conversation_id>" so the last 8 runs are in the model context

  @req-AGENT-4 @req-AGENT-6
  Scenario Outline: The conversation title is the first 60 characters of the first message
    Given ChatGPT sign-in state is signed in
    When the client sends POST "/api/agent/chat" with body {"message": "<message>"}
    Then the new conversation title is "<title>"

    Examples:
      | message                                                                          | title                                                        |
      | Quote SBIN                                                                       | Quote SBIN                                                   |
      | Quote    SBIN   please                                                           | Quote SBIN please                                            |
      | Compare the daily RSI of SBIN, HDFCBANK and ICICIBANK over the last three months | Compare the daily RSI of SBIN, HDFCBANK and ICICIBANK over t |

  @negative @req-AGENT-4 @req-AGENT-6
  Scenario: Whitespace-only chat text passes raw length validation
    Given ChatGPT sign-in state is signed in
    When the client sends POST "/api/agent/chat" with body {"message": "   "}
    Then a new conversation has title "New chat"
    And its user message content is ""
    And the agent is invoked with an empty message

  @req-AGENT-4 @req-AGENT-6
  Scenario: An unknown conversation id creates a new conversation
    Given ChatGPT sign-in state is signed in
    When the client sends POST "/api/agent/chat" with body {"message": "Quote SBIN", "conversation_id": "missing"}
    Then the "start" frame contains a newly generated conversation id different from "missing"

  @req-AGENT-5 @req-AGENT-13
  Scenario: Without trading enabled the agent gets only the ten read tools and a read only rule
    Given ChatGPT sign-in state is signed in
    And agent trading is switched off
    When a chat turn starts
    Then the agent tools are "search_symbols", "get_quote", "get_history", "get_technicals", "get_funds", "get_positions", "get_holdings", "get_order_book", "get_trade_book" and "get_master_contract_status"
    And the system prompt says trading is switched off and the user can switch it on in Agent settings
    And the agent allows at most 20 tool calls per turn

  @negative @req-AGENT-5 @req-AGENT-13
  Scenario Outline: Tool failures are returned to the model as readable text
    Given the agent tool "<tool>" is called with <args>
    And <condition>
    Then the tool returns "<result>"
    And the "tool_end" frame for it has "ok" false

    Examples:
      | tool           | args                                                       | condition                                          | result                                                                                         |
      | get_quote      | {"symbol": "SBINX", "exchange": "NSE"}                     | the symbol is not in the master                    | Error: SBINX on NSE is not in the symbol master. Use search_symbols first.                     |
      | get_history    | {"symbol": "SBIN", "exchange": "NSE", "interval": "7m"}    | nothing else                                       | Error: interval must be one of 1m, 3m, 5m, 10m, 15m, 30m, 1h, 2h, D.                           |
      | get_funds      | {}                                                         | Limits answers HTTP 401                            | Error: the TradeSmart session has expired. Ask the user to log in to SmartTrader again.         |
      | get_order_book | {}                                                         | OrderBook answers {"stat": "Not_Ok", "emsg": "Invalid Input"} | Error from TradeSmart: Invalid Input                                                |
      | get_positions  | {}                                                         | PositionBook raises a connect timeout              | Error: get_positions failed (ConnectTimeout).                                                  |
      | search_symbols | {"query": "   "}                                          | nothing else                                       | Error: give a symbol or name to search for.                                                    |

  @req-AGENT-5 @req-AGENT-13
  Scenario Outline: Empty broker data is reported plainly, for example when the market is closed
    Given the agent tool "<tool>" is called with <args>
    And <condition>
    Then the tool returns "<result>"

    Examples:
      | tool           | args                                                    | condition                                                       | result                                                                   |
      | get_history    | {"symbol": "SBIN", "exchange": "NSE", "interval": "5m"} | TPSeries answers {"stat": "Not_Ok", "emsg": "no data"}           | No candles for SBIN on 5m. The market may be closed for this range.      |
      | get_technicals | {"symbol": "SBIN", "exchange": "NSE"}                   | EODChartData returns 10 candles                                 | Not enough candles for SBIN on D to compute indicators.                  |
      | get_positions  | {}                                                      | PositionBook answers {"stat": "Not_Ok", "emsg": "no data"}       | No positions today.                                                      |
      | get_order_book | {}                                                      | OrderBook answers []                                            | No orders today.                                                         |
      | get_trade_book | {}                                                      | TradeBook answers []                                            | No trades today.                                                         |
      | get_holdings   | {}                                                      | Holdings answers []                                             | No holdings.                                                             |

  @req-AGENT-5 @req-AGENT-13
  Scenario: Indicator math used by get_technicals
    Given closes 1 to 30
    Then SMA(5) is 28.0 and RSI(14) is 100.0
    And EMA(10) of thirty closes of 2.0 is 2.0
    And ATR(14) of twenty candles with high 11, low 9 and close 10 is 2.0

  @req-AGENT-4
  Scenario: Tool arguments that arrive only in the done events are recovered
    Given the ChatGPT stream announces a function call with empty arguments and sends no argument deltas
    When the "response.function_call_arguments.done" event carries the complete arguments
    Then the chat stream emits those arguments once at the tool call index
    And a tool call whose arguments were already streamed is left unchanged

  @negative @req-AGENT-14
  Scenario Outline: LLM provider errors end the stream with a readable message
    Given ChatGPT sign-in state is signed in
    And the model run fails with "<provider_error>"
    When the client sends POST "/api/agent/chat" with body {"message": "Quote SBIN"}
    Then the response status is 200
    And an "error" frame has "message" "<message>"
    And the last frame is {"type": "done", "reason": "error"}

    Examples:
      | provider_error                                   | message                                                                                   |
      | Not signed in to ChatGPT                         | ChatGPT sign-in is missing or expired. Open Agent settings and sign in again.            |
      | invalid refresh token                            | ChatGPT sign-in is missing or expired. Open Agent settings and sign in again.            |
      | Error code: 429 rate limit exceeded              | ChatGPT usage limit reached for this plan. Try again later or pick another model.        |
      | You have hit your usage limit                    | ChatGPT usage limit reached for this plan. Try again later or pick another model.        |
      | The model gpt-x does not exist                   | This model is not available on your ChatGPT plan. Pick another model in Agent settings.  |
      | Internal server error from upstream              | Internal server error from upstream                                                      |

  @negative @req-AGENT-14
  Scenario: A provider error message is cut to 400 characters
    Given ChatGPT sign-in state is signed in
    And the model run fails with a 1000 character message that matches no known pattern
    When the client sends POST "/api/agent/chat" with body {"message": "hi"}
    Then the "error" frame "message" is the first 400 characters of that message

  @req-AGENT-4
  Scenario: A running turn can be cancelled
    Given ChatGPT sign-in state is signed in
    And a chat turn with run id "run-1" is streaming
    When the client sends POST "/api/agent/chat/run-1/cancel"
    Then the response body is {"cancelled": true}
    And the stream ends with {"type": "done", "reason": "cancelled"}

  @req-AGENT-4
  Scenario: Cancelling an unknown run
    When the client sends POST "/api/agent/chat/unknown-run/cancel"
    Then the response body is {"cancelled": false}

  @req-AGENT-4
  Scenario: Closing the browser request cancels the run
    Given a chat turn with run id "run-1" is streaming
    When the event loop observes the request is disconnected while consuming the next event
    Then the agent run "run-1" is cancelled
    And accumulated text, tool calls or a pending approval are saved as the assistant message if any exist

  @negative @req-AGENT-4
  Scenario: Task cancellation persists output without guaranteeing a done frame
    Given a streaming run has accumulated assistant text
    When the stream task raises CancelledError
    Then the accumulated assistant text is saved in the finally block
    And CancelledError is re-raised without yielding the final "done" frame

  # ---------------------------------------------------------------------------
  # Conversations
  # ---------------------------------------------------------------------------

  @req-AGENT-6
  Scenario: Renaming and deleting a conversation
    Given a conversation "<conversation_id>" with a user and an assistant message
    When the client sends PATCH "/api/agent/conversations/<conversation_id>" with body {"title": "  SBIN notes  "}
    Then the response status is 200 and the response field "title" is "SBIN notes"
    When the client sends DELETE "/api/agent/conversations/<conversation_id>"
    Then the response body is {"status": "ok"}
    And no "agent_message" rows remain for that conversation
    And deletion of the Agno session "<conversation_id>" is attempted in "data/agent.db"

  @negative @req-AGENT-6
  Scenario: Whitespace-only rename produces an empty title
    Given a conversation "existing" exists
    When the client sends PATCH "/api/agent/conversations/existing" with body {"title": "   "}
    Then the response status is 200 and the response field "title" is ""

  @negative @req-AGENT-6
  Scenario: Transcript deletion succeeds when Agno cleanup fails
    Given a conversation "existing" has saved messages
    And deleting its Agno session raises an exception
    When the client sends DELETE "/api/agent/conversations/existing"
    Then the response body is {"status": "ok"}
    And the conversation and its messages are removed from the application database

  @req-AGENT-6 @req-AGENT-12
  Scenario: A later broker login can read the installation's earlier transcripts and audit
    Given a conversation and an audit row were saved under a previous broker account
    And a different broker account now has the active local session
    When the client lists conversations and order audit rows
    Then those earlier rows are included without filtering by broker user id

  @negative @req-AGENT-6
  Scenario Outline: Conversation not found or invalid title
    When the client sends <method> "/api/agent/conversations/<id>" with body <body>
    Then the response status is <status>

    Examples:
      | method | id      | body                       | status |
      | GET    | missing | none                       | 404    |
      | PATCH  | missing | {"title": "x"}             | 404    |
      | PATCH  | <id>    | {"title": ""}              | 422    |
      | PATCH  | <id>    | {"title": "<121 characters>"} | 422 |
      | DELETE | missing | none                       | 200    |

  # ---------------------------------------------------------------------------
  # Risk settings and order safety guardrails
  # ---------------------------------------------------------------------------

  @smoke @req-AGENT-8 @req-AGENT-9
  Scenario: Risk settings default to trading off with conservative limits
    Given no "agent_risk" row exists
    When the client sends GET "/api/agent/risk"
    Then the response body is:
      """
      {"trading_enabled": false, "max_order_value": 50000.0, "max_quantity": 500, "max_orders_per_day": 20,
       "allowed_exchanges": ["NSE", "BSE"], "exchanges": ["NSE", "BSE", "NFO", "BFO", "CDS", "MCX"]}
      """

  @req-AGENT-8 @req-AGENT-9
  Scenario: Allowed exchanges are stored in canonical order without duplicates
    When the client sends PUT "/api/agent/risk" with body {"trading_enabled": true, "max_order_value": 50000, "max_quantity": 500, "max_orders_per_day": 20, "allowed_exchanges": ["MCX", "NSE", "NSE"]}
    Then the response status is 200
    And the response field "allowed_exchanges" is ["NSE", "MCX"]
    And the response field "trading_enabled" is true

  @negative @req-AGENT-8 @req-AGENT-9
  Scenario Outline: Invalid risk settings are rejected
    When the client sends PUT "/api/agent/risk" with <change> applied to a valid body
    Then the response status is 422
    And the response field "detail" <detail>

    Examples:
      | change                                       | detail                                                   |
      | allowed_exchanges ["NSE", "NSE_INDEX"]       | is "Unknown exchange: NSE_INDEX"                         |
      | max_order_value 0                            | is a list of validation errors naming "max_order_value"  |
      | max_order_value 10000001                     | is a list of validation errors naming "max_order_value"  |
      | max_quantity 0                               | is a list of validation errors naming "max_quantity"     |
      | max_orders_per_day 1001                      | is a list of validation errors naming "max_orders_per_day" |

  @req-AGENT-10
  Scenario: Every order tool requires confirmation
    When the order tools are built
    Then "place_order", "modify_order" and "cancel_order" each have requires_confirmation true

  @smoke @req-AGENT-10
  Scenario: A proposed order pauses the run for approval
    Given ChatGPT sign-in state is signed in
    And agent trading is switched on
    And the model calls "place_order" with {"symbol": "SBIN", "exchange": "NSE", "side": "BUY", "quantity": 1} as tool call "call-1"
    When the client sends POST "/api/agent/chat" with body {"message": "Buy 1 SBIN"}
    Then a "confirm" frame has "run_id", "state" "pending" and one requirement with "id" "call-1" and "tool_name" "place_order"
    And the requirement "summary" is "BUY 1 SBIN on NSE at market (sent as a protected limit), MIS, MARKET"
    And the last frame is {"type": "done", "reason": "paused"}
    And the stored assistant message has "pending.state" "pending"
    And an "agent_order_audit" row with status "proposed" exists
    And nothing was sent to PlaceOrder

  @smoke @req-AGENT-10
  Scenario Outline: The user approves or rejects the proposed order
    Given a run "run-1" is paused for "place_order" tool call "call-1"
    When the client sends POST "/api/agent/chat/confirm" with body {"conversation_id": "CONVERSATION_ID", "run_id": "run-1", "decisions": {"call-1": <decision>}}
    Then the response status is 200 with media type "text/event-stream"
    And the first frame is {"type": "run", "run_id": "run-1"}
    And the requirement is <confirmed>
    And the audit statuses for the conversation are <audit>
    And the stored pending message has "state" "decided"

    Examples:
      | decision | confirmed                                                                   | audit                    |
      | true     | confirmed and the tool body runs                                            | "proposed" and then the tool's own rows |
      | false    | rejected with the note not to retry and the tool body does not run         | "proposed", "rejected"   |

  @negative @req-AGENT-10
  Scenario: A decision missing for a requirement counts as a rejection
    Given a run "run-1" is paused for "place_order" tool call "call-1"
    When the client sends POST "/api/agent/chat/confirm" with "decisions" {}
    Then the requirement "call-1" is rejected
    And an "agent_order_audit" row with status "rejected" exists

  @negative @req-AGENT-10
  Scenario Outline: An approval can only be used while it is pending
    Given <situation>
    When the client sends POST "/api/agent/chat/confirm" for run "run-1"
    Then the response status is 409
    And the response field "detail" is "<detail>"

    Examples:
      | situation                                                                                 | detail                                                          |
      | the approval for "run-1" was already decided                                              | This approval is no longer pending.                             |
      | a new chat message was sent in the conversation, which marked the approval "expired"     | This approval is no longer pending.                             |
      | the latest pending message belongs to run "run-2"                                         | This approval is no longer pending.                             |
      | the approval is pending but the Agno store has no paused run "run-1"                      | The paused run was not found. Ask again to propose the order.   |

  @negative @req-AGENT-10
  Scenario: Confirm body validation
    When the client sends POST "/api/agent/chat/confirm" with body {"conversation_id": "", "run_id": "run-1", "decisions": {}}
    Then the response status is 422

  @negative @req-AGENT-11
  Scenario Outline: The risk check runs again inside the approved tool and refuses without sending anything
    Given agent risk settings are <risk>
    When the approved "place_order" tool runs with <args>
    Then the tool returns "Refused by the risk check, nothing was sent: <reason>"
    And an "agent_order_audit" row with status "refused" and message "<reason>" exists
    And nothing was sent to PlaceOrder

    Examples:
      | risk                                         | args                                                                                     | reason                                                                 |
      | trading off                                  | {"symbol": "SBIN", "exchange": "NSE", "side": "BUY", "quantity": 10}                     | trading is switched off in Agent settings                              |
      | trading on, defaults                         | {"symbol": "SBIN", "exchange": "MCX", "side": "BUY", "quantity": 10}                     | MCX is not in the allowed exchanges (NSE, BSE)                         |
      | trading on, allowed_exchanges []             | {"symbol": "SBIN", "exchange": "NSE", "side": "BUY", "quantity": 10}                     | NSE is not in the allowed exchanges (none)                             |
      | trading on, defaults                         | {"symbol": "SBIN", "exchange": "NSE", "side": "BUY", "quantity": 501}                    | quantity must be between 1 and 500                                     |
      | trading on, defaults                         | {"symbol": "SBIN", "exchange": "NSE", "side": "BUY", "quantity": 0}                      | quantity must be between 1 and 500                                     |
      | trading on, defaults                         | {"symbol": "SBIN", "exchange": "NSE", "side": "BUY", "quantity": 100}                    | estimated value 96,390.00 is above the limit of 50,000.00              |
      | trading on, defaults                         | {"symbol": "SBIN", "exchange": "NSE", "side": "BUY", "quantity": 10, "price_type": "LIMIT"} | a LIMIT or SL order needs a price                                   |
      | trading on, defaults                         | {"symbol": "SBIN", "exchange": "NSE", "side": "BUY", "quantity": 10, "price_type": "SL-M"}  | an SL or SL-M order needs a trigger price                           |
      | trading on, defaults                         | {"symbol": "SBIN", "exchange": "NSE", "side": "HOLD", "quantity": 10}                    | side must be BUY or SELL                                               |
      | trading on, defaults                         | {"symbol": "SBIN", "exchange": "NSE", "side": "BUY", "quantity": 10, "product": "BO"}    | product must be MIS, CNC or NRML                                       |
      | trading on, defaults                         | {"symbol": "SBIN", "exchange": "NSE", "side": "BUY", "quantity": 10, "price_type": "STOP"} | price type must be MARKET, LIMIT, SL or SL-M                         |
      | trading on, defaults                         | {"symbol": "NOPE", "exchange": "NSE", "side": "BUY", "quantity": 10}                     | NOPE on NSE is not in the symbol master                                |

  @negative @req-AGENT-11
  Scenario: Trading switched off between proposal and approval still blocks the order
    Given a run is paused for a "place_order" proposal made while agent trading was on
    And the user switches agent trading off
    When the user approves the order
    Then the tool returns "Refused by the risk check, nothing was sent: trading is switched off in Agent settings"
    And nothing was sent to PlaceOrder

  @smoke @req-AGENT-11
  Scenario: An approved market order is sent as a protected limit and audited
    Given agent trading is switched on with default limits
    And PlaceOrder answers {"stat": "Ok", "norenordno": "ORD1"}
    When the approved "place_order" tool runs with {"symbol": "SBIN", "exchange": "NSE", "side": "BUY", "quantity": 10}
    Then the tool returns JSON with "status" "placed", "order_id" "ORD1", "sent_price" 963.9 and "estimated_value" 9639.0
    And the "note" says to check get_order_book and that it was sent as a limit at 963.9 with market price protection on LTP 959.1
    And the order sent to TradeSmart has brsymbol "SBIN-EQ", price type "MARKET" mapped to "LMT" and price 963.9
    And the audit statuses are "approved" and then "placed" with order_id "ORD1" and message "sent as MARKET 963.9"

  @negative @req-AGENT-11 @req-AGENT-9
  Scenario: The same order within 60 seconds is refused as a duplicate
    Given agent trading is switched on with default limits
    And the agent placed BUY 10 "SBIN" on "NSE" 30 seconds ago
    When the approved "place_order" tool runs with {"symbol": "SBIN", "exchange": "NSE", "side": "BUY", "quantity": 10}
    Then the tool returns "Refused by the risk check, nothing was sent: the same order was placed less than a minute ago"
    And only one order was sent to TradeSmart

  @negative @req-AGENT-11 @req-AGENT-9
  Scenario: The daily agent order limit is enforced per IST day
    Given agent trading is switched on with "max_orders_per_day" 1
    And the agent already placed one order since midnight IST
    When the approved "place_order" tool runs with {"symbol": "SBIN", "exchange": "NSE", "side": "BUY", "quantity": 5}
    Then the tool returns "Refused by the risk check, nothing was sent: the daily limit of 1 agent orders is reached"

  @negative @req-AGENT-11
  Scenario Outline: Broker failures inside the approved order tool are audited and reported
    Given agent trading is switched on with default limits
    And <broker_problem>
    When the approved "place_order" tool runs with {"symbol": "SBIN", "exchange": "NSE", "side": "BUY", "quantity": 10}
    Then the tool returns "<result>"
    And an "agent_order_audit" row with status "failed" exists

    Examples:
      | broker_problem                                                          | result                                                                                                  |
      | PlaceOrder answers {"stat": "Not_Ok", "emsg": "<broker rejection reason>"} | Order not placed. TradeSmart said: <broker rejection reason>                                         |
      | PlaceOrder answers HTTP 401                                              | Order not placed. TradeSmart said: the TradeSmart session has expired; the user must log in again    |
      | GetQuotes raises a connect timeout                                       | Order not placed: could not read the live price (ConnectTimeout while talking to TradeSmart).         |

  @negative @req-AGENT-11
  Scenario: A market order without a live price is refused by the agent
    Given agent trading is switched on with default limits
    And GetQuotes for "SBIN" answers {"stat": "Ok", "lp": "0"}
    When the approved "place_order" tool runs with {"symbol": "SBIN", "exchange": "NSE", "side": "BUY", "quantity": 10}
    Then the tool returns "Refused by the risk check, nothing was sent: no live price to protect a market order"

  @req-AGENT-10 @req-AGENT-11
  Scenario Outline: The agent's modify and cancel tools
    Given agent trading is switched on with default limits
    And OrderBook contains open order "11" for "SBIN-EQ" on "NSE"
    When the approved "<tool>" tool runs with <args>
    Then the tool returns "<result>"

    Examples:
      | tool         | args                                                                       | result                                                                                         |
      | modify_order | {"order_id": "11", "quantity": 5, "price_type": "LIMIT", "price": 945.5}   | {"status": "modified", "order_id": "11"}                                                       |
      | modify_order | {"order_id": "11", "quantity": 5, "price_type": "MARKET", "price": 945.5}  | Refused by the risk check, nothing was sent: price type must be LIMIT or SL                    |
      | modify_order | {"order_id": "11", "quantity": 0, "price_type": "LIMIT", "price": 945.5}   | Refused by the risk check, nothing was sent: quantity must be 1 to 500 and price above 0       |
      | modify_order | {"order_id": "11", "quantity": 100, "price_type": "LIMIT", "price": 945.5} | Refused by the risk check, nothing was sent: value above the limit of 50,000.00                |
      | modify_order | {"order_id": "77", "quantity": 5, "price_type": "LIMIT", "price": 945.5}   | Refused by the risk check, nothing was sent: order 77 is not in today's order book             |
      | cancel_order | {"order_id": "11"}                                                         | {"status": "cancel requested", "order_id": "11"}                                               |

  @negative @req-AGENT-11
  Scenario: Agent placement does not enforce lot-size multiples
    Given agent trading is switched on with MCX allowed and sufficient quantity and value limits
    And the symbol master contains a tradable MCX instrument with lot size 100
    And GetQuotes returns a positive live price and PlaceOrder accepts the order
    When the approved "place_order" tool places a LIMIT order for 150 units of that instrument at a positive price
    Then PlaceOrder receives quantity 150 without a local lot-size refusal

  @negative @req-AGENT-11
  Scenario: Agent modification relies on the broker for status exchange and trigger validation
    Given agent trading is switched on with only NSE allowed and sufficient quantity and value limits
    And OrderBook contains completed order "11" on MCX
    And ModifyOrder accepts the request
    When the approved "modify_order" tool runs with {"order_id": "11", "quantity": 1, "price_type": "SL", "price": 100, "trigger_price": 0}
    Then ModifyOrder is called for the MCX order with trigger price 0
    And the tool returns JSON with "status" "modified" and "order_id" "11"

  @req-AGENT-10 @req-AGENT-11
  Scenario Outline: The approval card summary for modify and cancel
    When an approval summary is built for "<tool>" with <args>
    Then the summary is "<summary>"

    Examples:
      | tool         | args                                                                     | summary                                      |
      | modify_order | {"order_id": "11", "quantity": 5, "price_type": "LIMIT", "price": 945.5} | Modify order 11: quantity 5, LIMIT at 945.5  |
      | cancel_order | {"order_id": "11"}                                                       | Cancel order 11                              |
      | place_order  | {"symbol": "SBIN", "exchange": "NSE", "side": "SELL", "quantity": 2, "price_type": "LIMIT", "price": 960, "product": "CNC"} | SELL 2 SBIN on NSE at 960, CNC, LIMIT |

  @req-AGENT-8 @req-AGENT-13
  Scenario: With trading on the agent gets the order tools and the trading rule
    Given ChatGPT sign-in state is signed in
    And agent trading is switched on
    When a chat turn starts
    Then the agent tools are the ten read tools plus "place_order", "modify_order" and "cancel_order"
    And the system prompt tells the model to quote first, propose exactly what was asked and never retry a rejected or refused order

  @req-AGENT-12
  Scenario: The order audit lists the latest 100 rows newest first
    Given 120 "agent_order_audit" rows exist
    When the client sends GET "/api/agent/orders/audit"
    Then the response contains 100 rows ordered by id descending
    And each row has "id", "created_at", "conversation_id", "tool", "args", "status", "order_id" and "message"
    And "created_at" is an ISO 8601 timestamp with offset "+05:30"
