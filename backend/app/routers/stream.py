import logging
from urllib.parse import urlparse

import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.broker.history import data_exchange
from app.broker.stream import Instrument, StreamError, relay_ticks
from app.config import CORS_ORIGINS, SESSION_COOKIE
from app.crypto import decrypt
from app.db import SessionLocal
from app.deps import find_active_session
from app.models import SymToken

logger = logging.getLogger(__name__)

router = APIRouter()


def origin_allowed(origin: str | None, host: str | None) -> bool:
    """Browsers send cookies on cross-site WebSocket handshakes, so the Origin must be ours."""
    if not origin:
        return False
    if origin in CORS_ORIGINS:
        return True
    return bool(host) and urlparse(origin).netloc == host


def _session_credentials(raw_cookie: str | None) -> tuple[str, str] | None:
    with SessionLocal() as db:
        session = find_active_session(db, raw_cookie)
        if session is None or not session.user_id:
            return None
        token = decrypt(session.access_token_enc)
        return (session.user_id, token) if token else None


def _lookup(symbol: str, exchange: str) -> Instrument | None:
    with SessionLocal() as db:
        row = db.scalar(select(SymToken).where(SymToken.symbol == symbol, SymToken.exchange == exchange).limit(1))
        if row is None or not row.token:
            return None
        return Instrument(
            symbol=row.symbol,
            exchange=row.exchange or exchange,
            key=f"{data_exchange(row.brexchange or exchange)}|{row.token}",
        )


async def _resolve(symbol: str, exchange: str) -> Instrument | None:
    return await run_in_threadpool(_lookup, symbol, exchange)


@router.websocket("/api/ws/ticks")
async def ticks(ws: WebSocket) -> None:
    if not origin_allowed(ws.headers.get("origin"), ws.headers.get("host")):
        await ws.close(code=4403, reason="Origin not allowed")
        return
    credentials = await run_in_threadpool(_session_credentials, ws.cookies.get(SESSION_COOKIE))
    # Accept before closing so the browser receives 4401 and stops reconnecting;
    # a close before accept would arrive as a bare HTTP 403.
    await ws.accept()
    if credentials is None:
        await ws.close(code=4401, reason="Not authenticated")
        return
    user_id, access_token = credentials
    try:
        await relay_ticks(ws, user_id=user_id, access_token=access_token, resolve=_resolve)
    except WebSocketDisconnect:
        pass
    except StreamError as exc:
        logger.info("Tick stream ended: %s", exc)
        try:
            await ws.send_json({"type": "status", "state": "error", "message": str(exc)})
            await ws.close(code=1011)
        except (RuntimeError, WebSocketDisconnect):
            pass
    except websockets.ConnectionClosed:
        logger.info("TradeSmart stream connection closed")
        try:
            await ws.close(code=1011)
        except RuntimeError:
            pass
