"""Fundamental research tools for the agent, backed by the FinEdge Financials API.

Every tool is a closure over the FinEdge API key, so the key never enters the
prompt or the tool arguments. FinEdge returns large raw payloads (absolute rupees,
daily rows, hundreds of line items); the tools trim them to what an analyst
needs and convert units, so the model reads Rs crore and percentages.
"""

import datetime as dt
import functools
import logging
import math
import statistics
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.agent import finedge
from app.agent.finedge import FinEdgeError
from app.agent.tools import _dump

logger = logging.getLogger(__name__)

CRORE = 1e7
SYMBOLS_TTL = 24 * 3600
INDEX_TTL = 15 * 60
MAX_COMPARE = 6
QUOTE_BATCH = 100
SAMPLE_PLAN_NOTE = "The free Basic plan only covers ITC, RELIANCE and HDFCBANK."

PL_LINES = (
    "revenueFromOperations", "otherIncome", "income", "costOfMaterialsConsumed", "employeeBenefitExpense",
    "otherExpenses", "expenses", "financeCosts", "depreciationAndAmortisation", "exceptionalItemsBeforeTax",
    "profitBeforeTax", "taxExpense", "profitLossForPeriod", "profitOrLossAttributableToOwners",
    "interestEarned", "feesAndCommissionIncome",
)
BS_LINES = (
    "equityCapital", "reserves", "totalEquity", "equityAttributableToOwnersOfParent", "nonControllingInterest",
    "borrowingsNoncurrent", "borrowingsCurrent", "deposits", "tradePayablesCurrent", "currentLiabilities",
    "noncurrentLiabilities", "liabilities", "propertyPlantAndEquipment", "capitalWorkInProgress", "goodwill",
    "noncurrentInvestments", "currentInvestments", "inventories", "tradeReceivablesCurrent",
    "cashAndCashEquivalents", "otherBankBalances", "currentAssets", "noncurrentAssets", "assets",
)
CF_LINES = (
    "cashFlowsFromOperatingActivities", "purchaseOfPPEClassifiedAsInvesting", "purchaseOfFixed&IntangibleAssets",
    "cashFlowsFromInvestingActivities", "dividendsPaidClassifiedAsFinancing",
    "proceedsFromBorrowingsClassifiedAsFinancing", "repaymentsOfBorrowingsClassifiedAsFinancing",
    "cashFlowsFromFinancingActivities", "netCashFlow",
)
STATEMENT_LINES = {"pl": PL_LINES, "bs": BS_LINES, "cf": CF_LINES}
PER_SHARE = {"eps"}
META_KEYS = {"year", "header", "period_end", "period_start", "result_date", "result_timestamp",
             "dilutedOutstandingShares"}
STATEMENT_PERIODS = {
    "pl": ("annual", "quarterly", "halfyearly", "ttm"),
    "bs": ("annual", "quarterly", "ytd"),
    "cf": ("annual", "quarterly", "ytd"),
}
RATIO_TYPES = {"pr": "profitability", "le": "leverage", "li": "liquidity", "ef": "efficiency"}
ANNOUNCEMENT_KINDS = {
    "announcements": "/api/v1/corp-announcements",
    "credit_ratings": "/api/v1/credit-ratings",
    "transcripts": "/api/v1/investor-call-transcripts",
    "presentations": "/api/v1/investor-presentations",
}
NOISE = (
    "trading window", "certificate under sebi", "regulation 74(5)", "newspaper", "loss of share certificate",
    "duplicate share", "issue of duplicate",
)
RETURN_WINDOWS = (("1M", 21), ("3M", 63), ("6M", 126), ("1Y", 252), ("3Y", 756), ("5Y", 1260))
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


# ---------- unit and shape helpers ----------


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        f = float(str(value).replace("%", "").replace(",", "").strip())
    except ValueError:
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _cr(value: Any) -> float | None:
    f = _num(value)
    return None if f is None else round(f / CRORE, 2)


def _pct(value: Any) -> float | None:
    f = _num(value)
    return None if f is None else round(f * 100, 2)


def _r(value: Any, digits: int = 2) -> float | None:
    f = _num(value)
    return None if f is None else round(f, digits)


def _change(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return round((new / old - 1) * 100, 2)


def _period_label(row: dict[str, Any]) -> str:
    """'Mar 2026' from period_end 20260331, falling back to FinEdge's header or year."""
    end = row.get("period_end")
    if isinstance(end, int) and end > 19000000:
        return f"{MONTHS[(end // 100) % 100 - 1]} {end // 10000}"
    return str(row.get("header") or row.get("year") or "")


def _symbol(symbol: str) -> str:
    return symbol.strip().upper()


def _symbols(symbols: str) -> list[str]:
    out: list[str] = []
    for part in symbols.replace(";", ",").split(","):
        s = _symbol(part)
        if s and s not in out:
            out.append(s)
    return out


def _date(value: Any) -> dt.date | None:
    text = str(value or "").strip()[:19]
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(text if fmt != "%Y-%m-%d" else text[:10], fmt).date()
        except ValueError:
            continue
    return None


def _percentile(values: list[float], current: float) -> float:
    return round(sum(1 for v in values if v <= current) / len(values) * 100, 1)


def _series_stats(values: list[float], current: float | None) -> dict[str, Any] | None:
    clean = [v for v in values if v is not None and v > 0]
    if not clean:
        return None
    stats = {"median": round(statistics.median(clean), 2), "min": round(min(clean), 2), "max": round(max(clean), 2)}
    if current is not None and current > 0:
        stats = {"current": round(current, 2), **stats, "percentile_of_current": _percentile(clean, current)}
    return stats


def _quietly(call: Callable[[], Any]) -> Any:
    """The call's result, or None when FinEdge refuses it (one symbol in a batch)."""
    try:
        return call()
    except FinEdgeError:
        return None


def _first_list(data: Any) -> list[Any]:
    """The first list value in a FinEdge object (corporate action keys vary by action)."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list):
                return v
    return []


# ---------- the tools ----------


def build_research_tools(api_key: str) -> list[Callable[..., str]]:
    """The agent's fundamental research toolset for one FinEdge API key."""

    def get(path: str, params: dict[str, Any] | None = None) -> Any:
        return finedge.get(path, api_key, params)

    def guarded(fn: Callable[..., str]) -> Callable[..., str]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> str:
            try:
                return fn(*args, **kwargs)
            except FinEdgeError as exc:
                if exc.invalid_key:
                    return "Error: FinEdge rejected the API key. Ask the user to update it in Agent settings."
                if exc.not_in_plan:
                    return ("Not available: the FinEdge plan on this API key does not cover this company or data. "
                            + SAMPLE_PLAN_NOTE)
                if exc.status_code == 404:
                    return "Not found: FinEdge has no data for that symbol. Use search_companies to find the right symbol."
                if exc.status_code == 0:
                    return f"Error: {exc.message}. Try again in a minute."
                return f"Error from FinEdge ({exc.status_code}): {exc.message}"
            except Exception as exc:  # noqa: BLE001 - the model gets a readable failure instead of a crash
                logger.warning("Research tool %s failed: %s", fn.__name__, exc.__class__.__name__)
                return f"Error: {fn.__name__} failed ({exc.__class__.__name__})."

        return wrapper

    def statements(symbol: str, code: str, period: str, consolidated: bool) -> tuple[list[dict[str, Any]], str]:
        """Statement rows newest first, falling back to standalone when there are no consolidated accounts."""
        bases = ("c", "s") if consolidated else ("s",)
        for basis in bases:
            data = get(f"/api/v1/financials/{finedge.path_symbol(symbol)}",
                       {"statement_type": basis, "statement_code": code, "period": period})
            rows = [r for r in _first_list(data) if isinstance(r, dict)]
            if rows:
                rows.sort(key=lambda r: (r.get("period_end") or 0, r.get("year") or 0), reverse=True)
                return rows, "consolidated" if basis == "c" else "standalone"
        return [], "consolidated" if consolidated else "standalone"

    def ratio_rows(symbol: str, ratio_type: str, basis: str) -> list[dict[str, Any]]:
        data = get(f"/api/v1/ratios/{finedge.path_symbol(symbol)}", {"statement_type": basis, "ratio_type": ratio_type})
        return [r for r in _first_list(data) if isinstance(r, dict)]

    def metrics(symbol: str, ratio_type: str, basis: str) -> dict[str, Any]:
        data = get(f"/api/v1/financial-metrics/{finedge.path_symbol(symbol)}",
                   {"statement_type": basis, "ratio_type": ratio_type})
        found = data.get("financial_metrics") if isinstance(data, dict) else None
        return found if isinstance(found, dict) else {}

    def quotes(symbols: list[str]) -> dict[str, dict[str, Any]]:
        if not symbols:
            return {}
        # The v2 quote refuses BSE codes: quote the NSE symbol when the company has one,
        # and report it under the code the caller asked for.
        nse: dict[str, str] = {}
        if any(s.isdigit() for s in symbols):
            nse = {str(r.get("bse_code")): str(r.get("nse_code")).upper() for r in symbol_master() if r.get("nse_code")}
        asked = {nse.get(s, s): s for s in symbols}
        symbols = list(asked)
        data: dict[str, Any] = {}
        # FinEdge takes at most 100 symbols per quote call.
        for start in range(0, len(symbols), QUOTE_BATCH):
            batch = symbols[start:start + QUOTE_BATCH]
            try:
                data.update(get("/api/v2/quote", {"symbol": batch}) or {})
            except FinEdgeError as exc:
                # FinEdge refuses the whole batch when one symbol is outside the plan: quote one by one instead.
                if not exc.not_in_plan or len(symbols) == 1:
                    raise
                with ThreadPoolExecutor(max_workers=8) as pool:
                    for part in pool.map(lambda s: _quietly(lambda: get("/api/v2/quote", {"symbol": [s]})), batch):
                        data.update(part or {})
        out: dict[str, dict[str, Any]] = {}
        for sym, q in (data or {}).items():
            if not isinstance(q, dict):
                continue
            price, high52 = _num(q.get("current_price", q.get("price"))), _num(q.get("high52"))
            key = sym.strip().upper()
            out[asked.get(key, key)] = {
                "price": price,
                "change_pct": _r(q.get("change")),
                "open": _num(q.get("open_price")),
                "high": _num(q.get("high_price")),
                "low": _num(q.get("low_price")),
                "volume": q.get("volume"),
                "high_52w": high52,
                "low_52w": _num(q.get("low52")),
                "pct_below_52w_high": _change(price, high52),
                "market_cap_cr": _r(q.get("market_cap")),
                "trade_time": q.get("tradetime"),
            }
        return out

    def symbol_master() -> list[dict[str, Any]]:
        return finedge.cached("stock-symbols", SYMBOLS_TTL, lambda: _first_list(get("/api/v1/stock-symbols")))

    # --- tools exposed to the model ---

    def search_companies(query: str, limit: int = 10) -> str:
        """Find Indian listed companies in FinEdge by name, NSE symbol or BSE code.

        Args:
            query: Company name, NSE symbol or BSE code, for example "infosys", "HDFCBANK" or "500875".
            limit: Maximum rows to return (1 to 25).
        """
        q = query.strip().lower()
        if not q:
            return "Error: give a company name or symbol."
        exact, starts, contains = [], [], []
        words = q.split()
        for row in symbol_master():
            codes = [str(row.get(k) or "").lower() for k in ("symbol", "nse_code", "bse_code")]
            name = str(row.get("name") or "").lower()
            if q in codes:
                exact.append(row)
            elif any(c.startswith(q) for c in codes if c) or name.startswith(q):
                starts.append(row)
            elif all(w in name for w in words):
                contains.append(row)
        rows = (exact + starts + contains)[: max(1, min(limit, 25))]
        return _dump({"source": "FinEdge", "matches": [
            {"symbol": r.get("symbol"), "name": r.get("name"), "nse_code": r.get("nse_code") or None,
             "bse_code": r.get("bse_code") or None, "has_consolidated": r.get("consolidated_ind")} for r in rows
        ]})

    def get_company_profile(symbol: str) -> str:
        """Business description, sector, industry, market cap and the latest annual report link.

        Args:
            symbol: NSE symbol or BSE code, for example "ITC" or "500875".
        """
        sym = finedge.path_symbol(symbol)
        p = get(f"/api/v1/company-profile/{sym}") or {}
        report = None
        try:
            reports = _first_list(get(f"/api/v1/company-reports/{sym}"))
            latest = max((r for r in reports if isinstance(r, dict)), key=lambda r: r.get("year") or 0, default=None)
            if latest:
                report = {"year": latest.get("year"), "url": latest.get("nse_url") or latest.get("bse_url")}
        except FinEdgeError:
            pass
        description = str(p.get("description") or "")
        return _dump({
            "source": "FinEdge", "symbol": p.get("symbol"), "name": p.get("name"),
            "nse_code": p.get("nse_code"), "bse_code": p.get("bse_code"),
            "macro_sector": p.get("macro_sector"), "sector": p.get("sector"), "industry": p.get("industry"),
            "sub_industry": p.get("sub_industry"), "market_cap_cr": _r(p.get("market_cap")),
            "website": p.get("website"),
            "description": description[:900] + (" ..." if len(description) > 900 else ""),
            "latest_annual_report": report,
        })

    def get_financial_statements(
        symbol: str, statement: str = "pl", period: str = "annual", periods: int = 5, consolidated: bool = True,
    ) -> str:
        """Profit and loss, balance sheet or cash flow statement in Rs crore, oldest period first.

        Args:
            symbol: NSE symbol or BSE code.
            statement: "pl" profit and loss, "bs" balance sheet or "cf" cash flow.
            period: "annual" or "quarterly"; also "ttm" or "halfyearly" for pl, "ytd" for bs and cf.
            periods: How many recent periods to return (1 to 12).
            consolidated: True for consolidated accounts (falls back to standalone when none exist).
        """
        code, period = statement.strip().lower(), period.strip().lower()
        if code not in STATEMENT_LINES:
            return "Error: statement must be pl, bs or cf."
        if period not in STATEMENT_PERIODS[code]:
            return f"Error: period for {code} must be one of {', '.join(STATEMENT_PERIODS[code])}."
        sym = _symbol(symbol)
        rows, basis = statements(sym, code, period, consolidated)
        if not rows:
            return f"Not found: FinEdge has no {code} {period} statements for {sym}."
        n = max(1, min(periods, 12))
        lines = STATEMENT_LINES[code]
        known = sum(1 for k in lines if _num(rows[0].get(k)))
        # Banks and insurers have no revenueFromOperations line and use their own line items.
        financial_company = code == "pl" and not _num(rows[0].get("revenueFromOperations"))
        revenue_key = next((k for k in ("revenueFromOperations", "interestEarned", "income")
                            if _num(rows[0].get(k))), "revenueFromOperations")
        profit_key = next((k for k in ("profitLossForPeriod", "profitOrLossAttributableToOwners")
                           if _num(rows[0].get(k))), "profitLossForPeriod")
        out_rows = []
        # Quarterly growth is year on year, so compare with the row four quarters back.
        lag = 4 if period == "quarterly" else 1
        for i, row in enumerate(rows[:n]):
            if known >= 3 and not financial_company:
                values = {k: _cr(row.get(k)) for k in lines if _num(row.get(k))}
            else:
                values = {k: _cr(v) for k, v in row.items() if k not in META_KEYS | PER_SHARE and _num(v)}
                values = dict(list(values.items())[:40])
            if code == "pl" and _num(row.get("eps")) is not None:
                values["eps_rs"] = _r(row.get("eps"))
            if code == "bs":
                debt = sum(_num(row.get(k)) or 0 for k in ("borrowingsNoncurrent", "borrowingsCurrent"))
                cash = sum(_num(row.get(k)) or 0 for k in ("cashAndCashEquivalents", "otherBankBalances",
                                                           "currentInvestments"))
                values["totalBorrowings"] = _cr(debt)
                values["netDebt"] = _cr(debt - cash)
            if code == "cf":
                cfo = _num(row.get("cashFlowsFromOperatingActivities"))
                capex = _num(row.get("purchaseOfFixed&IntangibleAssets")) or _num(row.get("purchaseOfPPEClassifiedAsInvesting"))
                if cfo is not None:
                    # Capex is reported as a negative outflow.
                    values["freeCashFlow"] = _cr(cfo - abs(capex or 0))
            if code == "pl" and i + lag < len(rows):
                prev = rows[i + lag]
                values["revenue_growth_pct"] = _change(_num(row.get(revenue_key)), _num(prev.get(revenue_key)))
                values["profit_growth_pct"] = _change(_num(row.get(profit_key)), _num(prev.get(profit_key)))
            out_rows.append({"period": _period_label(row), **values})
        units = "Rs crore"
        if code == "pl":
            units += ("; eps_rs in rupees per share; growth in percent "
                      + ("year on year versus the same quarter last year" if lag == 4 else "versus the prior period")
                      + f", revenue from {revenue_key}, profit from {profit_key}")
        return _dump({
            "source": "FinEdge", "symbol": sym, "statement": code, "period": period, "basis": basis,
            "units": units, "rows": list(reversed(out_rows)),
        })

    def get_financial_ratios(symbol: str, years: int = 5, consolidated: bool = True) -> str:
        """Profitability, leverage, liquidity and efficiency ratios per year, including the trailing twelve months.

        Args:
            symbol: NSE symbol or BSE code.
            years: How many recent years to return (1 to 10).
            consolidated: True for consolidated accounts (falls back to standalone when none exist).
        """
        sym = _symbol(symbol)
        bases = ("c", "s") if consolidated else ("s",)
        merged: dict[str, dict[str, Any]] = {}
        basis_used = None
        for basis in bases:
            for ratio_type in RATIO_TYPES:
                for row in ratio_rows(sym, ratio_type, basis):
                    key = str(row.get("header") or row.get("year"))
                    entry = merged.setdefault(key, {"period": key, "year": row.get("year")})
                    for k, v in row.items():
                        if k in META_KEYS:
                            continue
                        entry[k] = _pct(v) if ratio_type == "pr" else _r(v)
            if merged:
                basis_used = "consolidated" if basis == "c" else "standalone"
                break
        if not merged:
            return f"Not found: FinEdge has no ratios for {sym}."
        rows = sorted(merged.values(), key=lambda r: (r.get("year") or 0, r["period"] == "TTM"), reverse=True)
        rows = rows[: max(1, min(years, 10)) + 1]
        for r in rows:
            r.pop("year", None)
        return _dump({
            "source": "FinEdge", "symbol": sym, "basis": basis_used,
            "units": "margins, returns, tax rate and payout in percent; leverage and liquidity as ratios (x); "
                     "days as days; turnover as times",
            "rows": list(reversed(rows)),
        })

    def get_growth_metrics(symbol: str, consolidated: bool = True) -> str:
        """3 and 5 year growth rates, 3 and 5 year average margins and returns, and 3 year cash flow totals.

        Args:
            symbol: NSE symbol or BSE code.
            consolidated: True for consolidated accounts (falls back to standalone when none exist).
        """
        sym = _symbol(symbol)
        for basis in (("c", "s") if consolidated else ("s",)):
            growth = metrics(sym, "gr", basis)
            if not growth:
                continue
            averages = metrics(sym, "av", basis)
            totals = metrics(sym, "cu", basis)
            return _dump({
                "source": "FinEdge", "symbol": sym, "basis": "consolidated" if basis == "c" else "standalone",
                "units": "growth rates are annualised percent (CAGR); averages in percent; cash totals in Rs crore",
                "growth_cagr_pct": {k: _pct(v) for k, v in growth.items()},
                "averages_pct": {k: _pct(v) for k, v in averages.items()},
                "cash_flow_3y_totals_cr": {k: _cr(v) for k, v in totals.items()},
            })
        return f"Not found: FinEdge has no growth metrics for {sym}."

    def get_stock_quotes(symbols: str) -> str:
        """Latest price (delayed 15 to 30 minutes), change, 52 week range and market cap for up to 20 companies.

        Args:
            symbols: Comma separated NSE symbols or BSE codes, for example "ITC,HINDUNILVR".
        """
        syms = _symbols(symbols)[:20]
        if not syms:
            return "Error: give at least one symbol."
        found = quotes(syms)
        missing = [s for s in syms if s not in found]
        return _dump({"source": "FinEdge", "units": "price in rupees; market_cap_cr in Rs crore; changes in percent",
                      "quotes": found, "not_found": missing or None})

    def get_price_performance(symbol: str, years: int = 3, benchmark: str = "NIF50") -> str:
        """Price trend summary from adjusted daily prices: returns, CAGR, volatility, drawdown, moving averages and benchmark returns.

        Args:
            symbol: NSE symbol or BSE code.
            years: Look back in calendar years (1 to 10).
            benchmark: FinEdge index code for comparison, for example "NIF50" (Nifty 50) or "NIF500".
        """
        sym = _symbol(symbol)
        years = max(1, min(years, 10))
        this_year = dt.date.today().year
        data = get(f"/api/v1/daily-quotes/{finedge.path_symbol(sym)}", {"from": this_year - years, "to": this_year})
        rows = [r for r in _first_list(data) if isinstance(r, dict) and _num(r.get("close_price"))]
        rows.sort(key=lambda r: str(r.get("quote_date")))
        if len(rows) < 2:
            return f"Not found: FinEdge has no daily prices for {sym}."
        closes = [float(r["close_price"]) for r in rows]
        last = closes[-1]
        returns = {label: _change(last, closes[-1 - n]) for label, n in RETURN_WINDOWS if len(closes) > n}
        span_years = max((_date(rows[-1]["quote_date"]) - _date(rows[0]["quote_date"])).days / 365.25, 0.01)
        year_closes = closes[-253:]
        daily = [math.log(b / a) for a, b in zip(year_closes, year_closes[1:]) if a > 0 and b > 0]
        peak, drawdown = closes[0], 0.0
        for c in closes:
            peak = max(peak, c)
            drawdown = min(drawdown, c / peak - 1)
        sma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else None
        sma200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else None
        volumes = [_num(r.get("volume")) or 0 for r in rows]
        monthly: dict[str, float] = {}
        for r in rows:
            monthly[str(r["quote_date"])[:7]] = float(r["close_price"])
        bench = None
        try:
            index_rows = finedge.cached("index-price-returns", INDEX_TTL,
                                        lambda: _first_list(get("/api/v1/index/price-returns")))
            match = next((x for x in index_rows if str(x.get("index_symbol")).upper() == benchmark.strip().upper()), None)
            if match:
                bench = {"index": match.get("index_name"), "as_of": (match.get("dates") or {}).get("last_date"),
                         "returns_pct": {k: match.get(k) for k in ("1M", "3M", "6M", "1Y", "3Y", "5Y")}}
        except FinEdgeError:
            pass
        return _dump({
            "source": "FinEdge", "symbol": sym, "as_of": rows[-1]["quote_date"],
            "units": "prices in rupees, adjusted for splits, bonuses and dividends; returns and volatility in percent",
            "last_close": last,
            "returns_pct": returns,
            "cagr_pct": round(((last / closes[0]) ** (1 / span_years) - 1) * 100, 2) if closes[0] > 0 else None,
            "cagr_period": f"{rows[0]['quote_date']} to {rows[-1]['quote_date']}",
            "volatility_1y_annualised_pct": round(statistics.pstdev(daily) * math.sqrt(252) * 100, 2) if len(daily) > 20 else None,
            "max_drawdown_pct": round(drawdown * 100, 2),
            "high_52w": max(year_closes), "low_52w": min(year_closes),
            "sma_50": _r(sma50), "sma_200": _r(sma200),
            "pct_vs_sma_50": _change(last, sma50), "pct_vs_sma_200": _change(last, sma200),
            "avg_volume_20d": int(sum(volumes[-20:]) / min(20, len(volumes))),
            "avg_volume_200d": int(sum(volumes[-200:]) / min(200, len(volumes))),
            "monthly_closes": [{"month": m, "close": c} for m, c in list(monthly.items())[-12:]],
            "benchmark": bench,
        })

    def get_valuation_history(symbol: str, years: int = 5, consolidated: bool = True) -> str:
        """PE, PB, price to sales and price to free cash flow: yearly history and where today's multiple sits in its range.

        Args:
            symbol: NSE symbol or BSE code.
            years: Look back for the daily multiple range (1 to 8 years, data starts in 2018).
            consolidated: True for consolidated earnings (falls back to standalone when none exist).
        """
        sym = _symbol(symbol)
        years = max(1, min(years, 8))
        for basis in (("c", "s") if consolidated else ("s",)):
            annual = [r for r in _first_list(get(f"/api/v1/annual-price-ratios/{finedge.path_symbol(sym)}",
                                                 {"statement_type": basis})) if isinstance(r, dict)]
            if not annual:
                continue
            daily = [r for r in _first_list(get(
                f"/api/v1/daily-price-ratios/{finedge.path_symbol(sym)}",
                {"statement_type": basis, "from": max(2018, dt.date.today().year - years)},
            )) if isinstance(r, dict)]
            daily.sort(key=lambda r: str(r.get("quote_date")))
            latest = daily[-1] if daily else {}
            ranges = {}
            for k in ("pe", "pb", "ps", "pfcf"):
                stats = _series_stats([_num(r.get(k)) for r in daily], _num(latest.get(k)))
                if stats:
                    ranges[k] = stats
            annual.sort(key=lambda r: (r.get("year") or 0, r.get("header") == "TTM"))
            return _dump({
                "source": "FinEdge", "symbol": sym, "basis": "consolidated" if basis == "c" else "standalone",
                "units": "multiples in times (x); average_price in rupees; percentile_of_current is the share of "
                         "trading days with a multiple at or below today's",
                "as_of": latest.get("quote_date"),
                "daily_range_since": daily[0].get("quote_date") if daily else None,
                "current_vs_range": ranges,
                "yearly": [{"period": r.get("header") or r.get("year"), "average_price": _r(r.get("average_price")),
                            **{k: _r(r.get(k)) for k in ("pe", "pb", "ps", "pfcf")}} for r in annual[-9:]],
            })
        return f"Not found: FinEdge has no valuation history for {sym}."

    def get_peers(symbol: str, group: str = "industry", max_peers: int = 8) -> str:
        """Peer companies in the same industry or sector, largest first, with price and market cap.

        Args:
            symbol: NSE symbol or BSE code.
            group: "sub_industry" (tightest), "industry", "sector" or "macro_sector" (broadest).
            max_peers: Maximum peers to return (1 to 20).
        """
        sym = _symbol(symbol)
        group = group.strip().lower()
        if group not in ("sub_industry", "industry", "sector", "macro_sector"):
            return "Error: group must be sub_industry, industry, sector or macro_sector."
        data = get(f"/api/v1/peers/{finedge.path_symbol(sym)}", {"group": group})
        peers = [p for p in _symbols(",".join(str(x) for x in _first_list(data))) if p != sym]
        if not peers:
            return _dump({"source": "FinEdge", "symbol": sym, "group": group, "peers": []})
        found = _quietly(lambda: quotes(peers + [sym])) or {}
        def rank(p: str) -> tuple[bool, float]:
            q = found.get(p) or {}
            # FinEdge has penny quotes with impossible market caps (Rs 0.03 and Rs 39 lakh crore):
            # rank anything under Rs 1 last so it cannot pose as the industry leader.
            return (q.get("price") or 0) < 1, -(q.get("market_cap_cr") or 0)

        ranked = sorted(peers, key=rank)
        return _dump({
            "source": "FinEdge", "symbol": sym, "group": group, "units": "price in rupees; market cap in Rs crore",
            "company": {"symbol": sym, **{k: (found.get(sym) or {}).get(k) for k in ("price", "market_cap_cr")}},
            "peers": [{"symbol": p, **{k: (found.get(p) or {}).get(k) for k in ("price", "market_cap_cr", "change_pct")}}
                      for p in ranked[: max(1, min(max_peers, 20))]],
        })

    def compare_companies(symbols: str, consolidated: bool = True) -> str:
        """Side by side fundamentals for 2 to 6 companies: size, growth, margins, returns, leverage and valuation.

        Args:
            symbols: Comma separated NSE symbols or BSE codes, for example "ITC,HINDUNILVR,NESTLEIND".
            consolidated: True for consolidated accounts (each company falls back to standalone when needed).
        """
        syms = _symbols(symbols)
        if not 2 <= len(syms) <= MAX_COMPARE:
            return f"Error: give between 2 and {MAX_COMPARE} symbols."

        def one(sym: str) -> dict[str, Any]:
            row: dict[str, Any] = {"symbol": sym}
            try:
                profile = get(f"/api/v1/company-profile/{finedge.path_symbol(sym)}") or {}
                row.update(name=profile.get("name"), industry=profile.get("industry"))
                pl, basis = statements(sym, "pl", "annual", consolidated)
                code = "c" if basis == "consolidated" else "s"
                row["basis"] = basis
                if pl:
                    row["latest_fy"] = _period_label(pl[0])
                    row["revenue_cr"] = _cr(pl[0].get("revenueFromOperations"))
                    row["net_profit_cr"] = _cr(pl[0].get("profitLossForPeriod"))
                growth, averages = metrics(sym, "gr", code), metrics(sym, "av", code)
                row.update(
                    revenue_cagr_3y_pct=_pct(growth.get("revenueGrowth3years")),
                    revenue_cagr_5y_pct=_pct(growth.get("revenueGrowth5years")),
                    profit_cagr_3y_pct=_pct(growth.get("netIncomeGrowth3years")),
                    eps_cagr_3y_pct=_pct(growth.get("epsGrowth3years")),
                    dividend_payout_3y_avg_pct=_pct(averages.get("dividendPayoutPct3YrsAvg")),
                )
                pr = ratio_rows(sym, "pr", code)
                le = ratio_rows(sym, "le", code)
                latest_pr = max(pr, key=lambda r: (r.get("year") or 0, r.get("header") == "TTM"), default={})
                latest_le = max(le, key=lambda r: (r.get("year") or 0, r.get("header") == "TTM"), default={})
                row.update(
                    ratios_period=latest_pr.get("header"),
                    ebitda_margin_pct=_pct(latest_pr.get("ebitdaMargin")),
                    net_margin_pct=_pct(latest_pr.get("netMargin")),
                    roe_pct=_pct(latest_pr.get("returnOnEquity")),
                    roce_pct=_pct(latest_pr.get("returnOnCapital")),
                    debt_to_equity=_r(latest_le.get("totalDebtToEquity")),
                )
                valuation = _first_list(get(f"/api/v1/annual-price-ratios/{finedge.path_symbol(sym)}",
                                            {"statement_type": code}))
                latest_v = max((r for r in valuation if isinstance(r, dict)),
                               key=lambda r: (r.get("year") or 0, r.get("header") == "TTM"), default={})
                row.update(pe=_r(latest_v.get("pe")), pb=_r(latest_v.get("pb")))
            except FinEdgeError as exc:
                row["error"] = ("not covered by this FinEdge plan" if exc.not_in_plan
                                else "not found" if exc.status_code == 404 else exc.message)
            return row

        with ThreadPoolExecutor(max_workers=MAX_COMPARE) as pool:
            rows = list(pool.map(one, syms))
        try:
            found = quotes([r["symbol"] for r in rows if "error" not in r])
        except FinEdgeError:
            found = {}
        for r in rows:
            q = found.get(r["symbol"]) or {}
            r["price"], r["market_cap_cr"] = q.get("price"), q.get("market_cap_cr")
        bases = {r.get("basis") for r in rows if r.get("basis")}
        return _dump({
            "source": "FinEdge",
            "units": "Rs crore; growth, margins, returns and payout in percent; debt_to_equity, pe and pb in times",
            "note": "Mixed consolidated and standalone figures: compare with care." if len(bases) > 1 else None,
            "companies": rows,
        })

    def get_shareholding(symbol: str, quarters: int = 6, top_holders: int = 0) -> str:
        """Shareholding pattern by category (promoters, foreign and domestic institutions, public) over recent quarters.

        Args:
            symbol: NSE symbol or BSE code.
            quarters: How many recent quarters to return (1 to 12).
            top_holders: Also list this many of the largest named holders above 1 percent (0 to 15).
        """
        sym = finedge.path_symbol(symbol)
        data = get(f"/api/v1/shareholdings/pattern/{sym}", {"period": "quarterly"}) or {}
        columns = [c for c in (data.get("columns") or []) if isinstance(c, str)][-max(1, min(quarters, 12)):]
        categories = [{"category": r.get("name"), "pct": {c: _r((r.get("data") or {}).get(c)) for c in columns}}
                      for r in data.get("rows") or [] if isinstance(r, dict)]
        shareholders = None
        try:
            summary = (get(f"/api/v1/shareholdings/summary/{sym}", {"period": "quarterly"}) or {}).get("summary") or []
            by_header = {s.get("header"): s for s in summary if isinstance(s, dict)}
            shareholders = {c: (by_header.get(c) or {}).get("totalShareholders") for c in columns}
            pledged = {c: _r((by_header.get(c) or {}).get("pledgedSharesPct")) for c in columns}
        except FinEdgeError:
            pledged = {}
        holders = None
        if top_holders > 0:
            current = get(f"/api/v1/shareholdings/ownership-current/{sym}") or {}
            rows = [h for h in current.get("ownerships") or [] if isinstance(h, dict) and _num(h.get("totalShares"))]
            # Live data is not sorted and gives shareholdingPct as a fraction (0.1646 is 16.46 percent).
            rows.sort(key=lambda h: _num(h.get("totalShares")) or 0, reverse=True)
            holders = [{"name": h.get("shareholder_name"), "pct": _pct(h.get("shareholdingPct")),
                        "shares": int(_num(h.get("totalShares")) or 0)} for h in rows[: min(top_holders, 15)]]
        return _dump({
            "source": "FinEdge", "symbol": _symbol(symbol), "company": data.get("company_name"),
            "units": "percent of total shares", "quarters": columns, "categories": categories,
            "total_shareholders": shareholders,
            "promoter_pledged_pct": pledged if any(v is not None for v in pledged.values()) else None,
            "top_holders": holders,
        })

    def get_dividends_and_actions(symbol: str, years: int = 5) -> str:
        """Dividend history per calendar year, trailing twelve month dividend and yield, and all bonus issues and splits.

        Args:
            symbol: NSE symbol or BSE code.
            years: How many recent calendar years of dividends to summarise (1 to 15).
        """
        sym = _symbol(symbol)
        path = finedge.path_symbol(sym)
        dividends = [d for d in _first_list(get(f"/api/v1/dividend/{path}")) if isinstance(d, dict)]
        today = dt.date.today()
        per_year: dict[int, dict[str, Any]] = {}
        ttm = 0.0
        latest = None
        for d in dividends:
            when = _date(d.get("date"))
            amount = _num(d.get("adj_amount")) or _num(d.get("amount")) or 0
            if when is None:
                continue
            y = per_year.setdefault(when.year, {"year": when.year, "dps_rs": 0.0, "count": 0})
            y["dps_rs"] = round(y["dps_rs"] + amount, 2)
            y["count"] += 1
            if (today - when).days <= 365:
                ttm += amount
            if latest is None or when > _date(latest["date"]):
                latest = {"date": d.get("date"), "amount_rs": amount, "type": d.get("dividend_type")}
        actions: dict[str, list[Any]] = {}
        for action in ("bonus", "split", "rights"):
            try:
                actions[action] = [{"date": a.get("date"), "detail": a.get("action")}
                                   for a in _first_list(get(f"/api/v1/corporate-actions/{action}/{path}"))
                                   if isinstance(a, dict)]
            except FinEdgeError:
                actions[action] = []
        price = None
        try:
            price = (quotes([sym]).get(sym) or {}).get("price")
        except FinEdgeError:
            pass
        return _dump({
            "source": "FinEdge", "symbol": sym,
            "units": "rupees per share, adjusted for splits and bonuses; yield in percent",
            "latest_dividend": latest,
            "ttm_dps_rs": round(ttm, 2),
            "ttm_dividend_yield_pct": round(ttm / price * 100, 2) if price else None,
            "by_year": sorted(per_year.values(), key=lambda y: y["year"])[-max(1, min(years, 15)):],
            "corporate_actions": actions,
        })

    def get_announcements(symbol: str, days: int = 90, kind: str = "announcements", limit: int = 15) -> str:
        """Recent exchange filings for a company, newest first, with links to the PDF.

        Args:
            symbol: NSE symbol or BSE code.
            days: How many days back to look (1 to 365).
            kind: "announcements" (all filings), "credit_ratings", "transcripts" (earnings calls) or "presentations".
            limit: Maximum filings to return (1 to 30).
        """
        kind = kind.strip().lower()
        if kind not in ANNOUNCEMENT_KINDS:
            return f"Error: kind must be one of {', '.join(ANNOUNCEMENT_KINDS)}."
        sym = _symbol(symbol)
        today = dt.date.today()
        start = today - dt.timedelta(days=max(1, min(days, 365)))
        rows = [r for r in _first_list(get(ANNOUNCEMENT_KINDS[kind], {
            "symbol": sym, "from_date": start.isoformat(), "to_date": today.isoformat()})) if isinstance(r, dict)]
        if kind == "announcements":
            rows = [r for r in rows if not any(n in f"{r.get('category')} {r.get('description')}".lower() for n in NOISE)]
        rows.sort(key=lambda r: r.get("timestamp_unix") or 0, reverse=True)
        return _dump({
            "source": "FinEdge", "symbol": sym, "kind": kind, "from": start.isoformat(), "to": today.isoformat(),
            "filings": [{
                "date": r.get("announcement_date") or r.get("ex_date"),
                "category": r.get("category"),
                "description": str(r.get("description") or "")[:300],
                "pdf": r.get("pdf_file_link") or r.get("pdf_file_link_hist") or None,
            } for r in rows[: max(1, min(limit, 30))]],
        })

    def get_index_overview(index: str = "NIF50") -> str:
        """Index level, change, PE, PB, dividend yield and 1 month to 10 year returns, for market context and benchmarks.

        Args:
            index: FinEdge index code or name, for example "NIF50", "Nifty Bank", "Nifty FMCG" or "SENSEX".
        """
        feed = finedge.cached("index-daily-feed", INDEX_TTL,
                              lambda: _first_list(get("/api/v1/index/market-price/daily-feed")))
        q = index.strip().lower()
        match = (next((x for x in feed if str(x.get("index_symbol")).lower() == q), None)
                 or next((x for x in feed if str(x.get("index_name")).lower() == q), None)
                 or next((x for x in feed if q in str(x.get("index_name")).lower()), None))
        if match is None:
            return f"Not found: no index matches {index}. Try a name such as Nifty 50, Nifty Bank or Nifty IT."
        returns = finedge.cached("index-price-returns", INDEX_TTL,
                                 lambda: _first_list(get("/api/v1/index/price-returns")))
        ret = next((x for x in returns if x.get("index_symbol") == match.get("index_symbol")), {})
        return _dump({
            "source": "FinEdge", "index_symbol": match.get("index_symbol"), "index": match.get("index_name"),
            "as_of": match.get("quote_date"), "units": "level in points; change and returns in percent",
            "close": match.get("close_price"), "change_pct": match.get("change_pct"),
            "pe": match.get("pe"), "pb": match.get("pb"), "dividend_yield_pct": match.get("div_yield"),
            "returns_pct": {k: ret.get(k) for k in ("1M", "3M", "6M", "1Y", "3Y", "5Y", "7Y", "10Y") if k in ret} or None,
        })

    return [
        guarded(fn)
        for fn in (
            search_companies, get_company_profile, get_financial_statements, get_financial_ratios,
            get_growth_metrics, get_stock_quotes, get_price_performance, get_valuation_history, get_peers,
            compare_companies, get_shareholding, get_dividends_and_actions, get_announcements, get_index_overview,
        )
    ]


RESEARCH_TOOL_DESCRIPTIONS = [
    ("search_companies", "Find listed companies by name, NSE symbol or BSE code"),
    ("get_company_profile", "Business, sector, industry, market cap and annual report"),
    ("get_financial_statements", "P&L, balance sheet and cash flow in Rs crore"),
    ("get_financial_ratios", "Margins, returns, leverage, liquidity and efficiency"),
    ("get_growth_metrics", "3 and 5 year growth, average returns and cash flow totals"),
    ("get_stock_quotes", "Delayed price, 52 week range and market cap"),
    ("get_price_performance", "Returns, CAGR, volatility, drawdown and moving averages"),
    ("get_valuation_history", "PE, PB, PS history and today's percentile"),
    ("get_peers", "Peer companies ranked by market cap"),
    ("compare_companies", "Side by side fundamentals for 2 to 6 companies"),
    ("get_shareholding", "Promoter, institutional and public holding trend"),
    ("get_dividends_and_actions", "Dividend history, yield, bonuses and splits"),
    ("get_announcements", "Recent filings, credit ratings, call transcripts"),
    ("get_index_overview", "Index level, valuation and returns"),
]
