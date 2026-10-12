"""Planner Agent 하네스: 한 번의 사용자 메시지(턴)를 처리한다. 명세: docs/agent/README.md

1. 대화 기록 + 현재 일정(이어받은 대기 제안이 있으면 그 위에서)을 담은 시스템 프롬프트로 LLM 호출
2. LLM 이 도구를 부르면 실행하고 결과를 넣어 다시 호출 (최대 max_steps). 진행 상황은 status·step 이벤트로
3. recommend_places 는 추천 카드를 바로 보낸다
4. 변경 도구로 쌓인 초안이 있으면 턴 끝에 planner 에 변경 제안으로 저장한다 (사용자가 승인해야 반영)
5. 여행 중(trip 상태 active)이면 동행 모드: 현재 상황 블록을 넣고 지난 일은 바꾸지 않는다 (docs/agent/README.md §11)
6. 모든 턴의 시스템 프롬프트에 docent 메모리 요약을 넣는다. 동행 모드 대화는 가이드 노트·웹 검색(턴당 상한)을 쓴다
7. 장소 맥락이 있는 질문(place_id, 앱 가이드 노트 화면)은 여행 중이 아니어도 현장 해설처럼 그 장소의 노트·근거부터 찾는다

LLM 응답은 수 분이 걸릴 수 있으므로 DB 는 턴 시작(기록 읽기)과 끝(기록 저장)에만 짧게 쓰고,
동기 DB 호출은 스레드에서 실행해 이벤트 루프를 막지 않는다.
"""
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from anyio import to_thread
from sqlalchemy import select, update

from app.agent import clock
from app.agent.api import ApiError, ServiceApi
from app.agent.config import settings
from app.agent.llm import (
    Citation,
    Finish,
    LLMError,
    LLMProvider,
    ReasoningDelta,
    TextDelta,
    ToolCall,
    WebSearchCall,
    supports_web_search,
)
from app.agent.models import AgentMessage, AgentSession
from app.agent.prompts import build_system, build_system_parts
from app.agent.review import SKIP_CODES, summarize
from app.agent.tools import STEP_KIND, Draft, Toolbox, tool_specs
from app.db import SessionLocal

Event = dict  # {"event": 이름, "data": dict}
LLM_RETRIES = 2

# LLM 호출에 덧붙이는 안내 (DB 대화 기록에는 남기지 않는다). 보통은 그 호출에만 붙였다 떼고,
# 기록 편집 금지 공급자(append_only)에게는 그 턴의 메시지 끝에 덧붙이기만 한다 (docs/agent/README.md §7.1)
EMPTY_REPLY_NOTE = ("[시스템] 방금 응답이 비어 있었다. 필요한 도구를 호출해 요청을 끝까지 처리하거나, "
                    "사용자에게 할 말을 답하라.")
FINAL_STEP_NOTE = ("[시스템] 이번 턴의 도구 호출 한도에 도달해 더는 도구를 쓸 수 없다. 지금까지 도구로 실제로 한 변경만 "
                   "짧게 정리해 답하라 (그 변경이 제안 카드로 전달된다). 하지 않은 변경을 했다고 말하지 말고, "
                   "남은 일은 다음 요청에서 이어서 하겠다고만 말하라.")
CHANGES_NOTE = ("[시스템] 지금 변경 제안 초안의 확정 변경 (답변이 끝나면 이것만 제안 카드 하나로 전달된다):\n{changes}\n"
                "변경 후 일정에 남는 문제:\n{left}\n"
                "답변의 시각·문제는 이 기준(현재 일정)으로 말한다. 답변에서 일정을 바꾼다고 말하는 것은 이 목록에 있는 "
                "변경뿐이다. 목록에 없는 변경(예: 다른 날로 미루기)을 권하고 "
                "싶으면 도구로 초안에 넣거나, 넣지 않을 거면 '다른 방법으로 …도 할 수 있어요'처럼 선택지로만 한 줄 말한다.")
NO_CHANGES_NOTE = ("[시스템] 지금 변경 제안 초안이 비어 있다 (제안 카드가 만들어지지 않는다). 일정을 바꾸려면 변경 도구를 "
                   "써야 한다. 도구로 바꾸지 않은 변경을 했다거나 제안한다고 말하지 말고, 권하고 싶은 변경은 선택지로만 말한다.")
WEB_DONE_NOTE = "[시스템] 이번 턴의 웹 검색 한도({n}번)를 다 썼다. 더 검색하지 말고 지금까지 찾은 자료로 답하라."
# 동행 모드 대화에서 이어받은 대기 제안을 고치지 않고 끝난 턴의 답변 끝에 붙인다 (제안은 그대로 남는다)
PENDING_NOTICE = "아직 승인 대기 중인 변경안이 있어요."
REVIEW_NOTE = ("[시스템] 방금 쓴 답변은 아직 사용자에게 보내지 않았다. 일정을 검토했더니 아래 문제가 남아 있다. "
               "must 는 반드시, should 는 가능하면 고친 뒤 review_plan 으로 다시 확인하라. 다 고친 뒤 최종 답변을 처음부터 "
               "다시 써라. 고칠 수 없는 문제는 최종 답변에서 이유를 짧게 말하라.\n")

STEP_TITLE = {"search": "장소 찾기", "place": "장소 정보 확인", "plan": "일정 확인", "edit": "일정 조정",
              "rule": "조건 반영", "review": "일정 검토", "recommend": "추천 정리", "proposal": "변경안 만들기",
              "safety": "안전 확인", "route": "이동시간 확인",
              "memory": "기억하기", "forget": "기억 지우기", "recall": "기억 확인", "research": "자료 찾기",
              "guide": "가이드 노트 확인"}
STATUS_TEXT = {"search": "장소를 찾고 있어요.", "place": "장소 정보를 확인하고 있어요.", "plan": "일정을 확인하고 있어요.",
               "edit": "일정을 조정하고 있어요.", "rule": "조건을 반영하고 있어요.", "review": "일정을 검토하고 있어요.",
               "recommend": "추천할 곳을 고르고 있어요.", "proposal": "변경안을 만들고 있어요.",
               "safety": "날씨와 안전을 확인하고 있어요.", "route": "이동 시간을 계산하고 있어요.",
               "memory": "기억해 두고 있어요.", "forget": "기억을 지우고 있어요.", "recall": "기억을 확인하고 있어요.",
               "research": "자료를 찾고 있어요.", "guide": "가이드 노트를 확인하고 있어요."}
# 합치지 않는 단계: 기억하기는 항목마다 앱이 "기억했어요 · {detail}" 표시를 한다
UNMERGED_STEPS = {"memory", "forget"}

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
        if self.items and self.items[-1]["kind"] == kind and kind not in UNMERGED_STEPS:
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


def _save(session_id: str, rows: list[AgentMessage]) -> int | None:
    """저장하고 마지막 행의 id 를 돌려준다."""
    with SessionLocal.begin() as db:
        db.add_all(rows)
        db.execute(update(AgentSession).where(AgentSession.id == session_id)
                   .values(updated_at=datetime.now(timezone.utc)))
        db.flush()
        return rows[-1].id if rows else None


def add_note(session_id: str, text: str) -> None:
    """앱에서 일어난 일(카드 동작 등)을 다음 턴의 LLM 이 알 수 있게 숨김 메시지로 남긴다."""
    _save(session_id, [AgentMessage(session_id=session_id, role="user", content=f"[알림] {text}", hidden=True)])


async def active_trip(api: ServiceApi, plan_id: int) -> dict | None:
    """진행 중인 여행일의 TripState. 여행 중이 아니거나 trip 에 접근할 수 없으면 None (계획 모드로 동작)."""
    try:
        state = await api.trip_state(plan_id)
    except ApiError as e:
        if e.status != 404:  # 404: trip 이 없거나(아직 배포 전) 계획을 모름
            log.warning("plan=%s trip 상태를 받지 못해 계획 모드로: %s", plan_id, e)
        return None
    return state if isinstance(state, dict) and state.get("active") else None


async def memory_summary(api: ServiceApi, plan_id: int) -> tuple[str | None, str | None]:
    """docent 메모리 요약 (프롬프트용)과 해설 길이 선호 style_length (short | long | None).
    docent 에 접근할 수 없으면 (None, None) (메모리 없이 턴은 계속)."""
    try:
        summary = await api.memory_summary(plan_id)
    except ApiError as e:
        log.warning("plan=%s 메모리 요약을 받지 못함: %s", plan_id, e)
        return None, None
    return summary["text"], summary.get("style_length")


async def latest_pending(api: ServiceApi, plan_id: int, session_id: str) -> dict | None:
    """이 세션이 만든, 아직 대기 중인 최신 변경 제안 (상세)."""
    for p in await api.list_proposals(plan_id, status="pending"):
        if p["origin"] == session_id:
            return await api.get_proposal(plan_id, p["id"])
    return None


# ---------- 턴 ----------

async def place_title(api: ServiceApi, plan: dict, place_id: str) -> str:
    """장소 맥락 질문의 장소 이름: 일정에 있으면 그 제목, 아니면 places 상세, 그것도 못 받으면 place_id."""
    for d in plan.get("days") or []:
        for i in d["items"]:
            if i["place_id"] == place_id and i.get("place"):
                return i["place"]["title"]
    try:
        return (await api.get_place(place_id)).get("title") or place_id
    except ApiError as e:
        log.info("place=%s 장소 이름을 받지 못함: %s", place_id, e)
        return place_id


async def run_turn(session_id: str, plan_id: int, user_text: str, llm: LLMProvider, api: ServiceApi, *,
                   repropose: dict | None = None, trip: dict | None = None, fetch_trip: bool = True,
                   hidden: bool = False, place_id: str | None = None,
                   trigger: dict | None = None) -> AsyncIterator[Event]:
    """repropose: 다시 만들 제안 (ProposalDetail). 빈 초안에서 시작하고, 새 제안이 그것을 대체한다.
    trip: 여행 상태 (TripState). fetch_trip 이면 trip 에서 조회한다. active 면 동행 모드.
    hidden: 사용자 메시지를 화면에 보이지 않게 저장 (선제 턴의 '[이벤트] …').
    place_id: 사용자가 그 장소의 가이드 노트를 보며 묻는다 (앱). 숨김 맥락 한 줄(사용자 메시지로 남기지 않음) + 현장 해설 도구,
    그 턴의 메시지(사용자·답)에 place_id 를 남긴다. 세션은 그대로 하나다 (맥락·제안·선제 턴이 이어진다).
    trigger: 선제 턴의 계기 (schemas.Trigger). 그 턴의 답(assistant 행)에 남기고 done 에도 싣는다.

    대화 기록은 턴이 끝날 때 한 번 저장한다. 오류로 끝나거나 연결이 끊겨도 그때까지의 완결된 기록
    (도구 호출과 결과의 짝이 맞는 부분)과 이미 만든 카드는 저장한다.
    """
    yield _ev("start", session_id=session_id)
    sid = session_id[:8]

    draft = Draft()
    pending = dropped = None
    if repropose:
        draft.base_proposal_id = repropose["id"]
        request = repropose["request"]
    else:
        request = user_text
        pending = await latest_pending(api, plan_id, session_id)
        if pending:
            draft.base_proposal_id = pending["id"]
            if pending["base_changed"] or pending.get("progress_changed"):
                # 그 뒤로 일정이 바뀌었거나, 제안이 바꾸는 곳을 그사이 다녀옴 -> 빈 초안에서 시작, 새 제안이 생기면 대체
                dropped, pending = pending, None
            else:
                draft.ops, draft.continued = pending["ops"], True
                if pending["request"]:  # 이어서 고친 제안은 원래 요청부터 누적 (다시 제안할 때 원래 의도 유지)
                    request = f"{pending['request']}\n{user_text}"
    toolbox = Toolbox(api, plan_id, draft, session_id)

    # 여행 중이면 동행 모드. '지금'은 trip 의 now (trip 도 safety 시각을 따른다), 아니면 safety 시계
    if fetch_trip:
        trip = await active_trip(api, plan_id)
    elif trip is not None and not trip.get("active"):
        trip = None
    if trip and (trip_now := clock.parse(trip.get("now"))):
        now, simulated = trip_now, trip.get("clock") == "sim"
    else:
        now, simulated = await clock.now(api)
    if trip:
        toolbox.trip = {"day": trip.get("day"), "now": f"{now:%H:%M}"}
    plan_view = await toolbox.current_view()  # 동행 모드면 실제 진행 기준 시각 (toolbox.trip 을 먼저 정한다)
    base_version = toolbox.last_plan["version"]

    history = await to_thread.run_sync(_load_history, session_id, settings.history_messages)
    review_on = settings.review_rounds > 0
    pushes_left = settings.review_rounds
    # 동행 모드 대화(선제 턴 제외)와 장소 맥락 질문은 현장 해설: 가이드 노트와 웹 검색(턴당 상한). 내장 검색이 없는 공급자는 docent 검색 함수 도구
    docent = (bool(trip) or place_id is not None) and not hidden
    place = {"place_id": place_id, "title": await place_title(api, toolbox.last_plan, place_id)} if place_id else None
    builtin_web = supports_web_search(llm)
    toolbox.web_limit = max(0, settings.web_search_per_turn) if docent else 0
    specs = tool_specs(review_on, proactive=hidden, guide=docent, web_functions=toolbox.web_limit > 0 and not builtin_web)
    memory, style_length = await memory_summary(api, plan_id)
    rules, context = build_system_parts(plan_view, pending, dropped, repropose, review_on, now=now,
                                        simulated=simulated, trip=trip, plan=toolbox.last_plan, memory=memory,
                                        docent=docent, web_searches=toolbox.web_limit, style_length=style_length,
                                        place=place)
    # 기록 편집 금지 공급자(Claude): 시스템 프롬프트는 고정 규칙만 두고(턴이 바뀌어도 같아 캐시된다), 턴마다 바뀌는
    # 맥락(현재 상황·메모리·지금 시각·현재 일정)은 이번 사용자 메시지 뒤 system 메시지로 둔다. 이후로는 덧붙이기만 한다
    append_only = getattr(llm, "append_only", False)
    if append_only:
        messages = [{"role": "system", "content": rules}, *history, {"role": "user", "content": user_text},
                    {"role": "system", "content": context}]
    else:
        messages = [{"role": "system", "content": build_system(
            plan_view, pending, dropped, repropose, review_on, now=now, simulated=simulated, trip=trip,
            plan=toolbox.last_plan, memory=memory, docent=docent, web_searches=toolbox.web_limit,
            style_length=style_length, place=place)},
            *history, {"role": "user", "content": user_text}]
    new_rows = [AgentMessage(session_id=session_id, role="user", content=user_text, hidden=hidden)]
    complete = 1  # new_rows[:complete] 는 저장해도 되는 완결된 기록 (도구 호출·결과 짝이 맞음)
    said: list[str] = []  # 이번 턴에 사용자에게 보인 말 (도구 호출 전 말 포함)
    steps = Steps()
    cards: list[dict] = []  # 이 턴에 만든 카드 [{"type", "id"}]
    saved = False

    def closing_rows(note: str | None = None) -> list[AgentMessage]:
        """저장할 행. 단계·카드는 마지막 assistant 행에 붙인다 (없거나 note 가 있으면 새 행)."""
        rows = new_rows[:complete]
        if note is not None or rows[-1].role != "assistant" or rows[-1].tool_calls:
            rows.append(AgentMessage(session_id=session_id, role="assistant", content=note or ""))
        rows[-1].steps = steps.items or None
        rows[-1].cards = cards or None
        for r in rows:  # 장소 맥락 질문: 그 턴의 사용자 메시지와 답 모두 (앱이 장소별로 걸러 보여준다)
            r.place_id = place_id
            if trigger and r.role == "assistant":  # 선제 턴: 답만 (숨김 '[이벤트]' 행·도구 결과는 아님)
                r.trigger = trigger
        return rows

    async def save(note: str | None = None) -> int | None:
        nonlocal saved
        saved = True
        return await to_thread.run_sync(_save, session_id, closing_rows(note))

    def edits_plan() -> bool:
        """이번 턴이 일정을 고치는 턴인가: 도구로 초안을 실제로 바꿨거나 선제 턴(이벤트 자체가 변경 요청).
        이어받은 대기 제안만 있는 턴(예: 동행 중 장소 질문)은 아니다 — 검토 관문·확정 변경 안내로 수정을 강요하지 않는다.
        사용자가 말로 변경을 요청하면 LLM 이 변경 도구를 쓰므로 draft.changed 로 잡힌다."""
        return draft.changed or hidden

    def changes_note() -> list[str]:
        """답변이 실제 초안과 어긋나지 않게, 일정을 고치는 턴에는 매 호출에 확정 변경 목록을 준다.
        기록에는 남기지 않는다."""
        if edits_plan() and toolbox.changes and draft.ops:
            left = [v["message"] for v in toolbox.last_plan["violations"] if v["code"] not in SKIP_CODES]
            return [CHANGES_NOTE.format(changes="\n".join(f"- {c}" for c in toolbox.changes),
                                        left="\n".join(f"- {m}" for m in left) or "없음")]
        if draft.changed or (hidden and step_no > 0):
            return [NO_CHANGES_NOTE]
        return []

    sent = False  # append_only: 첫 호출을 보냈는지

    def append_notes(notes: list[str]) -> None:
        """append_only: 이번 호출의 안내를 메시지 끝에 덧붙인다. 다음 사용자(도구 결과) 메시지가 오면 지워지는
        turn-scoped system 메시지라 모델은 가장 최근 것만 본다. 보낸 메시지는 고치지 않는다.
        system 메시지는 user 메시지 뒤에만 올 수 있어, 그 밖의 자리(빈 응답 뒤 다시 묻기)면 안내 없이 다시 보낸다."""
        if not notes:
            return
        text = "\n\n".join(notes)
        if messages[-1]["role"] in ("user", "tool"):
            messages.append({"role": "system", "content": text, "clear_at": "next_user_message"})
        elif not sent:  # 첫 호출 전: 아직 보내지 않은 맥락 메시지에 합친다
            messages[-1]["content"] += "\n\n" + text
        else:
            log.info("session=%s 안내를 붙일 자리가 없어 안내 없이 다시 호출", sid)

    usage = {"input": 0, "cached": 0, "output": 0, "reasoning": 0, "calls": 0}  # 이 턴의 LLM 토큰 사용량
    nudged = False
    extra: list[str] = []  # 이번 호출에만 붙이는 안내 (기록에는 남기지 않음)
    try:
        for step_no in range(settings.max_steps):
            # 마지막 단계에서는 도구 없이 호출해 반드시 답변으로 끝나게 한다
            tools = specs if step_no < settings.max_steps - 1 else []
            if not tools and step_no > 0:
                extra = [FINAL_STEP_NOTE]
            # 일정을 바꾼 턴에서 검토 관문이 남아 있으면, 이 단계의 텍스트는 바로 내보내지 않고 보관한다.
            # 끝내려는 답변이 검토에서 되돌려지면 그 답변은 사용자에게 보이지 않은 채 버려진다.
            gated = (review_on and edits_plan() and bool(draft.ops) and pushes_left > 0
                     and step_no < settings.max_steps - 2)
            notes = changes_note() + extra
            if append_only:
                # 웹 검색 도구는 턴 내내 그대로라(요청마다 max_uses = 턴 상한) 다 쓴 뒤로는 안내로 막는다
                if builtin_web and toolbox.web_limit and not toolbox.web_left:
                    notes.append(WEB_DONE_NOTE.format(n=toolbox.web_limit))
                append_notes(notes)
            for attempt in range(LLM_RETRIES + 1):
                text, calls, finish, reasoning_len, native = "", [], "", 0, None
                if append_only:
                    # 도구 목록(내장 웹 검색 도구와 그 상한 포함)은 턴 내내 같게, 마지막 단계는 도구를 못 쓰게만 한다
                    web = {"web_search": toolbox.web_limit} if builtin_web and toolbox.web_limit else {}
                    stream = llm.stream(messages, specs, allow_tools=bool(tools), **web)
                else:
                    # 공급자 내장 웹 검색: 남은 횟수만큼 (도구를 줄 때만)
                    web = {"web_search": toolbox.web_left} if builtin_web and tools and toolbox.web_left else {}
                    stream = llm.stream(messages + [{"role": "user", "content": n} for n in notes], tools, **web)
                sent = True
                try:
                    async for ev in stream:
                        match ev:
                            case TextDelta(text=t):
                                text += t
                                if not gated:
                                    yield _ev("text", delta=t)
                            case ReasoningDelta(text=t):
                                reasoning_len += len(t)
                                yield _ev("thinking", delta=t)
                            case WebSearchCall(query=q, results=found):
                                toolbox.web_used += 1
                                for r in found:  # 결과를 본 자료로 (Claude: 인용이 없는 답에서도 출처로 고를 수 있게)
                                    toolbox.add_source("W", r["title"], r["url"])
                                for e in steps.start("research"):
                                    yield e
                                yield steps.finish(True, f"웹 검색 · {q}"[:60] if q else "웹 검색")
                            case Citation(url=u, title=t):
                                toolbox.cite(u, t)
                            case ToolCall():
                                calls.append(ev)
                            case Finish(reason=r, usage=u):
                                finish, native = r, ev.native
                                usage["calls"] += 1
                                for k, v in (u or {}).items():
                                    usage[k] = usage.get(k, 0) + (v or 0)
                    break
                except LLMError as e:
                    # 로컬 모델은 가끔 형식이 깨진 도구 호출을 내서 서버 파서가 실패한다.
                    # 아직 답변 텍스트를 내보내지 않았으면 같은 단계를 다시 시도한다 (거절·SDK 가 이미 재시도한 오류는 제외).
                    log.warning("session=%s step=%d attempt=%d LLM 오류: %s", sid, step_no, attempt, e)
                    if not e.retryable or (text and not gated) or attempt == LLM_RETRIES:
                        # 공급자 원문(결제 URL 등)은 위 로그에만. 사용자·기록에는 고정 문구와 코드만
                        message = e.user_message
                        await save(message)
                        yield _error(e.code, message)
                        return

            log.info("session=%s step=%d finish=%s text=%d reasoning=%d tools=%s", sid, step_no, finish,
                     len(text), reasoning_len, [c.name for c in calls])
            extra = []
            if not tools:  # 도구를 주지 않았는데도 도구 호출을 내는 모델이 있다 -> 무시하고 답변으로 끝낸다
                calls = []
            if not text.strip() and not calls and tools and not nudged:
                # 생각만 하고 아무것도 내지 않고 끝나는 경우가 있다 -> 한 번만 되묻는다 (빈 응답은 기록에 넣지 않는다)
                nudged = True
                extra = [EMPTY_REPLY_NOTE]
                continue
            if gated and not calls:
                # 검토 관문: 일정을 바꾼 턴을 끝내기 전에 검토하고, 문제가 남았으면 답변을 보내지 않고 다시 고치게 한다
                for ev in steps.start("review"):
                    yield ev
                issues = await toolbox.review_now()
                yield steps.finish(True, summarize(issues))
                blocking = [i for i in issues if i["level"] in ("must", "should")]
                if blocking:
                    pushes_left -= 1
                    listing = "\n".join(f"- [{i['level']}] {i['message']}" for i in blocking)
                    if append_only:
                        # 되돌린 답변도 (사용자에게는 보내지 않은 채) 그대로 이어 두고, 검토 결과를 덧붙인다
                        messages.append({"role": "assistant", "content": text, **({"native": native} if native else {})})
                        messages.append({"role": "user", "content": REVIEW_NOTE + listing})
                    else:
                        extra = [REVIEW_NOTE + listing]
                    continue
            if gated and text:  # 보관했던 텍스트를 이제 내보낸다
                yield _ev("text", delta=text)
            if text.strip():
                said.append(text.strip())
            tool_calls = [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                          for c in calls]
            messages.append({"role": "assistant", "content": text,
                             **({"tool_calls": tool_calls} if tool_calls else {}),
                             **({"native": native} if native else {})})  # 공급자 원본 블록 (같은 턴에 그대로 다시 보낸다)
            new_rows.append(AgentMessage(session_id=session_id, role="assistant", content=text,
                                         tool_calls=tool_calls or None, hidden=bool(tool_calls) and not text.strip()))
            if not calls:
                complete = len(new_rows)
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
                new_rows.append(AgentMessage(session_id=session_id, role="tool",
                                             content=outcome.history or outcome.content,
                                             tool_call_id=c.id, tool_ok=outcome.ok, hidden=True))
            complete = len(new_rows)

        # 초안 -> 변경 제안
        proposal = None
        full_text = "\n\n".join(said)
        if draft.changed and draft.ops and not (await api.preview(plan_id, draft.ops, live=toolbox.live))["changes"]:
            draft.ops = []  # 고치다 보니 원래대로 돌아옴 -> 빈 제안을 만들지 않는다
        if draft.changed and draft.ops:
            for ev in steps.start("proposal", "잠시만요..."):
                yield ev
            try:
                proposal = await api.create_proposal(
                    plan_id, draft.ops, summary=full_text, request=request, origin=session_id,
                    supersedes=draft.base_proposal_id, expected_version=base_version, live=toolbox.live)
            except ApiError as e:
                yield steps.finish(False, "만들지 못함")
                if e.code == "version_conflict":
                    code, message = "plan_changed", "응답을 만드는 동안 일정이 바뀌어 변경안을 만들지 않았어요. 다시 요청해 주세요."
                elif e.code in ("supersede_conflict", "proposal_not_pending"):
                    code, message = "proposal_changed", ("이어서 고치던 제안이 그사이 승인되거나 거절돼서 이번 변경은 "
                                                         "제안으로 만들지 않았어요. 다시 요청해 주세요.")
                else:
                    code, message = e.code, f"변경안을 저장하지 못했습니다: {e.message}"
                await save(message)
                yield _error(code, message)
                return
            count = sum(1 for ch in proposal["changes"] if ch["kind"] != "day_end")
            yield steps.finish(True, f"변경 {count}건")
            cards.append({"type": "proposal", "id": proposal["id"]})
        elif draft.changed and draft.continued and draft.base_proposal_id:
            try:  # 이어받은 제안의 변경을 모두 취소하고 끝남 -> 그 제안 거절
                await api.reject_proposal(plan_id, draft.base_proposal_id)
            except ApiError as e:
                log.warning("session=%s 이어받은 제안 거절 실패: %s", sid, e)
        elif repropose and (repropose.get("base_changed") or repropose.get("progress_changed")):
            # 다시 제안했지만 새 변경이 없음 (원래 문제가 지금은 없는 등) -> 원래 제안은 승인할 수 없는 채 대기로 남지 않게 정리
            try:
                await api.reject_proposal(plan_id, repropose["id"])
            except ApiError as e:
                log.warning("session=%s 다시 제안 턴의 원래 제안 정리 실패: %s", sid, e)

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
            complete = len(new_rows)
        if docent and pending and not draft.changed and PENDING_NOTICE not in full_text:
            # 장소 질문 등으로 대기 제안을 건드리지 않은 동행 모드 턴: 제안이 아직 승인 대기 중임을 한 줄로 알린다
            yield _ev("text", delta="\n\n" + PENDING_NOTICE)
            full_text += "\n\n" + PENDING_NOTICE
            last_row = new_rows[complete - 1]  # 이 턴의 마지막 답변 행 (도구 호출 없는 assistant)
            last_row.content = (last_row.content.rstrip() + "\n\n" + PENDING_NOTICE).strip()

        # 카드 이벤트를 보내기 전에 기록부터 저장한다 (여기서 연결이 끊겨도 카드와 대화 기록이 어긋나지 않게)
        message_id = await save()
        if proposal:
            yield _ev("proposal", **proposal)
        if steps.summary:
            yield _ev("steps_done", count=len(steps.items), summary=steps.summary)
        log.info("session=%s mode=%s%s usage %s", sid, "guide" if trip else "plan",
                 f" place={place_id}" if place_id else "", usage)
        yield _ev("done", text=full_text, cards=cards, usage=usage, message_id=message_id,
                  mode="guide" if trip else "plan", trigger=trigger)
    finally:
        if not saved:
            # 연결 끊김(취소)이나 예상 못 한 예외: 여기서는 await 할 수 없어 짧게 동기로 저장한다
            try:
                _save(session_id, closing_rows("(응답이 중간에 끊겼어요)"))
            except Exception:
                log.exception("session=%s 중단된 턴 기록 저장 실패", sid)
