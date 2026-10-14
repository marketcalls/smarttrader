import logging
import secrets
from urllib.parse import quote, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.broker import master_contract
from app.broker.tradesmart import BrokerError, build_authorize_url, generate_access_token
from app.config import SESSION_COOKIE, frontend_url, to_ist_iso, utcnow
from app.crypto import decrypt, encrypt
from app.deps import find_active_session, get_db, hash_session_value
from app.models import AuthSession, BrokerConfig

logger = logging.getLogger(__name__)

router = APIRouter()


class LoginRequest(BaseModel):
    api_key: str
    api_secret: str | None = None
    redirect_url: str


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


@router.get("/api/config")
def get_config(db: Session = Depends(get_db)) -> dict:
    cfg = db.get(BrokerConfig, 1)
    if cfg is None:
        return {"api_key": None, "redirect_url": None, "has_secret": False}
    return {
        "api_key": cfg.api_key,
        "redirect_url": cfg.redirect_url,
        "has_secret": bool(cfg.api_secret_enc),
    }


@router.post("/api/auth/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> dict:
    api_key = payload.api_key.strip()
    api_secret = (payload.api_secret or "").strip()
    redirect_url = payload.redirect_url.strip()

    if not api_key:
        raise HTTPException(status_code=400, detail="API key is required")
    if not _is_http_url(redirect_url):
        raise HTTPException(status_code=400, detail="Redirect URL must be an http or https URL")

    cfg = db.get(BrokerConfig, 1)
    if not api_secret and (cfg is None or not cfg.api_secret_enc):
        raise HTTPException(status_code=400, detail="API secret is required")

    if cfg is None:
        cfg = BrokerConfig(id=1)
        db.add(cfg)
    cfg.api_key = api_key
    cfg.redirect_url = redirect_url
    if api_secret:
        cfg.api_secret_enc = encrypt(api_secret)
    cfg.updated_at = utcnow()
    db.commit()
    logger.info("Broker config saved (secret updated: %s)", bool(api_secret))
    return {"authorize_url": build_authorize_url(api_key)}


def _login_error(message: str) -> RedirectResponse:
    target = f"{frontend_url()}/login?error={quote(message[:300], safe='')}"
    return RedirectResponse(url=target, status_code=302)


@router.get("/tradesmart/callback")
def tradesmart_callback(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    params = request.query_params
    code = (params.get("code") or "").strip()
    if not code:
        broker_msg = params.get("error_description") or params.get("error") or params.get("emsg")
        message = "TradeSmart did not return an authorization code"
        if broker_msg:
            message += f": {broker_msg}"
        logger.warning("Callback without code")
        return _login_error(message)

    cfg = db.get(BrokerConfig, 1)
    api_secret = decrypt(cfg.api_secret_enc) if cfg else None
    if cfg is None or not cfg.api_key or not api_secret:
        return _login_error("API key and secret are not configured. Save them and log in again.")

    try:
        result = generate_access_token(cfg.api_key, api_secret, code)
    except BrokerError as exc:
        return _login_error(f"TradeSmart login failed: {exc}")
    except httpx.HTTPError as exc:
        logger.error("GenAcsTok request failed: %s", exc.__class__.__name__)
        return _login_error("Could not reach TradeSmart. Please try again.")
    except Exception:
        logger.exception("Unexpected error during token exchange")
        return _login_error("Unexpected error during login")

    raw_cookie = secrets.token_urlsafe(32)
    db.execute(update(AuthSession).where(AuthSession.is_active.is_(True)).values(is_active=False))
    db.add(
        AuthSession(
            session_hash=hash_session_value(raw_cookie),
            user_id=result.user_id,
            user_name=result.user_name,
            access_token_enc=encrypt(result.access_token),
            created_at=utcnow(),
            is_active=True,
        )
    )
    db.commit()
    logger.info("Login successful for user %s", result.user_id)

    if not master_contract.start_download_in_background():
        logger.info("Master contract download already running, not starting another")

    response = RedirectResponse(url=f"{frontend_url()}/dashboard", status_code=302)
    # Host-only cookie (no Domain): cookies ignore ports, so the browser also
    # sends it to the Vite dev server on 127.0.0.1:5173.
    response.set_cookie(
        key=SESSION_COOKIE,
        value=raw_cookie,
        httponly=True,
        samesite="lax",
        path="/",
        secure=False,
    )
    return response


@router.get("/api/auth/session")
def get_session(request: Request, db: Session = Depends(get_db)) -> dict:
    session = find_active_session(db, request.cookies.get(SESSION_COOKIE))
    if session is None:
        return {"authenticated": False, "user_id": None, "user_name": None, "login_time": None}
    return {
        "authenticated": True,
        "user_id": session.user_id,
        "user_name": session.user_name,
        "login_time": to_ist_iso(session.created_at),
    }


@router.post("/api/auth/logout")
def logout(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    session = find_active_session(db, request.cookies.get(SESSION_COOKIE))
    if session is not None:
        session.is_active = False
        db.commit()
        logger.info("Logged out user %s", session.user_id)
    response = JSONResponse({"status": "ok"})
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="lax")
    return response
