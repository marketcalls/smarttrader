import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.agent import chatgpt_auth, finedge, trading
from app.agent.builder import (
    DEFAULT_MODEL,
    REASONING_EFFORTS,
    agent_db,
    available_models,
    build_agent,
)
from app.agent.research import RESEARCH_TOOL_DESCRIPTIONS, build_research_tools
from app.agent.tools import TOOL_DESCRIPTIONS, build_tools
from app.config import to_ist_iso, utcnow
from app.crypto import decrypt
from app.db import SessionLocal
from app.deps import get_db, require_session
from app.models import AgentConversation, AgentMessage, AgentOrderAudit, AgentSetting, AuthSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent", dependencies=[Depends(require_session)])

MAX_MESSAGE_CHARS = 16_000
TOOL_RESULT_PREVIEW = 4_000
_running: dict[str, Any] = {}


# ---------- settings ----------


class SettingsBody(BaseModel):
    model: str = Field(min_length=1, max_length=80)
    reasoning_effort: str | None = None
    instructions: str | None = Field(default=None, max_length=4_000)


def _settings(db: Session, *, commit: bool = True) -> AgentSetting:
    """The settings row, created with defaults on first use. Inside a caller's transaction pass commit=False."""
    row = db.get(AgentSetting, 1)
    if row is None:
        models = available_models()
        row = AgentSetting(id=1, model=DEFAULT_MODEL if DEFAULT_MODEL in models else (models[0] if models else DEFAULT_MODEL),
                           reasoning_effort="medium", instructions=None, updated_at=utcnow())
        db.add(row)
        if commit:
            db.commit()
        else:
            db.flush()
    return row


def _settings_dict(row: AgentSetting) -> dict[str, Any]:
    return {"model": row.model, "reasoning_effort": row.reasoning_effort, "instructions": row.instructions or ""}


@router.get("/status")
def status(db: Session = Depends(get_db)) -> dict:
    account = chatgpt_auth.account_status()
    return {**account, "provider": "chatgpt", **_settings_dict(_settings(db)),
            "trading_enabled": trading.risk_settings()["trading_enabled"]}


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)) -> dict:
    return {
        **_settings_dict(_settings(db)),
        "models": available_models(),
        "reasoning_efforts": list(REASONING_EFFORTS),
        "tools": [{"name": n, "description": d} for n, d in TOOL_DESCRIPTIONS],
        "research_tools": [{"name": n, "description": d} for n, d in RESEARCH_TOOL_DESCRIPTIONS],
        "finedge": finedge.key_status(),
        "order_tools": [
            {"name": "place_order", "description": "Place an order after your approval"},
            {"name": "modify_order", "description": "Modify an open order after your approval"},
            {"name": "cancel_order", "description": "Cancel an open order after your approval"},
        ],
        "trading_enabled": trading.risk_settings()["trading_enabled"],
    }


@router.put("/settings")
def put_settings(body: SettingsBody, db: Session = Depends(get_db)) -> dict:
    if body.model not in available_models():
        raise HTTPException(status_code=422, detail="Unknown model")
    if body.reasoning_effort not in (None, "", *REASONING_EFFORTS):
        raise HTTPException(status_code=422, detail="Reasoning effort must be low, medium or high")
    row = _settings(db)
    row.model = body.model
    row.reasoning_effort = body.reasoning_effort or None
    row.instructions = (body.instructions or "").strip() or None
    row.updated_at = utcnow()
    db.commit()
    return _settings_dict(row)


# ---------- FinEdge API key ----------
# A rejected FinEdge key is answered with 422, never 401: the frontend treats any 401
# as an expired SmartTrader session and signs the user out.


class FinEdgeKeyBody(BaseModel):
    api_key: str = Field(min_length=8, max_length=256, pattern=r"^\S+$")


def _finedge_failure(exc: finedge.FinEdgeError) -> HTTPException:
    if exc.invalid_key:
        return HTTPException(status_code=422, detail="FinEdge rejected the API key. Check it and try again.")
    if exc.status_code == 0:
        return HTTPException(status_code=502, detail="FinEdge is unreachable. Try again in a minute.")
    return HTTPException(status_code=502, detail=f"FinEdge error ({exc.status_code}): {exc.message}")


@router.get("/finedge")
def finedge_status() -> dict:
    return finedge.key_status()


@router.put("/finedge")
def finedge_save(body: FinEdgeKeyBody) -> dict:
    finedge.write_key(body.api_key)
    finedge.clear_cache()
    logger.info("FinEdge API key saved")
    return finedge.key_status()


@router.delete("/finedge")
def finedge_remove() -> dict:
    finedge.clear_key()
    finedge.clear_cache()
    logger.info("FinEdge API key removed")
    return finedge.key_status()


@router.post("/finedge/test")
async def finedge_test() -> dict:
    """Two small profile calls with the saved key: one any plan can read and one that needs a paid plan."""
    key = finedge.read_key()
    if not key:
        raise HTTPException(status_code=409, detail="Save a FinEdge API key first.")
    try:
        return await run_in_threadpool(finedge.test_key, key)
    except finedge.FinEdgeError as exc:
        raise _finedge_failure(exc)


# ---------- ChatGPT sign-in ----------


def _flow_dict(flow: chatgpt_auth.LoginFlow | None) -> dict:
    if flow is None:
        return {"state": "none"}
    return {
        "state": flow.state,
        "user_code": flow.user_code if flow.state == "pending" else None,
        "verification_url": flow.verification_url,
        "expires_in": max(0, int(flow.expires_at - time.time())),
        "error": flow.error,
    }


@router.post("/chatgpt/login")
async def chatgpt_login_start() -> dict:
    try:
        flow = await run_in_threadpool(chatgpt_auth.start_login)
    except Exception as exc:  # noqa: BLE001 - surfaced to the settings page
        logger.warning("ChatGPT device code request failed: %s", exc.__class__.__name__)
        raise HTTPException(status_code=502, detail="Could not start ChatGPT sign-in. Try again in a minute.")
    return _flow_dict(flow)


@router.get("/chatgpt/login")
def chatgpt_login_status() -> dict:
    return _flow_dict(chatgpt_auth.current_flow())


@router.delete("/chatgpt/login")
def chatgpt_login_cancel() -> dict:
    chatgpt_auth.cancel_login()
    return {"state": "none"}


@router.post("/chatgpt/logout")
def chatgpt_logout() -> dict:
    chatgpt_auth.cancel_login()
    chatgpt_auth.clear_tokens()
    return {"signed_in": False}


@router.post("/test")
async def test_model(db: Session = Depends(get_db)) -> dict:
    """One small completion through the configured model, without tools or history."""
    if not chatgpt_auth.account_status()["signed_in"]:
        raise HTTPException(status_code=409, detail="Sign in with ChatGPT first.")
    settings = _settings(db)
    agent = build_agent(model=settings.model, reasoning_effort="low", instructions=None, tools=[],
                        session_id=f"test-{uuid.uuid4().hex}")
    agent.db = None
    agent.add_history_to_context = False
    started = time.perf_counter()
    try:
        result = await agent.arun("Reply with exactly: OK")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=_provider_message(exc))
    if getattr(result, "status", None) is not None and str(result.status).lower().endswith("error"):
        raise HTTPException(status_code=502, detail=_provider_message(result.content))
    return {"ok": True, "model": settings.model, "reply": str(result.content or "")[:200],
            "latency_ms": int((time.perf_counter() - started) * 1000)}


# ---------- conversations ----------


def _conversation_dict(c: AgentConversation) -> dict:
    return {"id": c.id, "title": c.title, "model": c.model, "created_at": to_ist_iso(c.created_at),
            "updated_at": to_ist_iso(c.updated_at)}


def _message_dict(m: AgentMessage) -> dict:
    return {
        "id": m.id, "role": m.role, "content": m.content,
        "tools": json.loads(m.tools_json) if m.tools_json else [],
        "usage": json.loads(m.usage_json) if m.usage_json else None,
        "pending": json.loads(m.pending_json) if m.pending_json else None,
        "created_at": to_ist_iso(m.created_at),
    }


@router.get("/conversations")
def list_conversations(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(AgentConversation).order_by(AgentConversation.updated_at.desc()).limit(200))
    return [_conversation_dict(c) for c in rows]


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str, db: Session = Depends(get_db)) -> dict:
    convo = db.get(AgentConversation, conversation_id)
    if convo is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = db.scalars(
        select(AgentMessage).where(AgentMessage.conversation_id == conversation_id).order_by(AgentMessage.id)
    )
    return {**_conversation_dict(convo), "messages": [_message_dict(m) for m in messages]}


class RenameBody(BaseModel):
    title: str = Field(min_length=1, max_length=120)


@router.patch("/conversations/{conversation_id}")
def rename_conversation(conversation_id: str, body: RenameBody, db: Session = Depends(get_db)) -> dict:
    convo = db.get(AgentConversation, conversation_id)
    if convo is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    convo.title = body.title.strip()
    db.commit()
    return _conversation_dict(convo)


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, db: Session = Depends(get_db)) -> dict:
    db.execute(delete(AgentMessage).where(AgentMessage.conversation_id == conversation_id))
    convo = db.get(AgentConversation, conversation_id)
    if convo is not None:
        db.delete(convo)
    db.commit()
    try:
        agent_db().delete_session(conversation_id)
    except Exception:  # noqa: BLE001 - the transcript is gone either way
        logger.info("No Agno session to delete for %s", conversation_id)
    return {"status": "ok"}


# ---------- chat ----------


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    conversation_id: str | None = None


class ConfirmBody(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=64)
    run_id: str = Field(min_length=1, max_length=64)
    decisions: dict[str, bool]


def _frame(kind: str, **data: Any) -> str:
    return f"data: {json.dumps({'type': kind, **data}, default=str)}\n\n"


def _provider_message(exc: Any) -> str:
    text = str(getattr(exc, "message", None) or exc or "")
    lowered = text.lower()
    if "not signed in to chatgpt" in lowered or "refresh token" in lowered:
        return "ChatGPT sign-in is missing or expired. Open Agent settings and sign in again."
    if "rate limit" in lowered or "429" in lowered or "usage limit" in lowered:
        return "ChatGPT usage limit reached for this plan. Try again later or pick another model."
    if "model" in lowered and ("not found" in lowered or "not supported" in lowered or "does not exist" in lowered):
        return "This model is not available on your ChatGPT plan. Pick another model in Agent settings."
    return text[:400] or "The model request failed."


def _preview(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= TOOL_RESULT_PREVIEW else text[:TOOL_RESULT_PREVIEW] + " ..."


def _credentials(session: AuthSession) -> tuple[str, str]:
    if not chatgpt_auth.account_status()["signed_in"]:
        raise HTTPException(status_code=409, detail="Sign in with ChatGPT in Agent settings first.")
    access_token = decrypt(session.access_token_enc)
    if not access_token or not session.user_id:
        raise HTTPException(status_code=401, detail="Session expired, please log in again")
    return access_token, session.user_id


def _agent_for(conversation_id: str, access_token: str, user_id: str, *, include_orders: bool) -> tuple[Any, str, dict]:
    """Build the agent for one conversation. Order tools are included when trading is on (or a paused order is resumed)."""
    with SessionLocal.begin() as db:
        settings = _settings(db, commit=False)
        model, effort, extra = settings.model, settings.reasoning_effort, settings.instructions
    risk = trading.risk_settings()
    context: dict[str, Any] = {"conversation_id": conversation_id, "run_id": None}
    tools: list[Any] = list(build_tools(access_token, user_id))
    finedge_key = finedge.read_key()
    if finedge_key:
        tools += build_research_tools(finedge_key)
    if risk["trading_enabled"] or include_orders:
        tools += trading.build_order_tools(access_token, user_id, context)
    agent = build_agent(model=model, reasoning_effort=effort, instructions=extra, tools=tools,
                        session_id=conversation_id, user_id=user_id, trading=risk["trading_enabled"],
                        research=bool(finedge_key))
    return agent, model, context


async def _stream_run(
    *, agent: Any, events_source: Any, conversation_id: str, model: str, request: Request,
    context: dict[str, Any], first: str | None,
) -> AsyncIterator[str]:
    """Translate Agno run events into frames, then save the assistant turn."""
    text: list[str] = []
    tools: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    usage: dict[str, Any] | None = None
    paused: dict[str, Any] | None = None
    run_id: str | None = context.get("run_id")
    reason = "stop"
    started = time.perf_counter()
    first_token_ms: int | None = None
    if first:
        yield first
    try:
        async for ev in events_source:
            if await request.is_disconnected():
                reason = "cancelled"
                if run_id:
                    await agent.acancel_run(run_id)
                break
            kind = getattr(ev, "event", "")
            if kind in ("RunStarted", "RunContinued"):
                run_id = ev.run_id or run_id
                context["run_id"] = run_id
                if run_id:
                    _running[run_id] = agent
                yield _frame("run", run_id=run_id)
            elif kind == "RunContent":
                if isinstance(ev.content, str) and ev.content:
                    if first_token_ms is None:
                        first_token_ms = int((time.perf_counter() - started) * 1000)
                    text.append(ev.content)
                    yield _frame("token", delta=ev.content)
                if getattr(ev, "reasoning_content", None):
                    yield _frame("reasoning", delta=ev.reasoning_content)
            elif kind == "ReasoningContentDelta" and ev.reasoning_content:
                yield _frame("reasoning", delta=ev.reasoning_content)
            elif kind == "ToolCallStarted" and ev.tool:
                t = ev.tool
                entry = {"id": t.tool_call_id or uuid.uuid4().hex, "name": t.tool_name, "args": t.tool_args or {},
                         "ok": None, "result": None, "duration": None}
                tools.append(entry)
                by_id[entry["id"]] = entry
                yield _frame("tool_start", id=entry["id"], name=entry["name"], args=entry["args"])
            elif kind in ("ToolCallCompleted", "ToolCallError") and ev.tool:
                t = ev.tool
                entry = by_id.get(t.tool_call_id or "") or {"id": t.tool_call_id or uuid.uuid4().hex, "name": t.tool_name, "args": t.tool_args or {}}
                if entry["id"] not in by_id:
                    tools.append(entry)
                    by_id[entry["id"]] = entry
                result = t.result if kind == "ToolCallCompleted" else (getattr(ev, "error", None) or t.result)
                result_text = str(result or "")
                entry["ok"] = (kind == "ToolCallCompleted" and not t.tool_call_error
                               and not result_text.startswith(("Error", "Refused", "Order not placed", "Not ")))
                entry["result"] = _preview(result if result is not None else "")
                duration = getattr(t.metrics, "duration", None) if t.metrics else None
                entry["duration"] = round(duration, 2) if isinstance(duration, (int, float)) else None
                yield _frame("tool_end", **entry)
            elif kind == "RunPaused":
                run_id = ev.run_id or run_id
                requirements = []
                for req in ev.requirements or []:
                    te = req.tool_execution
                    if te is None or not req.needs_confirmation:
                        continue
                    args = te.tool_args or {}
                    requirements.append({"id": te.tool_call_id, "tool_name": te.tool_name, "args": args,
                                         "summary": trading.order_summary(te.tool_name, args)})
                    trading.audit(conversation_id, run_id, te.tool_name, args, "proposed")
                paused = {"run_id": run_id, "requirements": requirements, "state": "pending"}
                reason = "paused"
                yield _frame("confirm", **paused)
            elif kind == "RunCompleted":
                m = ev.metrics
                if m is not None:
                    usage = {"input_tokens": m.input_tokens, "output_tokens": m.output_tokens,
                             "total_tokens": m.total_tokens, "reasoning_tokens": m.reasoning_tokens,
                             "duration": round(m.duration, 2) if m.duration else None,
                             "ttft_ms": first_token_ms, "model": model}
                    yield _frame("usage", **usage)
                if not text and isinstance(ev.content, str) and ev.content:
                    text.append(ev.content)
                    yield _frame("token", delta=ev.content)
            elif kind == "RunError":
                reason = "error"
                yield _frame("error", message=_provider_message(ev.content))
            elif kind == "RunCancelled":
                reason = "cancelled"
    except asyncio.CancelledError:
        reason = "cancelled"
        raise
    except Exception as exc:  # noqa: BLE001 - every failure ends the stream with a readable frame
        reason = "error"
        logger.warning("Agent run failed: %s", exc.__class__.__name__)
        yield _frame("error", message=_provider_message(exc))
    finally:
        if run_id:
            _running.pop(run_id, None)
        content = "".join(text)
        if content or tools or paused:
            with SessionLocal.begin() as db:
                db.add(AgentMessage(
                    conversation_id=conversation_id, role="assistant", content=content,
                    tools_json=json.dumps(tools, default=str) if tools else None,
                    usage_json=json.dumps(usage) if usage else None,
                    pending_json=json.dumps(paused) if paused else None, created_at=utcnow(),
                ))
                convo = db.get(AgentConversation, conversation_id)
                if convo is not None:
                    convo.updated_at = utcnow()
    yield _frame("done", reason=reason)


_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@router.post("/chat")
async def chat(body: ChatBody, request: Request, session: AuthSession = Depends(require_session)) -> StreamingResponse:
    access_token, user_id = _credentials(session)
    message = body.message.strip()
    now = utcnow()
    with SessionLocal.begin() as db:
        model = _settings(db, commit=False).model
        convo = db.get(AgentConversation, body.conversation_id) if body.conversation_id else None
        if convo is None:
            title = " ".join(message.split())[:60] or "New chat"
            convo = AgentConversation(id=uuid.uuid4().hex, title=title, model=model, created_at=now, updated_at=now)
            db.add(convo)
        convo.updated_at = now
        convo.model = model
        db.add(AgentMessage(conversation_id=convo.id, role="user", content=message, created_at=now))
        conversation_id = convo.id
        # A new question abandons any approval still waiting in this conversation.
        for row in db.scalars(select(AgentMessage).where(
            AgentMessage.conversation_id == conversation_id, AgentMessage.pending_json.is_not(None)
        )):
            pending = json.loads(row.pending_json)
            if pending.get("state") == "pending":
                row.pending_json = json.dumps({**pending, "state": "expired"})

    agent, model, context = _agent_for(conversation_id, access_token, user_id, include_orders=False)
    source = agent.arun(message, stream=True, stream_events=True, session_id=conversation_id, user_id=user_id)
    return StreamingResponse(
        _stream_run(agent=agent, events_source=source, conversation_id=conversation_id, model=model, request=request,
                    context=context, first=_frame("start", conversation_id=conversation_id, model=model)),
        media_type="text/event-stream", headers=_SSE_HEADERS,
    )


@router.post("/chat/confirm")
async def confirm(body: ConfirmBody, request: Request, session: AuthSession = Depends(require_session)) -> StreamingResponse:
    """Resume a run paused for order approval with the user's decision on each proposed order."""
    access_token, user_id = _credentials(session)
    with SessionLocal.begin() as db:
        row = db.scalar(
            select(AgentMessage)
            .where(AgentMessage.conversation_id == body.conversation_id, AgentMessage.pending_json.is_not(None))
            .order_by(AgentMessage.id.desc())
            .limit(1)
        )
        pending = json.loads(row.pending_json) if row else None
        if not pending or pending.get("run_id") != body.run_id or pending.get("state") != "pending":
            raise HTTPException(status_code=409, detail="This approval is no longer pending.")
        decided = {r["id"]: bool(body.decisions.get(r["id"], False)) for r in pending["requirements"]}
        row.pending_json = json.dumps({**pending, "state": "decided", "decisions": decided})

    agent, model, context = _agent_for(body.conversation_id, access_token, user_id, include_orders=True)
    context["run_id"] = body.run_id
    run = await agent.aget_run_output(body.run_id, session_id=body.conversation_id)
    if run is None or not getattr(run, "is_paused", False):
        raise HTTPException(status_code=409, detail="The paused run was not found. Ask again to propose the order.")
    for req in run.active_requirements:
        te = req.tool_execution
        if te is None or not req.needs_confirmation:
            continue
        if decided.get(te.tool_call_id, False):
            req.confirm()
        else:
            req.reject(note="The user rejected this in the app. Do not retry it; ask what they want instead.")
            trading.audit(body.conversation_id, body.run_id, te.tool_name, te.tool_args or {}, "rejected")

    source = agent.acontinue_run(run_id=body.run_id, requirements=run.requirements, session_id=body.conversation_id,
                                 user_id=user_id, stream=True, stream_events=True)
    return StreamingResponse(
        _stream_run(agent=agent, events_source=source, conversation_id=body.conversation_id, model=model,
                    request=request, context=context, first=_frame("run", run_id=body.run_id)),
        media_type="text/event-stream", headers=_SSE_HEADERS,
    )


class RiskBody(BaseModel):
    trading_enabled: bool
    max_order_value: float = Field(gt=0, le=10_000_000)
    max_quantity: int = Field(gt=0, le=1_000_000)
    max_orders_per_day: int = Field(gt=0, le=1_000)
    allowed_exchanges: list[str]


@router.get("/risk")
def get_risk() -> dict:
    return {**trading.risk_settings(), "exchanges": list(trading.ALL_EXCHANGES)}


@router.put("/risk")
def put_risk(body: RiskBody) -> dict:
    unknown = set(body.allowed_exchanges) - set(trading.ALL_EXCHANGES)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown exchange: {', '.join(sorted(unknown))}")
    saved = trading.save_risk_settings(body.model_dump())
    logger.info("Agent trading %s", "enabled" if saved["trading_enabled"] else "disabled")
    return {**saved, "exchanges": list(trading.ALL_EXCHANGES)}


@router.get("/orders/audit")
def order_audit(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(AgentOrderAudit).order_by(AgentOrderAudit.id.desc()).limit(100))
    return [
        {"id": r.id, "created_at": to_ist_iso(r.created_at), "conversation_id": r.conversation_id, "tool": r.tool,
         "args": json.loads(r.args_json), "status": r.status, "order_id": r.order_id, "message": r.message}
        for r in rows
    ]


@router.post("/chat/{run_id}/cancel")
async def cancel_chat(run_id: str) -> dict:
    agent = _running.get(run_id)
    if agent is not None:
        await agent.acancel_run(run_id)
    return {"cancelled": agent is not None}
