import json

import httpx
import pytest

from app.broker.tradesmart import (
    BrokerError,
    SessionExpiredError,
    compute_funds,
    fetch_funds,
    new_client,
    position_pnl,
)

LIMITS_OK = {
    "stat": "Ok",
    "cash": "100000.50",
    "payin": "2500.00",
    "payout": "1000.00",
    "brkcollamt": "50000.25",
    "marginused": "12000.00",
}
NO_DATA = {"stat": "Not_Ok", "request_time": "16:19:47 20-06-2026", "emsg": 'Error Occurred : 5 "no data"'}
EXPIRED = {"stat": "Not_Ok", "emsg": "Session Expired :  Invalid Acct Id "}


def make_client(routes: dict[str, tuple[int, object]], seen: list | None = None) -> httpx.Client:
    """Client whose transport answers by endpoint name, recording requests."""

    def handler(request: httpx.Request) -> httpx.Response:
        endpoint = request.url.path.rsplit("/", 1)[-1]
        if seen is not None:
            seen.append(request)
        status, body = routes[endpoint]
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body)

    return new_client(transport=httpx.MockTransport(handler))


def test_compute_funds_basic():
    positions = [
        {"rpnl": "150.50", "urmtom": "-20.25", "netqty": "10"},
        {"rpnl": "-50", "urmtom": "30", "netqty": "0"},
    ]
    funds = compute_funds(LIMITS_OK, positions)
    assert funds["available_cash"] == pytest.approx(100000.50 + 2500 - 12000)
    assert funds["collateral"] == pytest.approx(50000.25)
    assert funds["utilised_debits"] == pytest.approx(12000)
    assert funds["m2m_realized"] == pytest.approx(100.50)
    assert funds["m2m_unrealized"] == pytest.approx(9.75)
    assert funds["cash"] == pytest.approx(100000.50)
    assert funds["payin"] == pytest.approx(2500)
    assert funds["payout"] == pytest.approx(1000)


def test_unrealized_fallback_when_urmtom_zero():
    entry = {"urmtom": "0", "rpnl": "0", "netqty": "-50", "lp": "98", "netavgprc": "100", "prcftr": "1.5"}
    realized, unrealized = position_pnl(entry)
    assert realized == 0
    assert unrealized == pytest.approx((98 - 100) * -50 * 1.5)


def test_unrealized_fallback_defaults_prcftr_to_one():
    _, unrealized = position_pnl({"netqty": "2", "lp": "110", "netavgprc": "100"})
    assert unrealized == pytest.approx(20)


def test_no_fallback_when_flat():
    _, unrealized = position_pnl({"urmtom": "0", "netqty": "0", "lp": "110", "netavgprc": "100"})
    assert unrealized == 0


def test_non_list_positionbook_means_zero_pnl():
    funds = compute_funds(LIMITS_OK, NO_DATA)
    assert funds["m2m_realized"] == 0
    assert funds["m2m_unrealized"] == 0
    assert compute_funds(LIMITS_OK, None)["m2m_realized"] == 0


def test_missing_and_garbage_fields_count_as_zero():
    funds = compute_funds({"stat": "Ok", "cash": "", "payin": None, "marginused": "abc"}, [{"rpnl": "nan"}])
    assert funds == {
        "available_cash": 0.0,
        "collateral": 0.0,
        "m2m_unrealized": 0.0,
        "m2m_realized": 0.0,
        "utilised_debits": 0.0,
        "cash": 0.0,
        "payin": 0.0,
        "payout": 0.0,
    }


def test_fetch_funds_sends_jdata_and_bearer():
    seen: list[httpx.Request] = []
    positions = [{"rpnl": "10", "urmtom": "5", "netqty": "1"}]
    with make_client({"Limits": (200, LIMITS_OK), "PositionBook": (200, positions)}, seen) as client:
        funds = fetch_funds("tok123", "AB1234", client)
    assert funds["m2m_realized"] == 10
    assert funds["m2m_unrealized"] == 5
    assert funds["fetched_at"].endswith("+05:30")
    assert [r.url.path.rsplit("/", 1)[-1] for r in seen] == ["Limits", "PositionBook"]
    for request in seen:
        assert request.headers["Authorization"] == "Bearer tok123"
        assert request.headers["Content-Type"] == "text/plain"
        assert "Mozilla" in request.headers["User-Agent"]
        body = request.content.decode()
        assert body.startswith("jData=")
        assert json.loads(body[len("jData="):]) == {"uid": "AB1234", "actid": "AB1234"}


def test_fetch_funds_with_no_positions():
    with make_client({"Limits": (200, LIMITS_OK), "PositionBook": (200, NO_DATA)}) as client:
        funds = fetch_funds("tok", "U1", client)
    assert funds["m2m_realized"] == 0
    assert funds["available_cash"] == pytest.approx(90500.50)


def test_fetch_funds_positionbook_non_json_means_zero_pnl():
    with make_client({"Limits": (200, LIMITS_OK), "PositionBook": (200, "<html>oops</html>")}) as client:
        funds = fetch_funds("tok", "U1", client)
    assert funds["m2m_unrealized"] == 0


@pytest.mark.parametrize(
    "routes",
    [
        {"Limits": (401, EXPIRED)},
        {"Limits": (200, EXPIRED)},
        {"Limits": (401, "Unauthorized")},
        {"Limits": (200, LIMITS_OK), "PositionBook": (401, EXPIRED)},
        {"Limits": (200, LIMITS_OK), "PositionBook": (200, EXPIRED)},
    ],
)
def test_fetch_funds_session_expired(routes):
    with make_client(routes) as client, pytest.raises(SessionExpiredError):
        fetch_funds("tok", "U1", client)


def test_fetch_funds_limits_error_raises_broker_error():
    with make_client({"Limits": (200, {"stat": "Not_Ok", "emsg": "Invalid Input"})}) as client:
        with pytest.raises(BrokerError) as info:
            fetch_funds("tok", "U1", client)
    assert not isinstance(info.value, SessionExpiredError)
    assert str(info.value) == "Invalid Input"


def test_fetch_funds_limits_non_json_raises_broker_error():
    with make_client({"Limits": (403, "<html>blocked</html>")}) as client:
        with pytest.raises(BrokerError) as info:
            fetch_funds("tok", "U1", client)
    assert "HTTP 403" in str(info.value)
