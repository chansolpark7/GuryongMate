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
    assert res["trigger"] == {"kind": "event", "type": "delay", "event_id": 41, "reason": None,
                              "at": "2026-10-10T14:20:00+09:00"}
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
    assert "- 오늘 남은 일정: 1곳 / 머무는 중 1곳 / 완료 1곳" in system  # 머무는 곳은 따로 (A10)

    # 대화 화면: 숨김 이벤트 메시지는 안 보이고, 선제 턴 답변은 앞 턴과 다른 말풍선 (id = message_id)
    detail = ok(client.get(f"/agent/sessions/{sid}", headers=H))
    msgs = detail["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "assistant"]
    assert not any("[이벤트]" in m["content"] for m in msgs)
    assert msgs[-1]["id"] == res["message_id"] and msgs[-1]["cards"][0]["proposal"]["id"] == card["proposal"]["id"]
    assert [m["trigger"] for m in msgs] == [None, None, res["trigger"]]  # 선제 턴 답에만 계기

    # 다음 턴의 LLM 은 이벤트와 답변을 기록으로 안다
    up.trip = trip_state(pid, items)
    chat(client, sid, "고마워", [["네!"]])
    assert any(m["role"] == "user" and m["content"].startswith("[이벤트]") for m in th.llm.calls[-1])


def test_event_trigger_survives_mixed_turns(client, trip_plan, up):  # noqa: F811
    """선제 턴 답(도구 호출 전·후 여러 행이 한 말풍선으로 합쳐져도)에만 trigger. 일반 대화·장소 질문(place_id)은 null."""
    sid, pid, items = trip_plan
    up.trip = trip_state(pid, items)
    assert last(chat(client, sid, "여기 어때?", [["좋은 곳이에요."]]), "done")["trigger"] is None
    ask_place = {"message": "여기 왜 유명해?", "place_id": "p-house"}
    th.llm.script = [["백 년 된 거리예요."]]
    th.sse(client.post(f"/agent/sessions/{sid}/chat", json=ask_place, headers=H))

    behind = {**delay_event(pid, items), "id": 77, "now": "2026-10-10T14:50:00+09:00",
              "payload": {"reason": "behind_schedule", "delay_minutes": 40}}
    th.llm.script = [["먼저 확인해 볼게요.", ("check_safety", {})], ["40분 늦어지고 있어요."]]
    res = ok(client.post(f"/agent/sessions/{sid}/events", json={"event": behind, "trip": trip_state(pid, items)},
                         headers=H))
    assert res["trigger"] == {"kind": "event", "type": "delay", "event_id": 77, "reason": "behind_schedule",
                              "at": "2026-10-10T14:50:00+09:00"}
    safety = {"id": 78, "type": "safety_raised", "message": "방파제 풍랑 주의", "created_at": "2026-10-10T15:00:00+09:00",
              "payload": {"reason": {"not": "a string"}}}
    th.llm.script = [["방파제에 풍랑 주의보가 났어요."]]
    res2 = ok(client.post(f"/agent/sessions/{sid}/events", json={"event": safety, "trip": trip_state(pid, items)},
                          headers=H))
    assert res2["trigger"] == {"kind": "event", "type": "safety_raised", "event_id": 78, "reason": None,
                               "at": "2026-10-10T15:00:00+09:00"}  # now 가 없으면 created_at
    th.llm.script = [["네."]]
    th.sse(client.post(f"/agent/sessions/{sid}/chat", json=ask_place, headers=H))

    msgs = ok(client.get(f"/agent/sessions/{sid}", headers=H))["messages"]
    assert [(m["role"], m["place_id"], (m["trigger"] or {}).get("event_id")) for m in msgs] == [
        ("user", None, None), ("assistant", None, None),
        ("user", "p-house", None), ("assistant", "p-house", None),
        ("assistant", None, 77), ("assistant", None, 78),
        ("user", "p-house", None), ("assistant", "p-house", None)]
    assert msgs[4]["content"] == "먼저 확인해 볼게요.\n\n40분 늦어지고 있어요."  # 도구 호출 전·후 답이 한 말풍선
    assert msgs[4]["trigger"] == res["trigger"] and msgs[5]["trigger"] == res2["trigger"]

    # 같은 이벤트를 다시 보내면 이전 결과 그대로 (trigger 포함)
    again = ok(client.post(f"/agent/sessions/{sid}/events", json={"event": behind, "trip": trip_state(pid, items)},
                           headers=H))
    assert again["trigger"] == res["trigger"]


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


def behind_event(pid: int, items: dict, suggest: dict) -> dict:
    """일정 밀림 (delay, payload.reason=behind_schedule, docs/trip/README.md §3.2). 물회집에 머무는 중, 카페가 35분 늦음."""
    return {"id": 42, "plan_id": pid, "day": 1, "type": "delay", "level": 3, "severity": "warning",
            "created_at": "2026-10-10T14:20:00+09:00", "now": "2026-10-10T14:20:00+09:00",
            "item_ids": [items["p-cafe"]], "message": "카페 도착이 15:00 예정에서 15:35으로 35분 늦어질 것 같아요.",
            "payload": {"reason": "behind_schedule", "new_violations": [], "delay_minutes": 35, "plan_version": 3,
                        "next": {"item_id": items["p-cafe"], "title": "카페", "planned_start": "15:00", "eta": "15:35"},
                        "end": {"planned": "16:00", "expected": "16:35"}, "suggest": suggest},
            "status": "queued", "agent": None, "card": None}


def test_behind_schedule_event_message():
    text = event_message(behind_event(1, {"p-cafe": 5}, {"kind": "extend_stay", "item_id": 4, "title": "물회집",
                                                         "stay_minutes": 60, "from_stay_minutes": 30}))
    assert text.startswith("[이벤트] 일정 밀림: 카페 도착이 15:00 예정에서 15:35으로 35분 늦어질 것 같아요.")
    assert "오늘 예상 종료: 16:35 (계획 16:00)" in text
    assert "추천 변경: update_item(item_id=4, stay_minutes=60)" in text and "물회집 체류 30분 → 60분" in text
    assert "남은 일정을 미룰까요" in text and "다른 방법" in text  # 할 일: 밀림 지침 (위반 지침 아님)
    shift = event_message(behind_event(1, {"p-cafe": 5}, {"kind": "shift_next", "item_id": 5, "title": "카페",
                                                          "start_time": "15:35", "from_start": "15:00"}))
    assert 'update_item(item_id=5, start_time="15:35")' in shift and "이동 중이라" in shift
    # 위반으로 난 delay 는 그대로 '일정 지연'
    assert event_message({"type": "delay", "message": "x", "payload": {"reason": "violations"}}).startswith("[이벤트] 일정 지연: x")


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

    # 이어받은 대기 제안만 있고 이번 턴에 바꾸지 않으면 확정 변경 안내를 붙이지 않는다 (제안은 맥락에 대기 제안으로만)
    events = chat(client, sid, "고마워", [["네!"]])
    assert not any(m["content"].startswith("[시스템]") for m in th.llm.calls[-1][1:])
    assert "'카페' 체류 60분 → 30분" in system_of(len(th.llm.calls) - 1)
    assert last(events, "done")["text"] == "네!\n\n아직 승인 대기 중인 변경안이 있어요."


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
    """동행 모드에서 LLM 이 보는 일정·변경 요약은 실제 진행(물회집 13:40 도착) + planner 의 지금 기준이다.
    지금은 물회집 예정 종료(14:36)를 넘긴 14:50 — 그 전에는 예정 종료에 떠난다고 봐서(규칙 B) 종료 시각이 계획과 같다."""
    from datetime import datetime

    from app.planner import clock as planner_clock
    planner_clock._clock = planner_clock.FixedClock(datetime(2026, 10, 10, 14, 50, tzinfo=planner_clock.KST))
    sid, pid, items = trip_plan
    up.trip = trip_state(pid, items)
    baseline = th.plan_of(client, pid)["days"][0]
    events = chat(client, sid, "카페 짧게", [[("update_item", {"item_id": items["p-cafe"], "stay_minutes": 30})],
                                            ["줄였어요."]])
    import json as _json
    plan_json = system_of(0).split("## 현재 일정\n", 1)[1].split("\n\n", 1)[0]
    food = next(i for i in _json.loads(plan_json)["days"][0]["items"] if i["item_id"] == items["p-food"])
    assert food["time"] == "13:40~14:50"
    prop = last(events, "proposal")
    live_end = prop["before"]["days"][0]["end_at"]
    assert live_end != baseline["end_at"]
    assert f"1일차 종료: {live_end} → " in [c["message"] for c in prop["changes"] if c["kind"] == "day_end"][0]


# ---------- 대기 제안이 있는 동행 모드의 장소 질문 (장면 ④ 뒤) ----------

PENDING = "아직 승인 대기 중인 변경안이 있어요."


@pytest.fixture()
def pending_event(client, trip_plan, up, monkeypatch):  # noqa: F811
    """선제 턴이 같은 날 중복(가옥거리 한 번 더, 검토 should)이 남은 제안을 만들어 승인 대기로 둔다. 이후 검토 관문 켬."""
    from app.agent.config import settings
    sid, pid, items = trip_plan
    th.llm.script = [[("add_item", {"place_id": "p-house"})], ["가옥거리를 한 번 더 넣었어요."]]
    res = ok(client.post(f"/agent/sessions/{sid}/events",
                         json={"event": delay_event(pid, items), "trip": trip_state(pid, items)}, headers=H))
    [card] = res["cards"]
    monkeypatch.setattr(settings, "review_rounds", 2)
    up.trip = trip_state(pid, items)
    th.llm.calls.clear()
    return sid, pid, items, card["proposal"]


@pytest.mark.parametrize("question", ["구룡포항 도착했어. 여기 어떤 곳이야?", "왜 이름이 구룡포야?"])
def test_place_question_does_not_reopen_pending_proposal(client, pending_event, question):  # noqa: F811
    """장소 질문은 검토 관문·확정 변경 안내 없이 그 질문에 답하고, 대기 제안은 그대로 두고 답 끝에 한 줄로 알린다."""
    sid, pid, _, prop = pending_event
    events = chat(client, sid, question, [["아홉 마리 용이 승천했다는 이야기에서 온 이름이에요."]])  # 관문이 돌면 대본이 모자라 실패
    assert len(th.llm.calls) == 1
    assert not any(e == "step" and d["kind"] == "review" for e, d in events)
    assert not any(m["role"] == "user" and m["content"].startswith("[시스템]") for m in th.llm.calls[0])
    system = system_of(0)
    assert f"## 승인 대기 중인 제안 #{prop['id']}" in system and "이 제안을 고치지 말고 질문에만 답한다" in system
    assert "왜 이름이 구룡포야?" in system  # 현장 해설 규칙: 장소 질문은 일정을 고치지 않는다
    done = last(events, "done")
    assert done["text"] == f"아홉 마리 용이 승천했다는 이야기에서 온 이름이에요.\n\n{PENDING}"
    assert done["cards"] == [] and "proposal" not in dict(events)
    assert ok(client.get(f"/planner/plans/{pid}/proposals/{prop['id']}", headers=H))["status"] == "pending"
    msgs = ok(client.get(f"/agent/sessions/{sid}", headers=H))["messages"]
    assert msgs[-1]["content"].endswith(PENDING)


def test_change_request_with_pending_still_goes_through_gate(client, pending_event):  # noqa: F811
    """일정을 실제로 바꾼 턴은 이어받은 제안(같은 날 중복 should) 위에서 검토 관문이 돈다."""
    sid, pid, items, prop = pending_event
    events = chat(client, sid, "카페는 30분만", [
        [("update_item", {"item_id": items["p-cafe"], "stay_minutes": 30})],   # 이어받은 초안을 실제로 바꿈
        ["줄였어요"], ["줄였어요."], ["카페를 30분으로 줄였어요."],             # 관문이 중복(should)으로 두 번 되돌려 보냄
    ])
    assert [d["detail"] for e, d in events if e == "step" and d["kind"] == "review" and d["status"] == "done"][0] != "문제 없음"
    assert len(th.llm.calls) == 4
    done = last(events, "done")
    assert done["text"] == "카페를 30분으로 줄였어요." and PENDING not in done["text"]
    new = last(events, "proposal")
    assert ok(client.get(f"/planner/plans/{pid}/proposals/{prop['id']}", headers=H))["status"] == "superseded"
    assert new["status"] == "pending"


def test_style_length_sets_answer_length(client, trip_plan, up):  # noqa: F811
    """메모리 style_length 가 short 면 동행 모드 대화의 해설 답변을 1~2문장으로, 선제 턴에는 넣지 않는다."""
    sid, pid, items = trip_plan
    item = ok(client.post("/docent/memory", json={"scope": "user", "category": "style", "text": "해설은 핵심만 짧게",
                                                  "source": "user_edit"}, headers=H), 201)
    try:
        up.trip = trip_state(pid, items)
        chat(client, sid, "여기 어떤 곳이야?", [["가옥거리예요."]])
        assert "## 해설 길이" in system_of(0) and "1~2문장" in system_of(0)
        th.llm.script = [["늦어졌어요. 다른 방법으로 카페를 빼도 돼요."]]
        ok(client.post(f"/agent/sessions/{sid}/events",
                       json={"event": delay_event(pid, items), "trip": trip_state(pid, items)}, headers=H))
        assert "## 해설 길이" not in system_of(1) and "짧은 세 문단" in th.llm.calls[1][-1]["content"]
        ok(client.patch(f"/docent/memory/{item['id']}", json={"text": "해설은 자세히"}, headers=H))
        chat(client, sid, "더 얘기해줘", [["네."]])
        assert "4~6문장" in system_of(2)
    finally:
        client.delete(f"/docent/memory/{item['id']}", headers=H)


# ---------- 선제 턴 중복 ----------

def test_same_event_id_returns_previous_result(client, trip_plan, up):  # noqa: F811
    """trip 이 같은 이벤트를 다시 보내면 LLM 턴을 다시 돌리지 않고 이전 결과(message_id·proposal)를 돌려준다."""
    sid, pid, items = trip_plan
    body = {"event": delay_event(pid, items), "trip": trip_state(pid, items)}
    th.llm.script = [[("update_item", {"item_id": items["p-cafe"], "stay_minutes": 30})], ["카페를 줄였어요."]]
    first = ok(client.post(f"/agent/sessions/{sid}/events", json=body, headers=H))
    calls = len(th.llm.calls)
    again = ok(client.post(f"/agent/sessions/{sid}/events", json=body, headers=H))
    assert len(th.llm.calls) == calls
    assert again["message_id"] == first["message_id"] and again["cards"][0]["proposal"]["id"] == first["cards"][0]["proposal"]["id"]
    assert len(ok(client.get(f"/planner/plans/{pid}/proposals", headers=H))) == 1

    # 다른 이벤트는 새 턴. 오류로 끝난 이벤트는 다시 보내면 다시 돈다
    from app.agent.llm import LLMError
    other = {**body, "event": {**body["event"], "id": 42}}
    th.llm.script = [[LLMError("credit https://billing", retryable=False, quota=True)]]
    failed = ok(client.post(f"/agent/sessions/{sid}/events", json=other, headers=H))
    assert failed["error"]["code"] == "llm_quota" and "billing" not in failed["error"]["message"]
    th.llm.script = [["다시 확인했어요."]]
    retried = ok(client.post(f"/agent/sessions/{sid}/events", json=other, headers=H))
    assert retried["error"] is None and retried["message_id"] != first["message_id"]


# ---------- 동행 모드 거부 규칙·기본값·현재 상황 (docs/audit/logic-bugs-2026-10-12.md A3·A4·A6·A7·A8·A10) ----------

def tool_results(call: int = -1) -> list[str]:
    """그 호출에서 이번 턴(마지막 사용자 메시지 뒤)의 도구 결과."""
    msgs = th.llm.calls[call]
    start = max(n for n, m in enumerate(msgs) if m["role"] == "user" and not m["content"].startswith("[시스템]"))
    return [m["content"] for m in msgs[start:] if m["role"] == "tool"]


def test_guide_refuses_slot_before_passed_items(client, trip_plan, up):  # noqa: F811
    """A3: 가옥거리 완료 -> 물회집 머무는 중일 때 order 1·2 로 넣거나 옮기면 지난 시간대에 끼어든다 -> 거부. 3 이후·생략은 된다."""
    sid, pid, items = trip_plan
    up.trip = trip_state(pid, items)
    events = chat(client, sid, "카페 앞으로", [
        [("add_item", {"place_id": "p-cafe", "order": 1}),
         ("add_item", {"place_id": "p-cafe", "order": 2}),
         ("update_item", {"item_id": items["p-cafe"], "order": 1})],
        ["그 자리는 이미 지났어요."]])
    results = tool_results()
    assert len(results) == 3 and all("not_allowed_during_trip" in r for r in results)
    assert "3번째 이후" in results[0] and "물회집" in results[0]
    assert "proposal" not in dict(events)

    events = chat(client, sid, "카페 하나 더", [
        [("add_item", {"place_id": "p-cafe", "order": 3}), ("add_item", {"place_id": "p-house"})], ["넣었어요."]])
    assert not any("not_allowed_during_trip" in r for r in tool_results())
    assert "proposal" in dict(events)


def test_guide_guard_normalizes_string_ids(client, trip_plan, up):  # noqa: F811
    """A4: "88" 같은 문자열 id·day·order 로도 거부 규칙을 우회하지 못한다."""
    sid, pid, items = trip_plan
    up.trip = trip_state(pid, items)
    events = chat(client, sid, "가옥거리 고쳐", [
        [("update_item", {"item_id": str(items["p-house"]), "stay_minutes": 10}),
         ("remove_item", {"item_id": str(items["p-house"])}),
         ("update_item", {"item_id": str(items["p-food"]), "order": "3"}),
         ("add_item", {"place_id": "p-cafe", "day": "1", "order": "1"}),
         ("update_item", {"item_id": items["p-cafe"], "order": "abc"})],
        ["못 바꿔요."]])
    results = tool_results()
    assert all("not_allowed_during_trip" in r for r in results[:4]), results
    assert "완료" in results[0] and "완료" in results[1] and "체류 시간" in results[2]
    assert "인자가 잘못됐습니다" in results[4]
    assert "proposal" not in dict(events)


def test_guide_past_day_and_default_day(client, trip_plan, up):  # noqa: F811
    """A3·A6: 2일차 여행 중이면 1일차(지난 날)에 넣거나 옮기거나 그 항목을 고칠 수 없고, day 를 생략하면 오늘(2일차)."""
    sid, pid, items = trip_plan
    ok(client.patch(f"/planner/plans/{pid}", json={"end_date": "2026-10-11"}, headers=H))
    ok(client.post(f"/planner/plans/{pid}/items", json={"place_id": "p-cafe", "day": 2}, headers=H), 201)
    day2 = th.plan_of(client, pid)["days"][1]["items"][0]["id"]
    up.trip = {**trip_state(pid, items), "day": 2, "current": None, "next": None}
    events = chat(client, sid, "어제 일정에 넣어줘", [
        [("add_item", {"place_id": "p-food", "day": 1}),
         ("update_item", {"item_id": day2, "day": 1}),
         ("update_item", {"item_id": items["p-cafe"], "stay_minutes": 20})],
        ["지난 날은 못 바꿔요."]])
    results = tool_results()
    assert all("not_allowed_during_trip" in r and "지난 날" in r for r in results), results
    assert "proposal" not in dict(events)

    events = chat(client, sid, "물회집 넣어줘", [[("add_item", {"place_id": "p-food"})], ["넣었어요."]])
    p = last(events, "proposal")
    assert [c["day"] for c in p["changes"] if c["kind"] == "added"] == [2]

    th.llm.script = [[("recommend_places", {"title": "추천", "candidates": [{"place_id": "p-house"}]})], ["골라 보세요."]]
    rec = last(th.sse(client.post(f"/agent/sessions/{sid}/chat", json={"message": "추천"}, headers=H)), "recommendation")
    assert rec["candidates"][0]["day"] == 2


def test_update_item_drops_unchanged_filler(client, trip_plan, up):  # noqa: F811
    """A8: 채움값 start_time "none" 이 같이 와도 머무는 곳의 체류 변경은 된다 (장면 ③). 고정 시각이 있던 곳은 해제했다고 알린다."""
    sid, pid, items = trip_plan
    up.trip = trip_state(pid, items)
    events = chat(client, sid, "더 있을래", [
        [("update_item", {"item_id": items["p-food"], "start_time": "none", "stay_minutes": 90, "travel_mode": None})],
        ["늘렸어요."]])
    [result] = tool_results()
    assert "not_allowed_during_trip" not in result and "plan_after" in result
    assert "proposal" in dict(events)

    ok(client.patch(f"/planner/plans/{pid}/items/{items['p-cafe']}", json={"start_time": "16:30"}, headers=H))
    chat(client, sid, "카페 짧게", [
        [("update_item", {"item_id": items["p-cafe"], "start_time": "none", "stay_minutes": 30})], ["줄였어요."]])
    [result] = tool_results()
    assert "고정 시각 16:30 을 해제했다" in result

    chat(client, sid, "그대로", [[("update_item", {"item_id": items["p-cafe"], "stay_minutes": 30})], ["그대로예요."]])
    assert "바뀌는 값이 없어" in tool_results()[0]


def test_situation_marks_approved_basis_with_pending(client, trip_plan, up):  # noqa: F811
    """A7: 대기 제안이 다음 장소 시각을 바꾸면, 현재 상황의 '다음'은 승인된 계획 기준이라고 밝히고 제안 적용 시각을 함께 적는다."""
    sid, pid, items = trip_plan
    up.trip = trip_state(pid, items)
    chat(client, sid, "물회집 더", [[("update_item", {"item_id": items["p-food"], "stay_minutes": 150})], ["늘렸어요."]])
    turn = len(th.llm.calls)
    chat(client, sid, "다음 어디야?", [["카페예요."]])
    line = next(x for x in system_of(turn).split("\n") if x.startswith("- 다음:"))
    assert line.startswith("- 다음: 카페 15:00 예정, 예상 도착 15:12 (12분 늦음) — 승인된 계획 기준. "
                           "승인 대기 제안을 적용한 현재 일정에서는 ") and line.endswith(" 시작"), line
    # 대기 제안이 없으면 trip 값 그대로
    assert "승인된 계획 기준" not in next(x for x in system_of(0).split("\n") if x.startswith("- 다음:"))
