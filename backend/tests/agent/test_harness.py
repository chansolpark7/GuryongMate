"""Agent 하네스 테스트. LLM 은 대본대로 움직이는 가짜, planner·places 는 같은 앱을 ASGI 로 호출한다.

    docker compose run --rm api pytest tests/agent
"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.agent.api import get_http
from app.agent.llm import Finish, TextDelta, ToolCall, get_llm
from app.agent.models import AgentSession
from app.db import SessionLocal, init_db
from app.main import app
from app.planner.models import PlannerPlan
from app.planner.places import get_place_lookup
from app.planner.schemas import PlaceInfo

USER = "test-agent-user"
H = {"X-User-Id": USER}
PLACES = {
    "p-house": PlaceInfo(id="p-house", title="가옥거리", lat=35.9880, lng=129.5570),
    "p-food": PlaceInfo(id="p-food", title="물회집", lat=35.9907, lng=129.5570),
}


class FakeLookup:
    def get(self, place_id):
        return PLACES.get(place_id)


class ScriptedLLM:
    """호출될 때마다 script 에서 하나씩 꺼내 응답.
    각 항목은 str(텍스트), (도구이름, 인자dict), async 함수(턴 도중 외부 사건), 예외(발생시킴)의 목록."""

    name = "fake"

    def __init__(self):
        self.script: list[list] = []
        self.calls: list[list[dict]] = []
        self.tools_seen: list[list[dict]] = []

    async def stream(self, messages, tools):
        self.calls.append(list(messages))
        self.tools_seen.append(tools)
        for n, part in enumerate(self.script.pop(0)):
            if callable(part):  # 턴 도중에 일어나는 외부 사건 (예: 다른 화면에서 제안 승인)
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
    init_db()

    def cleanup():
        with SessionLocal.begin() as s:
            s.execute(delete(AgentSession).where(AgentSession.user_id.like("test-%")))
            s.execute(delete(PlannerPlan).where(PlannerPlan.user_id.like("test-%")))

    cleanup()
    global asgi
    asgi = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://app")
    app.dependency_overrides |= {get_place_lookup: FakeLookup, get_llm: lambda: llm, get_http: lambda: asgi}
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    cleanup()


def ok(resp, status=200):
    assert resp.status_code == status, resp.text
    return resp.json()


def sse(resp) -> list[tuple[str, dict]]:
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    out = []
    for block in resp.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n"))
        out.append((lines["event"], json.loads(lines["data"])))
    return out


@pytest.fixture()
def session(client):
    plan = ok(client.post("/planner/plans", json={"title": "t", "start_date": "2026-10-10", "day_start": "12:30",
                                                  "day_end": "18:00"}, headers=H), 201)
    ok(client.post(f"/planner/plans/{plan['id']}/items", json={"place_id": "p-house"}, headers=H), 201)
    s = ok(client.post("/agent/sessions", json={"plan_id": plan["id"]}, headers=H), 201)
    return s["id"], plan["id"]


def test_turn_streams_events_and_creates_proposal(client, session):
    sid, pid = session
    llm.script = [
        ["알아볼게요", ("add_item", {"place_id": "p-food", "stay_minutes": 50, "memo": "점심"})],
        ["물회집을 추가하는 걸 제안해요."],
    ]
    events = sse(client.post(f"/agent/sessions/{sid}/chat", json={"message": "점심 넣어줘"}, headers=H))
    names = [e for e, _ in events]
    assert names[0] == "start" and names[-1] == "done"
    assert names.index("tool_call") < names.index("tool_result") < names.index("proposal") < names.index("done")
    text = "".join(d["delta"] for e, d in events if e == "text")
    assert text == "알아볼게요\n\n물회집을 추가하는 걸 제안해요."

    proposal = dict(events)["proposal"]
    assert proposal["status"] == "pending" and proposal["origin"] == sid
    assert [c["kind"] for c in proposal["changes"]][0] == "added"
    assert dict(events)["done"]["proposal_id"] == proposal["id"]
    # 아직 반영 안 됨
    assert len(ok(client.get(f"/planner/plans/{pid}", headers=H))["days"][0]["items"]) == 1

    # 두 번째 LLM 호출에는 도구 결과(미리보기 일정)가 들어가 있다
    tool_msg = llm.calls[1][-1]
    assert tool_msg["role"] == "tool" and "plan_after" in tool_msg["content"]

    # 이어서 수정 -> 이전 제안 위에 쌓이고 이전 제안은 superseded
    llm.script = [[("update_item", {"item_id": -1, "stay_minutes": 30})], ["30분으로 줄였어요."]]
    events = dict(sse(client.post(f"/agent/sessions/{sid}/chat", json={"message": "짧게"}, headers=H)))
    p2 = events["proposal"]
    assert any("물회집" in c["message"] and "30분" in c["message"] for c in p2["changes"])
    assert ok(client.get(f"/planner/plans/{pid}/proposals/{proposal['id']}", headers=H))["status"] == "superseded"
    assert "승인 대기 중인 제안" in llm.calls[-2][0]["content"]  # 시스템 프롬프트에 대기 제안 표시

    # 승인 -> 반영, 다음 턴 LLM 이 알 수 있게 알림 기록
    result = ok(client.post(f"/agent/sessions/{sid}/proposals/{p2['id']}/accept", headers=H))
    items = result["plan"]["days"][0]["items"]
    assert [i["place_id"] for i in items] == ["p-house", "p-food"] and items[1]["stay_minutes"] == 30

    detail = ok(client.get(f"/agent/sessions/{sid}", headers=H))
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant", "user", "assistant"]
    assert detail["messages"][1]["content"] == "알아볼게요\n\n물회집을 추가하는 걸 제안해요."  # 스트림과 같은 한 말풍선
    assert detail["pending_proposal"] is None

    llm.script = [["네!"]]
    sse(client.post(f"/agent/sessions/{sid}/chat", json={"message": "고마워"}, headers=H))
    history = llm.calls[-1]
    assert any(m["role"] == "user" and "승인" in m["content"] for m in history)


def test_tool_errors_are_returned_to_llm(client, session):
    sid, pid = session
    llm.script = [
        [("add_item", {"place_id": "nope"}), ("update_item", "{broken json"), ("no_such_tool", {})],
        ["장소를 찾지 못했어요."],
    ]
    events = sse(client.post(f"/agent/sessions/{sid}/chat", json={"message": "x"}, headers=H))
    results = [d for e, d in events if e == "tool_result"]
    assert [r["ok"] for r in results] == [False, False, False]
    assert "proposal" not in dict(events)
    tool_msgs = [m for m in llm.calls[-1] if m["role"] == "tool"]
    assert "장소 'nope'" in tool_msgs[0]["content"]


def test_non_stream_and_discard(client, session):
    sid, pid = session
    llm.script = [[("add_item", {"place_id": "p-food"})], ["추가 제안"]]
    r = ok(client.post(f"/agent/sessions/{sid}/chat", params={"stream": "false"}, json={"message": "a"}, headers=H))
    assert r["text"] == "추가 제안" and r["proposal"]["status"] == "pending"
    assert r["tool_calls"][0]["name"] == "add_item" and r["tool_calls"][0]["ok"]
    first = r["proposal"]["id"]

    llm.script = [[("discard_changes", {})], ["취소했어요"]]
    r = ok(client.post(f"/agent/sessions/{sid}/chat", params={"stream": "false"}, json={"message": "취소"}, headers=H))
    assert r["proposal"] is None
    assert ok(client.get(f"/planner/plans/{pid}/proposals/{first}", headers=H))["status"] == "rejected"


def test_max_steps_forces_final_answer(client, session, monkeypatch):
    from app.agent.config import settings
    monkeypatch.setattr(settings, "max_steps", 2)
    sid, _ = session
    llm.script = [[("view_plan", {})], ["끝"]]
    sse(client.post(f"/agent/sessions/{sid}/chat", json={"message": "a"}, headers=H))
    assert llm.tools_seen[-2] and llm.tools_seen[-1] == []  # 마지막 단계는 도구 없이


def test_session_access(client, session):
    sid, _ = session
    assert client.get(f"/agent/sessions/{sid}", headers={"X-User-Id": "test-other"}).status_code == 404
    assert client.post("/agent/sessions", json={"plan_id": 999999}, headers=H).status_code == 404


def _first_item(client, pid):
    return ok(client.get(f"/planner/plans/{pid}", headers=H))["days"][0]["items"][0]


def test_fixed_start_time_survives_continued_proposal(client, session):
    """대기 제안을 이어서 고칠 때 생략된 start_time 이 null(해제)로 바뀌면 안 된다."""
    sid, pid = session
    house = _first_item(client, pid)["id"]
    ok(client.patch(f"/planner/plans/{pid}/items/{house}", json={"start_time": "13:00"}, headers=H))
    llm.script = [[("update_item", {"item_id": house, "stay_minutes": 30})], ["줄였어요"]]
    sse(client.post(f"/agent/sessions/{sid}/chat", json={"message": "짧게"}, headers=H))
    llm.script = [[("add_item", {"place_id": "p-food"})], ["추가"]]
    p2 = dict(sse(client.post(f"/agent/sessions/{sid}/chat", json={"message": "점심"}, headers=H)))["proposal"]
    assert not any("시작 고정" in c["message"] for c in p2["changes"])
    assert {"op": "update_item", "item_id": house, "stay_minutes": 30} in p2["ops"]  # 보낸 필드만
    result = ok(client.post(f"/agent/sessions/{sid}/proposals/{p2['id']}/accept", headers=H))
    assert result["plan"]["days"][0]["items"][0]["start_time"] == "13:00"


def test_update_item_null_start_time_clears(client, session):
    sid, pid = session
    house = _first_item(client, pid)["id"]
    ok(client.patch(f"/planner/plans/{pid}/items/{house}", json={"start_time": "13:00"}, headers=H))
    llm.script = [[("update_item", {"item_id": house, "start_time": None})], ["해제"]]
    p = dict(sse(client.post(f"/agent/sessions/{sid}/chat", json={"message": "고정 풀어"}, headers=H)))["proposal"]
    assert any("시작 고정 13:00 → 없음" in c["message"] for c in p["changes"])


def test_proposal_decided_during_turn_is_not_revived(client, session):
    """턴 도중 이어받은 제안이 승인되면, 그 ops 를 다시 담은 제안을 만들지 않는다 (이중 반영 방지)."""
    sid, pid = session
    llm.script = [[("add_item", {"place_id": "p-food"})], ["추가"]]
    p1 = dict(sse(client.post(f"/agent/sessions/{sid}/chat", json={"message": "점심"}, headers=H)))["proposal"]

    async def accept_elsewhere():  # 다른 화면에서 planner API 로 바로 승인
        r = await asgi.post(f"/planner/plans/{pid}/proposals/{p1['id']}/accept", headers=H)
        assert r.status_code == 200
    llm.script = [[accept_elsewhere, ("add_item", {"place_id": "p-house"})], ["또 추가"]]
    events = dict(sse(client.post(f"/agent/sessions/{sid}/chat", json={"message": "하나 더"}, headers=H)))
    assert "proposal" not in events and "그사이" in events["error"]["message"]
    places = [i["place_id"] for i in ok(client.get(f"/planner/plans/{pid}", headers=H))["days"][0]["items"]]
    assert places.count("p-food") == 1


def test_accept_blocked_while_turn_running(client, session):
    from app.agent import router as agent_router
    sid, pid = session
    llm.script = [[("add_item", {"place_id": "p-food"})], ["추가"]]
    p = dict(sse(client.post(f"/agent/sessions/{sid}/chat", json={"message": "a"}, headers=H)))["proposal"]
    agent_router._active[sid] = __import__("time").monotonic()
    try:
        assert client.post(f"/agent/sessions/{sid}/proposals/{p['id']}/accept", headers=H).status_code == 409
        assert client.post(f"/agent/sessions/{sid}/chat", json={"message": "b"}, headers=H).status_code == 409
    finally:
        agent_router._active.pop(sid, None)
    assert ok(client.post(f"/agent/sessions/{sid}/proposals/{p['id']}/accept", headers=H))["proposal"]["status"] == "accepted"


def test_unexpected_error_becomes_error_event_and_releases_session(client, session):
    sid, _ = session
    llm.script = [[RuntimeError("boom")]]
    events = sse(client.post(f"/agent/sessions/{sid}/chat", json={"message": "a"}, headers=H))
    assert events[-1][0] == "error"
    llm.script = [["다시 정상"]]
    assert dict(sse(client.post(f"/agent/sessions/{sid}/chat", json={"message": "b"}, headers=H)))["done"]


def test_history_keeps_short_tool_results(client, session):
    from sqlalchemy import select
    from app.agent.models import AgentMessage
    sid, _ = session
    llm.script = [[("add_item", {"place_id": "p-food", "start_time": "12:00"})], ["추가"]]
    sse(client.post(f"/agent/sessions/{sid}/chat", json={"message": "a"}, headers=H))
    assert "plan_after" in llm.calls[-1][-1]["content"]  # 이번 턴엔 전체 일정
    assert "start_time" in llm.calls[-1][-1]["content"]  # 무시한 인자를 알려줌
    with SessionLocal() as db:
        tool_rows = db.scalars(select(AgentMessage).where(AgentMessage.session_id == sid,
                                                          AgentMessage.role == "tool")).all()
    assert tool_rows and all("plan_after" not in m.content for m in tool_rows)  # 기록엔 요약만
