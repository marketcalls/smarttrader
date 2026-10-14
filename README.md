# SmartTrader

A simple trading application for TradeSmart Online (Noren v2 API).

- Log in with your TradeSmart API key, API secret and redirect URL (stored in SQLite,
  secret encrypted, no `.env` files).
- Login goes through the TradeSmart OAuth page and returns to the app.
- The master contract (about 1.7 lakh symbols across NSE, BSE, NFO, BFO, CDS, MCX)
  downloads automatically after login.
- The dashboard shows funds (available cash, collateral, realized and unrealized M2M,
  margin used), master contract status and a symbol search.

Stack: FastAPI 0.143 + SQLite (Python 3.14, uv) and React 19.3 + TypeScript 7 + Vite 8 +
Tailwind 4. See [docs/README.md](docs/README.md) for the implementation documentation,
API contracts, requirements and known gaps. `SPEC.md` is the original specification;
where it differs, the code and implementation docs describe current behavior.

## Redirect URL

Register this redirect URL with TradeSmart and enter the same value on the login page:

```
http://127.0.0.1:8000/tradesmart/callback
```

Use `127.0.0.1` everywhere (not `localhost`) so the session cookie is shared.

## Run

Requirements for the supplied app: Python 3.14 with uv. Node 24 is needed for
frontend development only after the missing source has been recovered.

This checkout has no frontend source files. Preserve `frontend/dist`, which is the
only supplied UI. Do not delete it or run a frontend build. The Vite development
server cannot provide a working app without `frontend/src/main.tsx` and its imports.

Single port (recommended):

```
cd backend
uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Open http://127.0.0.1:8000

Use `python -m uvicorn`: the copied virtual environment's `uvicorn.exe` launcher
points at an old project location. Keep one worker because downloads, sign-in flows
and agent cancellation use process-local state.

Backend development with the existing UI:

```
cd backend
uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload --reload-dir app
```

Continue to open http://127.0.0.1:8000. The intended two-port Vite workflow is
documented in [docs/design/README.md](docs/design/README.md), but requires restored
frontend source. The OAuth callback prefers the bundled UI whenever
`frontend/dist/index.html` exists.

## Tests

```
cd backend
uv run python -m pytest
```

## Data

Runtime data lives in `backend/data/` (created automatically):

- `smarttrader.db`: SQLite database for broker config, sessions, symbols, download
  status, agent settings, encrypted ChatGPT tokens, chat transcripts and order audit.
- `agent.db`: Agno conversation context and runs, created when agent persistence is used.
- `secret.key`: encryption key for broker secrets, access tokens and ChatGPT tokens.
  Keep it with a private backup of the databases; losing it requires re-entering the
  broker secret and signing in again to both services. Transcripts and audit data
  are not encrypted. See [storage details](docs/schema/files-and-formats.md).
