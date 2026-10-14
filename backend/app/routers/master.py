import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.broker import master_contract
from app.config import to_ist_iso
from app.deps import get_db, require_session
from app.models import MasterContractStatus, SymToken

router = APIRouter(dependencies=[Depends(require_session)])

SEARCH_LIMIT = 50


@router.get("/api/master-contract/status")
def master_status(db: Session = Depends(get_db)) -> dict:
    row = db.get(MasterContractStatus, 1)
    if row is None:
        return {
            "status": "idle", "message": None, "total_records": 0,
            "exchanges": {}, "started_at": None, "finished_at": None,
        }
    try:
        exchanges = json.loads(row.exchanges_json or "{}")
    except ValueError:
        exchanges = {}
    return {
        "status": row.status,
        "message": row.message,
        "total_records": row.total_records or 0,
        "exchanges": exchanges,
        "started_at": to_ist_iso(row.started_at),
        "finished_at": to_ist_iso(row.finished_at),
    }


@router.post("/api/master-contract/download", status_code=202)
def master_download() -> JSONResponse:
    if not master_contract.start_download_in_background():
        raise HTTPException(status_code=409, detail="Master contract download already in progress")
    return JSONResponse({"status": "downloading"}, status_code=202)


def _like_escape(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("/api/symbols/search")
def search_symbols(
    q: str = Query(..., min_length=2, max_length=64),
    exchange: str | None = Query(None, max_length=16),
    db: Session = Depends(get_db),
) -> list[dict]:
    terms = q.strip().upper().split()
    if not terms or len("".join(terms)) < 2:
        raise HTTPException(status_code=422, detail="Query must be at least 2 characters")

    stmt = select(SymToken)
    for term in terms[:5]:
        pattern = f"%{_like_escape(term)}%"
        stmt = stmt.where(
            or_(
                SymToken.symbol.like(pattern, escape="\\"),
                SymToken.brsymbol.like(pattern, escape="\\"),
                SymToken.name.like(pattern, escape="\\"),
            )
        )
    if exchange and exchange.strip():
        stmt = stmt.where(SymToken.exchange == exchange.strip().upper())

    exact = "".join(terms)
    rank = case(
        (SymToken.symbol == exact, 0),
        (SymToken.symbol.like(f"{_like_escape(terms[0])}%", escape="\\"), 1),
        else_=2,
    )
    stmt = stmt.order_by(rank, func.length(SymToken.symbol), SymToken.symbol).limit(SEARCH_LIMIT)

    return [
        {
            "symbol": r.symbol,
            "brsymbol": r.brsymbol,
            "name": r.name,
            "exchange": r.exchange,
            "token": r.token,
            "expiry": r.expiry,
            "strike": r.strike,
            "lotsize": r.lotsize,
            "instrumenttype": r.instrumenttype,
            "tick_size": r.tick_size,
        }
        for r in db.scalars(stmt)
    ]
