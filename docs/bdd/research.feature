@research
Feature: Fundamental research with FinEdge: API key, research tools, reports, comparisons and recommendations
  As an AI-assisted trader
  I want the agent to read company fundamentals and price history with my own FinEdge API key
  So that I can ask fundamental questions, get a research report, compare companies and get a medium or long
  term buy or sell research view, with every figure taken from the data

  Code: backend/app/routers/agent.py (FinEdge key routes, _agent_for), backend/app/agent/finedge.py,
        backend/app/agent/research.py, backend/app/agent/builder.py (RESEARCH_RULES, RESEARCH_OFF_RULE),
        frontend bundle AgentConfigPage-*.js

  Background:
    Given the SmartTrader backend is running
    And an active session for user_id "AB1234" with a valid broker access token
    And the LLM provider and the FinEdge Financials API are replaced by test doubles
    And the FinEdge test double covers "ITC" and "HINDUNILVR" and answers 401 "ER002 - user not authorized to view this content" for any other company

  # ---------------------------------------------------------------------------
  # FinEdge API key in Agent settings
  # ---------------------------------------------------------------------------

  @smoke @req-AGENT-15 @req-NFR-14
  Scenario: Saving a FinEdge API key stores it encrypted and returns only a mask
    Given no FinEdge API key is saved
    When the client sends GET "/api/agent/finedge"
    Then the response body is {"configured": false, "masked_key": null, "updated_at": null}
    When the client sends PUT "/api/agent/finedge" with body {"api_key": "fe-test-key-12345678"}
    Then the response status is 200
    And the response field "configured" is true
    And the response field "masked_key" is "********5678"
    And the response field "updated_at" is an ISO 8601 timestamp with offset "+05:30"
    And the response body does not contain "fe-test-key-12345678"
    And the "agent_secret" row "finedge_api_key" holds a Fernet token that does not contain the key
    And the FinEdge research cache is cleared
    And the server log says "FinEdge API key saved" without the key

  @negative @req-AGENT-15
  Scenario Outline: A malformed key is rejected with 422 and nothing is stored
    Given no FinEdge API key is saved
    When the client sends PUT "/api/agent/finedge" with body {"api_key": <api_key>}
    Then the response status is 422
    And the response field "detail" is a list of validation errors naming "api_key"
    And no FinEdge API key is saved

    Examples:
      | api_key              | reason                        |
      | "short"              | fewer than 8 characters       |
      | "has a space in it"  | contains whitespace           |
      | 300 times "x"        | more than 256 characters      |

  @req-AGENT-15 @req-NFR-14
  Scenario: The saved key is never returned by any endpoint
    Given the FinEdge API key "fe-test-key-12345678" is saved
    When the client sends GET "/api/agent/finedge"
    And the client sends GET "/api/agent/settings"
    Then neither response body contains "fe-test-key-12345678"
    And the settings field "finedge" has "configured" true and "masked_key" "********5678"
    And the settings field "research_tools" lists 14 tools with "name" and "description", starting with "search_companies" and "get_company_profile"

  @req-AGENT-15
  Scenario: Removing the key
    Given the FinEdge API key "fe-test-key-12345678" is saved
    When the client sends DELETE "/api/agent/finedge"
    Then the response status is 200
    And the response body is {"configured": false, "masked_key": null, "updated_at": null}
    And the "agent_secret" row "finedge_api_key" no longer exists
    And the FinEdge research cache is cleared
    And the server log says "FinEdge API key removed"

  @negative @req-NFR-14
  Scenario: The key is masked in FinEdge errors and never leaks through network failures
    Given the FinEdge client sends the key as the "token" query parameter
    When FinEdge answers 400 with a body that contains the key
    Then the FinEdge error has status 400 and a message where the key is replaced with "***"
    When the connection to FinEdge fails with an error text that contains the request URL
    Then the FinEdge error has status 0 and its message does not contain the key

  @req-AGENT-16
  Scenario: The FinEdge client retries busy answers and encodes symbols for the URL path
    When FinEdge answers 429 twice and then 200 {"ok": true}
    Then the client returns {"ok": true} after three requests
    And statuses 429, 500, 502, 503 and 504 are retried at most twice, after 1 s and 2 s
    When the company profile of "m&m" is requested
    Then the request path ends with "/api/v1/company-profile/M%26M"
    When FinEdge answers 200 with an empty body
    Then the client returns no data instead of an error

  @smoke @req-AGENT-15 @req-AGENT-20
  Scenario Outline: Testing the key reports whether the plan covers all companies or only the sample
    Given the FinEdge API key "fe-test-key-12345678" is saved
    And FinEdge answers the "ITC" company profile with 200 {"name": "ITC Ltd"}
    And FinEdge answers the "TCS" company profile with <tcs answer>
    When the client sends POST "/api/agent/finedge/test"
    Then the response status is 200
    And the response field "ok" is true
    And the response field "coverage" is "<coverage>"
    And the response field "sample_company" is "ITC Ltd"
    And the response field "latency_ms" is the ITC call time in milliseconds
    And the settings page shows "<message>"

    Examples:
      | tcs answer                                                    | coverage | message                                                                                 |
      | 200 {"name": "TCS"}                                           | all      | Key works and covers all companies. FinEdge answered in N s.                            |
      | 401 "ER002 - user not authorized to view this content"        | sample   | Key works, but this plan only covers the sample companies ITC, RELIANCE and HDFCBANK.   |

  @negative @req-AGENT-15
  Scenario: Testing without a saved key returns 409
    Given no FinEdge API key is saved
    When the client sends POST "/api/agent/finedge/test"
    Then the response status is 409
    And the response field "detail" is "Save a FinEdge API key first."
    And no FinEdge call is made

  @negative @req-AGENT-15
  Scenario Outline: FinEdge failures during the key test are mapped without a 401
    Given the FinEdge API key "fe-test-key-12345678" is saved
    And FinEdge answers <answer>
    When the client sends POST "/api/agent/finedge/test"
    Then the response status is <status>
    And the response field "detail" is "<detail>"
    And the SmartTrader session stays active

    Examples:
      | answer                                                   | status | detail                                                   |
      | 401 "ER006 - missing or invalid token" to every call     | 422    | FinEdge rejected the API key. Check it and try again.    |
      | with a network failure                                   | 502    | FinEdge is unreachable. Try again in a minute.           |
      | 500 "Internal error" to every call, including retries    | 502    | FinEdge error (500): Internal error                      |

  @ux @req-AGENT-15
  Scenario: The settings card saves, then tests the key, and can remove it
    Given the Agent settings page is open and no FinEdge API key is saved
    Then the "FinEdge API key" card shows the badge "Not set" and the hint "Get a key at finedgeapi.com. The free Basic plan only covers ITC, RELIANCE and HDFCBANK."
    When the user pastes a key into the password field and clicks "Save key"
    Then the field is cleared, the badge changes to "Key saved" and the key test runs by itself
    And the placeholder shows "Saved key ********5678"
    And the test result shows the coverage message, or "Test failed" with the detail
    When the user clicks "Remove key"
    Then the badge changes back to "Not set" and the test result is cleared

  @ux @req-AGENT-15 @req-AGENT-16
  Scenario: Research tools are listed and dimmed until a key is saved
    Given the Agent settings page is open and no FinEdge API key is saved
    Then the Tools card lists the 14 research tools after the read tools
    And each research tool is dimmed and its description ends with "(needs a FinEdge key)"
    When a FinEdge API key is saved
    Then the research tools are shown at full strength without the suffix

  # ---------------------------------------------------------------------------
  # Agent toolset and prompt
  # ---------------------------------------------------------------------------

  @smoke @req-AGENT-16
  Scenario: The agent gets the research tools and rules only when a key is saved
    Given ChatGPT sign-in state is signed in
    And agent trading is switched off
    And no FinEdge API key is saved
    When a chat turn starts with "Research ITC"
    Then the agent tools include no research tool such as "compare_companies"
    And the system prompt says research is off and a FinEdge API key can be added in Agent settings
    And the agent allows at most 20 tool calls per turn
    When the FinEdge API key "fe-test-key-12345678" is saved and another chat turn starts
    Then the agent tools are the ten read tools plus "search_companies", "get_company_profile", "get_financial_statements", "get_financial_ratios", "get_growth_metrics", "get_stock_quotes", "get_price_performance", "get_valuation_history", "get_peers", "compare_companies", "get_shareholding", "get_dividends_and_actions", "get_announcements" and "get_index_overview"
    And the system prompt contains the fundamental research rules
    And the agent allows at most 40 tool calls per turn
    And the key does not appear in the prompt or in any tool argument

  # ---------------------------------------------------------------------------
  # Research tools on canned FinEdge answers
  # ---------------------------------------------------------------------------

  @smoke @req-AGENT-16
  Scenario: Financial statements are in Rs crore, oldest period first, with growth
    Given FinEdge returns consolidated annual P&L rows for "ITC" for FY 2024, 2026 and 2025, in that order
    And revenueFromOperations is 70,000, 84,000 and 75,000 crore in rupees, EPS is 16.0, 17.6 and 16.4 and financeCosts is 0
    When the "get_financial_statements" tool runs with {"symbol": "itc", "periods": 2}
    Then the result field "basis" is "consolidated"
    And the row periods are "Mar 2025" then "Mar 2026"
    And the latest row has "revenueFromOperations" 84000.0, "eps_rs" 17.6 and "revenue_growth_pct" 12.0
    And the latest row has no "financeCosts" field because zero lines are left out

  @req-AGENT-16
  Scenario: Consolidated statements fall back to standalone
    Given FinEdge has no consolidated P&L rows for "HINDUNILVR" but has standalone rows
    When the "get_financial_statements" tool runs with {"symbol": "HINDUNILVR", "periods": 1}
    Then the result field "basis" is "standalone"

  @negative @req-AGENT-16
  Scenario Outline: Statement arguments are validated before any FinEdge call
    When the "get_financial_statements" tool runs with {"symbol": "ITC", "statement": "<statement>", "period": "<period>"}
    Then the tool returns "<message>"

    Examples:
      | statement | period | message                                                  |
      | xx        | annual | Error: statement must be pl, bs or cf.                   |
      | bs        | ttm    | Error: period for bs must be one of annual, quarterly, ytd. |

  @req-AGENT-16
  Scenario: Ratios are converted to percent and leverage stays in times
    Given FinEdge returns profitability ratios for "ITC" with a "TTM" row of netMargin 0.25 and returnOnEquity 0.28
    And leverage ratios with a "TTM" row of totalDebtToEquity 0.03
    When the "get_financial_ratios" tool runs with {"symbol": "ITC"}
    Then the last row has "period" "TTM" and "netMargin" 25.0
    And the last row has "totalDebtToEquity" 0.03

  @negative @req-AGENT-16 @req-AGENT-20
  Scenario Outline: Tool failures are returned to the model as readable text
    Given FinEdge answers <answer>
    When the "get_company_profile" tool runs with {"symbol": "<symbol>"}
    Then the tool returns text starting with "<text>"
    And the text does not contain the key

    Examples:
      | answer                                              | symbol | text                                                                                            |
      | 401 "ER002 - user not authorized to view this content" | TCS  | Not available: the FinEdge plan on this API key does not cover this company or data. The free Basic plan only covers ITC, RELIANCE and HDFCBANK. |
      | 401 "ER006 - missing or invalid token"              | ITC    | Error: FinEdge rejected the API key. Ask the user to update it in Agent settings.              |
      | 404                                                 | XYZ    | Not found: FinEdge has no data for that symbol. Use search_companies to find the right symbol. |
      | with a network failure                              | ITC    | Error: FinEdge is unreachable (ConnectError). Try again in a minute.                           |

  @req-AGENT-16
  Scenario: Company search puts exact matches first and accepts BSE codes
    Given the FinEdge symbol list has "ITC" (BSE 500875), "ITCHOTELS" (BSE 544325) and "HDFCBANK" (BSE 500180)
    When the "search_companies" tool runs with {"query": "itc"}
    Then the matches are "ITC" then "ITCHOTELS"
    When the "search_companies" tool runs with {"query": "500180"}
    Then the first match is "HDFCBANK"

  @req-AGENT-16
  Scenario: The company profile trims the description and links the latest annual report
    Given FinEdge returns a profile for "ITC" with a 2,000 character description and an annual report for 2025
    When the "get_company_profile" tool runs with {"symbol": "ITC"}
    Then the result "description" is the first 900 characters followed by " ..."
    And the result "latest_annual_report" has "year" 2025 and the report URL

  @req-AGENT-20
  Scenario: Quotes fall back to one by one when a symbol is outside the plan
    Given FinEdge refuses the quote batch for "ITC" and "TCS" because "TCS" is outside the plan
    And FinEdge quotes "ITC" alone at current_price 250.0, change "-1.25%", high52 400.0 and low52 240.0
    When the "get_stock_quotes" tool runs with {"symbols": "ITC, TCS"}
    Then the "ITC" quote has "change_pct" -1.25 and "pct_below_52w_high" -37.5
    And the result field "not_found" is ["TCS"]

  @req-AGENT-18
  Scenario: Peers leave out the company and are ranked by market cap
    Given FinEdge lists the industry peers of "ITC" as " HINDUNILVR", "ITC" and " NESTLEIND"
    And "HINDUNILVR" has a quote with market cap 500000.0 and "NESTLEIND" is outside the plan
    When the "get_peers" tool runs with {"symbol": "ITC"}
    Then the peers are "HINDUNILVR" then "NESTLEIND"
    And the "NESTLEIND" peer has "market_cap_cr" null

  @smoke @req-AGENT-18 @req-AGENT-20
  Scenario: Comparing companies marks uncovered companies and mixed accounting bases
    Given "ITC" has consolidated figures, "HINDUNILVR" has only standalone P&L rows and "TCS" is outside the plan
    When the "compare_companies" tool runs with {"symbols": "ITC,HINDUNILVR,TCS"}
    Then the "ITC" row has "revenue_cr" 84000.0, "roe_pct" 28.0, "pe" 19.9, "revenue_cagr_3y_pct" 5.56 and "price" 250.0
    And the "HINDUNILVR" row has "basis" "standalone"
    And the "TCS" row has "error" "not covered by this FinEdge plan"
    And the result field "note" is "Mixed consolidated and standalone figures: compare with care."

  @negative @req-AGENT-18
  Scenario Outline: A comparison needs 2 to 6 companies
    When the "compare_companies" tool runs with {"symbols": "<symbols>"}
    Then the tool returns "Error: give between 2 and 6 symbols."

    Examples:
      | symbols                                   |
      | ITC                                       |
      | ITC,HINDUNILVR,NESTLEIND,TCS,INFY,WIPRO,SBIN |

  @req-AGENT-16
  Scenario: Announcements skip routine filings and are sorted newest first
    Given FinEdge returns filings for "ITC": "Trading Window" closure, "Outcome of Board Meeting" (older) and "Acquisition" (newer)
    When the "get_announcements" tool runs with {"symbol": "ITC"}
    Then the filing categories are "Acquisition" then "Outcome of Board Meeting"

  # ---------------------------------------------------------------------------
  # Reports and recommendations (prompt rules, model behaviour)
  # ---------------------------------------------------------------------------

  @req-AGENT-17
  Scenario: A research report follows the fixed structure and has no rating unless asked
    Given a FinEdge API key is saved and the model follows the research rules
    When the user asks "Give me a research report on ITC"
    Then the agent calls the research tools before writing
    And the report has the sections summary, business, financial performance over 5 years, profitability and returns, balance sheet and cash flow, valuation against its own history and its peers, shareholding, dividends, recent filings, key risks and conclusion
    And every figure carries its unit and period and the report states whether figures are consolidated or standalone
    And the report contains no Buy, Accumulate, Hold, Reduce or Sell rating

  @req-AGENT-18
  Scenario: A comparison answer is one table followed by who leads
    Given a FinEdge API key is saved and the model follows the research rules
    When the user asks "Compare ITC, HINDUNILVR and NESTLEIND"
    Then the agent calls "compare_companies"
    And the answer shows one table with a row per metric and a column per company
    And it explains who leads on growth, profitability, balance sheet strength and valuation

  @req-AGENT-19
  Scenario: A recommendation request without a horizon makes the agent ask for one
    Given a FinEdge API key is saved and the model follows the research rules
    When the user asks "Should I buy ITC?"
    Then the agent asks whether the user means medium term (6 to 18 months) or long term (3 years or more)
    And it gives no rating until the user answers

  @req-AGENT-19
  Scenario Outline: A buy or sell research view states the rating and horizon and ends with the disclaimer
    Given a FinEdge API key is saved and the model follows the research rules
    When the user asks for a buy or sell view on "ITC" for the <horizon>
    Then the agent weighs <factors>
    And the answer starts with a rating of Buy, Accumulate, Hold, Reduce or Sell and the horizon
    And it gives the thesis, supporting numbers, a valuation view with the method used, risks, what would change the view and the data date
    And it never promises a return
    And it ends with "This is a research view for education, not investment advice. Consult a SEBI registered investment adviser before investing."
    And no order tool is called

    Examples:
      | horizon                       | factors                                                                                                   |
      | medium term (6 to 18 months)  | recent quarterly earnings momentum, price trend against the 50 and 200 day averages, valuation percentile and near-term catalysts in filings |
      | long term (3 years or more)   | 5 year growth, return on equity and capital, cash conversion, debt, dividend record and valuation against history |

  @req-AGENT-20
  Scenario: On a sample-only plan the agent names the missing data and carries on
    Given the saved FinEdge key covers only the sample companies
    And the model follows the research rules
    When the user asks "Compare ITC and TCS"
    Then the answer says that TCS is not covered by the FinEdge plan
    And it still reports the ITC figures from the tool results
