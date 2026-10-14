import base64
import json
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import crypto
from app.agent import chatgpt_auth, finedge, trading, tools as agent_tools
from app.deps import get_db, hash_session_value
from app.main import app
from app.models import AgentMessage, AuthSession, Base
from app.routers import agent as agent_router


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(crypto, "_fernet", Fernet(Fernet.generate_key()))
    for module in (chatgpt_auth, finedge, agent_tools, agent_router, trading):
        monkeypatch.setattr(module, "SessionLocal", TestSession)

    def override_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    with TestSession.begin() as db:
        db.add(AuthSession(session_hash=hash_session_value("cookie"), user_id="AB1", user_name="T",
                           access_token_enc=crypto.encrypt("broker-token"), is_active=True))
    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    client.cookies.set("st_session", "cookie")
    yield client, TestSession
    app.dependency_overrides.clear()
    engine.dispose()


def _jwt(claims: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"e30.{body}.sig"


def test_tokens_are_encrypted_and_status_reads_the_id_token(env):
    _, TestSession = env
    chatgpt_auth.write_tokens({
        "access_token": "a", "refresh_token": "r",
        "id_token": _jwt({"email": "me@example.com", chatgpt_auth.PLAN_CLAIM: {"chatgpt_plan_type": "plus"}}),
    })
    with TestSession() as db:
        from app.models import AgentSecret

        stored = db.get(AgentSecret, chatgpt_auth.SECRET_NAME).value_enc
    assert "refresh_token" not in stored
    assert chatgpt_auth.read_tokens()["refresh_token"] == "r"
    assert chatgpt_auth.account_status() == {"signed_in": True, "email": "me@example.com", "plan": "plus"}
    chatgpt_auth.clear_tokens()
    assert chatgpt_auth.account_status()["signed_in"] is False


def test_litellm_authenticator_uses_the_table_and_never_blocks(env):
    from litellm.llms.chatgpt.authenticator import Authenticator
    from litellm.llms.chatgpt.common_utils import GetAccessTokenError

    chatgpt_auth.install()
    auth = Authenticator()
    with pytest.raises(GetAccessTokenError):
        auth.get_access_token()  # no tokens: raises instead of starting a console login
    auth._write_auth_file({"access_token": "x", "refresh_token": "y"})
    assert chatgpt_auth.read_tokens() == {"access_token": "x", "refresh_token": "y"}


def test_indicator_math():
    closes = [float(x) for x in range(1, 31)]
    assert agent_tools._sma(closes, 5) == 28.0
    assert agent_tools._rsi(closes) == 100.0
    assert agent_tools._ema([2.0] * 30, 10) == 2.0
    candles = [{"high": 11.0, "low": 9.0, "close": 10.0} for _ in range(20)]
    assert agent_tools._atr(candles) == 2.0


def test_settings_roundtrip_and_validation(env):
    client, _ = env
    s = client.get("/api/agent/settings").json()
    assert s["models"] and s["model"] in s["models"]
    assert {t["name"] for t in s["tools"]} >= {"get_quote", "get_funds"}
    model = s["models"][-1]
    r = client.put("/api/agent/settings", json={"model": model, "reasoning_effort": "high", "instructions": " short "})
    assert r.status_code == 200 and r.json() == {"model": model, "reasoning_effort": "high", "instructions": "short"}
    assert client.put("/api/agent/settings", json={"model": "nope"}).status_code == 422


def test_chat_requires_chatgpt_sign_in(env):
    client, _ = env
    assert client.post("/api/agent/chat", json={"message": "hi"}).status_code == 409


class StubAgent:
    """Yields Agno-shaped events: a tool call, two content chunks and a completion."""

    def __init__(self):
        self.cancelled = []

    async def arun(self, message, **kwargs):
        tool = SimpleNamespace(tool_call_id="c1", tool_name="get_quote", tool_args={"symbol": "SBIN", "exchange": "NSE"},
                               result='{"ltp": 950.5}', tool_call_error=None, metrics=SimpleNamespace(duration=0.25))
        yield SimpleNamespace(event="RunStarted", run_id="r1")
        yield SimpleNamespace(event="ToolCallStarted", tool=tool)
        yield SimpleNamespace(event="ToolCallCompleted", tool=tool)
        yield SimpleNamespace(event="RunContent", content="SBIN is at ", reasoning_content=None)
        yield SimpleNamespace(event="RunContent", content="950.50.", reasoning_content=None)
        metrics = SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15, reasoning_tokens=0, duration=1.5)
        yield SimpleNamespace(event="RunCompleted", metrics=metrics, content="SBIN is at 950.50.")

    async def acancel_run(self, run_id):
        self.cancelled.append(run_id)
        return True


def test_chat_streams_frames_and_saves_the_transcript(env, monkeypatch):
    client, TestSession = env
    monkeypatch.setattr(agent_router.chatgpt_auth, "account_status", lambda: {"signed_in": True, "email": None, "plan": None})
    seen = {}

    def fake_build(**kwargs):
        seen.update(kwargs)
        return StubAgent()

    monkeypatch.setattr(agent_router, "build_agent", fake_build)
    with client.stream("POST", "/api/agent/chat", json={"message": "Quote SBIN"}) as r:
        assert r.status_code == 200
        frames = [json.loads(line[5:]) for line in r.iter_lines() if line.startswith("data:")]
    kinds = [f["type"] for f in frames]
    assert kinds == ["start", "run", "tool_start", "tool_end", "token", "token", "usage", "done"]
    assert frames[3]["ok"] is True and frames[3]["duration"] == 0.25
    assert frames[-1]["reason"] == "stop"
    assert [t.__name__ for t in seen["tools"]][:2] == ["search_symbols", "get_quote"]
    conversation_id = frames[0]["conversation_id"]
    detail = client.get(f"/api/agent/conversations/{conversation_id}").json()
    assert detail["title"] == "Quote SBIN"
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][1]["content"] == "SBIN is at 950.50."
    assert detail["messages"][1]["tools"][0]["name"] == "get_quote"
    assert client.get("/api/agent/conversations").json()[0]["id"] == conversation_id
    assert client.delete(f"/api/agent/conversations/{conversation_id}").status_code == 200
    with TestSession() as db:
        assert db.query(AgentMessage).count() == 0


def test_tool_arguments_sent_only_in_done_events_are_recovered():
    from app.agent.litellm_patches import recover

    state: dict = {}
    added = {"type": "response.output_item.added", "output_index": 1,
             "item": {"type": "function_call", "name": "get_quote", "call_id": "c1", "arguments": ""}}
    assert recover(state, added) is None
    done = {"type": "response.function_call_arguments.done", "output_index": 1,
            "arguments": '{"symbol":"SBIN","exchange":"NSE"}'}
    assert recover(state, done) == (1, '{"symbol":"SBIN","exchange":"NSE"}', None)
    # The repeat in output_item.done must not deliver the arguments twice.
    item_done = {"type": "response.output_item.done", "output_index": 1,
                 "item": {"type": "function_call", "name": "get_quote", "arguments": '{"symbol":"SBIN"}'}}
    assert recover(state, item_done) is None


def test_streamed_arguments_are_left_alone():
    from app.agent.litellm_patches import install, recover

    state: dict = {}
    recover(state, {"type": "response.output_item.added", "output_index": 0,
                    "item": {"type": "function_call", "name": "get_funds", "arguments": ""}})
    recover(state, {"type": "response.function_call_arguments.delta", "output_index": 0, "delta": "{}"})
    assert recover(state, {"type": "response.function_call_arguments.done", "output_index": 0, "arguments": "{}"}) is None
    assert install() is True
