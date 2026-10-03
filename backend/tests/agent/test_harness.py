"""Agent 테스트 (명세: docs/agent/README.md). LLM 은 대본대로 움직이는 가짜, planner·places 는 같은 앱을 ASGI 로 호출.

    docker compose run --rm agent pytest tests/agent
"""
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.agent import router as agent_router
from app.agent.api import get_http
from app.agent.llm import Finish, TextDelta, ToolCall, get_llm
from app.agent.models import AgentMessage, AgentSession
from app.db import SessionLocal, init_db
from app.main import app
from app.planner.models import PlannerPlan
from app.planner.places import get_place_lookup
from app.planner.schemas import PlaceInfo

USER = "test-agent-user"
H = {"X-User-Id": USER}
PLACES = {p.id: p for p in [
    PlaceInfo(id="p-house", title="가옥거리", lat=35.9880, lng=129.5570),
    PlaceInfo(id="p-food", title="물회집", lat=35.9907, lng=129.5570),
    PlaceInfo(id="p-cafe", title="카페", lat=35.9894, lng=129.5570),
]}


class FakeLookup:
    def get(self, place_id):
        return PLACES.get(place_id)


class ScriptedLLM:
    """호출될 때마다 script 에서 하나씩 꺼내 응답.
    각 항목은 str(텍스트), (도구이름, 인자), async 함수(턴 도중 외부 사건), 예외(발생시킴)의 목록."""

    name = "fake"

    def __init__(self):
        self.script: list[list] = []
        self.calls: list[list[dict]] = []
        self.tools_seen: list[list[dict]] = []

    async def stream(self, messages, tools):
        self.calls.append(list(messages))
        self.tools_seen.append(tools)
        for n, part in enumerate(self.script.pop(0)):
            if callable(part):
                await part()
            elif isinstance(part, Exception):
                raise part
            elif isinstance(part, str):
                for ch in (part[:3], part[3:]):  # 조각으로 나눠 스트리밍 흉내
                    yield TextDelta(ch)
            else:
                name, args = part
                yield ToolCall(id=f"c{len(self.calls)}-{n}", name=name,
                               arguments=args if isinstance(args, str) else json.dumps(args))
        yield Finish("stop")


llm = ScriptedLLM()
asgi: httpx.AsyncClient | None = None


@pytest.fixture(scope="module")
def client():
    global asgi
    init_db()

    def cleanup():
        with SessionLocal.begin() as s:
            s.execute(delete(AgentSession).where(AgentSession.user_id.like("test-agent-%")))
            s.execute(delete(PlannerPlan).where(PlannerPlan.user_id.like("test-agent-%")))

    cleanup()
    asgi = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://app")
    app.dependency_overrides |= {get_place_lookup: FakeLookup, get_llm: lambda: llm, get_http: lambda: asgi}
    with TestClient(app) as c:
        yield c
    for dep in (get_place_lookup, get_llm, get_http):
        app.dependency_overrides.pop(dep, None)
    cleanup()


@pytest.fixture(autouse=True)
def _reset():
    llm.script, llm.calls, llm.tools_seen = [], [], []
    agent_router._active.clear()
    yield
    agent_router._active.clear()


# ---------- 도우미 ----------

def ok(resp, status=200):
    assert resp.status_code == status, resp.text
    return resp.json()


def err(resp, status: int, code: str) -> dict:
    assert resp.status_code == status, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == code and detail["message"], resp.text
    return detail


def sse(resp) -> list[tuple[str, dict]]:
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    out = []
    for block in resp.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n"))
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def chat(client, sid, message, script) -> list[tuple[str, dict]]:
    llm.script = script
    return sse(client.post(f"/agent/sessions/{sid}/chat", json={"message": message}, headers=H))


def last(events, name):
    return [d for e, d in events if e == name][-1]


def plan_of(client, pid):
    return ok(client.get(f"/planner/plans/{pid}", headers=H))


def places_in(plan, day=1):
    return [i["place_id"] for i in plan["days"][day - 1]["items"]]


@pytest.fixture()
def session(client):
    plan = ok(client.post("/planner/plans", json={"title": "t", "start_date": "2026-10-10", "day_start": "12:30",
                                                  "day_end": "18:00"}, headers=H), 201)
    ok(client.post(f"/planner/plans/{plan['id']}/items", json={"place_id": "p-house"}, headers=H), 201)
    s = ok(client.post("/agent/sessions", json={"plan_id": plan["id"]}, headers=H), 201)
    return s["id"], plan["id"]


# ---------- 스트림·단계 ----------

def test_stream_events_steps_and_proposal(client, session):
    sid, pid = session
    events = chat(client, sid, "점심 넣어줘", [
        ["알아볼게요", ("search_places", {"query": "물회"}), ("search_places", {"query": "회"})],
        [("add_item", {"place_id": "p-food", "stay_minutes": 50, "memo": "점심"})],
        ["물회집을 추가하는 걸 제안해요."],
    ])
    names = [e for e, _ in events]
    assert names[0] == "start" and names[-3:] == ["proposal", "steps_done", "done"]
    # status 는 단계 종류가 바뀔 때마다
    assert [d["text"] for e, d in events if e == "status"] == ["장소를 찾고 있어요.", "일정을 조정하고 있어요.",
                                                               "변경안을 만들고 있어요."]
    steps = [d for e, d in events if e == "step"]
    # 연속된 검색 두 번은 한 단계(s1)로 합쳐진다
    assert [(s["id"], s["kind"], s["status"]) for s in steps] == [
        ("s1", "search", "running"), ("s1", "search", "done"), ("s1", "search", "running"), ("s1", "search", "done"),
        ("s2", "edit", "running"), ("s2", "edit", "done"),
        ("s3", "proposal", "running"), ("s3", "proposal", "done")]
    assert steps[4]["title"] == "일정 조정" and steps[5]["detail"].startswith("물회집 추가")
    assert steps[6]["detail"] == "잠시만요..." and steps[7]["detail"] == "변경 1건"
    assert last(events, "steps_done") == {"count": 3, "summary": "3단계 완료 : 장소 찾기, 일정 조정, 변경안 만들기"}

    text = "".join(d["delta"] for e, d in events if e == "text")
    assert text == "알아볼게요\n\n물회집을 추가하는 걸 제안해요."
    prop = last(events, "proposal")
    assert prop["status"] == "pending" and prop["origin"] == sid and prop["request"] == "점심 넣어줘"
    assert prop["summary"] == "알아볼게요\n\n물회집을 추가하는 걸 제안해요."
    assert last(events, "done") == {"text": text, "cards": [{"type": "proposal", "id": prop["id"]}]}
    assert places_in(plan_of(client, pid)) == ["p-house"]  # 아직 반영 안 됨
    assert llm.calls[2][-1]["role"] == "tool" and "plan_after" in llm.calls[2][-1]["content"]  # add_item 결과


def test_tool_error_marks_step_error_and_turn_continues(client, session):
    sid, pid = session
    events = chat(client, sid, "x", [
        [("add_item", {"place_id": "nope"}), ("update_item", "{broken json"), ("no_such_tool", {})],
        ["장소를 찾지 못했어요."],
    ])
    steps = [d for e, d in events if e == "step"]
    assert steps[-1]["status"] == "error" and steps[-1]["kind"] == "edit"
    assert "proposal" not in dict(events) and last(events, "done")["cards"] == []
    tool_msgs = [m for m in llm.calls[-1] if m["role"] == "tool"]
    assert "place_not_found" in tool_msgs[0]["content"] and "알 수 없는 도구" in tool_msgs[2]["content"]


def test_answer_only_turn_has_no_steps(client, session):
    sid, _ = session
    events = chat(client, sid, "안녕", [["안녕하세요!"]])
    assert [e for e, _ in events] == ["start", "text", "text", "done"]


def test_empty_answer_gets_fallback_text(client, session):
    sid, _ = session
    events = chat(client, sid, "a", [[("add_item", {"place_id": "p-food"})], [""]])
    assert last(events, "done")["text"].startswith("변경안을 만들었어요")


def test_max_steps_forces_final_answer(client, session, monkeypatch):
    from app.agent.config import settings
    monkeypatch.setattr(settings, "max_steps", 2)
    sid, _ = session
    chat(client, sid, "a", [[("view_plan", {})], [("view_plan", {}), "끝"]])
    assert llm.tools_seen[0] and llm.tools_seen[1] == []  # 마지막 단계는 도구 없이 (도구 호출해도 무시)


def test_non_stream_result(client, session):
    sid, _ = session
    llm.script = [[("search_places", {"query": "물회"}), ("add_item", {"place_id": "p-food"})], ["추가 제안"]]
    r = ok(client.post(f"/agent/sessions/{sid}/chat", params={"stream": "false"}, json={"message": "a"}, headers=H))
    assert r["text"] == "추가 제안" and r["error"] is None
    assert [s["kind"] for s in r["steps"]] == ["search", "edit", "proposal"]
    assert r["steps_summary"].startswith("3단계 완료")
    assert r["cards"][0]["type"] == "proposal" and r["cards"][0]["proposal"]["status"] == "pending"


# ---------- 변경 제안 ----------

def test_continue_pending_proposal_and_supersede(client, session):
    sid, pid = session
    p1 = last(chat(client, sid, "점심", [[("add_item", {"place_id": "p-food", "stay_minutes": 50})], ["제안"]]),
              "proposal")
    turn = len(llm.calls)
    events = chat(client, sid, "짧게", [[("update_item", {"item_id": -1, "stay_minutes": 30})], ["줄였어요"]])
    p2 = last(events, "proposal")
    assert any("물회집" in c["message"] and "30분" in c["message"] for c in p2["changes"])
    assert ok(client.get(f"/planner/plans/{pid}/proposals/{p1['id']}", headers=H))["status"] == "superseded"
    assert "승인 대기 중인 제안" in llm.calls[turn][0]["content"]


def test_fixed_start_time_survives_continued_proposal(client, session):
    sid, pid = session
    house = plan_of(client, pid)["days"][0]["items"][0]["id"]
    ok(client.patch(f"/planner/plans/{pid}/items/{house}", json={"start_time": "13:00"}, headers=H))
    chat(client, sid, "짧게", [[("update_item", {"item_id": house, "stay_minutes": 30})], ["줄였어요"]])
    p2 = last(chat(client, sid, "점심", [[("add_item", {"place_id": "p-food"})], ["추가"]]), "proposal")
    assert not any("시작 고정" in c["message"] for c in p2["changes"])
    result = ok(client.post(f"/agent/sessions/{sid}/proposals/{p2['id']}/accept", headers=H))
    assert result["plan"]["days"][0]["items"][0]["start_time"] == "13:00"


def test_pending_with_changed_base_is_dropped_then_superseded(client, session):
    sid, pid = session
    p1 = last(chat(client, sid, "점심", [[("add_item", {"place_id": "p-food"})], ["제안"]]), "proposal")
    ok(client.post(f"/planner/plans/{pid}/items", json={"place_id": "p-cafe"}, headers=H), 201)  # 직접 편집
    turn = len(llm.calls)
    p2 = last(chat(client, sid, "다시", [[("add_item", {"place_id": "p-food"})], ["새 제안"]]), "proposal")
    system = llm.calls[turn][0]["content"]
    assert "기준이 다르다" in system and "승인 대기 중인 제안" not in system
    assert [c["kind"] for c in p2["changes"]].count("added") == 1  # 이전 ops 를 이어받지 않음
    assert ok(client.get(f"/planner/plans/{pid}/proposals/{p1['id']}", headers=H))["status"] == "superseded"


def test_discard_rejects_continued_proposal(client, session):
    sid, pid = session
    p1 = last(chat(client, sid, "a", [[("add_item", {"place_id": "p-food"})], ["추가"]]), "proposal")
    events = chat(client, sid, "취소", [[("discard_changes", {})], ["취소했어요"]])
    assert "proposal" not in dict(events)
    assert ok(client.get(f"/planner/plans/{pid}/proposals/{p1['id']}", headers=H))["status"] == "rejected"


def test_accept_auto_records_note(client, session):
    sid, pid = session
    p = last(chat(client, sid, "점심", [[("add_item", {"place_id": "p-food"})], ["제안"]]), "proposal")
    r = ok(client.post(f"/agent/sessions/{sid}/proposals/{p['id']}/accept", headers=H))
    assert r["proposal"]["decision"] == "applied" and places_in(r["plan"]) == ["p-house", "p-food"]
    chat(client, sid, "고마워", [["네!"]])
    assert any(m["role"] == "user" and "[알림]" in m["content"] and "승인" in m["content"] for m in llm.calls[-1])
    err(client.post(f"/agent/sessions/{sid}/proposals/{p['id']}/reject", headers=H), 409, "proposal_not_pending")


def test_accept_base_changed_passthrough_then_overwrite(client, session):
    sid, pid = session
    p = last(chat(client, sid, "점심", [[("add_item", {"place_id": "p-food"})], ["제안"]]), "proposal")
    ok(client.post(f"/planner/plans/{pid}/items", json={"place_id": "p-cafe"}, headers=H), 201)
    d = err(client.post(f"/agent/sessions/{sid}/proposals/{p['id']}/accept", headers=H), 409, "base_changed")
    assert d["since_proposal"] and d["overwrite_effect"] and d["base_version"] < d["current_version"]
    r = ok(client.post(f"/agent/sessions/{sid}/proposals/{p['id']}/accept", json={"mode": "overwrite"}, headers=H))
    assert r["proposal"]["decision"] == "overwritten" and places_in(r["plan"]) == ["p-house", "p-food"]
    err(client.post(f"/agent/sessions/{sid}/proposals/{p['id']}/accept", json={"mode": "x"}, headers=H),
        422, "invalid_request")


def test_reject(client, session):
    sid, pid = session
    p = last(chat(client, sid, "점심", [[("add_item", {"place_id": "p-food"})], ["제안"]]), "proposal")
    assert ok(client.post(f"/agent/sessions/{sid}/proposals/{p['id']}/reject", headers=H))["status"] == "rejected"
    assert places_in(plan_of(client, pid)) == ["p-house"]


def test_repropose(client, session):
    sid, pid = session
    p1 = last(chat(client, sid, "점심 넣어줘", [[("add_item", {"place_id": "p-food"})], ["제안"]]), "proposal")
    ok(client.post(f"/planner/plans/{pid}/items", json={"place_id": "p-cafe"}, headers=H), 201)
    turn = len(llm.calls)
    llm.script = [[("add_item", {"place_id": "p-food", "order": 1})], ["다시 만들었어요"]]
    events = sse(client.post(f"/agent/sessions/{sid}/proposals/{p1['id']}/repropose", headers=H))
    p2 = last(events, "proposal")
    assert p2["request"] == "점심 넣어줘" and p2["base_changed"] is False
    assert ok(client.get(f"/planner/plans/{pid}/proposals/{p1['id']}", headers=H))["status"] == "superseded"
    system, user_msg = llm.calls[turn][0]["content"], llm.calls[turn][-1]["content"]
    assert "다시 제안 요청" in system and "점심 넣어줘" in system
    assert user_msg == "이전 제안을 현재 일정에 맞춰 다시 만들어줘."
    err(client.post(f"/agent/sessions/{sid}/proposals/{p1['id']}/repropose", headers=H), 409, "proposal_not_pending")


def test_plan_changed_during_turn(client, session):
    sid, pid = session

    async def edit_elsewhere():  # 턴 도중 다른 경로(앱 직접 편집)로 일정이 바뀜
        r = await asgi.post(f"/planner/plans/{pid}/items", json={"place_id": "p-cafe"}, headers=H)
        assert r.status_code == 201
    events = chat(client, sid, "a", [[edit_elsewhere, ("add_item", {"place_id": "p-food"})], ["제안"]])
    assert events[-1][0] == "error" and events[-1][1]["code"] == "plan_changed"
    assert ok(client.get(f"/planner/plans/{pid}/proposals", headers=H)) == []


def test_proposal_decided_during_turn_is_not_revived(client, session):
    sid, pid = session
    p1 = last(chat(client, sid, "점심", [[("add_item", {"place_id": "p-food"})], ["추가"]]), "proposal")

    async def reject_elsewhere():  # planner API 로 바로 거절 (Agent 잠금을 거치지 않는 경로)
        r = await asgi.post(f"/planner/plans/{pid}/proposals/{p1['id']}/reject", headers=H)
        assert r.status_code == 200
    events = chat(client, sid, "또", [[reject_elsewhere, ("add_item", {"place_id": "p-cafe"})], ["또 추가"]])
    assert events[-1][1]["code"] == "proposal_changed"
    assert all(p["status"] != "pending" for p in ok(client.get(f"/planner/plans/{pid}/proposals", headers=H)))


# ---------- 장소 추천 ----------

def test_recommend_places_card_and_actions(client, session):
    sid, pid = session
    events = chat(client, sid, "카페 추천해줘", [
        [("search_places", {"query": "카페"})],
        [("recommend_places", {"title": "쉬어갈 카페", "candidates": [
            {"place_id": "p-cafe", "day": 1, "time_hint": "14:00", "stay_minutes": 30, "reason": "바다 전망"},
            {"place_id": "p-food", "reason": "점심", "time_hint": None}]})],
        ["두 곳을 골라봤어요."],
    ])
    names = [e for e, _ in events]
    assert names.index("recommendation") < names.index("done") and "proposal" not in names
    rec = last(events, "recommendation")
    assert rec["title"] == "쉬어갈 카페" and rec["origin"] == sid and len(rec["candidates"]) == 2
    assert [c["state"] for c in rec["candidates"]] == ["not_in_plan", "not_in_plan"]
    assert last(events, "done")["cards"] == [{"type": "recommendation", "id": rec["id"]}]
    assert [d["kind"] for e, d in events if e == "step" and d["status"] == "done"] == ["search", "recommend"]

    cafe = rec["candidates"][0]["id"]
    url = f"/agent/sessions/{sid}/recommendations/{rec['id']}/candidates/{cafe}"
    r = ok(client.post(f"{url}/add", headers=H))
    assert r["recommendation"]["candidates"][0]["state"] == "in_plan" and "p-cafe" in places_in(r["plan"])
    err(client.post(f"{url}/add", headers=H), 409, "already_in_plan")
    r = ok(client.post(f"{url}/remove", headers=H))
    assert r["recommendation"]["candidates"][0]["state"] == "not_in_plan"
    err(client.post(f"{url}/remove", headers=H), 409, "not_in_plan")
    chat(client, sid, "고마워", [["네"]])
    notes = [m["content"] for m in llm.calls[-1] if m["role"] == "user" and "[알림]" in m["content"]]
    assert any("추가했습니다" in n for n in notes) and any("뺐습니다" in n for n in notes)


def test_recommend_invalid_candidates_is_tool_error(client, session):
    sid, _ = session
    events = chat(client, sid, "추천", [[("recommend_places", {"title": "t", "candidates": [{"place_id": "nope"}]})],
                                       ["못 찾았어요"]])
    assert "recommendation" not in dict(events)
    assert [d for e, d in events if e == "step"][-1]["status"] == "error"


# ---------- 세션 조회 ----------

def test_session_detail_cards_reflect_current_state(client, session):
    sid, pid = session
    p = last(chat(client, sid, "점심", [["알아볼게요", ("add_item", {"place_id": "p-food"})], ["제안해요"]]), "proposal")
    rec = last(chat(client, sid, "카페", [[("recommend_places", {"title": "카페",
                                                                  "candidates": [{"place_id": "p-cafe"}]})], ["골라보세요"]]),
               "recommendation")
    ok(client.post(f"/agent/sessions/{sid}/proposals/{p['id']}/accept", headers=H))
    ok(client.post(f"/agent/sessions/{sid}/recommendations/{rec['id']}/candidates/{rec['candidates'][0]['id']}/add",
                   headers=H))

    detail = ok(client.get(f"/agent/sessions/{sid}", headers=H))
    msgs = detail["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]  # 숨김 알림·도구 기록 제외
    first = msgs[1]
    assert first["content"] == "알아볼게요\n\n제안해요"
    assert [s["kind"] for s in first["steps"]] == ["edit", "proposal"]
    assert first["steps_summary"] == "2단계 완료 : 일정 조정, 변경안 만들기"
    assert first["cards"][0]["type"] == "proposal" and first["cards"][0]["proposal"]["status"] == "accepted"
    assert msgs[3]["cards"][0]["recommendation"]["candidates"][0]["state"] == "in_plan"


def test_history_keeps_short_tool_results(client, session):
    sid, _ = session
    chat(client, sid, "a", [[("add_item", {"place_id": "p-food", "start_time": "12:00"})], ["추가"]])
    assert "start_time" in llm.calls[-1][-1]["content"]  # 무시한 인자를 LLM 에 알려줌
    with SessionLocal() as db:
        tool_rows = db.scalars(select(AgentMessage).where(AgentMessage.session_id == sid,
                                                          AgentMessage.role == "tool")).all()
    assert tool_rows and all("plan_after" not in m.content for m in tool_rows)


# ---------- 접근·잠금·오류 ----------

def test_card_ownership(client, session):
    sid, pid = session
    p = last(chat(client, sid, "a", [[("add_item", {"place_id": "p-food"})], ["추가"]]), "proposal")
    other = ok(client.post("/agent/sessions", json={"plan_id": pid}, headers=H), 201)["id"]
    err(client.post(f"/agent/sessions/{other}/proposals/{p['id']}/accept", headers=H), 404, "proposal_not_found")
    err(client.post(f"/agent/sessions/{other}/proposals/999999/reject", headers=H), 404, "proposal_not_found")
    err(client.get(f"/agent/sessions/{sid}", headers={"X-User-Id": "test-agent-other"}), 404, "session_not_found")
    err(client.post(f"/agent/sessions/{sid}/proposals/{p['id']}/accept", headers={"X-User-Id": "test-agent-other"}),
        404, "session_not_found")
    rec = ok(client.post(f"/planner/plans/{pid}/recommendations",
                         json={"title": "앱이 직접 만든 추천", "candidates": [{"place_id": "p-cafe", "day": 1}]}, headers=H), 201)
    err(client.post(f"/agent/sessions/{sid}/recommendations/{rec['id']}/candidates/{rec['candidates'][0]['id']}/add",
                    headers=H), 404, "recommendation_not_found")


def test_busy_blocks_chat_and_card_actions(client, session):
    sid, pid = session
    p = last(chat(client, sid, "a", [[("add_item", {"place_id": "p-food"})], ["추가"]]), "proposal")
    agent_router._active[sid] = time.monotonic()
    base = f"/agent/sessions/{sid}"
    err(client.post(f"{base}/chat", json={"message": "b"}, headers=H), 409, "agent_busy")
    err(client.post(f"{base}/proposals/{p['id']}/accept", headers=H), 409, "agent_busy")
    err(client.post(f"{base}/proposals/{p['id']}/reject", headers=H), 409, "agent_busy")
    err(client.post(f"{base}/proposals/{p['id']}/repropose", headers=H), 409, "agent_busy")
    err(client.post(f"{base}/recommendations/1/candidates/1/add", headers=H), 409, "agent_busy")
    agent_router._active[sid] = time.monotonic() - 16 * 60  # 오래된 잠금은 끝난 것으로 본다
    ok(client.post(f"{base}/proposals/{p['id']}/accept", headers=H))


def test_unexpected_error_becomes_error_event_and_releases_session(client, session):
    sid, _ = session
    events = chat(client, sid, "a", [[RuntimeError("boom")]])
    assert events[-1] == ("error", {"code": "internal_error", "message": "서버 오류: RuntimeError"})
    assert last(chat(client, sid, "b", [["다시 정상"]]), "done")


def test_llm_error_event(client, session):
    from app.agent.llm import LLMError
    sid, _ = session
    events = chat(client, sid, "a", [[LLMError("x")], [LLMError("x")], [LLMError("x")]])  # 재시도 2번 후 실패
    assert events[-1][1]["code"] == "llm_failed" and len(llm.calls) == 3


@pytest.mark.parametrize("body", [{}, {"message": ""}, {"message": "   "}, {"message": "x" * 4001},
                                  {"message": "hi", "extra": 1}])
def test_chat_body_validation(client, session, body):
    sid, _ = session
    err(client.post(f"/agent/sessions/{sid}/chat", json=body, headers=H), 422, "invalid_request")


def test_session_create_errors(client):
    err(client.post("/agent/sessions", json={"plan_id": 999999}, headers=H), 404, "plan_not_found")
    err(client.post("/agent/sessions", json={"plan_id": "x"}, headers=H), 422, "invalid_request")
    err(client.post("/agent/sessions", json={"plan_id": 1}), 422, "invalid_request")  # 헤더 없음
    err(client.post("/agent/sessions/nope/chat", json={"message": "a"}, headers=H), 404, "session_not_found")
