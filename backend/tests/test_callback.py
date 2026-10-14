"""Callback redirect behaviour. Uses a temp SQLite DB and a throwaway Fernet key,
so the live data/ directory is never touched and no network calls are made."""

from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import config, crypto
from app.broker.tradesmart import BrokerError, LoginResult
from app.deps import get_db, hash_session_value
from app.main import app
from app.models import AuthSession, Base, BrokerConfig
from app.routers import auth as auth_router


@pytest.fixture
def env(tmp_path, monkeypatch):
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
        db.add(BrokerConfig(id=1, api_key="KEY", api_secret_enc=crypto.encrypt("SECRET"),
                            redirect_url="http://127.0.0.1:8000/tradesmart/callback"))

    started = []
    monkeypatch.setattr(auth_router.master_contract, "start_download_in_background",
                        lambda: started.append(True) or True)
    # Default: no frontend build, so redirects go to the dev server.
    monkeypatch.setattr(config, "FRONTEND_DIST", tmp_path / "no-dist")
    monkeypatch.delenv(config.FRONTEND_URL_ENV, raising=False)

    app.dependency_overrides[get_db] = override_db
    # Not used as a context manager, so the lifespan (live DB init) does not run.
    client = TestClient(app, follow_redirects=False)
    yield client, TestSession, started, tmp_path
    app.dependency_overrides.clear()
    engine.dispose()


def error_of(location: str) -> str:
    return parse_qs(urlparse(location).query)["error"][0]


def test_no_code_redirects_to_dev_login(env):
    client, *_ = env
    r = client.get("/tradesmart/callback")
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith("http://127.0.0.1:5173/login?error=")
    assert "authorization code" in error_of(loc)


def test_bogus_code_redirects_with_broker_message(env, monkeypatch):
    client, *_ = env

    def fail(*_args, **_kwargs):
        raise BrokerError('Error Occurred : 5 "no data"')

    monkeypatch.setattr(auth_router, "generate_access_token", fail)
    r = client.get("/tradesmart/callback?code=bogus")
    assert r.status_code == 302
    assert r.headers["location"].startswith("http://127.0.0.1:5173/login?error=")
    assert error_of(r.headers["location"]) == 'TradeSmart login failed: Error Occurred : 5 "no data"'
    assert "set-cookie" not in r.headers


def test_env_override_for_dev_frontend(env, monkeypatch):
    client, *_ = env
    monkeypatch.setenv(config.FRONTEND_URL_ENV, "http://localhost:5174/")
    r = client.get("/tradesmart/callback")
    assert r.headers["location"].startswith("http://localhost:5174/login?error=")


def test_relative_redirect_when_dist_exists(env, monkeypatch):
    client, _, _, tmp_path = env
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html></html>")
    monkeypatch.setattr(config, "FRONTEND_DIST", dist)
    r = client.get("/tradesmart/callback")
    assert r.headers["location"].startswith("/login?error=")


def test_success_sets_cookie_creates_session_and_starts_download(env, monkeypatch):
    client, TestSession, started, _ = env
    calls = []

    def ok(api_key, api_secret, code):
        calls.append((api_key, api_secret, code))
        return LoginResult(access_token="ACCESS", user_id="AB1234", user_name="Test User")

    monkeypatch.setattr(auth_router, "generate_access_token", ok)
    r = client.get("/tradesmart/callback?code=good")
    assert r.status_code == 302
    assert r.headers["location"] == "http://127.0.0.1:5173/dashboard"
    assert calls == [("KEY", "SECRET", "good")]
    assert started == [True]

    cookie = r.headers["set-cookie"]
    assert cookie.startswith("st_session=")
    lowered = cookie.lower()
    assert "httponly" in lowered and "samesite=lax" in lowered and "path=/" in lowered
    assert "secure" not in lowered and "domain" not in lowered

    raw = cookie.split(";", 1)[0].split("=", 1)[1]
    with TestSession() as db:
        rows = db.scalars(select(AuthSession)).all()
    assert len(rows) == 1
    assert rows[0].session_hash == hash_session_value(raw)
    assert rows[0].access_token_enc != "ACCESS"
    assert crypto.decrypt(rows[0].access_token_enc) == "ACCESS"

    # A second login deactivates the first session.
    r2 = client.get("/tradesmart/callback?code=again")
    with TestSession() as db:
        active = db.scalars(select(AuthSession).where(AuthSession.is_active.is_(True))).all()
    assert len(active) == 1
    assert r2.headers["set-cookie"] != cookie
