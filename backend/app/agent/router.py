"""Agent API. 앱은 어떤 LLM 이 돌고 있는지 몰라도 이 API 만 호출하면 된다.

흐름: 세션 생성(plan_id) -> chat (SSE 스트림) -> proposal 이벤트로 제안 수신 -> accept / reject
"""
import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.api import ApiError, ServiceApi, get_http
from app.agent.harness import add_note, pending_proposal, run_turn
from app.agent.llm import LLMProvider, get_llm
from app.agent.models import AgentMessage, AgentSession
from app.agent.schemas import (
    AgentInfo,
    ChatRequest,
    ChatResult,
    MessageView,
    SessionCreate,
    SessionDetail,
    SessionView,
    ToolCallLog,
)
from app.db import SessionLocal, get_db

router = APIRouter(prefix="/agent", tags=["agent"])

DbSession = Annotated[Session, Depends(get_db)]
UserId = Annotated[str, Header(alias="X-User-Id", min_length=1, max_length=64)]
Http = Annotated[httpx.AsyncClient, Depends(get_http)]
Llm = Annotated[LLMProvider, Depends(get_llm)]

_locks: dict[str, asyncio.Lock] = {}  # 세션당 동시에 한 턴만


def _get_session(db: Session, user_id: str, session_id: str) -> AgentSession:
    s = db.get(AgentSession, session_id)
    if s is None or s.user_id != user_id:
        raise HTTPException(404, f"세션 {session_id} 을 찾을 수 없습니다.")
    return s


def _api_error(e: ApiError) -> HTTPException:
    return HTTPException(e.status, e.detail)


@router.get("/info", response_model=AgentInfo)
def info(llm: Llm) -> AgentInfo:
    return AgentInfo(provider=llm.name)


@router.post("/sessions", response_model=SessionView, status_code=201)
async def create_session(body: SessionCreate, db: DbSession, user_id: UserId, http: Http) -> SessionView:
    try:
        await ServiceApi(http, user_id).get_plan(body.plan_id)  # 존재·소유 확인
    except ApiError as e:
        raise _api_error(e) from e
    s = AgentSession(id=uuid.uuid4().hex, user_id=user_id, plan_id=body.plan_id)
    db.add(s)
    db.commit()
    return SessionView.model_validate(s)


@router.get("/sessions", response_model=list[SessionView])
def list_sessions(db: DbSession, user_id: UserId, plan_id: int | None = None) -> list[SessionView]:
    q = select(AgentSession).where(AgentSession.user_id == user_id)
    if plan_id is not None:
        q = q.where(AgentSession.plan_id == plan_id)
    return [SessionView.model_validate(s) for s in db.scalars(q.order_by(AgentSession.updated_at.desc()))]


@router.get("/sessions/{session_id}", response_model=SessionDetail)
async def get_session(session_id: str, db: DbSession, user_id: UserId, http: Http) -> SessionDetail:
    s = _get_session(db, user_id, session_id)
    rows = db.scalars(select(AgentMessage).where(AgentMessage.session_id == s.id, AgentMessage.hidden.is_(False),
                                                 AgentMessage.role.in_(("user", "assistant")))
                      .order_by(AgentMessage.id))
    messages: list[MessageView] = []
    for m in rows:
        if not m.content.strip():
            continue
        prev = messages[-1] if messages else None
        if prev and prev.role == m.role == "assistant":  # 한 턴의 답변(도구 호출 전·후)은 스트림처럼 한 말풍선으로
            prev.content += "\n\n" + m.content
            prev.proposal_id = m.proposal_id or prev.proposal_id
        else:
            messages.append(MessageView(id=m.id, role=m.role, content=m.content, proposal_id=m.proposal_id,
                                        created_at=m.created_at))
    try:
        pending = await pending_proposal(ServiceApi(http, user_id), s.plan_id, s.id)
    except ApiError as e:
        raise _api_error(e) from e
    return SessionDetail(**SessionView.model_validate(s).model_dump(), messages=messages, pending_proposal=pending)


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: str, db: DbSession, user_id: UserId) -> Response:
    db.delete(_get_session(db, user_id, session_id))
    db.commit()
    return Response(status_code=204)


def _sse(ev: dict) -> str:
    return f"event: {ev['event']}\ndata: {json.dumps(ev['data'], ensure_ascii=False, default=str)}\n\n"


@router.post("/sessions/{session_id}/chat", response_model=ChatResult,
             responses={200: {"content": {"text/event-stream": {}}, "description": "stream=true 면 SSE 이벤트 스트림"}})
async def chat(session_id: str, body: ChatRequest, db: DbSession, user_id: UserId, http: Http, llm: Llm,
               stream: Annotated[bool, Query(description="true: SSE 실시간 스트림, false: 끝난 뒤 JSON 한 번")] = True):
    _get_session(db, user_id, session_id)  # 존재 확인은 스트림 시작 전에 (404 를 바로 돌려주기 위해)
    lock = _locks.setdefault(session_id, asyncio.Lock())
    if lock.locked():
        raise HTTPException(409, "이 세션은 이전 메시지에 응답하는 중입니다.")

    async def events() -> AsyncIterator[dict]:
        async with lock:
            with SessionLocal() as tdb:  # 스트림은 요청 의존성보다 오래 살아서 세션을 따로 연다
                s = tdb.get(AgentSession, session_id)
                try:
                    async for ev in run_turn(tdb, s, body.message, llm, ServiceApi(http, user_id)):
                        yield ev
                except ApiError as e:
                    yield {"event": "error", "data": {"message": e.message}}

    if stream:
        async def sse() -> AsyncIterator[str]:
            async for ev in events():
                yield _sse(ev)

        return StreamingResponse(sse(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    text, calls, proposal, error = "", {}, None, None
    async for ev in events():
        name, data = ev["event"], ev["data"]
        if name == "done":
            text = data["text"]
        elif name == "tool_call":
            calls[data["id"]] = data
        elif name == "tool_result":
            calls[data["id"]] |= {"ok": data["ok"], "summary": data["summary"]}
        elif name == "proposal":
            proposal = data
        elif name == "error":
            error = data["message"]
    return ChatResult(text=text, tool_calls=[ToolCallLog(**c) for c in calls.values() if "ok" in c],
                      proposal=proposal, error=error)


@router.post("/sessions/{session_id}/proposals/{proposal_id}/accept")
async def accept(session_id: str, proposal_id: int, db: DbSession, user_id: UserId, http: Http) -> dict:
    """제안 승인 -> 실제 일정에 반영. 응답은 planner AcceptResult {proposal, plan}."""
    s = _get_session(db, user_id, session_id)
    try:
        result = await ServiceApi(http, user_id).accept_proposal(s.plan_id, proposal_id)
    except ApiError as e:
        raise _api_error(e) from e
    add_note(db, s, f"사용자가 제안 #{proposal_id} 을 승인해 일정에 반영됐습니다.")
    return result


@router.post("/sessions/{session_id}/proposals/{proposal_id}/reject")
async def reject(session_id: str, proposal_id: int, db: DbSession, user_id: UserId, http: Http) -> dict:
    s = _get_session(db, user_id, session_id)
    try:
        result = await ServiceApi(http, user_id).reject_proposal(s.plan_id, proposal_id)
    except ApiError as e:
        raise _api_error(e) from e
    add_note(db, s, f"사용자가 제안 #{proposal_id} 을 거절했습니다. 일정은 그대로입니다.")
    return result
