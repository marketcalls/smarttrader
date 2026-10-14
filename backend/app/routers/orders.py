import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.broker import order_service
from app.broker.order_service import OrderInputError
from app.broker.tradesmart import BrokerError, SessionExpiredError
from app.crypto import decrypt
from app.deps import get_db, require_session
from app.models import AuthSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/orders")


class PlaceBody(BaseModel):
    symbol: str = Field(min_length=1, max_length=64)
    exchange: str = Field(min_length=2, max_length=16)
    side: str
    quantity: int = Field(gt=0, le=1_000_000)
    product: str = "MIS"
    price_type: str = "LIMIT"
    price: float = Field(default=0.0, ge=0)
    trigger_price: float = Field(default=0.0, ge=0)


class ModifyBody(BaseModel):
    price: float = Field(gt=0)
    quantity: int | None = Field(default=None, gt=0, le=1_000_000)
    trigger_price: float | None = Field(default=None, gt=0)


class ClosePositionBody(BaseModel):
    symbol: str = Field(min_length=1, max_length=64)
    exchange: str = Field(min_length=2, max_length=16)
    product: str
    side: str  # the position side: long or short
    quantity: int = Field(gt=0, le=1_000_000)


def _creds(session: AuthSession, db: Session) -> tuple[str, str]:
    token = decrypt(session.access_token_enc)
    if not token or not session.user_id:
        session.is_active = False
        db.commit()
        raise HTTPException(status_code=401, detail="Session expired, please log in again")
    return token, session.user_id


def _call(session: AuthSession, db: Session, fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except OrderInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except SessionExpiredError:
        session.is_active = False
        db.commit()
        raise HTTPException(status_code=401, detail="Session expired, please log in again")
    except BrokerError as exc:
        raise HTTPException(status_code=502, detail=f"TradeSmart: {exc}")
    except httpx.HTTPError as exc:
        logger.error("Order request failed: %s", exc.__class__.__name__)
        raise HTTPException(status_code=502, detail="Could not reach TradeSmart")


@router.get("/symbol")
def symbol_state(
    symbol: str = Query(..., min_length=1, max_length=64),
    exchange: str = Query(..., min_length=2, max_length=16),
    session: AuthSession = Depends(require_session),
    db: Session = Depends(get_db),
) -> dict:
    token, uid = _creds(session, db)
    return _call(session, db, order_service.symbol_state, token, uid, symbol, exchange)


@router.post("")
def place(body: PlaceBody, session: AuthSession = Depends(require_session), db: Session = Depends(get_db)) -> dict:
    token, uid = _creds(session, db)
    placed = _call(session, db, order_service.place, token, uid, **body.model_dump())
    logger.info("Chart order placed: %s %s %s on %s (%s)", body.side, body.quantity, body.symbol, body.exchange, placed.order_id)
    return {"order_id": placed.order_id, "sent_price": placed.sent_price, "ltp": placed.ltp}


@router.post("/{order_id}/modify")
def modify(order_id: str, body: ModifyBody, session: AuthSession = Depends(require_session),
           db: Session = Depends(get_db)) -> dict:
    token, uid = _creds(session, db)
    _call(session, db, order_service.modify, token, uid, order_id, price=body.price, quantity=body.quantity,
          trigger_price=body.trigger_price)
    logger.info("Chart order modified: %s to %s", order_id, body.price)
    return {"order_id": order_id, "status": "modified"}


@router.post("/{order_id}/cancel")
def cancel(order_id: str, session: AuthSession = Depends(require_session), db: Session = Depends(get_db)) -> dict:
    token, uid = _creds(session, db)
    _call(session, db, order_service.cancel, token, uid, order_id)
    logger.info("Chart order cancelled: %s", order_id)
    return {"order_id": order_id, "status": "cancel requested"}


@router.post("/close-position")
def close_position(body: ClosePositionBody, session: AuthSession = Depends(require_session),
                   db: Session = Depends(get_db)) -> dict:
    """Square off a position with an opposite market order (sent as a protected limit)."""
    token, uid = _creds(session, db)
    side = "SELL" if body.side == "long" else "BUY"
    placed = _call(session, db, order_service.place, token, uid, symbol=body.symbol, exchange=body.exchange, side=side,
                   quantity=body.quantity, product=body.product, price_type="MARKET")
    logger.info("Chart position closed: %s %s %s (%s)", side, body.quantity, body.symbol, placed.order_id)
    return {"order_id": placed.order_id, "sent_price": placed.sent_price}
