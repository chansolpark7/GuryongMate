"""Planner Agent 하네스: 한 번의 사용자 메시지(턴)를 처리한다.

1. 대화 기록 + 현재 일정(대기 중인 제안이 있으면 그 위에서)을 담은 시스템 프롬프트로 LLM 호출
2. LLM 이 도구를 부르면 실행하고 결과를 넣어 다시 호출 (최대 max_steps)
3. 변경 도구로 쌓인 초안이 있으면 planner 에 제안(Proposal)으로 저장 -> 사용자가 승인해야 반영
모든 진행 상황은 이벤트로 흘려보내 앱·CLI 가 실시간으로 표시한다 (schemas.py 의 이벤트 목록 참고).

LLM 응답은 수 분이 걸릴 수 있으므로 DB 는 턴 시작(기록 읽기)과 끝(기록 저장)에만 짧게 쓰고,
동기 DB 호출은 스레드에서 실행해 이벤트 루프를 막지 않는다.
"""
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from anyio import to_thread
from sqlalchemy import select, update

from app.agent.api import ApiError, ServiceApi
from app.agent.config import settings
from app.agent.llm import Finish, LLMError, LLMProvider, ReasoningDelta, TextDelta, ToolCall
from app.agent.models import AgentMessage, AgentSession
from app.agent.prompts import build_system
from app.agent.tools import TOOL_SPECS, Draft, Toolbox
from app.db import SessionLocal

Event = dict  # {"event": 이름, "data": dict}
LLM_RETRIES = 2

log = logging.getLogger("app.agent")
if not log.handlers:  # uvicorn 은 app 로거를 설정하지 않으므로 직접 출력
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(levelname)s [agent] %(message)s"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)


def _ev(event: str, /, **data) -> Event:
    return {"event": event, "data": data}


# ---------- DB (동기, 스레드에서 실행) ----------

def _load_history(session_id: str, limit: int) -> list[dict]:
    with SessionLocal() as db:
        rows = list(db.scalars(select(AgentMessage).where(AgentMessage.session_id == session_id)
                               .order_by(AgentMessage.id.desc()).limit(limit)))[::-1]
    while rows and rows[0].role != "user":  # 잘린 앞부분이 도구 응답 등으로 시작하지 않게
        rows.pop(0)
    out = []
    for m in rows:
        msg: dict = {"role": m.role, "content": m.content}
        if m.tool_calls:
            msg["tool_calls"] = m.tool_calls
        if m.role == "tool":
            msg["tool_call_id"] = m.tool_call_id
        out.append(msg)
    return out


def _save(session_id: str, rows: list[AgentMessage]) -> None:
    with SessionLocal.begin() as db:
        db.add_all(rows)
        db.execute(update(AgentSession).where(AgentSession.id == session_id)
                   .values(updated_at=datetime.now(timezone.utc)))


def add_note(session_id: str, text: str) -> None:
    """앱에서 일어난 일(제안 승인·거절 등)을 다음 턴의 LLM 이 알 수 있게 숨김 메시지로 남긴다."""
    _save(session_id, [AgentMessage(session_id=session_id, role="user", content=f"[알림] {text}", hidden=True)])


async def pending_proposal(api: ServiceApi, plan_id: int, session_id: str) -> dict | None:
    """이 세션이 만든, 아직 승인·거절되지 않은 최신 제안 (상세)."""
    for p in await api.list_proposals(plan_id, status="pending"):
        if p["origin"] == session_id:
            return await api.get_proposal(plan_id, p["id"])
    return None


# ---------- 턴 ----------

async def run_turn(session_id: str, plan_id: int, user_text: str, llm: LLMProvider,
                   api: ServiceApi) -> AsyncIterator[Event]:
    yield _ev("start", session_id=session_id)
    sid = session_id[:8]

    # 이어서 수정할 대기 제안이 있으면 그 ops 위에서 시작
    draft = Draft()
    dropped = None
    pending = await pending_proposal(api, plan_id, session_id)
    if pending:
        draft.base_proposal_id = pending["id"]
        if pending["applicable"]:
            draft.ops = pending["ops"]
        else:  # 그사이 사용자가 직접 일정을 바꿔 적용할 수 없게 됨 -> 새로 시작 (턴이 끝나면 대체·거절됨)
            draft.changed = True
            dropped, pending = pending, None
    toolbox = Toolbox(api, plan_id, draft)
    plan_view = await toolbox.current_view()

    history = await to_thread.run_sync(_load_history, session_id, settings.history_messages)
    messages = [{"role": "system", "content": build_system(plan_view, pending, dropped)},
                *history, {"role": "user", "content": user_text}]
    new_rows = [AgentMessage(session_id=session_id, role="user", content=user_text)]
    said: list[str] = []  # 이번 턴에 사용자에게 보인 말 (도구 호출 전 말 포함)

    for step in range(settings.max_steps):
        # 마지막 단계에서는 도구 없이 호출해 반드시 답변으로 끝나게 한다
        tools = TOOL_SPECS if step < settings.max_steps - 1 else []
        for attempt in range(LLM_RETRIES + 1):
            text, calls, finish, reasoning_len = "", [], "", 0
            try:
                async for ev in llm.stream(messages, tools):
                    match ev:
                        case TextDelta(text=t):
                            text += t
                            yield _ev("text", delta=t)
                        case ReasoningDelta(text=t):
                            reasoning_len += len(t)
                            yield _ev("thinking", delta=t)
                        case ToolCall():
                            calls.append(ev)
                        case Finish(reason=r):
                            finish = r
                break
            except LLMError as e:
                # 로컬 모델은 가끔 형식이 깨진 도구 호출을 내서 서버 파서가 실패한다.
                # 아직 답변 텍스트를 내보내지 않았으면 같은 단계를 다시 시도한다.
                log.warning("session=%s step=%d attempt=%d LLM 오류: %s", sid, step, attempt, e)
                if text or attempt == LLM_RETRIES:
                    yield _ev("error", message=f"LLM 호출 실패: {e}")
                    return

        log.info("session=%s step=%d finish=%s text=%d reasoning=%d tools=%s", sid, step, finish,
                 len(text), reasoning_len, [c.name for c in calls])
        if not tools:  # 도구를 주지 않았는데도 도구 호출을 내는 모델이 있다 -> 무시하고 답변으로 끝낸다
            calls = []
        if text.strip():
            said.append(text.strip())
        tool_calls = [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                      for c in calls]
        messages.append({"role": "assistant", "content": text, **({"tool_calls": tool_calls} if tool_calls else {})})
        new_rows.append(AgentMessage(session_id=session_id, role="assistant", content=text,
                                     tool_calls=tool_calls or None, hidden=bool(tool_calls) and not text.strip()))
        if not calls:
            break
        if text.strip():
            yield _ev("text", delta="\n\n")  # 도구 호출 앞의 말과 이후 답변을 구분

        for c in calls:
            yield _ev("tool_call", id=c.id, name=c.name, arguments=c.arguments)
            outcome = await toolbox.call(c.name, c.arguments)
            yield _ev("tool_result", id=c.id, name=c.name, ok=outcome.ok, summary=outcome.summary)
            messages.append({"role": "tool", "tool_call_id": c.id, "content": outcome.content})
            # 기록에는 짧은 형태만 남긴다 (다음 턴엔 최신 일정이 시스템 프롬프트로 들어가므로 옛 스냅샷은 불필요)
            new_rows.append(AgentMessage(session_id=session_id, role="tool", content=outcome.history or outcome.content,
                                         tool_call_id=c.id, tool_ok=outcome.ok, hidden=True))

    # 초안 -> 제안
    proposal = None
    full_text = "\n\n".join(said)
    if draft.changed:
        try:
            if draft.ops:
                proposal = await api.create_proposal(plan_id, draft.ops, full_text[:500], session_id,
                                                     draft.base_proposal_id)
            elif draft.base_proposal_id:
                await api.reject_proposal(plan_id, draft.base_proposal_id)
        except ApiError as e:
            if e.status == 409:  # 이어서 고치던 제안이 그사이 승인·거절됨
                msg = "이어서 고치던 제안이 그사이 승인되거나 거절돼서 이번 변경은 제안으로 만들지 않았어요. 다시 요청해 주세요."
            else:
                msg = f"제안을 저장하지 못했습니다: {e.message}"
            yield _ev("error", message=msg)
            return
    if not full_text:
        # 모델이 생각·도구 호출만 하고 답변 없이 끝난 경우에도 사용자에게는 항상 한마디 남긴다
        full_text = ("변경안을 만들었어요. 제안 카드에서 확인하고 괜찮으면 승인해 주세요." if proposal else
                     "죄송해요, 요청을 끝까지 처리하지 못했어요. 조금 더 구체적으로 말씀해 주시겠어요?")
        yield _ev("text", delta=full_text)
        new_rows.append(AgentMessage(session_id=session_id, role="assistant", content=full_text))
    if proposal:
        new_rows[-1].proposal_id = proposal["id"]

    # 제안 이벤트를 보내기 전에 기록부터 저장한다 (여기서 연결이 끊겨도 제안과 대화 기록이 어긋나지 않게)
    await to_thread.run_sync(_save, session_id, new_rows)
    if proposal:
        yield _ev("proposal", **proposal)
    yield _ev("done", text=full_text, proposal_id=proposal["id"] if proposal else None)
