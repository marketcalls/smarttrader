import hashlib
from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import SESSION_COOKIE
from app.db import SessionLocal
from app.models import AuthSession


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def hash_session_value(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def find_active_session(db: Session, raw: str | None) -> AuthSession | None:
    if not raw:
        return None
    return db.scalar(
        select(AuthSession).where(
            AuthSession.session_hash == hash_session_value(raw),
            AuthSession.is_active.is_(True),
        )
    )


def require_session(request: Request, db: Session = Depends(get_db)) -> AuthSession:
    session = find_active_session(db, request.cookies.get(SESSION_COOKIE))
    if session is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return session
