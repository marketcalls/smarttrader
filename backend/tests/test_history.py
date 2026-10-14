import json
from datetime import datetime

import httpx
import pytest

from app.broker.history import data_exchange, fetch_history, lookback_days, parse_candles
from app.broker.tradesmart import BrokerError, SessionExpiredError, new_client
from app.config import IST

NOW = datetime(2026, 10, 9, 15, 0, tzinfo=IST)


def make_client(body: object, seen: list, status: int = 200) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body)

    return new_client(transport=httpx.MockTransport(handler))


def jdata(request: httpx.Request) -> dict:
    return json.loads(request.content.decode().removeprefix("jData="))


def test_parse_intraday_sorts_dedupes_and_skips_empty_rows():
    rows = [
        {"stat": "Ok", "time": "09-10-2026 09:16:00", "into": "101", "inth": "103", "intl": "100", "intc": "102", "intv": "50"},
        {"stat": "Ok", "time": "09-10-2026 09:15:00", "into": "100", "inth": "101", "intl": "99", "intc": "101", "intv": "70"},
        {"stat": "Ok", "time": "09-10-2026 09:17:00", "into": "0", "inth": "0", "intl": "0", "intc": "0", "intv": "0"},
        {"stat": "Ok", "time": "09-10-2026 09:15:00", "into": "100", "inth": "101", "intl": "99", "intc": "101", "intv": "70"},
    ]
    candles = parse_candles(rows, daily=False)
    assert [c["open"] for c in candles] == [100.0, 101.0]
    # 09:15 IST is 03:45 UTC.
    assert candles[0]["time"] == int(datetime(2026, 10, 9, 9, 15, tzinfo=IST).timestamp())
    assert candles[1]["volume"] == 50


def test_parse_daily_uses_ssboe_and_accepts_json_strings():
    rows = [json.dumps({"ssboe": "1760000000", "into": "1", "inth": "2", "intl": "0.5", "intc": "1.5", "intv": "9"})]
    assert parse_candles(rows, daily=True) == [
        {"time": 1760000000, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 9}
    ]


def test_parse_no_data_is_empty_and_other_errors_raise():
    assert parse_candles({"stat": "Not_Ok", "emsg": 'Error Occurred : 5 "no data"'}, daily=False) == []
    with pytest.raises(BrokerError):
        parse_candles({"stat": "Not_Ok", "emsg": "Invalid token"}, daily=False)


def test_intraday_request_uses_tpseries_with_token_and_minutes():
    seen: list = []
    with make_client([], seen) as client:
        fetch_history(
            access_token="tok", user_id="AB1", exchange="NSE_INDEX", token="26000", brsymbol="Nifty 50",
            interval="15m", days=60, now=NOW, client=client,
        )
    assert seen[0].url.path.endswith("/TPSeries")
    assert seen[0].headers["authorization"] == "Bearer tok"
    body = jdata(seen[0])
    assert body["exch"] == "NSE" and body["token"] == "26000" and body["intrv"] == "15" and body["uid"] == "AB1"
    assert int(body["et"]) == int(NOW.timestamp())


def test_daily_request_uses_eodchartdata_symbol():
    seen: list = []
    with make_client([], seen) as client:
        fetch_history(
            access_token="tok", user_id="AB1", exchange="NSE", token="3045", brsymbol="SBIN-EQ",
            interval="D", days=30, now=NOW, client=client,
        )
    assert seen[0].url.path.endswith("/EODChartData")
    assert jdata(seen[0])["sym"] == "NSE:SBIN-EQ"


def test_expired_session_is_reported():
    seen: list = []
    with make_client({"stat": "Not_Ok", "emsg": "Session Expired :  Invalid Acct Id "}, seen) as client:
        with pytest.raises(SessionExpiredError):
            fetch_history(
                access_token="tok", user_id="AB1", exchange="NSE", token="1", brsymbol="X",
                interval="1m", days=1, now=NOW, client=client,
            )


def test_lookback_and_exchange_helpers():
    assert [lookback_days(i) for i in ("1m", "5m", "15m", "30m", "1h", "D")] == [7, 30, 60, 120, 180, 1095]
    assert data_exchange("BSE_INDEX") == "BSE" and data_exchange("NFO") == "NFO"


@pytest.fixture
def api(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import crypto
    from app.deps import get_db, hash_session_value
    from app.main import app
    from app.models import AuthSession, Base, SymToken
    from app.routers import chart as chart_router

    engine = create_engine(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)

    def override_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(crypto, "_fernet", Fernet(Fernet.generate_key()))
    with TestSession.begin() as db:
        db.add(AuthSession(session_hash=hash_session_value("cookie"), user_id="AB1", user_name="T",
                           access_token_enc=crypto.encrypt("tok"), is_active=True))
        db.add(SymToken(symbol="NIFTY", brsymbol="NIFTY INDEX", name="NIFTY", exchange="NSE_INDEX",
                        brexchange="NSE_INDEX", token="26000"))

    calls: list[dict] = []

    def fake_fetch(**kwargs):
        calls.append(kwargs)
        return [{"time": 1, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 0}]

    monkeypatch.setattr(chart_router, "fetch_history", fake_fetch)
    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    yield client, calls
    app.dependency_overrides.clear()
    engine.dispose()


def test_history_route_requires_session(api):
    client, _ = api
    assert client.get("/api/chart/intervals").status_code == 401
    assert client.get("/api/chart/history?symbol=NIFTY&exchange=NSE_INDEX&interval=5m").status_code == 401


def test_history_route_looks_up_the_symbol_and_returns_candles(api):
    client, calls = api
    client.cookies.set("st_session", "cookie")
    assert client.get("/api/chart/intervals").json()["intervals"][-1] == "D"
    r = client.get("/api/chart/history?symbol=nifty&exchange=NSE_INDEX&interval=15m")
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "NIFTY" and body["interval"] == "15m" and len(body["candles"]) == 1
    assert calls[0]["token"] == "26000" and calls[0]["days"] == 60 and calls[0]["access_token"] == "tok"


def test_history_route_rejects_unknown_symbol_and_interval(api):
    client, _ = api
    client.cookies.set("st_session", "cookie")
    assert client.get("/api/chart/history?symbol=NOPE&exchange=NSE&interval=5m").status_code == 404
    assert client.get("/api/chart/history?symbol=NIFTY&exchange=NSE_INDEX&interval=7m").status_code == 422


def test_closing_volume_glitch_is_clamped():
    def row(t, intv, v):
        return {"time": f"09-10-2026 {t}", "into": "1", "inth": "1", "intl": "1", "intc": "1", "intv": intv, "v": v}

    rows = [
        row("09:15:00", "255357", "270945"),
        row("09:20:00", "193158", "464103"),
        row("15:15:00", "0", "5899261"),
        row("15:20:00", "-5751432", "147829"),
        row("15:25:00", "5901059", "6048888"),
    ]
    assert [c["volume"] for c in parse_candles(rows, daily=False)] == [255357, 193158, 0, 0, 149627]
