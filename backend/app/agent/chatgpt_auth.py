"""Sign in with a ChatGPT subscription for LiteLLM's `chatgpt/` models.

LiteLLM ships the OAuth device-code flow used by OpenAI's Codex CLI, but it keeps
the tokens in a plain JSON file and, when no token is present, prints a code to
the server console and blocks for up to fifteen minutes. Here the flow is driven
from the settings page instead, and the tokens live encrypted in SQLite: the
authenticator's file reads and writes are routed to the `agent_secret` table and
its interactive login is replaced by an error that tells the user to sign in.
"""

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any

from app.config import DATA_DIR, utcnow
from app.crypto import decrypt, encrypt
from app.db import SessionLocal
from app.models import AgentSecret

# LiteLLM creates this directory on first use; keep it inside the app's data folder.
os.environ.setdefault("CHATGPT_TOKEN_DIR", str(DATA_DIR / "chatgpt"))

from litellm.llms.chatgpt import authenticator as _auth  # noqa: E402
from litellm.llms.chatgpt.common_utils import (  # noqa: E402
    CHATGPT_DEVICE_VERIFY_URL,
    GetAccessTokenError,
)

logger = logging.getLogger(__name__)

SECRET_NAME = "chatgpt_oauth"
LOGIN_TIMEOUT_SECONDS = 15 * 60
PLAN_CLAIM = "https://api.openai.com/auth"


def read_tokens() -> dict[str, Any] | None:
    with SessionLocal() as db:
        row = db.get(AgentSecret, SECRET_NAME)
        raw = decrypt(row.value_enc) if row else None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def write_tokens(data: dict[str, Any]) -> None:
    value = encrypt(json.dumps(data))
    with SessionLocal.begin() as db:
        row = db.get(AgentSecret, SECRET_NAME)
        if row is None:
            db.add(AgentSecret(name=SECRET_NAME, value_enc=value, updated_at=utcnow()))
        else:
            row.value_enc = value
            row.updated_at = utcnow()


def clear_tokens() -> None:
    with SessionLocal.begin() as db:
        row = db.get(AgentSecret, SECRET_NAME)
        if row is not None:
            db.delete(row)


def _no_interactive_login(self) -> dict[str, str]:
    raise GetAccessTokenError(
        message="Not signed in to ChatGPT. Open Agent settings and choose Sign in with ChatGPT.",
        status_code=401,
    )


_installed = False


def install() -> None:
    """Route LiteLLM's ChatGPT token storage to the encrypted table. Idempotent."""
    global _installed
    if _installed:
        return
    _auth.Authenticator._read_auth_file = lambda self: read_tokens()
    _auth.Authenticator._write_auth_file = lambda self, data: write_tokens(dict(data))
    _auth.Authenticator._ensure_token_dir = lambda self: None
    _auth.Authenticator._login_device_code = _no_interactive_login
    _auth.Authenticator._wait_for_access_token = lambda self, timeout_seconds: None
    _installed = True


@dataclass
class LoginFlow:
    flow_id: int
    user_code: str
    verification_url: str
    expires_at: float
    state: str = "pending"  # pending | complete | error | expired
    error: str | None = None


_lock = threading.Lock()
_flow: LoginFlow | None = None
_counter = 0


def _run_flow(flow: LoginFlow, device: dict[str, str]) -> None:
    authenticator = _auth.Authenticator()
    try:
        code = authenticator._poll_for_authorization_code(device)
        tokens = authenticator._exchange_code_for_tokens(code)
        write_tokens(dict(authenticator._build_auth_record(tokens)))
        state, error = "complete", None
        logger.info("ChatGPT sign-in completed")
    except GetAccessTokenError as exc:
        expired = getattr(exc, "status_code", None) == 408
        state, error = ("expired", "The code expired before it was approved.") if expired else ("error", str(exc.message))
        logger.warning("ChatGPT sign-in failed: %s", error)
    except Exception as exc:  # noqa: BLE001 - any failure ends this flow with a readable state
        state, error = "error", f"Sign-in failed ({exc.__class__.__name__})"
        logger.warning("ChatGPT sign-in failed: %s", exc.__class__.__name__)
    with _lock:
        if _flow is flow:
            flow.state, flow.error = state, error


def start_login() -> LoginFlow:
    """Request a device code; a background thread waits for the user to approve it."""
    global _flow, _counter
    install()
    device = _auth.Authenticator()._request_device_code()
    with _lock:
        _counter += 1
        flow = LoginFlow(
            flow_id=_counter,
            user_code=device["user_code"],
            verification_url=CHATGPT_DEVICE_VERIFY_URL,
            expires_at=time.time() + LOGIN_TIMEOUT_SECONDS,
        )
        _flow = flow
    threading.Thread(target=_run_flow, args=(flow, device), daemon=True, name="chatgpt-login").start()
    return flow


def current_flow() -> LoginFlow | None:
    with _lock:
        flow = _flow
        if flow and flow.state == "pending" and time.time() > flow.expires_at:
            flow.state, flow.error = "expired", "The code expired before it was approved."
        return flow


def cancel_login() -> None:
    global _flow
    with _lock:
        _flow = None


def account_status() -> dict[str, Any]:
    """Signed-in state and who is signed in, from the stored ID token. Never returns tokens."""
    tokens = read_tokens()
    if not tokens or not tokens.get("refresh_token"):
        return {"signed_in": False, "email": None, "plan": None}
    claims: dict[str, Any] = {}
    id_token = tokens.get("id_token")
    if isinstance(id_token, str):
        try:
            claims = dict(_auth.Authenticator.__new__(_auth.Authenticator)._decode_jwt_claims(id_token))
        except Exception:  # noqa: BLE001 - a malformed token only hides the account details
            claims = {}
    plan_claim = claims.get(PLAN_CLAIM)
    plan = plan_claim.get("chatgpt_plan_type") if isinstance(plan_claim, dict) else None
    return {"signed_in": True, "email": claims.get("email"), "plan": plan}
