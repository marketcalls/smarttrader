import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.broker.tradesmart import BrokerError, SessionExpiredError, fetch_funds
from app.crypto import decrypt
from app.deps import get_db, require_session
from app.models import AuthSession

logger = logging.getLogger(__name__)

router = APIRouter()


def _expire(db: Session, session: AuthSession) -> HTTPException:
    session.is_active = False
    db.commit()
    return HTTPException(status_code=401, detail="Session expired, please log in again")


@router.get("/api/funds")
def get_funds(session: AuthSession = Depends(require_session), db: Session = Depends(get_db)) -> dict:
    # get_db is cached per request, so `session` already belongs to `db`.
    token = decrypt(session.access_token_enc)
    if not token or not session.user_id:
        raise _expire(db, session)
    try:
        return fetch_funds(token, session.user_id)
    except SessionExpiredError:
        raise _expire(db, session)
    except BrokerError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except httpx.HTTPError as exc:
        logger.error("Funds request failed: %s", exc.__class__.__name__)
        raise HTTPException(status_code=502, detail="Could not reach TradeSmart")
