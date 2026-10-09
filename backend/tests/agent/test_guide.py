"""동행 모드·선제 턴 테스트 (명세: docs/agent/README.md §11). 가짜 LLM + 가짜 trip·safety 시계, planner 는 같은 앱.

    docker compose run --rm agent pytest tests/agent/test_guide.py
"""
import time

import httpx
import pytest

from app.agent import clock
from app.agent.api import get_http
from app.agent.prompts import event_message
from app.main import app
from tests.agent import test_harness as th
from tests.agent.test_harness import H, _reset, chat, client, err, last, ok, session  # noqa: F401  (픽스처)


class Upstream(httpx.AsyncBaseTransport):
    """trip 과 safety 시계(/safety/sim)는 가짜로, 나머지는 같은 앱(ASGI)으로 보낸다."""

    def __init__(self):
        self.inner = httpx.ASGITransport(app=app)
        self.trip: dict | int | Exception = 404  # TripState | 오류 HTTP 상태 | 연결 오류
        self.sim: str | None = None  # 가상 시각 (None 이면 404 sim_disabled -> 시스템 시각)
        self.trip_calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/trip/"):
            self.trip_calls += 1
            if isinstance(self.trip, Exception):
                raise self.trip
            if isinstance(self.trip, int):
                return httpx.Response(self.trip, json={"detail": {"code": "x", "message": "trip 오류"}})
            return httpx.Response(200, json=self.trip)
        if path == "/safety/sim":
            if self.sim is None:  # SAFETY_SOURCE=mock 이 아님
                return httpx.Response(404, json={"detail": {"code": "sim_disabled", "message": "mock 아님"}})
            return httpx.Response(200, json={"scenario": "calm", "now": self.sim, "offset_minutes": 0, "scenarios": {}})
        return await self.inner.handle_async_request(request)


@pytest.fixture()
def up(client):  # noqa: F811
    fake = Upstream()
    http = httpx.AsyncClient(transport=fake, base_url="http://app")
    app.dependency_overrides[get_http] = lambda: http
    clock.reset()
    yield fake
    app.dependency_overrides[get_http] = lambda: th.asgi
    clock.reset()


@pytest.fixture()
def trip_plan(client, session):  # noqa: F811
    """가옥거리(완료) -> 물회집(머무는 중) -> 카페(예정)."""
    sid, pid = session
    for place in ("p-food", "p-cafe"):
        ok(client.post(f"/planner/plans/{pid}/items", json={"place_id": place}, headers=H), 201)
    items = {i["place_id"]: i["id"] for i in th.plan_of(client, pid)["days"][0]["items"]}
    ok(client.post(f"/planner/plans/{pid}/items/{items['p-house']}/progress", json={"status": "done", "at": "13:30"},
                   headers=H))
    ok(client.post(f"/planner/plans/{pid}/items/{items['p-food']}/progress", json={"status": "visiting", "at": "13:40"},
                   headers=H))
    return sid, pid, items


def trip_state(pid: int, items: dict, active: bool = True) -> dict:
    return {"plan_id": pid, "active": active, "day": 1, "date": "2026-10-10", "started_at": "2026-10-10T12:20:00+09:00",
            "ended_at": None, "ended_reason": None, "now": "2026-10-10T14:20:00+09:00", "clock": "sim",
            "location": {"lat": 35.9907, "lng": 129.557, "accuracy": 12, "recorded_at": "2026-10-10T14:19:00+09:00",
                         "source": "cli"},
            "current": {"item_id": items["p-food"], "state": "visiting", "since": "13:40"},
            "next": {"item_id": items["p-cafe"], "title": "카페", "planned_start": "15:00", "eta": "15:12"},
            "delay_minutes": 12, "session_id": None}


def delay_event(pid: int, items: dict) -> dict:
    return {"id": 41, "plan_id": pid, "day": 1, "type": "delay", "level": 3, "severity": "warning",
            "created_at": "2026-10-10T14:20:00+09:00", "now": "2026-10-10T14:20:00+09:00",
            "item_ids": [items["p-cafe"]], "message": "카페 도착이 18:10으로 예상돼 하루 종료(18:00) 이후예요.",
            "payload": {"new_violations": [{"code": "day_overflow", "message": "1일차 종료 시각을 넘김"}],
                        "delay_minutes": 35},
            "status": "queued", "agent": None, "card": None}


def system_of(call: int = 0) -> str:
    return th.llm.calls[call][0]["content"]


# ---------- 선제 턴 ----------

def test_event_turn_creates_proposal(client, trip_plan, up):  # noqa: F811
    sid, pid, items = trip_plan
    up.trip = {**trip_state(pid, items), "active": False}
    chat(client, sid, "일정 괜찮아?", [["네, 괜찮아요."]])  # 계획 모드의 앞 턴 (말풍선이 합쳐지지 않는지 보려고)

    th.llm.script = [[("update_item", {"item_id": items["p-food"], "stay_minutes": 30})],
                     ["늦어져서 물회집을 30분만 머무는 걸로 줄였어요."]]
    body = {"event": delay_event(pid, items), "trip": trip_state(pid, items)}
    calls_before, n = up.trip_calls, len(th.llm.calls)
    res = ok(client.post(f"/agent/sessions/{sid}/events", json=body, headers=H))

    assert res["error"] is None and res["mode"] == "guide"
    assert res["text"] == "늦어져서 물회집을 30분만 머무는 걸로 줄였어요."
    [card] = res["cards"]
    assert card["type"] == "proposal" and card["proposal"]["origin"] == sid and card["proposal"]["status"] == "pending"
    assert isinstance(res["message_id"], int)
    assert up.trip_calls == calls_before  # 본문의 TripState 를 쓰고 다시 조회하지 않는다

    opening = th.llm.calls[n][-1]
    assert opening["role"] == "user" and opening["content"].startswith("[이벤트] 일정 지연: 카페 도착이")
    assert "1일차 종료 시각을 넘김" in opening["content"] and "예상 지연: 35분" in opening["content"]
    assert "할 일:" in opening["content"]
    system = system_of(n)
    assert "## 동행 모드" in system and "## 현재 상황 (여행 중)" in system
    assert "- 1일차 2026-10-10 14:20 (시뮬레이션 시각)" in system
    assert "- 위치: 물회집 (도착 13:40, 머무는 중)" in system
    assert "- 다음: 카페 15:00 예정, 예상 도착 15:12 (12분 늦음)" in system
    assert "- 오늘 남은 일정: 2곳 / 완료 1곳" in system

    # 대화 화면: 숨김 이벤트 메시지는 안 보이고, 선제 턴 답변은 앞 턴과 다른 말풍선 (id = message_id)
    detail = ok(client.get(f"/agent/sessions/{sid}", headers=H))
    msgs = detail["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "assistant"]
    assert not any("[이벤트]" in m["content"] for m in msgs)
    assert msgs[-1]["id"] == res["message_id"] and msgs[-1]["cards"][0]["proposal"]["id"] == card["proposal"]["id"]

    # 다음 턴의 LLM 은 이벤트와 답변을 기록으로 안다
    up.trip = trip_state(pid, items)
    chat(client, sid, "고마워", [["네!"]])
    assert any(m["role"] == "user" and m["content"].startswith("[이벤트]") for m in th.llm.calls[-1])


def test_event_busy_409(client, trip_plan, up):  # noqa: F811
    sid, pid, items = trip_plan
    th.agent_router._active[sid] = time.monotonic()
    body = {"event": delay_event(pid, items), "trip": trip_state(pid, items)}
    err(client.post(f"/agent/sessions/{sid}/events", json=body, headers=H), 409, "agent_busy")
    assert th.llm.calls == []


def test_event_request_validation(client, trip_plan, up):  # noqa: F811
    sid, pid, items = trip_plan
    url = f"/agent/sessions/{sid}/events"
    err(client.post(url, json={"event": delay_event(pid, items)}, headers=H), 422, "invalid_request")
    err(client.post(url, json={"event": {"message": "x"}, "trip": {}}, headers=H), 422, "invalid_request")
    err(client.post("/agent/sessions/nope/events", json={"event": {"type": "delay"}, "trip": {}}, headers=H),
        404, "session_not_found")


def test_safety_event_message():
    text = event_message({"type": "safety_raised", "message": "방파제 풍랑 주의", "item_ids": [3],
                          "payload": {"alert": {"level": "danger", "reason": "풍속 16m/s"}}})
    assert text.startswith("[이벤트] 안전 경고: 방파제 풍랑 주의")
    assert "item_id: 3" in text and "풍속 16m/s" in text and "check_safety" in text
    assert "할 일: 여행 중 상황이 바뀌었다" in event_message({"type": "something_new"})


# ---------- 동행 모드 (매 턴 trip 조회) ----------

def test_chat_in_guide_mode(client, trip_plan, up):  # noqa: F811
    sid, pid, items = trip_plan
    up.trip = trip_state(pid, items)
    events = chat(client, sid, "다음 어디야?", [["카페예요."]])
    assert up.trip_calls == 1
    assert last(events, "done")["mode"] == "guide"
    assert "## 동행 모드" in system_of() and "## 현재 상황 (여행 중)" in system_of()
    assert "## 현재 시각\n2026-10-10 14:20 (KST, 토요일, 시뮬레이션 시각)" in system_of()


@pytest.mark.parametrize("trip", [503, 404, httpx.ConnectError("down"), "inactive"])
def test_trip_unavailable_falls_back_to_plan_mode(client, trip_plan, up, trip):  # noqa: F811
    sid, pid, items = trip_plan
    up.trip = trip_state(pid, items, active=False) if trip == "inactive" else trip
    events = chat(client, sid, "물회집 다음에 카페 넣어줘", [
        [("update_item", {"item_id": items["p-house"], "stay_minutes": 40})], ["바꿨어요."]])
    assert up.trip_calls == 1
    assert last(events, "done")["mode"] == "plan" and "proposal" in dict(events)  # 계획 모드는 완료 항목도 막지 않는다
    assert "동행 모드" not in system_of() and "현재 상황" not in system_of()


def test_guide_mode_refuses_done_past_and_progress(client, trip_plan, up):  # noqa: F811
    sid, pid, items = trip_plan
    up.trip = trip_state(pid, items)
    events = chat(client, sid, "다 바꿔줘", [
        [("remove_item", {"item_id": items["p-house"]}),
         ("update_item", {"item_id": items["p-cafe"], "start_time": "13:00"}),
         ("update_item", {"item_id": items["p-food"], "order": 3}),
         ("update_item", {"item_id": items["p-cafe"], "status": "done"})],
        ["바꿀 수 없는 것들이에요."]])
    tools = [m for m in th.llm.calls[-1] if m["role"] == "tool"]
    assert len(tools) == 4 and all("not_allowed_during_trip" in m["content"] for m in tools)
    assert "완료" in tools[0]["content"] and "지난 시각(13:00)" in tools[1]["content"] and "14:20" in tools[1]["content"]
    assert "체류 시간" in tools[2]["content"] and "진행 상태" in tools[3]["content"]
    assert "proposal" not in dict(events)
    assert all(s["status"] != "done" for e, s in events if e == "step")

    # 남은 일정의 앞으로의 시각·지금 머무는 곳의 체류 시간은 바꿀 수 있다
    events = chat(client, sid, "카페는 16시에", [
        [("update_item", {"item_id": items["p-cafe"], "start_time": "16:00"}),
         ("update_item", {"item_id": items["p-food"], "stay_minutes": 30})], ["16시로 옮겼어요."]])
    assert "proposal" in dict(events)


# ---------- 시계 ----------

def test_clock_follows_safety_sim_in_plan_mode(client, session, up):  # noqa: F811
    sid, _ = session
    up.sim = "2026-10-10T09:05:00+09:00"
    chat(client, sid, "안녕", [["안녕하세요"]])
    assert "## 현재 시각\n2026-10-10 09:0" in system_of() and "시뮬레이션 시각)" in system_of()

    up.sim = None  # 404 sim_disabled -> 시스템 시각 (캐시가 끝난 뒤)
    clock.reset()
    chat(client, sid, "안녕", [["안녕하세요"]])
    assert "시뮬레이션" not in system_of(-1)


# ---------- 진행 변화 · 답변과 제안 일치 · 선제 턴 형식 ----------

@pytest.fixture()
def planner_now():
    """planner 의 지금을 여행일 14:20 으로 (실제 진행 기준 계산이 켜지도록)."""
    from datetime import datetime

    from app.planner import clock as planner_clock
    saved = planner_clock._clock
    planner_clock._clock = planner_clock.FixedClock(datetime(2026, 10, 10, 14, 20, tzinfo=planner_clock.KST))
    yield
    planner_clock._clock = saved


def test_progress_changed_proposal_is_refused_and_replaced(client, trip_plan, up):  # noqa: F811
    """머무는 곳의 체류를 줄이는 제안을 만든 뒤 그곳을 떠나면(done): 승인은 409 progress_changed,
    다음 턴은 그 제안을 이어받지 않고 새 제안으로 대체한다."""
    sid, pid, items = trip_plan
    up.trip = trip_state(pid, items)
    p1 = last(chat(client, sid, "늦었어", [[("update_item", {"item_id": items["p-food"], "stay_minutes": 30})],
                                          ["물회집은 30분만 있어요."]]), "proposal")
    ok(client.post(f"/planner/plans/{pid}/items/{items['p-food']}/progress", json={"status": "done", "at": "14:20"},
                   headers=H))

    d = err(client.post(f"/agent/sessions/{sid}/proposals/{p1['id']}/accept", headers=H), 409, "progress_changed")
    assert d["items"] == [{"item_id": items["p-food"], "title": "물회집", "done_at": "14:20"}]
    assert ok(client.get(f"/agent/sessions/{sid}", headers=H))["messages"][-1]["cards"][0]["proposal"][
        "progress_changed"] == [items["p-food"]]

    turn = len(th.llm.calls)
    p2 = last(chat(client, sid, "카페는 짧게", [[("update_item", {"item_id": items["p-cafe"], "stay_minutes": 30})],
                                               ["카페를 30분으로 줄여요."]]), "proposal")
    system = system_of(turn)
    assert "다녀왔다(완료)" in system and "체류 60분 → 30분" in system and "## 승인 대기 중인 제안" not in system
    assert [c["item_id"] for c in p2["changes"] if c["kind"] == "updated"] == [items["p-cafe"]]
    assert ok(client.get(f"/planner/plans/{pid}/proposals/{p1['id']}", headers=H))["status"] == "superseded"
    ok(client.post(f"/agent/sessions/{sid}/proposals/{p2['id']}/accept", headers=H))


def test_answer_is_written_against_confirmed_changes(client, trip_plan, up):  # noqa: F811
    """최종 답변을 쓰는 호출에는 초안의 확정 변경 목록이 붙는다 (기록에는 남지 않음)."""
    sid, pid, items = trip_plan
    up.trip = trip_state(pid, items)
    chat(client, sid, "늦었어", [[("update_item", {"item_id": items["p-cafe"], "stay_minutes": 30})], ["줄였어요."]])
    note = th.llm.calls[-1][-1]
    assert note["role"] == "user" and note["content"].startswith("[시스템] 지금 변경 제안 초안의 확정 변경")
    assert "'카페' 체류 60분 → 30분" in note["content"] and "다른 방법으로" in note["content"]
    assert not any(m["content"].startswith("[시스템]") for m in th.llm.calls[0])  # 초안이 없던 첫 호출에는 없음

    chat(client, sid, "고마워", [["네!"]])  # 이어받은 대기 제안이 있으면 첫 호출부터 그 변경이 기준
    assert "'카페' 체류 60분 → 30분" in th.llm.calls[-1][-1]["content"]
    assert not any(m["content"].startswith("[시스템]") for m in th.llm.calls[-1][1:-1])


def test_event_turn_single_proposal_format(client, trip_plan, up):  # noqa: F811
    """선제 턴: 추천 카드 도구 없음, 할 일에 '추천안 하나 + 다른 방법 한 줄', 변경 없이 끝내려 하면 빈 초안임을 알린다."""
    sid, pid, items = trip_plan
    body = {"event": delay_event(pid, items), "trip": trip_state(pid, items)}
    th.llm.script = [[("view_plan", {})], ["카페가 늦어져요. 다른 방법으로 카페를 빼도 돼요."]]
    res = ok(client.post(f"/agent/sessions/{sid}/events", json=body, headers=H))
    assert res["cards"] == []
    assert all("recommend_places" not in [t["function"]["name"] for t in tools] for tools in th.llm.tools_seen if tools)
    opening = th.llm.calls[0][-1]["content"]
    assert "추천안 하나만" in opening and "다른 방법으로" in opening
    assert th.llm.calls[1][-1]["content"].startswith("[시스템] 지금 변경 제안 초안이 비어 있다")

    up.trip = trip_state(pid, items)
    chat(client, sid, "추천해줘", [["네"]])  # 일반 대화에는 추천 카드 도구가 있다
    assert "recommend_places" in [t["function"]["name"] for t in th.llm.tools_seen[-1]]


def test_guide_view_and_changes_follow_live_progress(client, trip_plan, up, planner_now):  # noqa: F811
    """동행 모드에서 LLM 이 보는 일정·변경 요약은 실제 진행(물회집 13:40 도착) + planner 의 지금 기준이다."""
    sid, pid, items = trip_plan
    up.trip = trip_state(pid, items)
    baseline = th.plan_of(client, pid)["days"][0]
    events = chat(client, sid, "카페 짧게", [[("update_item", {"item_id": items["p-cafe"], "stay_minutes": 30})],
                                            ["줄였어요."]])
    import json as _json
    plan_json = system_of(0).split("## 현재 일정\n", 1)[1].split("\n\n", 1)[0]
    food = next(i for i in _json.loads(plan_json)["days"][0]["items"] if i["item_id"] == items["p-food"])
    assert food["time"] == "13:40~14:40"
    prop = last(events, "proposal")
    live_end = prop["before"]["days"][0]["end_at"]
    assert live_end != baseline["end_at"]
    assert f"1일차 종료: {live_end} → " in [c["message"] for c in prop["changes"] if c["kind"] == "day_end"][0]
