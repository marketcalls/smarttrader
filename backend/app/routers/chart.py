import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.broker.history import INTERVALS, MAX_LOOKBACK_DAYS, fetch_history, lookback_days
from app.broker.tradesmart import BrokerError, SessionExpiredError
from app.crypto import decrypt
from app.deps import get_db, require_session
from app.models import AuthSession, SymToken

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/chart/intervals", dependencies=[Depends(require_session)])
def chart_intervals() -> dict:
    return {"intervals": list(INTERVALS)}


@router.get("/api/chart/history")
def chart_history(
    symbol: str = Query(..., min_length=1, max_length=64),
    exchange: str = Query(..., min_length=2, max_length=16),
    interval: str = Query(...),
    days: int | None = Query(None, ge=1, le=MAX_LOOKBACK_DAYS),
    session: AuthSession = Depends(require_session),
    db: Session = Depends(get_db),
) -> dict:
    if interval not in INTERVALS:
        raise HTTPException(
            status_code=422, detail=f"Unsupported interval. Supported: {', '.join(INTERVALS)}"
        )
    symbol = symbol.strip().upper()
    exchange = exchange.strip().upper()
    row = db.scalar(
        select(SymToken).where(SymToken.symbol == symbol, SymToken.exchange == exchange).limit(1)
    )
    if row is None or not row.token:
        raise HTTPException(
            status_code=404,
            detail=f"{symbol} on {exchange} is not in the master contract. Download it or pick another symbol.",
        )

    token = decrypt(session.access_token_enc)
    if not token or not session.user_id:
        session.is_active = False
        db.commit()
        raise HTTPException(status_code=401, detail="Session expired, please log in again")

    try:
        candles = fetch_history(
            access_token=token,
            user_id=session.user_id,
            exchange=row.brexchange or exchange,
            token=row.token,
            brsymbol=row.brsymbol,
            interval=interval,
            days=days or lookback_days(interval),
        )
    except SessionExpiredError:
        session.is_active = False
        db.commit()
        raise HTTPException(status_code=401, detail="Session expired, please log in again")
    except BrokerError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except httpx.HTTPError as exc:
        logger.error("History request failed: %s", exc.__class__.__name__)
        raise HTTPException(status_code=502, detail="Could not reach TradeSmart")

    return {
        "symbol": row.symbol,
        "exchange": row.exchange,
        "name": row.name,
        "interval": interval,
        "candles": candles,
    }
