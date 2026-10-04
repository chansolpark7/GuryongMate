"""Agent API. 앱은 어떤 LLM 이 돌고 있는지 몰라도 이 API 만 호출하면 된다. 명세: docs/agent/README.md

흐름: 세션 생성(plan_id) -> chat (SSE) -> 카드 수신(제안·추천) -> 카드 동작(accept/reject/repropose, add/remove)
오류는 {"detail": {"code", "message", ...}} 형식이고, planner 가 거부한 오류는 코드·추가 필드를 그대로 전달한다.
"""
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from anyio import to_thread
from fastapi import APIRouter, Body, Depends, Header, Query, Response
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.api import ApiError, ServiceApi, get_http
from app.agent.errors import AgentError, AgentRoute
from app.agent.harness import STEP_TITLE, add_note, run_turn
from app.agent.llm import LLMProvider, get_llm
from app.agent.models import AgentMessage, AgentSession
from app.agent.schemas import (
    AcceptRequest,
    AgentInfo,
    ChatRequest,
    ChatResult,
    MessageView,
    SessionCreate,
    SessionDetail,
    SessionView,
    Step,
)
from app.db import get_db

router = APIRouter(prefix="/agent", tags=["agent"], route_class=AgentRoute)

DbSession = Annotated[Session, Depends(get_db)]
UserId = Annotated[str, Header(alias="X-User-Id", min_length=1, max_length=64)]
Http = Annotated[httpx.AsyncClient, Depends(get_http)]
Llm = Annotated[LLMProvider, Depends(get_llm)]

log = logging.getLogger("app.agent")

# 작업 중인 세션 -> 시작 시각. 세션당 한 번에 한 작업(턴 또는 카드 동작)만 처리한다.
# (정리 코드가 못 도는 경우에 대비해 오래된 항목은 끝난 것으로 본다. 프로세스 단위)
_active: dict[str, float] = {}
_STALE_SEC = 15 * 60

REPROPOSE_TEXT = "이전 제안을 현재 일정에 맞춰 다시 만들어줘."


def _busy(session_id: str) -> bool:
    started = _active.get(session_id)
    return started is not None and time.monotonic() - started < _STALE_SEC


def _check_idle(session_id: str) -> None:
    if _busy(session_id):
        raise AgentError(409, "agent_busy", "Agent 가 응답하는 중입니다. 응답이 끝난 뒤 다시 시도하세요.")


def _acquire(session_id: str) -> float:
    """세션 잠금. 확인과 등록 사이에 await 가 없어 이벤트 루프 안에서 원자적이다. 돌려준 토큰으로 푼다."""
    _check_idle(session_id)
    token = time.monotonic()
    _active[session_id] = token
    return token


def _release(session_id: str, token: float) -> None:
    if _active.get(session_id) == token:  # 그사이 오래된 잠금으로 보고 다른 작업이 잡았으면 건드리지 않는다
        _active.pop(session_id, None)


@asynccontextmanager
async def _hold(session_id: str):
    token = _acquire(session_id)
    try:
        yield
    finally:
        _release(session_id, token)


def _get_session(db: Session, user_id: str, session_id: str) -> AgentSession:
    s = db.get(AgentSession, session_id)
    if s is None or s.user_id != user_id:
        raise AgentError(404, "session_not_found", f"세션 {session_id} 을(를) 찾을 수 없습니다.")
    return s


def _summary(steps: list[dict] | None) -> str | None:
    if not steps:
        return None
    return f"{len(steps)}단계 완료 : " + ", ".join(s.get("title") or STEP_TITLE.get(s["kind"], s["kind"]) for s in steps)


# ---------- 세션 ----------

@router.get("/info", response_model=AgentInfo)
def info(llm: Llm) -> AgentInfo:
    return AgentInfo(provider=llm.name)


@router.post("/sessions", response_model=SessionView, status_code=201)
async def create_session(body: SessionCreate, db: DbSession, user_id: UserId, http: Http) -> SessionView:
    await ServiceApi(http, user_id).get_plan(body.plan_id)  # 존재·소유 확인 (없으면 404 plan_not_found 전달)
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


async def _resolve_card(api: ServiceApi, plan_id: int, ref: dict) -> dict | None:
    """카드 참조를 지금 상태의 카드로. 계획·카드가 사라졌으면 None."""
    try:
        if ref["type"] == "proposal":
            return {"type": "proposal", "proposal": await api.get_proposal(plan_id, ref["id"])}
        return {"type": "recommendation", "recommendation": await api.get_recommendation(plan_id, ref["id"])}
    except ApiError as e:
        if e.status == 404:
            return None
        raise


@router.get("/sessions/{session_id}", response_model=SessionDetail)
async def get_session(session_id: str, db: DbSession, user_id: UserId, http: Http) -> SessionDetail:
    s = _get_session(db, user_id, session_id)
    rows = db.scalars(select(AgentMessage).where(AgentMessage.session_id == s.id, AgentMessage.hidden.is_(False),
                                                 AgentMessage.role.in_(("user", "assistant")))
                      .order_by(AgentMessage.id))
    api = ServiceApi(http, user_id)
    messages: list[MessageView] = []
    for m in rows:
        if not m.content.strip() and not m.cards and not m.steps:
            continue
        cards = [c for ref in (m.cards or []) if (c := await _resolve_card(api, s.plan_id, ref))]
        steps = [Step.model_validate(x) for x in (m.steps or [])]
        prev = messages[-1] if messages else None
        if prev and prev.role == m.role == "assistant":  # 한 턴의 답변(도구 호출 전·후)은 스트림처럼 한 말풍선으로
            prev.content = "\n\n".join(x for x in (prev.content, m.content) if x.strip())
            prev.steps += steps
            prev.cards += cards
            prev.steps_summary = _summary([x.model_dump() for x in prev.steps])
        else:
            messages.append(MessageView(id=m.id, role=m.role, content=m.content, created_at=m.created_at,
                                        steps=steps, steps_summary=_summary(m.steps), cards=cards))
    return SessionDetail(**SessionView.model_validate(s).model_dump(), messages=messages)


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: str, db: DbSession, user_id: UserId) -> Response:
    s = _get_session(db, user_id, session_id)
    _check_idle(session_id)
    db.delete(s)
    db.commit()
    return Response(status_code=204)


# ---------- 대화 (SSE) ----------

def _sse(ev: dict) -> str:
    return f"event: {ev['event']}\ndata: {json.dumps(ev['data'], ensure_ascii=False, default=str)}\n\n"


async def _turn_response(session_id: str, plan_id: int, text: str, user_id: str, http: httpx.AsyncClient,
                         llm: LLMProvider, stream: bool, repropose: dict | None = None):
    token = _acquire(session_id)

    async def events() -> AsyncIterator[dict]:
        try:
            async for ev in run_turn(session_id, plan_id, text, llm, ServiceApi(http, user_id), repropose=repropose):
                yield ev
        except ApiError as e:
            yield {"event": "error", "data": {"code": e.code, "message": e.message}}
        except Exception as e:  # 예상 못 한 오류도 스트림을 그냥 끊지 말고 error 이벤트로 알린다
            log.exception("session=%s 턴 처리 중 오류", session_id[:8])
            yield {"event": "error", "data": {"code": "internal_error", "message": f"서버 오류: {type(e).__name__}"}}
        finally:
            _release(session_id, token)

    if stream:
        async def sse() -> AsyncIterator[str]:
            async for ev in events():
                yield _sse(ev)

        # 스트림이 시작되기 전에 연결이 끊기면 events() 의 finally 가 돌지 않으므로 응답 뒤에도 한 번 더 푼다
        return StreamingResponse(sse(), media_type="text/event-stream", background=BackgroundTask(_release, session_id, token),
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    text_out, steps, cards, summary, error, usage = "", {}, [], None, None, None
    async for ev in events():
        name, data = ev["event"], ev["data"]
        if name == "step":
            steps[data["id"]] = data
        elif name in ("proposal", "recommendation"):
            cards.append({"type": name, name: data})
        elif name == "steps_done":
            summary = data["summary"]
        elif name == "done":
            text_out, usage = data["text"], data.get("usage")
        elif name == "error":
            error = data
    return ChatResult(text=text_out, steps=[Step.model_validate(s) for s in steps.values()], steps_summary=summary,
                      cards=cards, error=error, usage=usage)


_STREAM_DOC = {200: {"content": {"text/event-stream": {}}, "description": "stream=true 면 SSE 이벤트 스트림"}}
StreamFlag = Annotated[bool, Query(description="true: SSE 실시간 스트림, false: 끝난 뒤 JSON 한 번")]


@router.post("/sessions/{session_id}/chat", response_model=ChatResult, responses=_STREAM_DOC)
async def chat(session_id: str, body: ChatRequest, db: DbSession, user_id: UserId, http: Http, llm: Llm,
               stream: StreamFlag = True):
    s = _get_session(db, user_id, session_id)  # 존재 확인은 스트림 시작 전에 (404 를 바로 돌려주기 위해)
    return await _turn_response(session_id, s.plan_id, body.message, user_id, http, llm, stream)


# ---------- 카드 동작 ----------

async def _own_proposal(api: ServiceApi, s: AgentSession, proposal_id: int) -> dict:
    try:
        prop = await api.get_proposal(s.plan_id, proposal_id)
    except ApiError as e:
        if e.status == 404:
            raise AgentError(404, "proposal_not_found", f"이 세션의 제안 {proposal_id} 이 없습니다.") from e
        raise
    if prop["origin"] != s.id:
        raise AgentError(404, "proposal_not_found", f"이 세션의 제안 {proposal_id} 이 없습니다.")
    return prop


async def _own_recommendation(api: ServiceApi, s: AgentSession, rec_id: int, cand_id: int) -> dict:
    try:
        rec = await api.get_recommendation(s.plan_id, rec_id)
    except ApiError as e:
        if e.status == 404:
            raise AgentError(404, "recommendation_not_found", f"이 세션의 추천 {rec_id} 이 없습니다.") from e
        raise
    if rec["origin"] != s.id:
        raise AgentError(404, "recommendation_not_found", f"이 세션의 추천 {rec_id} 이 없습니다.")
    cand = next((c for c in rec["candidates"] if c["id"] == cand_id), None)
    if cand is None:
        raise AgentError(404, "candidate_not_found", f"추천 {rec_id} 의 후보 {cand_id} 이 없습니다.")
    return cand


@router.post("/sessions/{session_id}/proposals/{proposal_id}/accept")
async def accept(session_id: str, proposal_id: int, db: DbSession, user_id: UserId, http: Http,
                 body: Annotated[AcceptRequest | None, Body()] = None) -> dict:
    """제안 승인. 응답은 planner {proposal, plan}. 제안 이후 일정이 바뀌었으면 409 base_changed 를 그대로 전달."""
    s = _get_session(db, user_id, session_id)
    async with _hold(session_id):
        api = ServiceApi(http, user_id)
        await _own_proposal(api, s, proposal_id)
        mode = (body or AcceptRequest()).mode
        result = await api.accept_proposal(s.plan_id, proposal_id, mode)
        how = "카드의 일정 그대로 덮어써 " if result["proposal"]["decision"] == "overwritten" else ""
        await to_thread.run_sync(add_note, session_id, f"사용자가 제안 #{proposal_id} 을 승인해 {how}일정에 반영됐습니다.")
    return result


@router.post("/sessions/{session_id}/proposals/{proposal_id}/reject")
async def reject(session_id: str, proposal_id: int, db: DbSession, user_id: UserId, http: Http) -> dict:
    """제안 거절. 응답은 planner ProposalDetail."""
    s = _get_session(db, user_id, session_id)
    async with _hold(session_id):
        api = ServiceApi(http, user_id)
        await _own_proposal(api, s, proposal_id)
        result = await api.reject_proposal(s.plan_id, proposal_id)
        await to_thread.run_sync(add_note, session_id, f"사용자가 제안 #{proposal_id} 을 거절했습니다. 일정은 그대로입니다.")
    return result


@router.post("/sessions/{session_id}/proposals/{proposal_id}/repropose", response_model=ChatResult,
             responses=_STREAM_DOC)
async def repropose(session_id: str, proposal_id: int, db: DbSession, user_id: UserId, http: Http, llm: Llm,
                    stream: StreamFlag = True):
    """제안을 지금 일정 기준으로 다시 만든다 (chat 과 같은 스트림). 새 제안이 원래 제안을 대체한다."""
    s = _get_session(db, user_id, session_id)
    _check_idle(session_id)
    prop = await _own_proposal(ServiceApi(http, user_id), s, proposal_id)
    if prop["status"] != "pending":
        raise AgentError(409, "proposal_not_pending", f"제안 {proposal_id} 은 이미 {prop['status']} 상태입니다.",
                         status=prop["status"])
    return await _turn_response(session_id, s.plan_id, REPROPOSE_TEXT, user_id, http, llm, stream, repropose=prop)


async def _candidate(session_id: str, rec_id: int, cand_id: int, action: str, db: Session, user_id: str,
                     http: httpx.AsyncClient) -> dict:
    s = _get_session(db, user_id, session_id)
    async with _hold(session_id):
        api = ServiceApi(http, user_id)
        cand = await _own_recommendation(api, s, rec_id, cand_id)
        result = await api.candidate_action(s.plan_id, rec_id, cand_id, action)
        title = (cand.get("place") or {}).get("title", cand["place_id"])
        verb = "일정에 추가했습니다" if action == "add" else "일정에서 뺐습니다"
        await to_thread.run_sync(add_note, session_id, f"사용자가 추천 #{rec_id} 의 '{title}'({cand['day']}일차)를 {verb}.")
    return result


@router.post("/sessions/{session_id}/recommendations/{recommendation_id}/candidates/{candidate_id}/add")
async def add_candidate(session_id: str, recommendation_id: int, candidate_id: int, db: DbSession, user_id: UserId,
                        http: Http) -> dict:
    """추천 후보를 일정에 추가. 응답은 planner {recommendation, plan}."""
    return await _candidate(session_id, recommendation_id, candidate_id, "add", db, user_id, http)


@router.post("/sessions/{session_id}/recommendations/{recommendation_id}/candidates/{candidate_id}/remove")
async def remove_candidate(session_id: str, recommendation_id: int, candidate_id: int, db: DbSession,
                           user_id: UserId, http: Http) -> dict:
    """추천 후보를 일정에서 뺀다. 응답은 planner {recommendation, plan}."""
    return await _candidate(session_id, recommendation_id, candidate_id, "remove", db, user_id, http)
