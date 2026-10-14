import logging
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.config import CORS_ORIGINS, FRONTEND_DIST
from app.crypto import get_fernet
from app.db import init_db
from app.routers import agent, auth, chart, funds, master, orders, stream

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# httpx logs full request lines at INFO; keep it quiet.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("smarttrader")


class _RedactAuthCode(logging.Filter):
    """Keep the OAuth authorization code out of the uvicorn access log."""

    _pattern = re.compile(r"(\bcode=)[^&\s]*")

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                self._pattern.sub(r"\1[redacted]", a) if isinstance(a, str) else a for a in record.args
            )
        return True


logging.getLogger("uvicorn.access").addFilter(_RedactAuthCode())


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    get_fernet()
    if FRONTEND_DIST.is_dir():
        logger.info("Serving frontend from %s", FRONTEND_DIST)
    else:
        logger.info("Frontend build not found at %s; API only", FRONTEND_DIST)
    yield


app = FastAPI(title="SmartTrader", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(funds.router)
app.include_router(master.router)
app.include_router(chart.router)
app.include_router(stream.router)
app.include_router(agent.router)
app.include_router(orders.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


# Registered last so every API route wins. Checked per request so a frontend
# build made after the server started is picked up.
@app.get("/{full_path:path}", include_in_schema=False)
def spa_fallback(full_path: str) -> FileResponse:
    if full_path == "api" or full_path.startswith(("api/", "tradesmart/")):
        raise HTTPException(status_code=404, detail="Not Found")
    dist = FRONTEND_DIST.resolve()
    index = dist / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=404, detail="Frontend not built. Run npm run build in frontend/.")
    # The built chunks can be patched in place under the same hashed name, so make
    # the browser revalidate (ETag, cheap 304) instead of reusing a stale copy.
    headers = {"Cache-Control": "no-cache"}
    if full_path:
        candidate = (dist / full_path).resolve()
        if candidate.is_relative_to(dist) and candidate.is_file():
            return FileResponse(candidate, headers=headers)
    return FileResponse(index, headers=headers)
