import json
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import crypto
from app.agent import chatgpt_auth, trading, tools as agent_tools
from app.broker.orders import OrderRequest, place_payload, protected_price
from app.deps import get_db, hash_session_value
from app.main import app
from app.models import AgentMessage, AgentOrderAudit, AuthSession, Base, SymToken
from app.routers import agent as agent_router


def test_market_price_protection_slabs_and_rounding():
    assert protected_price(959.1, "BUY", "EQ", 0.05) == 963.9  # 0.5% above 500, rounded up
    assert protected_price(959.1, "SELL", "EQ", 0.05) == 954.3  # rounded down
    assert protected_price(80.0, "BUY", "EQ", 0.05) == 81.6  # 2% below 100
    assert protected_price(8.0, "BUY", "CE", 0.05) == 8.4  # options: 5% below 10


def test_place_payload_maps_fields_and_encodes_ampersand():
    order = OrderRequest(exchange="NSE", brsymbol="M&M-EQ", side="BUY", quantity=2, product="MIS",
                         price_type="MARKET", price=1500.5)
    p = place_payload("AB1", order)
    assert p["tsym"] == "M%26M-EQ" and p["prctyp"] == "LMT" and p["prd"] == "I" and p["trantype"] == "B"
    assert p["prc"] == "1500.5" and p["ordersource"] == "API" and "trgprc" not in p
    sl = place_payload("AB1", OrderRequest(exchange="NSE", brsymbol="SBIN-EQ", side="SELL", quantity=1, product="CNC",
                                           price_type="SL", price=950, trigger_price=951))
    assert sl["prctyp"] == "SL-LMT" and sl["trgprc"] == "951" and sl["prd"] == "C"


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(crypto, "_fernet", Fernet(Fernet.generate_key()))
    for module in (chatgpt_auth, agent_tools, agent_router, trading):
        monkeypatch.setattr(module, "SessionLocal", TestSession)
    with TestSession.begin() as db:
        db.add(AuthSession(session_hash=hash_session_value("cookie"), user_id="AB1", user_name="T",
                           access_token_enc=crypto.encrypt("broker-token"), is_active=True))
        db.add(SymToken(symbol="SBIN", brsymbol="SBIN-EQ", name="SBI", exchange="NSE", brexchange="NSE",
                        token="3045", tick_size=0.05, instrumenttype="EQ", lotsize=1))
    sent: list = []
    # No network: quotes come back fixed and "placing" only records the order.
    monkeypatch.setattr(trading, "post_authenticated", lambda ep, j, tok: {"stat": "Ok", "lp": "959.10"})
    monkeypatch.setattr(trading, "broker_place_order", lambda tok, uid, order: sent.append(order) or "ORD1")

    def override_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    client.cookies.set("st_session", "cookie")
    yield client, TestSession, sent
    app.dependency_overrides.clear()
    engine.dispose()


def _place(**kw):
    tools = {t.name: t for t in trading.build_order_tools("tok", "AB1", {"conversation_id": "c", "run_id": "r"})}
    args = {"symbol": "SBIN", "exchange": "NSE", "side": "BUY", "quantity": 10, **kw}
    return tools["place_order"].entrypoint(**args)


def _enable(**overrides):
    values = {"trading_enabled": True, "max_order_value": 50_000.0, "max_quantity": 500,
              "max_orders_per_day": 20, "allowed_exchanges": ["NSE", "BSE"], **overrides}
    trading.save_risk_settings(values)


def test_order_tools_require_confirmation():
    tools = trading.build_order_tools("tok", "AB1", {})
    assert {t.name: t.requires_confirmation for t in tools} == {
        "place_order": True, "modify_order": True, "cancel_order": True,
    }


def test_risk_check_refuses_and_sends_nothing(env):
    _, TestSession, sent = env
    assert "trading is switched off" in _place()
    _enable()
    assert "not in the allowed exchanges" in _place(exchange="MCX")
    assert "quantity must be between" in _place(quantity=501)
    assert "above the limit" in _place(quantity=100)  # 100 x 963.90 > 50,000
    assert "needs a price" in _place(price_type="LIMIT")
    assert sent == []
    with TestSession() as db:
        assert db.query(AgentOrderAudit).filter_by(status="refused").count() == 5


def test_approved_market_order_is_sent_as_protected_limit_and_duplicates_are_blocked(env):
    _, _, sent = env
    _enable()
    result = json.loads(_place())
    assert result["status"] == "placed" and result["order_id"] == "ORD1" and result["sent_price"] == 963.9
    assert sent[0].price_type == "MARKET" and sent[0].price == 963.9 and sent[0].brsymbol == "SBIN-EQ"
    assert "less than a minute ago" in _place()
    assert len(sent) == 1
    _enable(max_orders_per_day=1)
    assert "daily limit" in _place(quantity=5)


class PausingAgent:
    """The first run pauses on a place_order call; continue_run then runs or skips it, as Agno would."""

    def __init__(self, decisions_seen: list):
        self.decisions_seen = decisions_seen
        self.tool = SimpleNamespace(tool_call_id="call-1", tool_name="place_order",
                                    tool_args={"symbol": "SBIN", "exchange": "NSE", "side": "BUY", "quantity": 1},
                                    result=None, tool_call_error=None, metrics=None)
        self.requirement = SimpleNamespace(tool_execution=self.tool, needs_confirmation=True, decision=None)
        self.requirement.confirm = lambda: setattr(self.requirement, "decision", True)
        self.requirement.reject = lambda note=None: setattr(self.requirement, "decision", False)

    async def arun(self, message, **kwargs):
        yield SimpleNamespace(event="RunStarted", run_id="run-1")
        yield SimpleNamespace(event="RunPaused", run_id="run-1", requirements=[self.requirement])

    async def aget_run_output(self, run_id, session_id=None):
        return SimpleNamespace(is_paused=True, active_requirements=[self.requirement], requirements=[self.requirement])

    async def acontinue_run(self, run_id, requirements, **kwargs):
        self.decisions_seen.append(self.requirement.decision)
        yield SimpleNamespace(event="RunContinued", run_id=run_id)
        text = "Order placed: ORD1." if self.requirement.decision else "Understood, I did not place it."
        yield SimpleNamespace(event="RunContent", content=text, reasoning_content=None)

    async def acancel_run(self, run_id):
        return True


@pytest.mark.parametrize("approve", [True, False])
def test_chat_pauses_for_approval_then_resumes(env, monkeypatch, approve):
    client, TestSession, _ = env
    monkeypatch.setattr(agent_router.chatgpt_auth, "account_status",
                        lambda: {"signed_in": True, "email": None, "plan": None})
    seen: list = []
    agent = PausingAgent(seen)
    monkeypatch.setattr(agent_router, "build_agent", lambda **kw: agent)

    with client.stream("POST", "/api/agent/chat", json={"message": "Buy 1 SBIN"}) as r:
        frames = [json.loads(line[5:]) for line in r.iter_lines() if line.startswith("data:")]
    confirm = next(f for f in frames if f["type"] == "confirm")
    assert confirm["requirements"][0]["summary"].startswith("BUY 1 SBIN on NSE")
    assert frames[-1] == {"type": "done", "reason": "paused"}
    convo = frames[0]["conversation_id"]
    stored = client.get(f"/api/agent/conversations/{convo}").json()["messages"][-1]
    assert stored["pending"]["state"] == "pending"

    body = {"conversation_id": convo, "run_id": confirm["run_id"], "decisions": {"call-1": approve}}
    with client.stream("POST", "/api/agent/chat/confirm", json=body) as r:
        frames = [json.loads(line[5:]) for line in r.iter_lines() if line.startswith("data:")]
    assert seen == [approve]
    text = "".join(f["delta"] for f in frames if f["type"] == "token")
    assert ("ORD1" in text) is approve
    # The same approval cannot be used twice.
    assert client.post("/api/agent/chat/confirm", json=body).status_code == 409
    with TestSession() as db:
        statuses = [a.status for a in db.query(AgentOrderAudit).order_by(AgentOrderAudit.id)]
        assert statuses == (["proposed"] if approve else ["proposed", "rejected"])
