# SmartTrader

Trading app for TradeSmart Online (Noren v2 API). FastAPI + SQLite backend in
`backend/app`, React 19 + TypeScript + Vite frontend in `frontend`. `SPEC.md` is the
original spec; `docs/` describes the system as built.

## Run and test

```
cd backend
uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
uv run pytest
```

Use `python -m uvicorn`, not `uv run uvicorn`: the venv's `uvicorn.exe` launcher points
at an old copy of the project and fails. Open http://127.0.0.1:8000 (not `localhost`).

`frontend/src` has no source files, so `npm run build` fails and the app serves the
existing `frontend/dist` bundle. Do not delete `frontend/dist`.

## Rule: keep the docs in sync with the code

Every change that adds, changes or removes behaviour must update the affected docs in
the same task. A change is not done until the docs match the code. This covers new
features, bug fixes that change behaviour, new or removed endpoints, tables, columns,
agent tools, risk checks, config options and UI pages.

Pure refactors with no visible change (renames inside a function, formatting, test-only
changes) need no doc update, but say so in your summary.

### Where each change goes

| Code changed | Update |
|---|---|
| `backend/app/routers/*.py` (route added, removed, path, params, body, response, status codes) | `docs/api/<router>.md` and the endpoint index in `docs/api/README.md` |
| `backend/app/broker/*.py` (calls to TradeSmart, payloads, websocket protocol) | `docs/api/broker-integration.md`, `docs/design/flows.md` |
| `backend/app/models.py`, `backend/app/db.py`, any `CREATE TABLE` or column change, `_ADDED_COLUMNS` | `docs/schema/core-tables.md` or `docs/schema/agent-tables.md`, the ER diagram in `docs/schema/README.md`, `docs/schema/code-models.md` |
| Files on disk, `secret.key`, encryption (`crypto.py`), master contract format | `docs/schema/files-and-formats.md`, `docs/design/security.md` |
| `backend/app/agent/*.py`, `routers/agent.py` (tools, guardrails, risk limits, model, SSE frames) | `docs/design/agent.md`, `docs/api/agent.md`, `docs/bdd/agent.feature` |
| Module structure, startup, background tasks, config (`config.py`, `main.py`, `deps.py`) | `docs/design/README.md`, `docs/design/backend.md` |
| Auth, cookies, CORS, origin checks, secrets handling | `docs/design/security.md` |
| Frontend pages, routes, components, API calls | `docs/design/frontend.md` |
| Any new or changed user-facing behaviour | `docs/prd/README.md` (user story, acceptance criteria, release status table) and the matching `docs/bdd/*.feature` |
| A risk or bug fixed, or a new one found | `docs/design/risks.md`, `docs/schema/risks-and-discrepancies.md` when it is about data |
| Behaviour that now differs from `SPEC.md` | The discrepancy list in the relevant doc |

### How to update

1. Before editing code, read the docs for the area you are touching so you know what
   they currently promise.
2. After the code change and tests pass, update every file the table points to. Edit the
   existing sections; do not append a changelog at the bottom.
3. PRD: add or edit the user story under its area with the next free ID (prefixes AUTH,
   MC, DASH, CHART, ORD, AGENT, UX, NFR). Never reuse or renumber an existing ID. Update
   the release status table (Implemented, Partial, Not implemented).
4. BDD: add or edit scenarios in the matching `.feature` file, tag them `@req-<ID>` with
   the PRD ID, `@smoke` for the happy path and `@negative` for failure cases, and update
   the traceability table in `docs/bdd/README.md` with the code file and the pytest test
   that covers it (or "not covered").
5. API: give a realistic request and response example and list every error status code.
6. Schema: list type, nullability, default, indexes, and which code reads and writes the
   column.
7. Diagrams are Mermaid. Update the sequence or ER diagram when a flow or table changes.
8. In your final summary, list the doc files you updated, or state why none were needed.

### Doc conventions

- The code is the source of truth. If code and docs disagree, fix the docs (or ask if
  the code looks wrong).
- Never copy real API keys, secrets, tokens, user ids or account data from
  `backend/data` into docs. Use placeholders such as `<API_KEY>` and `<USER_ID>`.
- Plain Markdown and Gherkin. No emoji or icons. No em dashes or en dashes; use commas,
  colons, parentheses or full stops. Check with a search for U+2014 and U+2013 before
  finishing.
