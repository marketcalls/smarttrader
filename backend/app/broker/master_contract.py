"""TradeSmart master contract: download, parse and store the symbol master.

Port of openalgo/broker/tradesmart/database/master_contract_db.py using the csv
module instead of pandas, so symbols follow the OpenAlgo conventions:
  * instrumenttype is EQ / FUT / CE / PE / INDEX (other NSE series kept as is)
  * indices live on NSE_INDEX / BSE_INDEX
  * expiry is DD-MMM-YY uppercase, empty for equities and indices
  * derivative symbol = name + expiry without dashes + [strike] + FUT/CE/PE
"""

import csv
import json
import logging
import re
import shutil
import tempfile
import threading
import time
import zipfile
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import datetime
from itertools import batched
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import delete, insert, update

from app.config import (
    DOWNLOAD_CONNECT_TIMEOUT,
    DOWNLOAD_READ_TIMEOUT,
    TMP_DIR,
    TRADESMART_HOST,
    USER_AGENT,
    ensure_data_dirs,
    utcnow,
)
from app.db import SessionLocal
from app.models import MasterContractStatus, SymToken

logger = logging.getLogger(__name__)

EXCHANGES = ("NSE", "BSE", "NFO", "CDS", "MCX", "BFO")
INSERT_BATCH = 10_000
MAX_EXTRACTED_BYTES = 512 * 1024 * 1024

NSE_INDEX_ALIASES = {
    "NIFTY50": "NIFTY",
    "NIFTYINDEX": "NIFTY",
    "NIFTYBANK": "BANKNIFTY",
    "NIFTYFIN": "FINNIFTY",
    "NIFTYFINSERVICE": "FINNIFTY",
    "NIFTYFINANCIALSERVICES": "FINNIFTY",
    "NIFTYNEXT50": "NIFTYNXT50",
    "NIFTYMIDSELECT": "MIDCPNIFTY",
    "NIFTYMIDCAPSELECT": "MIDCPNIFTY",
}

BSE_INDEX_ROWS = [
    {
        "symbol": "SENSEX", "brsymbol": "SENSEX", "name": "SENSEX",
        "exchange": "BSE_INDEX", "brexchange": "BSE_INDEX", "token": "1",
        "expiry": "", "strike": -1, "lotsize": 1, "instrumenttype": "INDEX", "tick_size": 0.05,
    },
    {
        "symbol": "BANKEX", "brsymbol": "BANKEX", "name": "BANKEX",
        "exchange": "BSE_INDEX", "brexchange": "BSE_INDEX", "token": "12",
        "expiry": "", "strike": -1, "lotsize": 1, "instrumenttype": "INDEX", "tick_size": 0.05,
    },
]

Row = dict[str, Any]


# ---------------------------------------------------------------------------
# Field helpers
# ---------------------------------------------------------------------------

def _clean(value: str | None) -> str:
    return (value or "").strip()


def _to_float_or_none(value: str | None) -> float | None:
    try:
        return float(_clean(value))
    except ValueError:
        return None


def _to_int(value: str | None) -> int:
    try:
        return int(float(_clean(value)))
    except ValueError:
        return 0


def format_expiry(date_str: str | None) -> str | None:
    """DD-MMM-YYYY (any case) to DD-MMM-YY uppercase; None when not a valid date."""
    try:
        return datetime.strptime(_clean(date_str), "%d-%b-%Y").strftime("%d-%b-%y").upper()
    except ValueError:
        return None


def handle_strike(value: str | None) -> int | float:
    """Integral strikes become int, others float, unparsable -1 (blank means -1)."""
    raw = _clean(value) or "-1"
    try:
        number = float(raw)
    except ValueError:
        return -1
    return int(number) if number.is_integer() else number


def format_derivative_symbol(name: str, expiry: str | None, strike: int | float, instrumenttype: str) -> str:
    compact_expiry = expiry.replace("-", "") if expiry else ""
    if instrumenttype == "FUT":
        return f"{name}{compact_expiry}FUT"
    if isinstance(strike, float) and strike.is_integer():
        strike = int(strike)
    return f"{name}{compact_expiry}{strike}{instrumenttype}"


def _reader(lines: Iterable[str]) -> csv.DictReader:
    return csv.DictReader(lines)


# ---------------------------------------------------------------------------
# Per exchange parsers. Each takes the lines of the extracted text file.
# ---------------------------------------------------------------------------

def _equity_symbol(brsymbol: str) -> str:
    # Strip only the series suffix: BAJAJ-AUTO-EQ -> BAJAJ-AUTO, M&M-EQ -> M&M
    if "-EQ" in brsymbol:
        return brsymbol.replace("-EQ", "")
    if "-BE" in brsymbol:
        return brsymbol.replace("-BE", "")
    return brsymbol


def _nse_index_symbol(symbol: str) -> str:
    normalized = symbol.upper().replace(" ", "").replace("-", "")
    return NSE_INDEX_ALIASES.get(normalized, normalized)


# The OpenAlgo reference divides NSE/BSE TickSize by 100 (paise). The live
# TradeSmart files already carry rupees (0.01, 0.05, 0.1, ...), so no division.
def parse_nse(lines: Iterable[str]) -> list[Row]:
    rows: list[Row] = []
    for rec in _reader(lines):
        token = _clean(rec.get("Token"))
        brsymbol = _clean(rec.get("TradingSymbol"))
        if not token or not brsymbol:
            continue
        instrument = _clean(rec.get("Instrument"))
        is_index = instrument == "INDEX"
        exchange = "NSE_INDEX" if is_index else "NSE"
        symbol = _equity_symbol(brsymbol)
        if is_index:
            symbol = _nse_index_symbol(symbol)
        rows.append({
            "symbol": symbol,
            "brsymbol": brsymbol,
            "name": _clean(rec.get("Symbol")),
            "exchange": exchange,
            "brexchange": exchange,
            "token": token,
            "expiry": "",
            "strike": -1,
            "lotsize": _to_int(rec.get("LotSize")),
            "instrumenttype": "EQ" if instrument in ("EQ", "BE") else instrument,
            "tick_size": _to_float_or_none(rec.get("TickSize")),
        })
    return rows


def parse_bse(lines: Iterable[str]) -> list[Row]:
    rows: list[Row] = []
    for rec in _reader(lines):
        token = _clean(rec.get("Token"))
        brsymbol = _clean(rec.get("TradingSymbol"))
        if not token or not brsymbol:
            continue
        rows.append({
            "symbol": brsymbol,
            "brsymbol": brsymbol,
            "name": _clean(rec.get("Symbol")),
            "exchange": "BSE",
            "brexchange": "BSE",
            "token": token,
            "expiry": "",
            "strike": -1,
            "lotsize": _to_int(rec.get("LotSize")),
            "instrumenttype": "EQ",
            "tick_size": _to_float_or_none(rec.get("TickSize")),
        })
    rows.extend(dict(r) for r in BSE_INDEX_ROWS)
    return rows


def _derivative_row(rec: dict[str, str | None], exchange: str, instrumenttype: str,
                    strike_field: str = "StrikePrice", name: str | None = None) -> Row:
    expiry = format_expiry(rec.get("Expiry"))
    strike = handle_strike(rec.get(strike_field))
    name = name if name is not None else _clean(rec.get("Symbol"))
    return {
        "symbol": format_derivative_symbol(name, expiry, strike, instrumenttype),
        "brsymbol": _clean(rec.get("TradingSymbol")),
        "name": name,
        "exchange": exchange,
        "brexchange": exchange,
        "token": _clean(rec.get("Token")),
        "expiry": expiry,
        "strike": strike,
        "lotsize": _to_int(rec.get("LotSize")),
        "instrumenttype": instrumenttype,
        "tick_size": _to_float_or_none(rec.get("TickSize")),
    }


def parse_nfo(lines: Iterable[str]) -> list[Row]:
    rows: list[Row] = []
    for rec in _reader(lines):
        if not _clean(rec.get("Token")) or not _clean(rec.get("TradingSymbol")):
            continue
        optiontype = _clean(rec.get("OptionType"))
        instrumenttype = "FUT" if optiontype == "XX" else optiontype
        rows.append(_derivative_row(rec, "NFO", instrumenttype))
    return rows


def _parse_noren_options(lines: Iterable[str], exchange: str, option_instrument: str,
                         min_token: int | None = None) -> list[Row]:
    rows: list[Row] = []
    for rec in _reader(lines):
        token = _clean(rec.get("Token"))
        if not token or not _clean(rec.get("TradingSymbol")):
            continue
        if min_token is not None:
            try:
                if int(token) <= min_token:
                    continue
            except ValueError:
                continue
        optiontype = _clean(rec.get("OptionType"))
        instrumenttype = "FUT" if optiontype == "XX" else _clean(rec.get("Instrument"))
        if instrumenttype == option_instrument:
            instrumenttype = optiontype
        rows.append(_derivative_row(rec, exchange, instrumenttype))
    return rows


def parse_cds(lines: Iterable[str]) -> list[Row]:
    # Tokens up to 100 are dummy or index rows in the CDS file.
    return _parse_noren_options(lines, "CDS", "OPTCUR", min_token=100)


def parse_mcx(lines: Iterable[str]) -> list[Row]:
    return _parse_noren_options(lines, "MCX", "OPTFUT")


_LEADING_LETTERS = re.compile(r"([A-Za-z]+)")


def _bfo_name(tradingsymbol: str) -> str:
    match = _LEADING_LETTERS.match(tradingsymbol)
    return match.group(1) if match else tradingsymbol


def _bfo_instrumenttype(tradingsymbol: str) -> str:
    for suffix in ("FUT", "CE", "PE"):
        if tradingsymbol.endswith(suffix):
            return suffix
    return "UNKNOWN"


def _bfo_strike_text(strike: int | float) -> str:
    if isinstance(strike, int):
        return str(strike)
    return f"{strike:.2f}".rstrip("0").rstrip(".")


def parse_bfo(lines: Iterable[str]) -> list[Row]:
    """BFO names its strike column "Strike" and has a trailing comma in every line.

    name and instrumenttype come from the trading symbol, as in OpenAlgo.
    """
    reader = _reader(lines)
    fields = reader.fieldnames or []
    strike_field = "Strike" if "Strike" in fields else "StrikePrice"
    rows: list[Row] = []
    for rec in reader:
        brsymbol = _clean(rec.get("TradingSymbol"))
        if not _clean(rec.get("Token")) or not brsymbol:
            continue
        name = _bfo_name(brsymbol)
        instrumenttype = _bfo_instrumenttype(brsymbol)
        row = _derivative_row(rec, "BFO", instrumenttype, strike_field=strike_field, name=name)
        if instrumenttype != "FUT":
            compact_expiry = row["expiry"].replace("-", "") if row["expiry"] else ""
            row["symbol"] = f"{name}{compact_expiry}{_bfo_strike_text(row['strike'])}{instrumenttype}"
        rows.append(row)
    return rows


PARSERS: dict[str, Callable[[Iterable[str]], list[Row]]] = {
    "NSE": parse_nse,
    "BSE": parse_bse,
    "NFO": parse_nfo,
    "CDS": parse_cds,
    "MCX": parse_mcx,
    "BFO": parse_bfo,
}


def symbols_url(exchange: str) -> str:
    return f"{TRADESMART_HOST}/{exchange}_symbols.txt.zip"


# ---------------------------------------------------------------------------
# Download and store
# ---------------------------------------------------------------------------

_download_lock = threading.Lock()


def is_download_running() -> bool:
    return _download_lock.locked()


def _set_status(**values: Any) -> None:
    with SessionLocal.begin() as db:
        db.execute(update(MasterContractStatus).where(MasterContractStatus.id == 1).values(**values))


def _download_zip(client: httpx.Client, url: str, dest: Path) -> None:
    with client.stream("GET", url) as response:
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}")
        with dest.open("wb") as fh:
            for chunk in response.iter_bytes(chunk_size=256 * 1024):
                fh.write(chunk)


def _extract_symbols_file(zip_path: Path, exchange: str, out_dir: Path) -> Path:
    expected = f"{exchange}_symbols.txt"
    with zipfile.ZipFile(zip_path) as zf:
        members = [m for m in zf.infolist() if not m.is_dir()]
        member = next((m for m in members if Path(m.filename).name.lower() == expected.lower()), None)
        if member is None and len(members) == 1:
            member = members[0]
        if member is None:
            raise RuntimeError(f"{expected} not found in zip")
        if member.file_size > MAX_EXTRACTED_BYTES:
            raise RuntimeError(f"{expected} is unexpectedly large")
        # Write to a fixed name rather than trusting paths inside the archive.
        target = out_dir / expected
        with zf.open(member) as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst)
    return target


def _fetch_and_parse(client: httpx.Client, exchange: str, work_dir: Path) -> list[Row]:
    zip_path = work_dir / f"{exchange}.zip"
    _set_status(message=f"Downloading {exchange}")
    _download_zip(client, symbols_url(exchange), zip_path)
    txt_path = _extract_symbols_file(zip_path, exchange, work_dir)
    _set_status(message=f"Processing {exchange}")
    with txt_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as fh:
        return PARSERS[exchange](fh)


def _replace_symtoken(rows: list[Row]) -> None:
    """Swap the whole table in one transaction so readers never see a partial table."""
    with SessionLocal.begin() as db:
        db.execute(delete(SymToken))
        for chunk in batched(rows, INSERT_BATCH):
            db.execute(insert(SymToken), list(chunk))


def _run_download() -> dict[str, Any]:
    ensure_data_dirs()
    started = time.perf_counter()
    _set_status(status="downloading", message="Starting download", started_at=utcnow(), finished_at=None)
    work_dir = Path(tempfile.mkdtemp(prefix="master_", dir=TMP_DIR))
    all_rows: list[Row] = []
    failures: dict[str, str] = {}
    try:
        timeout = httpx.Timeout(DOWNLOAD_READ_TIMEOUT, connect=DOWNLOAD_CONNECT_TIMEOUT)
        with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=timeout, follow_redirects=True) as client:
            for exchange in EXCHANGES:
                try:
                    rows = _fetch_and_parse(client, exchange, work_dir)
                    logger.info("Parsed %d rows for %s", len(rows), exchange)
                    all_rows.extend(rows)
                except Exception as exc:
                    failures[exchange] = str(exc) or exc.__class__.__name__
                    logger.exception("Master contract for %s failed, skipping", exchange)

        # Same (token, exchange) can only appear once; keep the first occurrence.
        seen: set[tuple[str, str]] = set()
        unique_rows: list[Row] = []
        for row in all_rows:
            key = (row["token"], row["exchange"])
            if key not in seen:
                seen.add(key)
                unique_rows.append(row)

        if not unique_rows:
            detail = "; ".join(f"{k}: {v}" for k, v in failures.items()) or "no data"
            message = f"No exchange could be loaded ({detail})"
            _set_status(status="error", message=message[:500], finished_at=utcnow())
            logger.error(message)
            return {"status": "error", "message": message, "total_records": 0, "exchanges": {}}

        _set_status(message=f"Saving {len(unique_rows)} records")
        _replace_symtoken(unique_rows)

        counts = dict(Counter(row["exchange"] for row in unique_rows))
        elapsed = time.perf_counter() - started
        message = f"Loaded {len(unique_rows)} records in {elapsed:.1f} s"
        if failures:
            message += f" (failed: {', '.join(failures)})"
        _set_status(
            status="success",
            message=message,
            total_records=len(unique_rows),
            exchanges_json=json.dumps(counts),
            finished_at=utcnow(),
        )
        logger.info("Master contract: %s, per exchange %s", message, counts)
        return {
            "status": "success",
            "message": message,
            "total_records": len(unique_rows),
            "exchanges": counts,
            "elapsed_seconds": round(elapsed, 2),
        }
    except Exception as exc:
        logger.exception("Master contract download failed")
        _set_status(status="error", message=f"Download failed: {exc}"[:500], finished_at=utcnow())
        return {"status": "error", "message": str(exc), "total_records": 0, "exchanges": {}}
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _worker() -> None:
    try:
        _run_download()
    finally:
        _download_lock.release()


def start_download_in_background() -> bool:
    """Start a download thread. Returns False if one is already running."""
    if not _download_lock.acquire(blocking=False):
        return False
    try:
        # Mark as downloading before returning so an immediate status poll sees it.
        _set_status(status="downloading", message="Queued", started_at=utcnow(), finished_at=None)
        threading.Thread(target=_worker, name="master-contract-download", daemon=True).start()
    except Exception:
        _download_lock.release()
        raise
    return True


def download_blocking() -> dict[str, Any]:
    """Run a download in the calling thread (scripts and manual runs)."""
    if not _download_lock.acquire(blocking=False):
        raise RuntimeError("A master contract download is already running")
    try:
        return _run_download()
    finally:
        _download_lock.release()
