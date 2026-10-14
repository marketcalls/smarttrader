import json

import httpx
import pytest

from app.agent import finedge, research
from app.models import AgentSecret
from app.routers import agent as agent_router
from tests.test_agent import StubAgent, env  # noqa: F401 - env is a fixture

KEY = "fe-test-key-12345678"


# ---------- the stored key and the settings routes ----------


def test_key_is_encrypted_and_never_returned(env):
    client, TestSession = env
    assert client.get("/api/agent/finedge").json() == {"configured": False, "masked_key": None, "updated_at": None}
    r = client.put("/api/agent/finedge", json={"api_key": KEY})
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True and body["masked_key"] == "********5678"
    assert KEY not in json.dumps(body)
    with TestSession() as db:
        stored = db.get(AgentSecret, finedge.SECRET_NAME).value_enc
    assert KEY not in stored
    assert finedge.read_key() == KEY
    settings = client.get("/api/agent/settings").json()
    assert settings["finedge"]["configured"] is True and KEY not in json.dumps(settings)
    assert [t["name"] for t in settings["research_tools"]][:2] == ["search_companies", "get_company_profile"]
    assert client.delete("/api/agent/finedge").json()["configured"] is False
    assert finedge.read_key() is None


@pytest.mark.parametrize("bad", ["short", "has a space in it", "x" * 300])
def test_invalid_keys_are_refused(env, bad):
    client, _ = env
    assert client.put("/api/agent/finedge", json={"api_key": bad}).status_code == 422
    assert finedge.read_key() is None


def test_key_test_route_maps_finedge_answers(env, monkeypatch):
    client, _ = env
    assert client.post("/api/agent/finedge/test").status_code == 409
    client.put("/api/agent/finedge", json={"api_key": KEY})
    monkeypatch.setattr(finedge, "test_key", lambda key: {"ok": True, "coverage": "sample", "sample_company": "ITC Ltd",
                                                          "latency_ms": 5})
    assert client.post("/api/agent/finedge/test").json()["coverage"] == "sample"

    def rejected(key):
        raise finedge.FinEdgeError(401, "ER006 - missing or invalid token")

    monkeypatch.setattr(finedge, "test_key", rejected)
    r = client.post("/api/agent/finedge/test")
    # Never 401: the frontend would read that as an expired SmartTrader session.
    assert r.status_code == 422 and "rejected" in r.json()["detail"]


def test_agent_gets_research_tools_only_with_a_key(env, monkeypatch):
    client, _ = env
    monkeypatch.setattr(agent_router.chatgpt_auth, "account_status", lambda: {"signed_in": True, "email": None, "plan": None})
    seen = []

    def fake_build(**kwargs):
        seen.append(kwargs)
        return StubAgent()

    monkeypatch.setattr(agent_router, "build_agent", fake_build)
    for _ in range(2):
        with client.stream("POST", "/api/agent/chat", json={"message": "Research ITC"}) as r:
            assert r.status_code == 200
            list(r.iter_lines())
        client.put("/api/agent/finedge", json={"api_key": KEY})
    without, with_key = ([t.__name__ for t in s["tools"]] for s in seen)
    assert "compare_companies" not in without and seen[0]["research"] is False
    assert "compare_companies" in with_key and seen[1]["research"] is True


# ---------- HTTP client ----------


def _client(handler) -> httpx.Client:
    return httpx.Client(base_url=finedge.BASE_URL, transport=httpx.MockTransport(handler))


def test_get_sends_the_token_and_masks_it_in_errors(monkeypatch):
    monkeypatch.setattr(finedge, "RETRY_DELAYS", (0, 0))
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.raw_path.split(b"?")[0] == b"/api/v1/company-profile/M%26M":
            return httpx.Response(200, json={"name": "Mahindra"})
        if request.url.path == "/busy" and len(calls) < 3:
            return httpx.Response(429, text="slow down")
        if request.url.path == "/busy":
            return httpx.Response(200, json={"ok": True})
        if request.url.path == "/empty":
            return httpx.Response(200, content=b"")
        return httpx.Response(400, text=f"bad request for token {KEY}")

    with _client(handler) as http:
        assert finedge.get(f"/api/v1/company-profile/{finedge.path_symbol('m&m')}", KEY, client=http) == {"name": "Mahindra"}
        assert calls[0].url.params["token"] == KEY
        assert finedge.get("/busy", KEY, client=http) == {"ok": True}
        assert len(calls) == 3
        assert finedge.get("/empty", KEY, client=http) is None
        with pytest.raises(finedge.FinEdgeError) as err:
            finedge.get("/other", KEY, {"symbol": None}, client=http)
    assert err.value.status_code == 400 and KEY not in err.value.message and "***" in err.value.message


def test_unreachable_error_does_not_leak_the_key():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot connect to {request.url}")

    with _client(handler) as http, pytest.raises(finedge.FinEdgeError) as err:
        finedge.get("/healthcheck", KEY, client=http)
    assert err.value.status_code == 0 and KEY not in err.value.message


@pytest.mark.parametrize("tcs_status,coverage", [(200, "all"), (401, "sample")])
def test_test_key_reports_plan_coverage(tcs_status, coverage):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/ITC"):
            return httpx.Response(200, json={"name": "ITC Ltd"})
        if tcs_status == 200:
            return httpx.Response(200, json={"name": "TCS"})
        return httpx.Response(401, text="ER002 - user not authorized to view this content")

    with _client(handler) as http:
        result = finedge.test_key(KEY, client=http)
    assert result["coverage"] == coverage and result["sample_company"] == "ITC Ltd"


def test_test_key_raises_for_a_wrong_key():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="ER006 - missing or invalid token")

    with _client(handler) as http, pytest.raises(finedge.FinEdgeError) as err:
        finedge.test_key("wrong-key-123", client=http)
    assert err.value.invalid_key and not err.value.not_in_plan


# ---------- research tools on canned FinEdge answers ----------

COVERED = {"ITC", "HINDUNILVR"}


def _pl(year: int, revenue: float, profit: float, eps: float) -> dict:
    return {"year": year, "period_end": year * 10000 + 331, "revenueFromOperations": revenue * 1e7,
            "otherIncome": 1e9, "profitBeforeTax": profit * 1.3e7, "profitLossForPeriod": profit * 1e7, "eps": eps,
            "financeCosts": 0}


def fake_finedge(path: str, api_key: str, params: dict | None = None) -> object:
    params = params or {}
    sym = path.rsplit("/", 1)[-1]
    if path.startswith("/api/v1/") and sym.isupper() and sym not in COVERED and "quote" not in path:
        raise finedge.FinEdgeError(401, "ER002 - user not authorized to view this content")
    if path == "/api/v2/quote":
        symbols = params["symbol"]
        if any(s not in COVERED for s in symbols) and len(symbols) > 1:
            raise finedge.FinEdgeError(401, "ER002 - user not authorized to view this content")
        if symbols[0] not in COVERED:
            raise finedge.FinEdgeError(401, "ER002 - user not authorized to view this content")
        return {s: {"current_price": 250.0, "change": "-1.25%", "high52": 400.0, "low52": 240.0,
                    "market_cap": 300000.0 if s == "ITC" else 500000.0} for s in symbols}
    if path.startswith("/api/v1/financials/"):
        if params["statement_type"] == "c" and sym == "HINDUNILVR":
            return {"symbol": sym, "financials": []}
        return {"symbol": sym, "financials": [_pl(2024, 70000, 20000, 16.0), _pl(2026, 84000, 22000, 17.6),
                                              _pl(2025, 75000, 20500, 16.4)]}
    if path.startswith("/api/v1/ratios/"):
        if params["ratio_type"] == "pr":
            return {"ratios": [{"header": "TTM", "year": 2026, "netMargin": 0.25, "returnOnEquity": 0.28},
                               {"header": "Mar 2025", "year": 2025, "netMargin": 0.27, "returnOnEquity": 0.3}]}
        if params["ratio_type"] == "le":
            return {"ratios": [{"header": "TTM", "year": 2026, "totalDebtToEquity": 0.03}]}
        return {"ratios": []}
    if path.startswith("/api/v1/financial-metrics/"):
        return {"financial_metrics": {"gr": {"revenueGrowth3years": 0.0556}, "av": {"dividendPayoutPct3YrsAvg": 0.78},
                                      "cu": {"freeCashFlow3yearsTotal": 455794400000}}[params["ratio_type"]]}
    if path.startswith("/api/v1/company-profile/"):
        return {"symbol": sym, "name": f"{sym} Ltd", "industry": "FMCG", "market_cap": 300000.0, "description": "x" * 2000}
    if path.startswith("/api/v1/company-reports/"):
        return {"reports": [{"year": 2025, "nse_url": "https://example.com/ar2025.pdf"}]}
    if path.startswith("/api/v1/annual-price-ratios/"):
        return {"price_ratios": [{"header": "TTM", "year": 2026, "pe": 19.9, "pb": 5.4}]}
    if path.startswith("/api/v1/peers/"):
        return {"peers": [" HINDUNILVR", "ITC", " NESTLEIND"]}
    if path == "/api/v1/corp-announcements":
        return [{"category": "Trading Window", "description": "closure", "timestamp_unix": 3},
                {"category": "Outcome of Board Meeting", "description": "Results", "timestamp_unix": 2,
                 "announcement_date": "2026-10-01 10:00:00", "pdf_file_link": "https://example.com/a.pdf"},
                {"category": "Acquisition", "description": "Bought a company", "timestamp_unix": 5,
                 "announcement_date": "2026-10-05 10:00:00"}]
    if path == "/api/v1/stock-symbols":
        return [{"symbol": "ITC", "name": "ITC Ltd", "nse_code": "ITC", "bse_code": "500875"},
                {"symbol": "ITCHOTELS", "name": "ITC Hotels Ltd", "nse_code": "ITCHOTELS", "bse_code": "544325"},
                {"symbol": "HDFCBANK", "name": "HDFC Bank Ltd", "nse_code": "HDFCBANK", "bse_code": "500180"}]
    raise AssertionError(f"unexpected call {path} {params}")


@pytest.fixture
def tools(monkeypatch):
    monkeypatch.setattr(finedge, "get", fake_finedge)
    finedge.clear_cache()
    yield {fn.__name__: fn for fn in research.build_research_tools(KEY)}
    finedge.clear_cache()


def test_statements_are_in_crore_oldest_first_with_growth(tools):
    out = json.loads(tools["get_financial_statements"](symbol="itc", periods=2))
    assert out["basis"] == "consolidated"
    assert [r["period"] for r in out["rows"]] == ["Mar 2025", "Mar 2026"]
    latest = out["rows"][-1]
    assert latest["revenueFromOperations"] == 84000.0 and latest["eps_rs"] == 17.6
    assert latest["revenue_growth_pct"] == 12.0 and "financeCosts" not in latest


def test_statements_fall_back_to_standalone(tools):
    out = json.loads(tools["get_financial_statements"](symbol="HINDUNILVR", periods=1))
    assert out["basis"] == "standalone"


def test_statement_arguments_are_validated(tools):
    assert tools["get_financial_statements"](symbol="ITC", statement="xx").startswith("Error")
    assert tools["get_financial_statements"](symbol="ITC", statement="bs", period="ttm").startswith("Error")


def test_ratios_are_converted_to_percent(tools):
    out = json.loads(tools["get_financial_ratios"](symbol="ITC"))
    ttm = out["rows"][-1]
    assert ttm["period"] == "TTM" and ttm["netMargin"] == 25.0 and ttm["totalDebtToEquity"] == 0.03


def test_tool_errors_are_readable(tools, monkeypatch):
    assert tools["get_company_profile"](symbol="TCS").startswith("Not available")

    def bad_key(*args, **kwargs):
        raise finedge.FinEdgeError(401, "ER006 - missing or invalid token")

    monkeypatch.setattr(finedge, "get", bad_key)
    assert "Agent settings" in tools["get_company_profile"](symbol="ITC")


def test_quotes_fall_back_to_one_by_one_when_a_symbol_is_outside_the_plan(tools):
    out = json.loads(tools["get_stock_quotes"](symbols="ITC, TCS"))
    assert out["quotes"]["ITC"]["change_pct"] == -1.25 and out["quotes"]["ITC"]["pct_below_52w_high"] == -37.5
    assert out["not_found"] == ["TCS"]


def test_large_peer_lists_are_quoted_in_batches_of_100(monkeypatch):
    calls = []

    def big(path, api_key, params=None):
        if path.startswith("/api/v1/peers/"):
            return {"peers": [f"P{i}" for i in range(150)]}
        calls.append(len(params["symbol"]))
        return {s: {"current_price": 0.03 if s == "P7" else 10.0,
                    "market_cap": {"P140": 1000.0, "P7": 3890399.91}.get(s, 1.0)} for s in params["symbol"]}

    monkeypatch.setattr(finedge, "get", big)
    peers = {fn.__name__: fn for fn in research.build_research_tools(KEY)}["get_peers"]
    out = json.loads(peers(symbol="ITC", max_peers=3))
    # P7 is a penny quote with an impossible market cap: it must not lead the ranking.
    assert calls == [100, 51] and out["peers"][0]["symbol"] == "P140"
    assert "P7" not in [p["symbol"] for p in out["peers"]]


def test_bse_codes_are_quoted_through_their_nse_symbol(tools):
    out = json.loads(tools["get_stock_quotes"](symbols="500875"))
    assert out["quotes"]["500875"]["price"] == 250.0 and out["not_found"] is None


def test_top_holders_are_sorted_and_in_percent(tools, monkeypatch):
    def holdings(path, api_key, params=None):
        if "ownership-current" in path:
            return {"ownerships": [{"shareholder_name": "Small", "shareholdingPct": 0.0196, "totalShares": 245},
                                   {"shareholder_name": "Large", "shareholdingPct": 0.1779, "totalShares": 2228}]}
        if "pattern" in path:
            return {"columns": ["Jun 2026", "Sep 2026"],
                    "rows": [{"name": "Institutions domestic", "data": {"Sep 2026": 49.82, "Jun 2026": 49.13}}]}
        return {"summary": [{"header": "Sep 2026", "totalShareholders": 4218619}]}

    monkeypatch.setattr(finedge, "get", holdings)
    out = json.loads(tools["get_shareholding"](symbol="ITC", top_holders=2))
    assert [h["name"] for h in out["top_holders"]] == ["Large", "Small"] and out["top_holders"][0]["pct"] == 17.79
    assert out["categories"][0]["pct"] == {"Jun 2026": 49.13, "Sep 2026": 49.82}
    assert out["total_shareholders"]["Sep 2026"] == 4218619


def test_peers_drop_the_company_and_rank_by_market_cap(tools):
    out = json.loads(tools["get_peers"](symbol="ITC"))
    assert [p["symbol"] for p in out["peers"]] == ["HINDUNILVR", "NESTLEIND"]
    assert out["peers"][1]["market_cap_cr"] is None


def test_compare_companies_marks_uncovered_companies(tools):
    out = json.loads(tools["compare_companies"](symbols="ITC,HINDUNILVR,TCS"))
    rows = {r["symbol"]: r for r in out["companies"]}
    assert rows["ITC"]["revenue_cr"] == 84000.0 and rows["ITC"]["roe_pct"] == 28.0 and rows["ITC"]["pe"] == 19.9
    assert rows["ITC"]["revenue_cagr_3y_pct"] == 5.56 and rows["ITC"]["price"] == 250.0
    assert rows["HINDUNILVR"]["basis"] == "standalone" and out["note"]
    assert rows["TCS"]["error"] == "not covered by this FinEdge plan"
    assert tools["compare_companies"](symbols="ITC").startswith("Error")


def test_announcements_skip_noise_and_sort_newest_first(tools):
    out = json.loads(tools["get_announcements"](symbol="ITC"))
    assert [f["category"] for f in out["filings"]] == ["Acquisition", "Outcome of Board Meeting"]


def test_search_puts_exact_matches_first(tools):
    out = json.loads(tools["search_companies"](query="itc"))
    assert [m["symbol"] for m in out["matches"]] == ["ITC", "ITCHOTELS"]
    assert json.loads(tools["search_companies"](query="500180"))["matches"][0]["symbol"] == "HDFCBANK"


def test_profile_trims_the_description(tools):
    out = json.loads(tools["get_company_profile"](symbol="ITC"))
    assert len(out["description"]) < 1000 and out["latest_annual_report"]["year"] == 2025
