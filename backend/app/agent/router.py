"""Agent API. 앱은 어떤 LLM 이 돌고 있는지 몰라도 이 API 만 호출하면 된다. 명세: docs/agent/README.md

흐름: 세션 생성(plan_id) -> chat (SSE) -> 카드 수신(제안·추천) -> 카드 동작(accept/reject/repropose, add/remove)
가이드 준비: POST /agent/plans/{plan_id}/guide/prepare -> 202 작업 -> GET …/guide/jobs/{job_id} (대화 세션과 별개).
  제안 승인·추천 추가 뒤에는 노트가 없는 장소를 자동으로 준비한다 (trip 은 여행 시작·도착 때 auto 로 부른다)
여행 중에는 trip 이 선제 턴(events)을 부르고, 모든 턴이 동행 모드(현재 상황 블록)로 돈다.
오류는 {"detail": {"code", "message", ...}} 형식이고, planner 가 거부한 오류는 코드·추가 필드를 그대로 전달한다.
"""
import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Annotated

import httpx
from anyio import to_thread
from fastapi import APIRouter, Body, Depends, Query, Response
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.agent import guide, limits
from app.agent.api import ApiError, ServiceApi, get_http
from app.agent.config import settings
from app.agent.errors import AgentError, AgentRoute
from app.agent.harness import STEP_TITLE, add_note, run_turn
from app.agent.prompts import event_message
from app.agent.llm import LLMProvider, get_llm
from app.agent.models import AgentEventTurn, AgentMessage, AgentSession
from app.agent.schemas import (
    AcceptRequest,
    AgentInfo,
    ChatRequest,
    ChatResult,
    EventRequest,
    GuideJobView,
    GuidePrepareRequest,
    MessageView,
    SessionCreate,
    SessionDetail,
    SessionView,
    Step,
)
from app.core.auth import CurrentUser, ForwardAuth, issue_token
from app.db import SessionLocal, get_db

router = APIRouter(prefix="/agent", tags=["agent"], route_class=AgentRoute)

DbSession = Annotated[Session, Depends(get_db)]
# 사용자: Authorization: Bearer <access 토큰> (개발 모드면 X-User-Id). planner 등은 받은 인증을 그대로 전달해 부른다
UserId = CurrentUser
Auth = ForwardAuth
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


@router.get("/limits")
def get_limits(db: DbSession, user_id: UserId) -> dict:
    """내 LLM 호출 수와 한도 (종류별 1시간·하루, 한도 없음은 null). docs/deploy/README.md §3.3"""
    return limits.usage(db, user_id)


@router.post("/sessions", response_model=SessionView, status_code=201)
async def create_session(body: SessionCreate, db: DbSession, user_id: UserId, auth: Auth, http: Http) -> SessionView:
    await ServiceApi(http, auth).get_plan(body.plan_id)  # 존재·소유 확인 (없으면 404 plan_not_found 전달)
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
async def get_session(session_id: str, db: DbSession, user_id: UserId, auth: Auth, http: Http) -> SessionDetail:
    s = _get_session(db, user_id, session_id)
    # 숨김 user 행(알림·이벤트)은 보이지 않지만 턴의 경계로 쓴다 (선제 턴 답변이 앞 턴 말풍선에 붙지 않게)
    rows = db.scalars(select(AgentMessage).where(AgentMessage.session_id == s.id,
                                                 or_(AgentMessage.hidden.is_(False), AgentMessage.role == "user"),
                                                 AgentMessage.role.in_(("user", "assistant")))
                      .order_by(AgentMessage.id))
    api = ServiceApi(http, auth)
    messages: list[MessageView] = []
    boundary = False
    for m in rows:
        if m.hidden:
            boundary = True
            continue
        if not m.content.strip() and not m.cards and not m.steps:
            continue
        cards = [c for ref in (m.cards or []) if (c := await _resolve_card(api, s.plan_id, ref))]
        steps = [Step.model_validate(x) for x in (m.steps or [])]
        prev = messages[-1] if messages and not boundary else None
        boundary = False
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


async def _turn_response(session_id: str, plan_id: int, text: str, auth: dict[str, str], http: httpx.AsyncClient,
                         llm: LLMProvider, stream: bool, repropose: dict | None = None, **turn):
    """turn: run_turn 에 그대로 (trip, fetch_trip, hidden — 선제 턴)."""
    token = _acquire(session_id)

    async def events() -> AsyncIterator[dict]:
        try:
            async for ev in run_turn(session_id, plan_id, text, llm, ServiceApi(http, auth), repropose=repropose,
                                     **turn):
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

    text_out, steps, cards, summary, error, done = "", {}, [], None, None, {}
    async for ev in events():
        name, data = ev["event"], ev["data"]
        if name == "step":
            steps[data["id"]] = data
        elif name in ("proposal", "recommendation"):
            cards.append({"type": name, name: data})
        elif name == "steps_done":
            summary = data["summary"]
        elif name == "done":
            text_out, done = data["text"], data
        elif name == "error":
            error = data
    return ChatResult(text=text_out, steps=[Step.model_validate(s) for s in steps.values()], steps_summary=summary,
                      cards=cards, error=error, usage=done.get("usage"), message_id=done.get("message_id"),
                      mode=done.get("mode"))


_STREAM_DOC = {200: {"content": {"text/event-stream": {}}, "description": "stream=true 면 SSE 이벤트 스트림"}}
StreamFlag = Annotated[bool, Query(description="true: SSE 실시간 스트림, false: 끝난 뒤 JSON 한 번")]


@router.post("/sessions/{session_id}/chat", response_model=ChatResult, responses=_STREAM_DOC)
async def chat(session_id: str, body: ChatRequest, db: DbSession, user_id: UserId, auth: Auth, http: Http, llm: Llm,
               stream: StreamFlag = True):
    s = _get_session(db, user_id, session_id)  # 존재 확인은 스트림 시작 전에 (404 를 바로 돌려주기 위해)
    _check_idle(session_id)
    limits.consume(db, user_id, "chat")  # 넘으면 429 rate_limited (공개 서버, AGENT_LIMIT_CHAT_*)
    return await _turn_response(session_id, s.plan_id, body.message, auth, http, llm, stream)


@router.post("/sessions/{session_id}/events", response_model=ChatResult)
async def proactive_turn(session_id: str, body: EventRequest, db: DbSession, user_id: UserId, auth: Auth, http: Http,
                         llm: Llm) -> ChatResult:
    """선제 턴 (trip 이 부른다, 앱은 부르지 않는다). 숨김 메시지 '[이벤트] …'로 턴을 시작해 Agent 가 먼저 말을 건다.

    chat 과 같은 잠금(409 agent_busy)·제안 생성·검토 관문을 쓴다. trip 은 본문의 TripState 를 쓴다 (다시 조회하지 않음).
    같은 event.id 가 다시 오면 (trip 의 재시도) 새 턴을 돌리지 않고 이전 결과를 그대로 돌려준다.
    """
    s = _get_session(db, user_id, session_id)
    event = body.event.model_dump()
    event_id = event.get("id")
    if event_id is not None and (done := db.get(AgentEventTurn, (session_id, event_id))) is not None:
        log.info("session=%s 선제 턴 event=%s id=%s 이미 처리함 -> 이전 결과", session_id[:8], event["type"], event_id)
        return ChatResult.model_validate(done.result)
    _check_idle(session_id)
    limits.consume(db, user_id, "event")  # 넘으면 429 -> trip 이 실패로 세고 다시 보낸다 (AGENT_LIMIT_EVENT_*)
    log.info("session=%s 선제 턴 event=%s id=%s", session_id[:8], event["type"], event_id)
    result = await _turn_response(session_id, s.plan_id, event_message(event), auth, http, llm, False,
                                  trip=body.trip, fetch_trip=False, hidden=True)
    if event_id is not None and result.error is None:
        await to_thread.run_sync(_save_event_turn, session_id, event_id, result.model_dump(mode="json"))
    return result


def _save_event_turn(session_id: str, event_id: int, result: dict) -> None:
    with SessionLocal.begin() as db:
        db.merge(AgentEventTurn(session_id=session_id, event_id=event_id, result=result))


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
async def accept(session_id: str, proposal_id: int, db: DbSession, user_id: UserId, auth: Auth, http: Http, llm: Llm,
                 body: Annotated[AcceptRequest | None, Body()] = None) -> dict:
    """제안 승인. 응답은 planner {proposal, plan}. 제안 이후 일정이 바뀌었으면 409 base_changed 를 그대로 전달.
    승인되면 노트가 없는 장소의 가이드를 백그라운드로 준비한다 (응답은 기다리지 않음)."""
    s = _get_session(db, user_id, session_id)
    async with _hold(session_id):
        api = ServiceApi(http, auth)
        await _own_proposal(api, s, proposal_id)
        mode = (body or AcceptRequest()).mode
        result = await api.accept_proposal(s.plan_id, proposal_id, mode)
        how = "카드의 일정 그대로 덮어써 " if result["proposal"]["decision"] == "overwritten" else ""
        await to_thread.run_sync(add_note, session_id, f"사용자가 제안 #{proposal_id} 을 승인해 {how}일정에 반영됐습니다.")
    _start_auto_prepare(s.plan_id, user_id, auth, http, llm, f"제안 #{proposal_id} 승인")
    return result


@router.post("/sessions/{session_id}/proposals/{proposal_id}/reject")
async def reject(session_id: str, proposal_id: int, db: DbSession, user_id: UserId, auth: Auth, http: Http) -> dict:
    """제안 거절. 응답은 planner ProposalDetail."""
    s = _get_session(db, user_id, session_id)
    async with _hold(session_id):
        api = ServiceApi(http, auth)
        await _own_proposal(api, s, proposal_id)
        result = await api.reject_proposal(s.plan_id, proposal_id)
        await to_thread.run_sync(add_note, session_id, f"사용자가 제안 #{proposal_id} 을 거절했습니다. 일정은 그대로입니다.")
    return result


@router.post("/sessions/{session_id}/proposals/{proposal_id}/repropose", response_model=ChatResult,
             responses=_STREAM_DOC)
async def repropose(session_id: str, proposal_id: int, db: DbSession, user_id: UserId, auth: Auth, http: Http, llm: Llm,
                    stream: StreamFlag = True):
    """제안을 지금 일정 기준으로 다시 만든다 (chat 과 같은 스트림). 새 제안이 원래 제안을 대체한다."""
    s = _get_session(db, user_id, session_id)
    _check_idle(session_id)
    prop = await _own_proposal(ServiceApi(http, auth), s, proposal_id)
    if prop["status"] != "pending":
        raise AgentError(409, "proposal_not_pending", f"제안 {proposal_id} 은 이미 {prop['status']} 상태입니다.",
                         status=prop["status"])
    limits.consume(db, user_id, "chat")
    return await _turn_response(session_id, s.plan_id, REPROPOSE_TEXT, auth, http, llm, stream, repropose=prop)


async def _candidate(session_id: str, rec_id: int, cand_id: int, action: str, db: Session, user_id: str,
                     auth: dict[str, str], http: httpx.AsyncClient, llm: LLMProvider) -> dict:
    s = _get_session(db, user_id, session_id)
    async with _hold(session_id):
        api = ServiceApi(http, auth)
        cand = await _own_recommendation(api, s, rec_id, cand_id)
        result = await api.candidate_action(s.plan_id, rec_id, cand_id, action)
        title = (cand.get("place") or {}).get("title", cand["place_id"])
        verb = "일정에 추가했습니다" if action == "add" else "일정에서 뺐습니다"
        await to_thread.run_sync(add_note, session_id, f"사용자가 추천 #{rec_id} 의 '{title}'({cand['day']}일차)를 {verb}.")
    if action == "add":
        _start_auto_prepare(s.plan_id, user_id, auth, http, llm, f"추천 #{rec_id} 추가")
    return result


@router.post("/sessions/{session_id}/recommendations/{recommendation_id}/candidates/{candidate_id}/add")
async def add_candidate(session_id: str, recommendation_id: int, candidate_id: int, db: DbSession, user_id: UserId,
                        auth: Auth, http: Http, llm: Llm) -> dict:
    """추천 후보를 일정에 추가. 응답은 planner {recommendation, plan}. 추가한 장소의 가이드는 백그라운드로 준비한다."""
    return await _candidate(session_id, recommendation_id, candidate_id, "add", db, user_id, auth, http, llm)


@router.post("/sessions/{session_id}/recommendations/{recommendation_id}/candidates/{candidate_id}/remove")
async def remove_candidate(session_id: str, recommendation_id: int, candidate_id: int, db: DbSession,
                           user_id: UserId, auth: Auth, http: Http, llm: Llm) -> dict:
    """추천 후보를 일정에서 뺀다. 응답은 planner {recommendation, plan}."""
    return await _candidate(session_id, recommendation_id, candidate_id, "remove", db, user_id, auth, http, llm)


# ---------- 가이드 준비 (docs/docent/README.md §3.3) ----------

GUIDE_TOKEN_TTL = timedelta(hours=2)  # 작업이 길어도 받은 토큰 만료와 상관없이 끝나게


# 자동 준비가 건너뛰는 노트 상태: 이미 있음·준비 중. failed 는 자동으로 다시 시도하지 않는다 (앱의 [다시 시도]로만)
AUTO_SKIP = ("ready", "pending", "preparing", "failed")
_auto_tasks: set[asyncio.Task] = set()  # 백그라운드 자동 준비 (GC 로 사라지지 않게 참조를 잡아 둔다)


async def _prepare(plan_id: int, user_id: str, auth: dict[str, str], http: httpx.AsyncClient, llm: LLMProvider,
                   consume: Callable[[], None], place_ids: list[str] | None = None, force: bool = False,
                   auto: bool = False) -> guide.GuideJob:
    """가이드 준비 요청 (엔드포인트·자동 준비 공통). 계획당 작업 하나: 진행 중이면 빠진 장소를 그 작업 뒤에 잇고 그 작업을 돌려준다.
    대상: place_ids(일정에 있어야 함, auto 면 없는 것은 무시) 또는 일정의 모든 장소 중 지금 준비 중·대기 중이 아닌 곳.
    force 가 아니면 ready 노트는 건너뛴다 (auto 면 AUTO_SKIP 모두). 새로 준비할 곳이 있을 때만 consume(호출 수 제한)을 부른다."""
    api = ServiceApi(http, auth)
    plan = await api.get_plan(plan_id)  # 존재·소유 확인 (404 plan_not_found 전달)
    running = guide.running_job(plan_id)
    if running is not None and running.user_id != user_id:  # 계획 주인만 여기까지 온다. 혹시 모를 경우를 막는다
        raise AgentError(409, "guide_busy", "이 계획의 가이드를 준비하는 중입니다.")
    places = guide.plan_places(plan)
    if place_ids is not None:
        missing = sorted(set(place_ids) - {p["place_id"] for p in places})
        if missing and not auto:
            raise AgentError(422, "place_not_in_plan", "일정에 없는 장소입니다.", place_ids=missing)
        places = [p for p in places if p["place_id"] in set(place_ids)]
    busy = guide.busy_places(plan_id)
    places = [p for p in places if p["place_id"] not in busy]
    if places and not force:
        skip = AUTO_SKIP if auto else ("ready",)
        status = {n["place_id"]: n["status"] for n in await api.list_notes(plan_id)}
        places = [p for p in places if status.get(p["place_id"]) not in skip]
    if not places:
        return running or guide.start(plan_id, user_id, [], llm, api)  # 진행 중인 작업, 없으면 바로 끝난 빈 작업
    consume()  # 새 작업(또는 이어서 할 장소)만 센다 (AGENT_LIMIT_GUIDE_*). 넘으면 429
    for p in places:  # 앱이 바로 '대기'로 보이게 먼저 표시 (이전 본문은 docent 가 남긴다)
        await api.put_note(plan_id, p["place_id"], {"status": "pending"})
    job_auth = {"Authorization": f"Bearer {issue_token(user_id, GUIDE_TOKEN_TTL)}"} if "Authorization" in auth else auth
    job_api = ServiceApi(http, job_auth)
    if running is not None:
        guide.enqueue(plan_id, user_id, places, llm, job_api)
        log.info("guide plan=%s job=%s 진행 중 -> 끝난 뒤 이어서 %d곳%s", plan_id, running.id[:8], len(places),
                 " (자동)" if auto else "")
        return running
    job = guide.start(plan_id, user_id, places, llm, job_api)
    log.info("guide plan=%s job=%s 시작: %d곳%s", plan_id, job.id[:8], len(places), " (자동)" if auto else "")
    return job


def _consume_guide(user_id: str) -> None:
    with SessionLocal() as db:
        limits.consume(db, user_id, "guide")


async def _auto_prepare(plan_id: int, user_id: str, auth: dict[str, str], http: httpx.AsyncClient, llm: LLMProvider,
                        why: str) -> None:
    """자동 준비 (docs/docent/README.md §3.2). 어떤 오류도 사용자 요청에 번지지 않게 로그만 남긴다."""
    try:
        await _prepare(plan_id, user_id, auth, http, llm, lambda: _consume_guide(user_id), auto=True)
    except AgentError as e:
        log.info("guide plan=%s 자동 준비(%s) 건너뜀: %s %s", plan_id, why, e.code, e.message)
    except ApiError as e:
        log.warning("guide plan=%s 자동 준비(%s) 실패: %s", plan_id, why, e)
    except Exception:
        log.exception("guide plan=%s 자동 준비(%s) 중 오류", plan_id, why)


def _start_auto_prepare(plan_id: int, user_id: str, auth: dict[str, str], http: httpx.AsyncClient, llm: LLMProvider,
                        why: str) -> None:
    """응답을 기다리게 하지 않게 백그라운드로 (제안 승인·추천 추가 뒤)."""
    if not settings.guide_auto:
        return
    task = asyncio.create_task(_auto_prepare(plan_id, user_id, auth, http, llm, why))
    _auto_tasks.add(task)
    task.add_done_callback(_auto_tasks.discard)


@router.post("/plans/{plan_id}/guide/prepare", response_model=GuideJobView, status_code=202)
async def prepare_guide(plan_id: int, db: DbSession, user_id: UserId, auth: Auth, http: Http, llm: Llm,
                        body: Annotated[GuidePrepareRequest | None, Body()] = None) -> dict:
    """계획의 장소마다 맞춤 가이드 노트를 백그라운드로 만든다. 계획당 작업 하나: 진행 중이면 그 작업을 돌려주고,
    그 작업에 없는 대상은 끝난 뒤 이어서 준비한다(queued). 대상: place_ids(일정에 있어야 함) 또는 일정의 모든 장소.
    force 가 아니면 이미 ready 인 노트는 건너뛴다. auto(trip 의 여행 시작·도착)면 노트가 있거나 실패한 곳도 건너뛴다."""
    body = body or GuidePrepareRequest()
    if body.auto and not settings.guide_auto:  # 자동 준비를 끔 -> 계획 확인만 하고 빈 작업
        api = ServiceApi(http, auth)
        await api.get_plan(plan_id)
        return (guide.running_job(plan_id) or guide.start(plan_id, user_id, [], llm, api)).view()
    job = await _prepare(plan_id, user_id, auth, http, llm, lambda: limits.consume(db, user_id, "guide"),
                         body.place_ids, body.force, body.auto)
    return job.view()


@router.get("/plans/{plan_id}/guide/jobs/{job_id}", response_model=GuideJobView)
def guide_job(plan_id: int, job_id: str, user_id: UserId) -> dict:
    """가이드 준비 진행 상태 (장소별 상태·진행 수·남은 시간). 끝난 작업은 1시간 동안 조회할 수 있다."""
    job = guide.get_job(job_id)
    if job is None or job.plan_id != plan_id or job.user_id != user_id:
        raise AgentError(404, "job_not_found", f"가이드 준비 작업 {job_id} 을(를) 찾을 수 없습니다.")
    return job.view()


# ---------- 탈퇴 ----------

@router.delete("/me", status_code=204)
def delete_my_data(db: DbSession, user_id: UserId) -> Response:
    """이 사용자의 대화 세션·메시지를 모두 지운다. account 탈퇴가 부른다."""
    for s in db.scalars(select(AgentSession).where(AgentSession.user_id == user_id)):
        db.delete(s)
    db.commit()
    return Response(status_code=204)
