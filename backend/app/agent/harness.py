"""Planner Agent 하네스: 한 번의 사용자 메시지(턴)를 처리한다.

1. 대화 기록 + 현재 일정(대기 중인 제안이 있으면 그 위에서)을 담은 시스템 프롬프트로 LLM 호출
2. LLM 이 도구를 부르면 실행하고 결과를 넣어 다시 호출 (최대 max_steps)
3. 변경 도구로 쌓인 초안이 있으면 planner 에 제안(Proposal)으로 저장 -> 사용자가 승인해야 반영
모든 진행 상황은 이벤트로 흘려보내 앱·CLI 가 실시간으로 표시한다 (schemas.py 의 이벤트 목록 참고).
"""
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.api import ApiError, ServiceApi
from app.agent.config import settings
from app.agent.llm import Finish, LLMError, LLMProvider, ReasoningDelta, TextDelta, ToolCall
from app.agent.models import AgentMessage, AgentSession
from app.agent.prompts import build_system
from app.agent.tools import TOOL_SPECS, Draft, Toolbox

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


def load_history(db: Session, session_id: str, limit: int) -> list[dict]:
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


async def pending_proposal(api: ServiceApi, plan_id: int, session_id: str) -> dict | None:
    """이 세션이 만든, 아직 승인·거절되지 않은 최신 제안 (상세)."""
    for p in await api.list_proposals(plan_id, status="pending"):
        if p["origin"] == session_id:
            return await api.get_proposal(plan_id, p["id"])
    return None


async def run_turn(db: Session, session: AgentSession, user_text: str, llm: LLMProvider,
                   api: ServiceApi) -> AsyncIterator[Event]:
    yield _ev("start", session_id=session.id)

    # 이어서 수정할 대기 제안이 있으면 그 ops 위에서 시작
    draft = Draft()
    pending = await pending_proposal(api, session.plan_id, session.id)
    if pending:
        draft.base_proposal_id = pending["id"]
        if pending["applicable"]:
            draft.ops = pending["ops"]
        else:  # 그사이 사용자가 직접 일정을 바꿔 적용할 수 없게 됨 -> 새로 시작
            draft.changed = True
            pending = None
    toolbox = Toolbox(api, session.plan_id, draft)
    plan_view = await toolbox.current_view()

    messages = [{"role": "system", "content": build_system(plan_view, pending)},
                *load_history(db, session.id, settings.history_messages),
                {"role": "user", "content": user_text}]
    new_rows = [AgentMessage(session_id=session.id, role="user", content=user_text)]
    final_text = ""

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
                log.warning("session=%s step=%d attempt=%d LLM 오류: %s", session.id[:8], step, attempt, e)
                if text or attempt == LLM_RETRIES:
                    yield _ev("error", message=f"LLM 호출 실패: {e}")
                    return

        log.info("session=%s step=%d finish=%s text=%d reasoning=%d tools=%s", session.id[:8], step, finish,
                 len(text), reasoning_len, [c.name for c in calls])
        if not tools:  # 도구를 주지 않았는데도 도구 호출을 내는 모델이 있다 -> 무시하고 답변으로 끝낸다
            calls = []
        tool_calls = [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                      for c in calls]
        assistant = {"role": "assistant", "content": text, **({"tool_calls": tool_calls} if tool_calls else {})}
        messages.append(assistant)
        new_rows.append(AgentMessage(session_id=session.id, role="assistant", content=text,
                                     tool_calls=tool_calls or None, hidden=bool(tool_calls) and not text.strip()))
        if not calls:
            final_text = text
            break
        if text.strip():
            yield _ev("text", delta="\n\n")  # 도구 호출 앞의 말과 이후 답변을 구분

        for c in calls:
            yield _ev("tool_call", id=c.id, name=c.name, arguments=c.arguments)
            outcome = await toolbox.call(c.name, c.arguments)
            yield _ev("tool_result", id=c.id, name=c.name, ok=outcome.ok, summary=outcome.summary)
            messages.append({"role": "tool", "tool_call_id": c.id, "content": outcome.content})
            new_rows.append(AgentMessage(session_id=session.id, role="tool", content=outcome.content,
                                         tool_call_id=c.id, tool_ok=outcome.ok, hidden=True))

    # 초안 -> 제안
    proposal = None
    if draft.changed:
        try:
            if draft.ops:
                summary = final_text.strip()[:500]
                proposal = await api.create_proposal(session.plan_id, draft.ops, summary, session.id,
                                                     draft.base_proposal_id)
            elif draft.base_proposal_id:
                await api.reject_proposal(session.plan_id, draft.base_proposal_id)
        except ApiError as e:
            yield _ev("error", message=f"제안을 저장하지 못했습니다: {e.message}")
            return
    if not final_text.strip():
        # 모델이 생각·도구 호출만 하고 답변 없이 끝난 경우에도 사용자에게는 항상 한마디 남긴다
        final_text = ("변경안을 만들었어요. 제안 카드에서 확인하고 괜찮으면 승인해 주세요." if proposal else
                      "죄송해요, 요청을 끝까지 처리하지 못했어요. 조금 더 구체적으로 말씀해 주시겠어요?")
        yield _ev("text", delta=final_text)
        new_rows.append(AgentMessage(session_id=session.id, role="assistant", content=final_text))
    if proposal:
        new_rows[-1].proposal_id = proposal["id"]
        yield _ev("proposal", **proposal)

    db.add_all(new_rows)
    session.updated_at = datetime.now(timezone.utc)
    db.commit()
    yield _ev("done", text=final_text, proposal_id=proposal["id"] if proposal else None)


def add_note(db: Session, session: AgentSession, text: str) -> None:
    """앱에서 일어난 일(제안 승인·거절 등)을 다음 턴의 LLM 이 알 수 있게 숨김 메시지로 남긴다."""
    db.add(AgentMessage(session_id=session.id, role="user", content=f"[알림] {text}", hidden=True))
    session.updated_at = datetime.now(timezone.utc)
    db.commit()

