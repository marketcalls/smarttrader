# SmartTrader implementation documentation

These documents describe the supplied implementation. The code is the source of
truth; [SPEC.md](../SPEC.md) records the original specification. Requirements marked
implemented and documented Gherkin scenarios do not by themselves establish
automated coverage or production readiness.

| Start here | Contents |
| --- | --- |
| [Run the supplied app](../README.md) | Working launch command, single-worker requirement and frontend source limitation |
| [Product requirements](prd/README.md) | User stories, acceptance criteria, release status and implementation boundaries |
| [API reference](api/README.md) | Endpoint inventory, request/response examples, authentication, validation and errors |
| [Technical design](design/README.md) | Architecture, process model, configuration and module responsibilities |
| [Security design](design/security.md) | Actual controls, credential/session lifecycle and deployment assumptions |
| [Known risks](design/risks.md) | Open code, reliability and operational gaps with recommendations |
| [Storage/schema](schema/README.md) | Application and Agno tables, indexes, defaults, disk formats and persistence |
| [Schema risks](schema/risks-and-discrepancies.md) | Integrity, concurrency, migration, ownership and recovery limitations |
| [BDD traceability](bdd/README.md) | Scenarios mapped to requirements, source and actual pytest assertions |

## Current operating constraints

- Run on `127.0.0.1` with one worker. Shared settings/history and process-local
  state assume one local installation; they do not provide multi-account isolation.
- Preserve `frontend/dist`. The source folders are empty, so a clean frontend build
  and frontend modifications are blocked until source recovery.
- Treat broker order acceptance separately from fills. Timeouts can leave uncertain
  outcomes; there is no durable idempotency/reconciliation protocol yet.
- Agent approval and risk checks have documented limits. The chart trading path
  does not inherit the agent's risk policy or trading switch.
- Protect both databases and their matching key. Transcripts/account data may be
  stored unencrypted even though credentials are encrypted.
- Fundamental research needs a FinEdge API key entered on the Agent settings page.
  It is stored encrypted in the database, not in `.env`. The settings card is a hand
  patch of the built chunk `AgentConfigPage-C4TKen9_.js` and is lost if the
  frontend is rebuilt from recovered source without porting it.

## Verification and audit scope

The 2026-10-14 documentation audit compared source and tests with the API, design,
schema, PRD and BDD documents. It corrected overstated guarantees and documented
existing gaps; application source was not changed.

After the FinEdge research feature was added, the backend suite passes 94
parametrized cases from 86 test functions in an offline run. The 224 documented
scenarios map to 33 full, 66 partial and 125 uncovered cases of scenario coverage; this is a manual assertion mapping, not code
coverage. Gherkin files are specifications, not an installed executable test suite.

API routes and example JSON/SSE, schema definitions in temporary in-memory stores,
test references and documentation links were checked. No live broker/provider
calls, production-data inspection, browser tests or frontend build were performed.

The separately requested production audit is saved in the sibling `Day07/codex`
folder as `PRODUCTION_READINESS_AUDIT.md`, with itemized scores, evidence, offline
reproductions and proposed fixes. Its recommendations are not implemented features.
