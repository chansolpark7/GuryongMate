"""Planner Agent 하네스: 한 번의 사용자 메시지(턴)를 처리한다. 명세: docs/agent/README.md

1. 대화 기록 + 현재 일정(이어받은 대기 제안이 있으면 그 위에서)을 담은 시스템 프롬프트로 LLM 호출
2. LLM 이 도구를 부르면 실행하고 결과를 넣어 다시 호출 (최대 max_steps). 진행 상황은 status·step 이벤트로
3. recommend_places 는 추천 카드를 바로 보낸다
4. 변경 도구로 쌓인 초안이 있으면 턴 끝에 planner 에 변경 제안으로 저장한다 (사용자가 승인해야 반영)

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
from app.agent.tools import STEP_KIND, TOOL_SPECS, Draft, Toolbox
from app.db import SessionLocal

Event = dict  # {"event": 이름, "data": dict}
LLM_RETRIES = 2

STEP_TITLE = {"search": "장소 찾기", "place": "장소 정보 확인", "plan": "일정 확인", "edit": "일정 조정",
              "rule": "조건 반영", "recommend": "추천 정리", "proposal": "변경안 만들기"}
STATUS_TEXT = {"search": "장소를 찾고 있어요.", "place": "장소 정보를 확인하고 있어요.", "plan": "일정을 확인하고 있어요.",
               "edit": "일정을 조정하고 있어요.", "rule": "조건을 반영하고 있어요.",
               "recommend": "추천할 곳을 고르고 있어요.", "proposal": "변경안을 만들고 있어요."}

log = logging.getLogger("app.agent")
if not log.handlers:  # uvicorn 은 app 로거를 설정하지 않으므로 직접 출력
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(levelname)s [agent] %(message)s"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)


def _ev(event: str, /, **data) -> Event:
    return {"event": event, "data": data}


def _error(code: str, message: str) -> Event:
    return _ev("error", code=code, message=message)


class Steps:
    """처리 단계. 같은 종류의 도구를 연달아 쓰면 한 단계로 합친다 (같은 id 를 다시 running -> done)."""

    def __init__(self):
        self.items: list[dict] = []
        self._status_kind: str | None = None

    def start(self, kind: str, detail: str = "") -> list[Event]:
        events = []
        if self.items and self.items[-1]["kind"] == kind:
            step = self.items[-1]
            step["status"] = "running"
        else:
            step = {"id": f"s{len(self.items) + 1}", "kind": kind, "status": "running", "title": STEP_TITLE[kind],
                    "detail": ""}
            self.items.append(step)
        step["detail"] = detail
        if self._status_kind != kind:
            self._status_kind = kind
            events.append(_ev("status", text=STATUS_TEXT[kind]))
        events.append(_ev("step", **step))
        return events

    def finish(self, ok: bool, detail: str) -> Event:
        step = self.items[-1]
        step["status"] = "done" if ok else "error"
        step["detail"] = detail
        return _ev("step", **step)

    @property
    def summary(self) -> str | None:
        if not self.items:
            return None
        return f"{len(self.items)}단계 완료 : " + ", ".join(s["title"] for s in self.items)


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
    """앱에서 일어난 일(카드 동작 등)을 다음 턴의 LLM 이 알 수 있게 숨김 메시지로 남긴다."""
    _save(session_id, [AgentMessage(session_id=session_id, role="user", content=f"[알림] {text}", hidden=True)])


async def latest_pending(api: ServiceApi, plan_id: int, session_id: str) -> dict | None:
    """이 세션이 만든, 아직 대기 중인 최신 변경 제안 (상세)."""
    for p in await api.list_proposals(plan_id, status="pending"):
        if p["origin"] == session_id:
            return await api.get_proposal(plan_id, p["id"])
    return None


# ---------- 턴 ----------

async def run_turn(session_id: str, plan_id: int, user_text: str, llm: LLMProvider, api: ServiceApi, *,
                   repropose: dict | None = None) -> AsyncIterator[Event]:
    """repropose: 다시 만들 제안 (ProposalDetail). 빈 초안에서 시작하고, 새 제안이 그것을 대체한다."""
    yield _ev("start", session_id=session_id)
    sid = session_id[:8]

    draft = Draft()
    pending = dropped = None
    if repropose:
        draft.base_proposal_id = repropose["id"]
    else:
        pending = await latest_pending(api, plan_id, session_id)
        if pending:
            draft.base_proposal_id = pending["id"]
            if pending["base_changed"]:  # 그 뒤로 일정이 바뀜 -> 빈 초안에서 시작, 새 제안이 생기면 대체
                dropped, pending = pending, None
            else:
                draft.ops, draft.continued = pending["ops"], True
    toolbox = Toolbox(api, plan_id, draft, session_id)
    plan_view = await toolbox.current_view()
    base_version = toolbox.last_plan["version"]

    history = await to_thread.run_sync(_load_history, session_id, settings.history_messages)
    messages = [{"role": "system", "content": build_system(plan_view, pending, dropped, repropose)},
                *history, {"role": "user", "content": user_text}]
    new_rows = [AgentMessage(session_id=session_id, role="user", content=user_text)]
    said: list[str] = []  # 이번 턴에 사용자에게 보인 말 (도구 호출 전 말 포함)
    steps = Steps()
    cards: list[dict] = []  # 이 턴에 만든 카드 [{"type", "id"}]

    for step_no in range(settings.max_steps):
        # 마지막 단계에서는 도구 없이 호출해 반드시 답변으로 끝나게 한다
        tools = TOOL_SPECS if step_no < settings.max_steps - 1 else []
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
                log.warning("session=%s step=%d attempt=%d LLM 오류: %s", sid, step_no, attempt, e)
                if text or attempt == LLM_RETRIES:
                    yield _error("llm_failed", f"LLM 호출 실패: {e}")
                    return

        log.info("session=%s step=%d finish=%s text=%d reasoning=%d tools=%s", sid, step_no, finish,
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
            kind = STEP_KIND.get(c.name)
            if kind:
                for ev in steps.start(kind):
                    yield ev
            outcome = await toolbox.call(c.name, c.arguments)
            if kind:
                yield steps.finish(outcome.ok, outcome.detail)
            if outcome.card:
                card_type = outcome.card["type"]
                cards.append({"type": card_type, "id": outcome.card[card_type]["id"]})
                yield _ev(card_type, **outcome.card[card_type])
            messages.append({"role": "tool", "tool_call_id": c.id, "content": outcome.content})
            # 기록에는 짧은 형태만 남긴다 (다음 턴엔 최신 일정이 시스템 프롬프트로 들어가므로 옛 스냅샷은 불필요)
            new_rows.append(AgentMessage(session_id=session_id, role="tool", content=outcome.history or outcome.content,
                                         tool_call_id=c.id, tool_ok=outcome.ok, hidden=True))

    # 초안 -> 변경 제안
    proposal = None
    full_text = "\n\n".join(said)
    if draft.changed and draft.ops:
        for ev in steps.start("proposal", "잠시만요..."):
            yield ev
        try:
            proposal = await api.create_proposal(
                plan_id, draft.ops, summary=full_text, request=repropose["request"] if repropose else user_text,
                origin=session_id, supersedes=draft.base_proposal_id, expected_version=base_version)
        except ApiError as e:
            yield steps.finish(False, "만들지 못함")
            if e.code == "version_conflict":
                yield _error("plan_changed", "응답을 만드는 동안 일정이 바뀌어 변경안을 만들지 않았어요. 다시 요청해 주세요.")
            elif e.code in ("supersede_conflict", "proposal_not_pending"):
                yield _error("proposal_changed", "이어서 고치던 제안이 그사이 승인되거나 거절돼서 이번 변경은 제안으로 "
                                                 "만들지 않았어요. 다시 요청해 주세요.")
            else:
                yield _error(e.code, f"변경안을 저장하지 못했습니다: {e.message}")
            return
        count = sum(1 for ch in proposal["changes"] if ch["kind"] != "day_end")
        yield steps.finish(True, f"변경 {count}건")
        cards.append({"type": "proposal", "id": proposal["id"]})
    elif draft.changed and draft.continued and draft.base_proposal_id:
        try:  # 이어받은 제안의 변경을 모두 취소하고 끝남 -> 그 제안 거절
            await api.reject_proposal(plan_id, draft.base_proposal_id)
        except ApiError as e:
            log.warning("session=%s 이어받은 제안 거절 실패: %s", sid, e)

    if not full_text:
        # 모델이 생각·도구 호출만 하고 답변 없이 끝난 경우에도 사용자에게는 항상 한마디 남긴다
        if proposal:
            full_text = "변경안을 만들었어요. 제안 카드에서 확인하고 괜찮으면 승인해 주세요."
        elif cards:
            full_text = "추천 장소를 골라 봤어요. 마음에 드는 곳을 추가해 보세요."
        else:
            full_text = "죄송해요, 요청을 끝까지 처리하지 못했어요. 조금 더 구체적으로 말씀해 주시겠어요?"
        yield _ev("text", delta=full_text)
        new_rows.append(AgentMessage(session_id=session_id, role="assistant", content=full_text))
    new_rows[-1].steps = steps.items or None
    new_rows[-1].cards = cards or None

    # 카드 이벤트를 보내기 전에 기록부터 저장한다 (여기서 연결이 끊겨도 카드와 대화 기록이 어긋나지 않게)
    await to_thread.run_sync(_save, session_id, new_rows)
    if proposal:
        yield _ev("proposal", **proposal)
    if steps.summary:
        yield _ev("steps_done", count=len(steps.items), summary=steps.summary)
    yield _ev("done", text=full_text, cards=cards)
