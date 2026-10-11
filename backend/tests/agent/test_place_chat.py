"""장소 맥락이 있는 질문 (ChatRequest.place_id, 앱 가이드 노트 화면. docs/agent/README.md §3·§6).

가짜 LLM(대본)·각본 LLM + 가짜 places 근거 조각·장소 상세(test_docent_tools.Upstream). 노트 저장은 같은 앱의 docent.

    docker compose run --rm agent pytest tests/agent/test_place_chat.py
"""
import pytest
from sqlalchemy import select

from app.agent.llm import get_llm
from app.agent.models import AgentMessage
from app.agent.scripted_llm import DEFAULT_DIR, Context, ScriptedProvider
from app.db import SessionLocal
from app.main import app
from tests.agent import test_harness as th
from tests.agent.test_docent_tools import NOTE, new_session, steps_of, tool_names, tool_results, trip_state, up, user  # noqa: F401
from tests.agent.test_harness import _reset, client, last, ok, sse  # noqa: F401  (픽스처)

PLACE_LINE = "## 지금 보는 장소\n사용자가 '가옥거리'(place_id p-house) 가이드 노트를 보며 묻는다."


def ask(client, h, sid, message, script=None, place_id=None):  # noqa: F811
    if script is not None:
        th.llm.script = script
    body = {"message": message} | ({"place_id": place_id} if place_id else {})
    return sse(client.post(f"/agent/sessions/{sid}/chat", json=body, headers=h))


def put_note(client, h, pid):  # noqa: F811
    ok(client.put(f"/docent/plans/{pid}/notes/p-house", json={
        "status": "ready", **{k: NOTE[k] for k in ("short", "long", "for_you")},
        "sources": [{"title": "포항시", "url": "https://www.pohang.go.kr/a"}]}, headers=h), 201)


def detail(client, h, sid):  # noqa: F811
    return ok(client.get(f"/agent/sessions/{sid}", headers=h))["messages"]


def test_place_question_context_tools_and_message_place_id(client, up, user):  # noqa: F811
    sid, pid = new_session(client, user)
    put_note(client, user, pid)
    events = ask(client, user, sid, "여기 왜 유명해요?", [
        [("get_guide_note", {"place_id": "p-house"}), ("search_passages", {"query": "가옥거리", "place_id": "p-house"})],
        ["백 년 전 일본 어민들이 살던 거리예요."]], place_id="p-house")
    assert last(events, "done")["mode"] == "plan"  # 여행 전이어도
    system = th.llm.calls[0][0]["content"]
    assert PLACE_LINE in system and "get_guide_note(이 place_id)" in system and "## 현장 해설" in system
    assert "get_guide_note" in tool_names(th.llm.tools_seen[0])  # 여행 중이 아니어도 노트 도구
    assert th.llm.calls[0][-1] == {"role": "user", "content": "여기 왜 유명해요?"}  # 맥락은 사용자 메시지에 섞지 않는다
    note = tool_results(th.llm.calls[1])[0]
    assert note["short"] == NOTE["short"]
    assert [s["kind"] for s in steps_of(events)] == ["guide", "research"]
    msgs = detail(client, user, sid)
    assert [(m["role"], m["content"], m["place_id"]) for m in msgs] == [
        ("user", "여기 왜 유명해요?", "p-house"), ("assistant", "백 년 전 일본 어민들이 살던 거리예요.", "p-house")]
    with SessionLocal() as db:  # 도구 호출·결과 행까지 그 턴의 모든 행에 남는다. 맥락 줄은 기록에 없다
        rows = list(db.scalars(select(AgentMessage).where(AgentMessage.session_id == sid)))
    assert {r.place_id for r in rows} == {"p-house"} and all("지금 보는 장소" not in r.content for r in rows)

    # 다음 턴(place_id 없음)은 같은 세션에서 이어지고 맥락 줄이 없다
    ask(client, user, sid, "고마워", [["천만에요."]])
    assert "## 지금 보는 장소" not in th.llm.calls[-1][0]["content"]
    assert "여기 왜 유명해요?" in [m["content"] for m in th.llm.calls[-1]]  # 이전 장소 질문도 기록으로 안다
    assert [m["place_id"] for m in detail(client, user, sid)] == ["p-house", "p-house", None, None]


def test_chat_without_place_id_unchanged(client, up, user):  # noqa: F811
    sid, _ = new_session(client, user)
    events = ask(client, user, sid, "안녕", [["안녕하세요."]])
    assert last(events, "done")["mode"] == "plan"
    system = th.llm.calls[0][0]["content"]
    assert "## 지금 보는 장소" not in system and "## 현장 해설" not in system
    assert "get_guide_note" not in tool_names(th.llm.tools_seen[0])
    assert [m["place_id"] for m in detail(client, user, sid)] == [None, None]


def test_place_question_during_trip_and_unknown_place(client, up, user):  # noqa: F811
    sid, pid = new_session(client, user)
    up.trip = trip_state(client, user, pid)
    ask(client, user, sid, "여긴 뭐가 유명해?", [["카페 이야기"]], place_id="p-cafe")  # 일정에 없는 장소 -> places 상세의 이름
    system = th.llm.calls[0][0]["content"]
    assert "## 현재 상황 (여행 중)" in system and "사용자가 '카페'(place_id p-cafe)" in system
    assert system.count("## 현장 해설") == 1


@pytest.mark.parametrize("body", [{"message": "x", "place_id": ""}, {"message": "x", "place_id": "p" * 65}])
def test_place_id_validation(client, up, user, body):  # noqa: F811
    sid, _ = new_session(client, user)
    resp = client.post(f"/agent/sessions/{sid}/chat", json=body, headers=user)
    assert resp.status_code == 422 and resp.json()["detail"]["code"] == "invalid_request"


# ---------- 각본 LLM ----------

@pytest.fixture()
def demo_scripts(client):  # noqa: F811
    provider = ScriptedProvider(DEFAULT_DIR, delay_ms=0, latency_ms=0)
    app.dependency_overrides[get_llm] = lambda: provider
    yield provider
    app.dependency_overrides[get_llm] = lambda: th.llm


def test_scripted_place_question(client, up, user, demo_scripts):  # noqa: F811
    sid, pid = new_session(client, user)
    put_note(client, user, pid)
    events = ask(client, user, sid, "이 거리는 언제 생겼어요?", place_id="p-house")
    steps = steps_of(events)
    assert [s["kind"] for s in steps] == ["guide", "research"] and steps[0]["detail"] == "준비됨"
    assert last(events, "done")["text"] == f"**가옥거리** 이야기예요.\n\n{NOTE['short']}"
    # 노트가 없으면 근거 조각으로 (결정적)
    sid2, _ = new_session(client, user)
    text = last(ask(client, user, sid2, "이 거리는 언제 생겼어요?", place_id="p-house"), "done")["text"]
    assert text.startswith("**가옥거리** 이야기예요.\n\n1900년대 초 일본 어민들이")
    assert [m["place_id"] for m in detail(client, user, sid2)] == ["p-house", "p-house"]


def test_scripted_place_context_detection_and_change_request():
    p = ScriptedProvider(DEFAULT_DIR, delay_ms=0, latency_ms=0)
    sys = {"role": "system", "content": "규칙\n\n" + PLACE_LINE + " 먼저 …"}
    ctx = Context([sys, {"role": "user", "content": "여기 사진 찍기 좋은 곳?"}])
    assert (ctx.place, ctx.place_title, ctx.fill("{{place:title}}")) == ("p-house", "가옥거리", "가옥거리")
    assert ctx.fill("{{last:get_guide_note.short||place:id}}") == "p-house"
    assert p.pick(ctx)["id"] == "place_question"
    # 같은 문장이 일반 각본(장면 ② 가옥거리 질문)에도 맞지만 장소 각본이 먼저
    assert p.pick(Context([sys, {"role": "user", "content": "가옥거리는 언제 지어졌어?"}]))["id"] == "place_question"
    # 일정 변경 요청은 장소 각본이 아니다 (평소처럼 제안 카드)
    change = p.pick(Context([sys, {"role": "user", "content": "여기 체류 시간 늘려줘"}]))
    assert change is None or change["id"] != "place_question"
    # place_id 없는 대화는 그대로
    plain = Context([{"role": "system", "content": "규칙"}, {"role": "user", "content": "가옥거리는 언제 지어졌어?"}])
    assert plain.place is None and p.pick(plain)["id"] != "place_question"
