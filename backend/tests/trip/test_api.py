"""trip API 통합 테스트 (실제 DB + 가짜 planner·safety·agent + FixedClock).

가짜 planner 의 기준 일정 (conftest.PLAN, 도보 = 직선 × 1.3 / 70m):
    짬홍 12:30~13:30 -> 구룡포항 13:37~14:07 -> 가옥거리 14:16~15:16 -> 과메기문화관 15:21~16:21 -> 근대문화역사관 16:26~17:06(17:30 마감)
가옥거리에 머물면: 15:40 이후 역사관이 17:30 을 넘고(outside_open_hours), 16:10 이후 18:00 을 넘는다(day_overflow).
"""
from datetime import datetime, timedelta

from app.db import SessionLocal
from app.trip import service
from app.trip.models import TripEvent

from .conftest import H, USER, at, err, ok

LUNCH, PORT, HOUSE, MUSEUM, HISTORY = "kto-2834330", "kto-2610501", "kto-1909659", "kto-2660046", "kto-2603493"


def item_id(env, order: int) -> int:
    return env.planner.item(env.plan_id, order)["id"]


def visited_until_port(env) -> None:
    """짬홍·구룡포항을 다녀온 것으로 planner 에 기록 (가옥거리로 가는 중)."""
    for order, arrived, done in ((1, "12:30", "13:30"), (2, "13:37", "14:00")):
        it = env.planner.item(env.plan_id, order)
        it["status"], it["arrived_at"], it["done_at"] = "done", arrived, done


def stored(e: dict) -> TripEvent:
    """API 응답에 없는 이벤트 필드(원인 키·전송 시각)를 DB 에서."""
    with SessionLocal() as s:
        return s.get(TripEvent, e["id"])


def cause(e: dict) -> str:
    return stored(e).cause_key


def progress_calls(env) -> list[tuple]:
    return [c[1:] for c in env.planner.calls if c[0] == "progress"]


# ---------- 여행일 ----------

def test_start_not_trip_day(env):
    env.clock.when = at("12:00").replace(day=9)
    d = err(env.c.post(env.url("/start"), headers=H), 409, "not_trip_day")
    assert (d["start_date"], d["end_date"]) == ("2026-10-10", "2026-10-10")


def test_start_plan_not_found(env):
    err(env.c.post("/trip/plans/9999/start", headers=H), 404, "plan_not_found")
    err(env.c.get("/trip/plans/9999/state", headers=H), 404, "plan_not_found")


def test_start_is_idempotent(env):
    before = env.state()
    assert before["active"] is False and before["day"] is None and before["clock"] == "sim"
    s = env.start("12:20")
    assert (s["active"], s["day"], s["date"], s["current"]["state"]) == (True, 1, "2026-10-10", "idle")
    assert s["session_id"] == f"sess-{env.plan_id}-0"  # 세션이 없어 agent 에 새로 만들었다
    again = env.start("12:25")
    assert again["started_at"] == s["started_at"] and again["session_id"] == s["session_id"]
    assert env.types() == ["day_started"]


def test_start_uses_given_or_latest_session(env):
    env.agent.sessions[env.plan_id] = ["old", "recent"]
    assert env.start()["session_id"] == "recent"
    assert env.start(session_id="mine")["session_id"] == "mine"


def test_start_resumes_planner_progress(env):
    """planner 에 이미 머무는 항목이 기록돼 있으면 그곳에서 시작한다."""
    it = env.planner.item(env.plan_id, 1)
    it["status"], it["arrived_at"] = "visiting", "12:31"
    s = env.start("12:40")
    assert s["current"] == {"item_id": it["id"], "state": "visiting", "since": "12:31"}


def test_end_and_restart_same_day(env):
    env.start("12:20")
    env.stay(LUNCH, "12:20", 2)
    s = ok(env.c.post(env.url("/end"), headers=H))
    assert (s["active"], s["ended_reason"]) == (False, "user")
    err(env.c.post(env.url("/location"), json={"lat": 35.99, "lng": 129.55}, headers=H), 409, "trip_not_active")
    # 같은 날 다시 시작하면 같은 여행일을 다시 연다 (이미 알린 도착은 다시 알리지 않는다)
    assert env.start("12:30")["active"] is True
    env.stay(LUNCH, "12:31", 2)
    assert env.types().count("arrived") == 1
    assert env.types() == ["day_started", "arrived", "day_ended", "day_started"]


def test_location_without_trip(env):
    err(env.c.post(env.url("/location"), json={"lat": 35.99, "lng": 129.55}, headers=H), 409, "trip_not_active")


# ---------- 도착·출발 ----------

def test_arrival_marks_planner_and_card(env):
    env.start("12:20")
    events = env.stay(LUNCH, "12:20", 2)
    assert [e["type"] for e in events] == ["arrived"]
    e = events[0]
    assert (e["level"], e["status"], e["severity"]) == (2, "delivered", "info")
    assert e["card"] == {"kind": "docent", "place_id": LUNCH, "item_id": item_id(env, 1)}
    assert e["message"] == "짬홍에 도착했어요. 이야기 들어볼까요?" and e["payload"]["out_of_order"] is False
    assert progress_calls(env) == [(item_id(env, 1), "visiting", "12:20")]  # 시각은 처음 들어온 때
    s = env.state()
    assert s["current"] == {"item_id": item_id(env, 1), "state": "visiting", "since": "12:20"}
    assert s["location"]["source"] == "cli" and s["next"]["item_id"] == item_id(env, 2)
    # after 로 이어 받기
    assert [x["type"] for x in env.events(after=e["id"])] == []
    assert [x["type"] for x in env.events(after=e["id"] - 1)] == ["arrived"]


def test_departure_and_next_message(env):
    env.start("12:20")
    env.stay(LUNCH, "12:20", 60, every_sec=300)
    mid = (35.9903, 129.5572)  # 짬홍과 구룡포항 사이
    events = env.stay(mid, "13:21", 3)
    dep = [e for e in events if e["type"] == "departed"]
    assert len(dep) == 1 and dep[0]["level"] == 1
    assert dep[0]["message"].startswith("짬홍을(를) 떠났어요. 다음은 구룡포항, 도보 약")
    assert (item_id(env, 1), "done", "13:21") in progress_calls(env)
    s = env.state()
    assert s["current"]["state"] == "moving" and s["next"]["item_id"] == item_id(env, 2)
    proj = [c[1] for c in env.planner.calls if c[0] == "projection"][-1]
    assert proj["position"] == {"state": "moving", "lat": mid[0], "lng": mid[1]} and proj["now"] == "13:24"


def test_ignored_points(env):
    env.start("12:20")
    lat, lng = env.coords(LUNCH)
    body = {"points": [
        {"lat": lat, "lng": lng, "accuracy": 10, "recorded_at": at("12:21").isoformat()},
        {"lat": lat, "lng": lng, "accuracy": 150, "recorded_at": at("12:22").isoformat()},  # 부정확
        {"lat": lat, "lng": lng, "accuracy": 10, "recorded_at": at("12:20").isoformat()},  # 순서 뒤바뀜
        {"lat": lat, "lng": lng, "recorded_at": at("12:23").isoformat()},  # accuracy 없음 -> 30
    ]}
    r = ok(env.c.post(env.url("/location"), json=body, headers=H))
    assert (r["accepted"], r["ignored"]) == (2, 2)
    assert [e["type"] for e in r["events"]] == ["arrived"]
    assert r["state"]["location"]["accuracy"] == 30
    err(env.c.post(env.url("/location"), json={"lat": 91, "lng": 0}, headers=H), 422, "invalid_request")
    err(env.c.post(env.url("/location"), json={"points": []}, headers=H), 422, "invalid_request")


def test_planner_down_fails_location(env):
    env.start("12:20")
    env.planner.down = True
    lat, lng = env.coords(LUNCH)
    err(env.c.post(env.url("/location"), json={"lat": lat, "lng": lng}, headers=H), 503, "planner_unavailable")


# ---------- 지연 ----------

def test_delay_once_and_again_when_worse(env):
    visited_until_port(env)
    env.start("14:10")
    events = env.stay(HOUSE, "14:10", 3)
    assert [e["type"] for e in events] == ["arrived"] and events[0]["payload"]["out_of_order"] is False
    # 가벼운 지연: 15분(TRIP_BEHIND_MIN) 미만으로 늦고 새 위반이 없으면 알리지 않는다
    for t in ("15:00", "15:20", "15:30"):
        r = env.loc(HOUSE, t)
        assert r["events"] == []
    assert r["state"]["delay_minutes"] == 14
    # 15분 넘게 밀림 -> 일정 밀림 (위반 없음)
    [b] = env.loc(HOUSE, "15:35")["events"]
    assert b["payload"]["reason"] == "behind_schedule" and b["payload"]["delay_minutes"] == 19
    # 역사관이 17:30 마감을 넘음 -> delay (단계 3, Agent 선제 턴)
    r = env.loc(HOUSE, "15:45")
    [d] = r["events"]
    assert (d["type"], d["level"], d["status"]) == ("delay", 3, "queued")  # 응답 시점에는 큐에 있다
    assert d["item_ids"] == [item_id(env, 5)] and "운영시간" in d["message"]
    # 29분 늦음: 밀림은 지난 알림(19분) + 15 전이라 위반이 이유
    assert d["payload"]["reason"] == "violations" and d["payload"]["delay_minutes"] == 29
    sent = env.agent.received[-1]
    assert sent[1]["id"] == d["id"] and sent[2]["active"] is True  # {"event", "trip"}
    delivered = env.events(after=d["id"] - 1)[0]
    assert delivered["status"] == "delivered" and delivered["agent"]["proposal_id"] == 902
    # 같은 지연은 다시 알리지 않는다 (위반 그대로, 밀림은 29 + 15 = 44분 전)
    for t in ("15:50", "15:55"):
        assert env.loc(HOUSE, t)["events"] == []
    # 더 나빠짐 (18:00 초과가 새로 생김) -> 다시 (밀림도 함께지만 이벤트는 하나, 이유는 새 위반)
    [d2] = env.loc(HOUSE, "16:15")["events"]
    assert d2["type"] == "delay" and d2["payload"]["reason"] == "violations"
    assert {v["code"] for v in d2["payload"]["new_violations"]} == {"outside_open_hours", "day_overflow"}
    assert len(env.agent.received) == 3


def test_delay_detected_by_tick_without_location(env):
    visited_until_port(env)
    env.start("14:10")
    env.stay(HOUSE, "14:10", 3)
    assert env.tick("15:30")["events"] == []
    [d] = env.tick("15:45")["events"]
    assert d["type"] == "delay"
    assert env.tick("15:55")["events"] == []


# ---------- 해소된 위반 다시 알리기 (§4.1 결정 2026-10-12) ----------

# 빠듯한 계획: 가옥거리 12:30~13:30 -> 구룡포항 13:39~14:29 -> 과메기문화관 14:42~15:42, 15:47 까지 (여유 5분)
TIGHT = {"title": "빠듯", "start_date": "2026-10-10", "day_start": "12:30", "day_end": "15:47",
         "items": [{"place_id": HOUSE, "stay_minutes": 60}, {"place_id": PORT, "stay_minutes": 50},
                   {"place_id": MUSEUM, "stay_minutes": 60}]}


def test_violation_alerted_again_after_accept_resolves_it(env):
    """사용자 로컬 스택에서 본 상황: 하루 초과(day_overflow) 알림 → 제안 승인으로 사라짐 → 오래 머물러 같은 항목의 하루 초과가 다시.
    위반이 10분(TRIP_RESOLVE_MIN) 이어서 사라졌으면 해소로 보고 다시 알린다. 밀림도 승인한 일정 기준 0 부터 센다."""
    env.plan_id = env.planner.create(USER, TIGHT)
    env.start("12:25")
    env.stay(HOUSE, "12:30", 3)
    [d1] = env.tick("13:36")["events"]  # 15:48 종료 (6분 늦음) -> 하루 초과
    assert d1["payload"]["reason"] == "violations" and d1["payload"]["plan_version"] == 1
    overflow = cause(d1)
    assert overflow == f"delay:day_overflow:1:{item_id(env, 3)}:None"
    pid = env.events(after=d1["id"] - 1)[0]["agent"]["proposal_id"]
    env.planner.accept(env.plan_id, pid, {MUSEUM: 45})  # 과메기문화관 60 -> 45분 승인 (version 2)
    assert env.tick("13:40")["events"] == []  # 15:37 종료: 위반이 사라짐 (13:40 부터)
    # 승인한 일정 기준으로 새로 센다: 구룡포항 13:39 예정 -> 15분 늦으면 묻는다 (승인 전 지연 6분과 상관없이)
    [d2] = env.tick("13:45")["events"]
    assert d2["payload"]["reason"] == "behind_schedule" and cause(d2) == f"delay:behind:1:{d1['id']}a:1"
    assert env.trip().delay_state["accepted"] == {"event": d1["id"], "version": 2, "ok": True}
    assert env.events(after=d2["id"] - 1)[0]["status"] == "queued"  # 쿨다운 (13:36 선제 턴 뒤 10분)
    assert env.tick("13:50")["events"] == []  # 위반이 10분째 없음 -> 해소 (다시 생기면 알린다)
    assert env.events(after=d2["id"] - 1)[0]["status"] == "delivered"
    assert env.trip().delay_state["released"] == {overflow: d2["id"]}
    [d3] = env.tick("13:51")["events"]  # 15:48 종료 -> 같은 항목의 하루 초과가 다시 -> 다시 알린다
    assert d3["payload"]["reason"] == "violations" and overflow in cause(d3).split("|")
    assert env.events(after=d3["id"] - 1)[0]["status"] == "queued"  # 쿨다운 (13:50 선제 턴 뒤 10분)
    assert env.tick("13:52")["events"] == []  # 다시 알린 것은 다시 센다
    assert env.tick("13:55")["events"] == [] and len(env.agent.received) == 2
    env.tick("14:00")
    assert [e["type"] for _, e, _ in env.agent.received] == ["delay", "delay", "delay"]
    assert env.agent.received[-1][1]["id"] == d3["id"]
    # 승인 확인은 version 이 바뀔 때 한 번만 planner 에 묻는다
    assert [c for c in env.planner.calls if c[0] == "proposals"] == [("proposals", env.plan_id)]


def _inject(env, violations: list[dict]) -> None:
    """가짜 planner 재계산의 new_violations 에 violations 를 덧붙인다 (테스트가 목록을 바꿔 위반을 켜고 끈다)."""
    orig = env.planner.projection

    def projection(plan_id, body, auth):
        r = orig(plan_id, body, auth)
        r["new_violations"] = r["new_violations"] + list(violations)
        return r
    env.planner.projection = projection


FIXED = {"code": "fixed_time_missed", "severity": "error", "day": 1, "item_id": 7, "constraint_id": None,
         "message": "고정 시각을 놓쳐요."}


def test_flapping_violation_is_not_repeated(env, monkeypatch):
    """위반이 켜졌다 꺼졌다 해도(TRIP_RESOLVE_MIN 안) 선제 턴은 한 번. 해소가 짧아도(1분) 쿨다운(10분) 안에는 하나만."""
    monkeypatch.setattr(service.settings, "behind_min", 0)  # 위반만 본다
    roomy(env)
    on: list[dict] = []
    _inject(env, on)
    on.append(FIXED)
    [d] = env.tick("13:00")["events"]
    for i, t in enumerate(("13:02", "13:04", "13:06", "13:08", "13:10", "13:12", "13:14", "13:16")):
        on[:] = [] if i % 2 == 0 else [FIXED]  # 2분마다 깜빡임 (10분 이어서 사라진 적 없음)
        assert env.tick(t)["events"] == []
    assert len(env.agent.received) == 1 and len(behind_events(env)) == 1
    # 해소 기준이 짧으면 다시 알리지만, 쿨다운 안에서는 큐에서 기다리다 사라지면 버려진다 -> 선제 턴은 쿨다운마다 하나
    monkeypatch.setattr(service.settings, "resolve_min", 1)
    for i, t in enumerate(("13:18", "13:20", "13:22", "13:24", "13:26", "13:28", "13:30", "13:32", "13:34")):
        on[:] = [] if i % 2 == 0 else [FIXED]
        env.tick(t)
    turns = [e for e in behind_events(env) if e["status"] == "delivered"]
    times = [stored(e).delivered_at for e in turns]
    assert len(turns) <= 3 and all(b - a >= timedelta(minutes=10) for a, b in zip(times, times[1:]))
    assert len(env.agent.received) == len(turns)


# ---------- 일정 밀림 (§3.2 결정 2026-10-11) ----------

# 여유 있는 계획: 가옥거리 12:30~13:30 -> 구룡포항 13:39~14:29 -> 과메기문화관 14:35~15:35 (18:00 까지 여유)
ROOMY = {"title": "여유", "start_date": "2026-10-10", "day_start": "12:30", "day_end": "18:00",
         "items": [{"place_id": HOUSE, "stay_minutes": 60}, {"place_id": PORT, "stay_minutes": 50},
                   {"place_id": MUSEUM, "stay_minutes": 60}]}


def roomy(env) -> None:
    env.plan_id = env.planner.create(USER, ROOMY)
    env.start("12:25")
    assert [e["type"] for e in env.stay(HOUSE, "12:30", 3)] == ["arrived"]


def behind_events(env) -> list[dict]:
    return [e for e in env.events() if e["type"] == "delay"]


def test_behind_schedule_asks_every_15_minutes_then_fresh_after_accept(env):
    roomy(env)
    planned = env.planner.get_plan(env.plan_id, {})["days"][0]["items"][1]["start_at"]
    assert env.tick("13:44")["events"] == []  # 가옥거리에 머문 채 14분 늦음: 조용
    [d] = env.tick("13:45")["events"]  # 15분 -> 묻는다 (위반 없음)
    assert (d["type"], d["level"], d["payload"]["reason"]) == ("delay", 3, "behind_schedule")
    assert d["message"] == f"구룡포항 도착이 {planned} 예정에서 {d['payload']['next']['eta']}으로 15분 늦어질 것 같아요."
    assert d["item_ids"] == [item_id(env, 2)] and d["payload"]["new_violations"] == []
    assert d["payload"]["suggest"] == {"kind": "extend_stay", "item_id": item_id(env, 1), "title": "구룡포 일본인 가옥거리",
                                       "stay_minutes": 90, "from_stay_minutes": 60}  # 12:30 ~ 13:45 + 15분
    assert env.events(after=d["id"] - 1)[0]["status"] == "delivered"
    for t in ("13:50", "13:59"):  # 같은 단계: 조용
        assert env.tick(t)["events"] == []
    [d2] = env.tick("14:00")["events"]  # 30분 (지난 알림 15분 + 15) -> 다시
    assert d2["payload"]["reason"] == "behind_schedule" and d2["payload"]["delay_minutes"] == 30
    assert d2["payload"]["suggest"]["stay_minutes"] == 105
    assert len(env.agent.received) == 2
    # 제안 승인 (가옥거리 105분, version 이 오른다) -> 승인한 일정 기준으로 0 부터 센다
    pid = env.events(after=d2["id"] - 1)[0]["agent"]["proposal_id"]
    env.planner.accept(env.plan_id, pid, {HOUSE: 105})
    assert env.planner.get_plan(env.plan_id, {})["days"][0]["items"][1]["start_at"] > planned
    for t in ("14:05", "14:20", "14:29"):  # 새 일정(14:15 까지 가옥거리)보다 14분 늦을 때까지 조용
        assert env.tick(t)["events"] == []
    assert env.state()["delay_minutes"] == 14
    [d3] = env.tick("14:30")["events"]  # 새 기준으로 15분 -> 다시 묻는다
    assert d3["payload"]["reason"] == "behind_schedule" and d3["payload"]["plan_version"] == 2
    assert cause(d3) == f"delay:behind:1:{d2['id']}a:1"
    assert len(behind_events(env)) == 3


def test_behind_schedule_not_fresh_when_plan_changed_without_accepting(env):
    """계획이 바뀌었어도 알림 뒤 제안을 승인한 것이 아니면(직접 고침) 지난 알림 기준 그대로 (지연이 15분 이상 줄면 새로 센다)."""
    roomy(env)
    [d] = env.tick("13:50")["events"]  # 20분
    env.planner.set_stay(env.plan_id, MUSEUM, 30)  # 뒤 장소만 줄임: 다음(구룡포항) 지연은 그대로
    assert env.tick("14:04")["events"] == []  # 34분: 지난 알림 20 + 15 전
    assert env.trip().delay_state["accepted"] == {"event": d["id"], "version": 2, "ok": False}
    [d2] = env.tick("14:05")["events"]
    assert cause(d2) == f"delay:behind:1:{d['id']}:1"


def test_behind_schedule_same_step_is_quiet_after_moving_on(env):
    """한 번 물은 지연은 다음 항목이 바뀌어도(이동·도착) 그보다 15분 더 밀리기 전에는 다시 묻지 않는다."""
    roomy(env)
    [d] = env.tick("14:05")["events"]
    walk = env.stay(PORT, "14:15", 5)  # 가옥거리를 떠나 구룡포항 도착 (다음은 과메기문화관)
    assert [e["type"] for e in walk] == ["departed", "arrived"]
    # 늦게 온 구룡포항은 예정 종료(14:29)에 떠난다고 본다 (규칙 B) -> 그 뒤로 오래 머물러 다시 35분쯤 늦음 (지난 알림 35분 + 15 전)
    assert env.tick("15:05")["events"] == [] and behind_events(env) == [env.events(after=d["id"] - 1)[0]]
    assert env.state()["delay_minutes"] >= 30 and env.state()["next"]["item_id"] == item_id(env, 3)


def test_behind_schedule_queue_and_cooldown(env, monkeypatch):
    roomy(env)
    env.agent.busy = True  # Agent 가 응답 중 -> 큐에 둔다
    [d1] = env.tick("14:05")["events"]
    [d2] = env.tick("14:35")["events"]
    # 같은 원인 묶음(delay): 새것이 큐의 옛것을 대체
    assert [(e["id"], e["status"]) for e in behind_events(env)] == [(d1["id"], "suppressed"), (d2["id"], "queued")]
    env.agent.busy = False
    env.tick("14:36")
    assert [e["status"] for e in behind_events(env)] == ["suppressed", "delivered"]
    # 쿨다운(10분) 안에 생긴 delay 는 큐에서 기다린다
    monkeypatch.setattr(service.settings, "proactive_cooldown_min", 40)
    [d3] = env.tick("15:05")["events"]  # 95분 -> 다시 (지난번 65분 + 15 넘음)
    assert env.events(after=d3["id"] - 1)[0]["status"] == "queued"
    env.tick("15:17")
    assert env.events(after=d3["id"] - 1)[0]["status"] == "delivered" and len(env.agent.received) == 2


# ---------- 단계 3 큐 ----------

def test_queue_one_at_a_time_priority_and_cooldown(env):
    visited_until_port(env)
    env.start("14:10")
    env.stay(HOUSE, "14:10", 3)
    env.agent.busy = True  # Agent 세션이 응답 중 (409 agent_busy)
    [d] = env.loc(HOUSE, "15:45")["events"]
    ev = env.events(after=d["id"] - 1)[0]
    assert ev["status"] == "queued" and ev["attempts"] == 0 and "agent_busy" in ev["last_error"]  # 응답 중은 실패가 아님
    env.planner.add_alert(env.plan_id, "raised", [(item_id(env, 4), "safe", "caution")])
    tick = env.tick("15:46")
    assert [e["type"] for e in tick["events"]] == ["safety_raised"]
    assert [e["status"] for e in env.events() if e["level"] == 3] == ["queued", "queued"]

    # 보내는 도중에 다른 전송(틱)이 들어와도 두 번째 것을 같이 보내지 않는다
    env.agent.busy = False
    concurrent = []
    env.agent.during_send = lambda: concurrent.append(service.dispatch(_trip_id(env), env.deps, env.clock))
    env.tick("15:47")
    assert concurrent == [0]
    # 안전이 먼저, 지연은 쿨다운(10분) 동안 큐에 남는다
    assert [r[1]["type"] for r in env.agent.received] == ["safety_raised"]
    assert [e["status"] for e in env.events() if e["level"] == 3] == ["queued", "delivered"]
    env.tick("15:52")
    assert len(env.agent.received) == 1
    env.tick("15:58")
    assert [r[1]["type"] for r in env.agent.received] == ["safety_raised", "delay"]


def test_newer_delay_suppresses_older_in_queue(env):
    visited_until_port(env)
    env.start("14:10")
    env.stay(HOUSE, "14:10", 3)
    env.agent.busy = True
    env.loc(HOUSE, "15:45")
    env.loc(HOUSE, "16:15")  # 더 나빠짐 -> 새 delay
    env.agent.busy = False
    env.tick("16:16")
    delays = [e for e in env.events() if e["type"] == "delay"]
    assert [e["status"] for e in delays] == ["suppressed", "delivered"]
    assert len(env.agent.received) == 1


def test_agent_unavailable_keeps_queued(env):
    """agent 의 선제 턴 API 가 아직 없거나 실패하면 queued 로 두고 다음 틱에 다시 보낸다."""
    visited_until_port(env)
    env.start("14:10")
    env.stay(HOUSE, "14:10", 3)
    env.agent.missing = True
    [d] = env.loc(HOUSE, "15:45")["events"]
    env.tick("15:46")
    ev = env.events(after=d["id"] - 1)[0]
    assert ev["status"] == "queued" and ev["attempts"] == 2 and ev["agent"] is None
    env.agent.missing = False
    env.tick("15:47")
    ev = env.events(after=d["id"] - 1)[0]
    assert ev["status"] == "delivered" and ev["last_error"] is None
    assert ev["agent"] == {"session_id": f"sess-{env.plan_id}-0", "message_id": 501, "proposal_id": 901}


def test_agent_failures_back_off_then_fail(env):
    """접근 불가·오류는 간격을 늘려(30초 → 60초) 다시 보내고, 3번 실패하면 failed 로 남겨 더 보내지 않는다."""
    visited_until_port(env)
    env.start("14:10")
    env.stay(HOUSE, "14:10", 3)
    env.agent.missing = True
    [d] = env.loc(HOUSE, "15:45")["events"]

    def ev():
        return next(e for e in env.events() if e["id"] == d["id"])

    assert (ev()["status"], ev()["attempts"]) == ("queued", 1)
    env.tick("15:45:20")  # 30초가 안 지남 -> 보내지 않는다
    assert ev()["attempts"] == 1
    env.tick("15:45:30")
    assert (ev()["status"], ev()["attempts"]) == ("queued", 2)
    env.tick("15:46:20")  # 두 번째 실패 뒤에는 60초
    assert ev()["attempts"] == 2
    env.tick("15:46:30")
    e = ev()
    assert (e["status"], e["attempts"], e["agent"]) == ("failed", 3, None) and "Not Found" in e["last_error"]
    # 고쳐져도 failed 는 다시 보내지 않는다 (앱은 이벤트 목록에서 실패를 본다)
    env.agent.missing = False
    env.tick("15:50")
    assert ev()["status"] == "failed" and env.agent.received == []
    v = ok(env.c.post(env.url(f"/events/{d['id']}/seen"), headers=H))
    assert v["status"] == "failed" and v["seen_at"]


def test_agent_failure_retry_when_clock_goes_back(env):
    """가상 시각을 되돌리면(실패 시각보다 이름) 기다리지 않고 다시 보낸다."""
    visited_until_port(env)
    env.start("14:10")
    env.stay(HOUSE, "14:10", 3)
    env.agent.missing = True
    [d] = env.loc(HOUSE, "15:45")["events"]
    env.agent.missing = False
    env.clock.when = at("15:44")
    assert service.dispatch(_trip_id(env), env.deps, env.clock) == 1
    assert next(e for e in env.events() if e["id"] == d["id"])["status"] == "delivered"


def test_busy_does_not_count_as_failure(env):
    visited_until_port(env)
    env.start("14:10")
    env.stay(HOUSE, "14:10", 3)
    env.agent.busy = True
    [d] = env.loc(HOUSE, "15:45")["events"]
    for hhmm in ("15:46", "15:47", "15:48", "15:49"):
        env.tick(hhmm)
    e = next(e for e in env.events() if e["id"] == d["id"])
    assert (e["status"], e["attempts"]) == ("queued", 0)
    env.agent.busy = False
    env.tick("15:50")
    assert next(e for e in env.events() if e["id"] == d["id"])["status"] == "delivered"


# ---------- 안전 ----------

def test_safety_alerts_collected_by_tick(env):
    env.start("12:20")
    env.stay(LUNCH, "12:20", 2)
    env.planner.on_scenario("storm")  # 짬홍 위험, 구룡포항·가옥거리 주의
    [e] = env.tick("12:30")["events"]
    assert (e["type"], e["level"], e["severity"]) == ("safety_raised", 3, "danger")
    assert e["item_ids"] == [item_id(env, n) for n in (1, 2, 3)]
    assert e["message"].startswith("남은 일정에 위험 신호가 생겼어요.") and "그 밖에 2건" in e["message"]
    assert [c for c in env.planner.calls if c[0] == "seen"] == [("seen", 1)]
    assert env.agent.received[0][1]["type"] == "safety_raised"
    assert env.tick("12:31")["events"] == []  # 가져간 경고는 다시 보지 않는다
    # 해제 -> 단계 1 알림
    env.planner.add_alert(env.plan_id, "cleared", [(item_id(env, 2), "caution", "safe")])
    [eased] = env.tick("12:40")["events"]
    assert (eased["type"], eased["level"], eased["status"]) == ("safety_eased", 1, "delivered")
    assert eased["message"] == "구룡포항의 위험 신호가 해제됐어요."


def test_alerts_for_done_items_or_other_days_are_ignored(env):
    env.start("12:20")
    it = env.planner.item(env.plan_id, 1)
    it["status"] = "done"
    env.planner.add_alert(env.plan_id, "raised", [(it["id"], "safe", "danger")])
    assert env.tick("12:30")["events"] == []
    assert [c for c in env.planner.calls if c[0] == "seen"] == []  # 가져가지 않았다 (계획 화면 배지로 남는다)


def test_near_hazard(env):
    env.start("12:20")
    env.safety.scenario = "storm"
    near = (35.991235 - 100 / 111_320, 129.557327)  # 수협 맨홀 남쪽 100m (구역 경계 밖 약 20m)
    [e] = env.loc(near, "12:21")["events"]
    assert (e["type"], e["level"], e["severity"], e["status"]) == ("near_hazard", 1, "danger", "delivered")
    assert e["message"] == "침수 감시 구역(구룡포수협_스마트맨홀 주변) 근처예요. 물이 고인 길은 피하세요."
    assert env.loc(near, "12:22")["events"] == []  # 같은 구역은 다시 알리지 않는다
    env.safety.scenario = "calm"
    assert env.loc(near, "12:23")["events"] == []
    assert env.agent.received == []  # LLM 없이


# ---------- 종료·기타 ----------

def test_end_suppresses_queued(env):
    visited_until_port(env)
    env.start("14:10")
    env.stay(HOUSE, "14:10", 3)
    env.agent.busy = True
    [d] = env.loc(HOUSE, "15:45")["events"]
    s = ok(env.c.post(env.url("/end"), headers=H))
    assert s["active"] is False
    events = env.events()
    assert next(e for e in events if e["id"] == d["id"])["status"] == "suppressed"
    assert events[-1]["type"] == "day_ended" and events[-1]["payload"] == {"reason": "user"}


def test_auto_end_by_tick(env):
    env.start("12:20")
    assert env.tick("19:00")["state"]["active"] is True  # 마지막 일정 17:06 + 120분 = 19:06
    r = env.tick("19:07")
    assert r["state"]["active"] is False and r["state"]["ended_reason"] == "auto"
    assert r["events"][-1]["type"] == "day_ended"


def test_tick_all_runs_active_trips(env):
    visited_until_port(env)
    env.start("14:10")
    env.stay(HOUSE, "14:10", 3)
    env.clock.when = at("15:45")
    assert service.tick_all(env.deps, env.clock, users=["test-trip-user"]) == 1
    assert "delay" in env.types() and len(env.agent.received) == 1


def test_seen(env):
    env.start("12:20")
    [e] = env.stay(LUNCH, "12:20", 2)
    v = ok(env.c.post(env.url(f"/events/{e['id']}/seen"), headers=H))
    assert v["status"] == "handled" and v["seen_at"]
    err(env.c.post(env.url("/events/999999/seen"), headers=H), 404, "event_not_found")
    err(env.c.post(env.url(f"/events/{e['id']}/seen"), headers={"X-User-Id": "test-trip-other"}), 404, "event_not_found")


def test_delete_me(env):
    env.start("12:20")
    env.stay(LUNCH, "12:20", 2)
    assert env.c.delete("/trip/me", headers=H).status_code == 204
    assert env.state()["active"] is False and env.events() == []


def _trip_id(env) -> int:
    with SessionLocal() as db:
        return service.latest_trip(db, "test-trip-user", env.plan_id).id


def test_near_places_progress_order(env):
    """가옥거리 -> 104m 떨어진 역사관: 같은 요청에서 planner 진행 상태는 가옥거리 done 다음 역사관 visiting, 이벤트도 departed 다음 arrived."""
    visited_until_port(env)
    env.start("14:10")
    env.stay(HOUSE, "14:10", 3)
    (hlat, hlng), (mlat, mlng) = env.coords(HOUSE), env.coords(HISTORY)
    t0, pts = at("14:30"), []
    for i in range(5):  # 25m 간격 걷기 (70m/분)
        f = min(i * 25 / 104, 1)
        pts.append((hlat + (mlat - hlat) * f, hlng + (mlng - hlng) * f, t0 + timedelta(seconds=i * 21.4)))
    pts += [(mlat, mlng, t0 + timedelta(seconds=86 + s)) for s in range(30, 181, 30)]
    env.clock.when = pts[-1][2]
    before = len(progress_calls(env))
    r = ok(env.c.post(env.url("/location"), headers=H, json={"points": [
        {"lat": la, "lng": ln, "accuracy": 10, "recorded_at": t.isoformat()} for la, ln, t in pts]}))
    assert [e["type"] for e in r["events"]] == ["departed", "arrived"]
    assert r["events"][0]["message"] == "구룡포 일본인 가옥거리을(를) 떠났어요."  # 다음 일정·도착 예정은 이미 지난 이야기
    arr = r["events"][1]
    assert arr["payload"]["place_id"] == HISTORY and datetime.fromisoformat(arr["now"]) == t0 + timedelta(seconds=3 * 21.4)
    assert [(c[0], c[1]) for c in progress_calls(env)[before:]] == [(item_id(env, 3), "done"), (item_id(env, 5), "visiting")]
    assert r["state"]["current"]["item_id"] == item_id(env, 5)
