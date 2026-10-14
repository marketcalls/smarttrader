import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.broker import stream as stream_mod
from app.broker.stream import Instrument, StreamError, TickState, merge_tick, relay_ticks
from app.main import app
from app.routers.stream import origin_allowed


def test_merge_tick_keeps_fields_missing_from_partial_feeds():
    state = TickState()
    assert merge_tick(state, {"t": "tf", "v": "10"}, now=1) is None
    assert merge_tick(state, {"t": "tk", "lp": "8801.00", "v": "21951"}, now=2) == {
        "ltp": 8801.0, "volume": 21951.0, "time": 2,
    }
    assert merge_tick(state, {"t": "tf", "v": "21954"}, now=3) == {"ltp": 8801.0, "volume": 21954.0, "time": 3}
    assert merge_tick(state, {"t": "tf", "lp": "0"}, now=4)["ltp"] == 8801.0


def test_origin_check():
    assert origin_allowed("http://127.0.0.1:5173", "127.0.0.1:8000")
    assert origin_allowed("http://127.0.0.1:8000", "127.0.0.1:8000")
    assert not origin_allowed("https://evil.example", "127.0.0.1:8000")
    assert not origin_allowed(None, "127.0.0.1:8000")


def test_ws_rejects_foreign_origin_and_missing_session():
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/api/ws/ticks", headers={"origin": "https://evil.example"}) as ws:
            ws.receive_json()
    assert exc.value.code == 4403
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/api/ws/ticks", headers={"origin": "http://127.0.0.1:5173"}) as ws:
            ws.receive_json()
    assert exc.value.code == 4401


class FakeBroker:
    """Stands in for the Noren socket: records sends, replays scripted messages."""

    def __init__(self, ack, feed):
        self.sent = []
        self._ack = ack
        self._feed = feed

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, raw):
        self.sent.append(json.loads(raw))

    async def recv(self):
        return json.dumps(self._ack)

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        while not any(m.get("t") == "t" for m in self.sent):
            await asyncio.sleep(0.01)
        for message in self._feed:
            yield json.dumps(message)
        await asyncio.sleep(3600)


class FakeBrowser:
    def __init__(self):
        self.sent = []
        self._first = True

    async def send_json(self, data):
        self.sent.append(data)

    async def receive_json(self):
        if self._first:
            self._first = False
            return {"action": "subscribe", "symbol": "crudeoil19oct26fut", "exchange": "MCX"}
        await asyncio.sleep(3600)


async def _resolve(symbol, exchange):
    return Instrument(symbol=symbol, exchange=exchange, key="MCX|569900")


def run_relay(broker, browser, monkeypatch, seconds=0.3):
    async def fake_connect(*args, **kwargs):
        return broker

    monkeypatch.setattr(stream_mod.websockets, "connect", fake_connect)

    async def go():
        try:
            await asyncio.wait_for(relay_ticks(browser, user_id="AB1", access_token="tok", resolve=_resolve), seconds)
        except TimeoutError:
            pass

    asyncio.run(go())


def test_relay_connects_subscribes_and_forwards_only_its_instrument(monkeypatch):
    broker = FakeBroker(
        {"t": "ak", "s": "OK", "uid": "AB1"},
        [
            {"t": "tk", "e": "MCX", "tk": "569900", "lp": "8801.00", "v": "21951"},
            {"t": "tf", "e": "MCX", "tk": "111", "lp": "1.00"},
            {"t": "tf", "e": "MCX", "tk": "569900", "lp": "8802.00"},
        ],
    )
    browser = FakeBrowser()
    run_relay(broker, browser, monkeypatch)
    assert broker.sent[0] == {"t": "a", "uid": "AB1", "actid": "AB1", "source": "API", "accesstoken": "tok"}
    assert {"t": "t", "k": "MCX|569900"} in broker.sent
    ticks = [m for m in browser.sent if m["type"] == "tick"]
    assert [t["ltp"] for t in ticks] == [8801.0, 8802.0]
    assert ticks[0]["symbol"] == "CRUDEOIL19OCT26FUT"
    assert browser.sent[0] == {"type": "status", "state": "connected"}


def test_relay_reports_refused_session(monkeypatch):
    broker = FakeBroker({"t": "ak", "s": "Not_Ok"}, [])
    with pytest.raises(StreamError):
        run_relay(broker, FakeBrowser(), monkeypatch)
