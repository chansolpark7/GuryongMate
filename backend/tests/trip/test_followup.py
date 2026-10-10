"""여행 중 후속 수정 (docs/trip/README.md §1.2, §3.2, §4.1, 2026-10-11):
1. 앱 활동(위치 전송·이벤트 조회)이 TRIP_ACTIVE_MIN(15분) 넘게 없으면 단계 3 전송 보류 → 돌아오면 재판정 후 지금도 유효한 최신 한 건만
2. 자동 종료: 계획 종료 + 120분이 지나도 최근 위치가 있으면 미룸, 위치가 끊기고 15분 뒤 종료, 자정은 그대로
3. 상황 해제: 이 여행일에 주의·위험이던 safety 상황이 모두 평상시로 → safety_eased (situation_cleared) 한 번
4. 여행일이 끝나면(사용자·자동·다른 날 시작) 그날 visiting 항목을 done 으로 정리, 곧 다시 열면 되돌림

이벤트 상태는 DB 에서 직접 읽는다 (GET events 는 앱 활동이라 보류를 풀어 버린다).
"""
from datetime import date, datetime, timedelta

from sqlalchemy import select

from app.db import SessionLocal
from app.trip import judge, policy, service
from app.trip.clock import KST
from app.trip.models import TripEvent

from .conftest import H, USER, at, ok
from .fakes import PLACES
from .test_api import HISTORY, HOUSE, LUNCH, PORT, ROOMY, item_id, visited_until_port


def rows(env) -> list[TripEvent]:
    with SessionLocal() as s:
        return list(s.scalars(select(TripEvent).where(TripEvent.user_id == USER, TripEvent.plan_id == env.plan_id)
                              .order_by(TripEvent.id)))


def level3(env) -> list[tuple[str, str]]:
    return [(e.type, e.status) for e in rows(env) if e.level == 3]


def sent(env) -> list[str]:
    return [r[1]["type"] for r in env.agent.received]


def points(place, start: str, end: str, every_sec: int = 60) -> dict:
    lat, lng = PLACES[place]["lat"], PLACES[place]["lng"]
    t, t1, out = at(start), at(end), []
    while t <= t1:
        out.append({"lat": lat, "lng": lng, "accuracy": 10, "recorded_at": t.isoformat(), "source": "app"})
        t += timedelta(seconds=every_sec)
    return {"points": out}


# ---------- 1. 앱 활동 없음 → 단계 3 보류 ----------

def test_no_activity_holds_level3_then_sends_latest_valid_once(env):
    visited_until_port(env)
    env.start("14:10")
    env.stay(HOUSE, "14:10", 3)  # 마지막 활동(위치) 14:13
    # 앱이 꺼짐: 틱만 돈다. 지연·안전 이벤트는 만들되 보내지 않는다 (시도 수도 세지 않음)
    [d1] = env.tick("15:45", app=False)["events"]
    [d2] = env.tick("16:15", app=False)["events"]  # 더 나빠짐 -> 새 delay
    env.planner.add_alert(env.plan_id, "raised", [(item_id(env, 4), "safe", "caution")])
    [s1] = env.tick("16:20", app=False)["events"]
    assert (d1["type"], d2["type"], s1["type"]) == ("delay", "delay", "safety_raised")
    held = [(e.type, e.status, e.attempts, e.last_error) for e in rows(env) if e.level == 3]
    assert held == [("delay", "suppressed", 0, service.HELD),  # 큐에서 새 delay 가 대체 (지금과 같은 규칙)
                    ("delay", "queued", 0, service.HELD), ("safety_raised", "queued", 0, service.HELD)]
    assert env.agent.received == []

    # 앱이 다시 열림 (이벤트 조회) -> 재판정 후 지금도 유효한 것 중 안전 하나만 보낸다. 지연은 suppressed
    env.clock.when = at("16:25")
    env.events()
    assert sent(env) == ["safety_raised"]
    assert level3(env) == [("delay", "suppressed"), ("delay", "suppressed"), ("safety_raised", "delivered")]
    s = next(e for e in rows(env) if e.id == s1["id"])
    assert (s.attempts, s.last_error) == (1, None)
    assert env.trip().active_at == at("16:25")  # 활동 시각은 trip 의 지금
    # 계속 열려 있으면 다시 재판정하지 않는다 (같은 지연이 다시 나오지도 않는다)
    env.clock.when = at("16:26")
    env.events()
    assert sent(env) == ["safety_raised"] and len(level3(env)) == 3


def test_activity_return_drops_delay_that_is_no_longer_valid(env):
    """앱이 꺼진 사이 실제로는 제때 움직였다: 돌아와 모아 둔 위치를 보내면 지연이 풀려 보류한 delay 는 보내지 않는다."""
    env.plan_id = env.planner.create(USER, ROOMY)  # 가옥거리 12:30~13:30 -> 구룡포항 13:39~14:29 -> 과메기문화관 14:35
    env.start("12:25")
    env.stay(HOUSE, "12:30", 3)  # 마지막 활동 12:33
    [d] = env.tick("14:05", app=False)["events"]  # 위치가 안 들어와 가옥거리에 계속 머무는 것으로 본다 -> 35분 밀림
    assert (d["type"], d["payload"]["reason"], d["status"]) == ("delay", "behind_schedule", "queued")
    assert env.agent.received == []
    # 다시 연결: 13:40 부터 구룡포항에 있었던 점들을 한 번에 (§2.2)
    env.clock.when = at("14:10")
    r = ok(env.c.post(env.url("/location"), json=points(PORT, "13:40", "14:10"), headers=H))
    # 단계 0~2 는 지금처럼 바로 delivered
    assert [(e["type"], e["status"]) for e in r["events"]] == [("departed", "delivered"), ("arrived", "delivered")]
    assert r["state"]["delay_minutes"] < 30
    assert level3(env) == [("delay", "suppressed")] and env.agent.received == []
    assert env.trip().located_at == at("14:10")


def test_levels_0_to_2_not_affected_by_inactivity(env):
    env.start("12:20")
    env.safety.scenario = "storm"
    env.set_active("11:00")  # 활동이 오래전
    near = (35.991235 - 100 / 111_320, 129.557327)
    lat, lng = near
    env.clock.when = at("12:21")
    r = ok(env.c.post(env.url("/location"), json={"lat": lat, "lng": lng, "accuracy": 10,
                                                   "recorded_at": at("12:21").isoformat()}, headers=H))
    assert [(e["type"], e["level"], e["status"]) for e in r["events"]] == [("near_hazard", 1, "delivered")]


def test_active_app_sends_immediately(env):
    """앱이 켜져 있으면 지금처럼 바로 보낸다 (위치가 없어도 이벤트 조회가 있으면 활동)."""
    visited_until_port(env)
    env.start("14:10")
    env.stay(HOUSE, "14:10", 3)
    env.set_active("15:40")  # 15:40 에 이벤트 조회
    [d] = env.tick("15:45", app=False)["events"]
    assert d["type"] == "delay" and sent(env) == ["delay"]


def test_tick_now_ahead_of_clock_still_holds(env):
    """가상 시각을 크게 옮긴 직후 trip 시계 캐시가 늦어도, 틱의 판정 시각(now)으로 활동을 본다."""
    visited_until_port(env)
    env.start("14:10")
    env.stay(HOUSE, "14:10", 3)
    env.clock.when = at("14:14")  # trip 의 시계는 아직 옛 시각
    r = ok(env.c.post(env.url(f"/tick?now={at('15:45').isoformat().replace('+', '%2B')}"), headers=H))
    assert [e["type"] for e in r["events"]] == ["delay"]
    assert env.agent.received == [] and level3(env) == [("delay", "queued")]


def test_activity_disabled_with_zero(env, monkeypatch):
    monkeypatch.setattr(service.settings, "active_min", 0)
    visited_until_port(env)
    env.start("14:10")
    env.stay(HOUSE, "14:10", 3)
    env.tick("15:45", app=False)
    assert sent(env) == ["delay"]


# ---------- 2. 자동 종료 ----------

def test_auto_end_waits_while_still_traveling_late(env):
    env.start("12:20")  # 마지막 일정 17:06 + 120분 = 19:06
    env.stay(HISTORY, "19:00", 10, every_sec=300)  # 많이 늦은 채 아직 다니는 중 (마지막 위치 19:10)
    assert env.tick("19:10")["state"]["active"] is True
    assert env.tick("19:25")["state"]["active"] is True  # 위치가 끊긴 지 15분
    r = env.tick("19:26")
    assert r["state"]["active"] is False and r["state"]["ended_reason"] == "auto"
    assert r["events"][-1]["type"] == "day_ended"
    assert r["events"][-1]["payload"] == {"reason": "auto", "detail": "plan_end"}


def test_auto_end_without_recent_location_as_before(env):
    env.start("12:20")
    env.stay(LUNCH, "12:20", 2)
    assert env.tick("19:05")["state"]["active"] is True
    assert env.tick("19:07")["state"]["active"] is False


def test_auto_end_at_midnight_even_with_location(env):
    env.start("12:20")
    env.stay(HISTORY, "23:50", 9, every_sec=60)
    env.clock.when = datetime(2026, 10, 11, 0, 1, tzinfo=KST)
    r = ok(env.c.post(env.url("/tick"), headers=H))
    assert r["state"]["active"] is False
    assert r["events"][-1]["payload"] == {"reason": "auto", "detail": "midnight"}


# ---------- 3. 상황 해제 ----------

def eased(env) -> list[TripEvent]:
    return [e for e in rows(env) if e.type == "safety_eased"]


def test_situation_cleared_once_per_storm(env):
    env.start("12:20")
    env.stay(LUNCH, "12:20", 2)
    assert env.tick("12:25")["events"] == []  # 평상시 -> 알릴 것 없음
    env.safety.scenario = "storm"
    assert env.tick("12:30")["events"] == []  # 주의·위험 상황 시작 (planner 경고는 따로)
    assert env.tick("12:40")["events"] == []
    env.safety.scenario = "calm"
    [e] = env.tick("13:05")["events"]
    assert (e["type"], e["level"], e["status"], e["severity"]) == ("safety_eased", 1, "delivered", "info")
    assert e["message"] == "악천후가 그쳤어요. 지금 구룡포는 특이사항이 없어요."
    assert e["payload"]["reason"] == "situation_cleared" and e["payload"]["warnings"] == ["풍랑주의보"]
    assert "flood_4" in e["payload"]["zones"] and e["item_ids"] == []
    assert env.tick("13:06")["events"] == [] and env.tick("13:10")["events"] == []  # 중복 없음
    # 다시 폭풍 -> 다시 그치면 한 번 더
    env.safety.scenario = "flood"
    assert env.tick("13:15")["events"] == []
    env.safety.scenario = "calm"
    assert [x["type"] for x in env.tick("13:20")["events"]] == ["safety_eased"]
    assert [e.cause_key for e in eased(env)] == ["safety_eased:situation:1:1", "safety_eased:situation:1:2"]
    assert env.agent.received == []  # LLM 없음


def test_situation_cleared_merges_with_planner_eased_in_same_tick(env):
    env.start("12:20")
    env.stay(LUNCH, "12:20", 2)
    env.safety.scenario = "storm"
    env.planner.on_scenario("storm")
    assert [x["type"] for x in env.tick("12:30")["events"]] == ["safety_raised"]
    env.safety.scenario = "calm"
    env.planner.add_alert(env.plan_id, "cleared", [(item_id(env, 2), "caution", "safe")])
    [e] = env.tick("12:40")["events"]  # planner 완화와 상황 해제가 같은 틱 -> 하나만
    assert e["type"] == "safety_eased" and e["message"] == "구룡포항의 위험 신호가 해제됐어요."
    assert e["payload"]["situation_cleared"] is True
    assert env.tick("12:45")["events"] == []
    assert len(eased(env)) == 1


def test_situation_unknown_zone_is_not_cleared(env):
    env.start("12:20")
    env.safety.scenario = "storm"
    env.tick("12:30")
    sit = {"fingerprint": "x", "zones": {"flood_4": None, "flood_8": "safe", "breakwater_north": "safe"}, "warnings": []}
    env.safety.situation = lambda: sit  # 침수 센서 자료가 끊김 (값 없음) -> 해제라고 하지 않는다
    assert env.tick("12:40")["events"] == []
    sit["zones"]["flood_4"] = "safe"
    assert [x["type"] for x in env.tick("12:50")["events"]] == ["safety_eased"]


def test_held_safety_raised_dropped_when_situation_cleared(env):
    env.start("12:20")
    env.stay(LUNCH, "12:20", 2)  # 마지막 활동 12:22
    env.safety.scenario = "storm"
    env.planner.on_scenario("storm")
    env.tick("12:45", app=False)
    assert level3(env) == [("safety_raised", "queued")] and env.agent.received == []
    env.safety.scenario = "calm"
    env.tick("13:30", app=False)  # 앱이 꺼진 사이 폭풍이 그침
    env.clock.when = at("13:35")
    env.events()
    assert level3(env) == [("safety_raised", "suppressed")] and env.agent.received == []


# ---------- 순수 함수 ----------

T = datetime(2026, 10, 10, 15, 0, tzinfo=KST)


def test_is_active_and_bump():
    assert judge.is_active(None, T, 15)  # 기록 없음 (이 기능 전 여행일)
    assert judge.is_active(T - timedelta(minutes=15), T, 15)
    assert not judge.is_active(T - timedelta(minutes=16), T, 15)
    assert judge.is_active(T + timedelta(hours=1), T, 15)  # 활동이 지금보다 늦음 (시계 캐시·되돌림)
    assert judge.is_active(T - timedelta(hours=5), T, 0)  # 0 이면 끔
    assert judge.bump_active(T, T - timedelta(minutes=3)) == T  # 시계 캐시가 늦어도 줄지 않는다
    assert judge.bump_active(T, T + timedelta(minutes=1)) == T + timedelta(minutes=1)
    assert judge.bump_active(T, T - timedelta(days=1)) == T - timedelta(days=1)  # 가상 시각을 크게 되돌림


def test_auto_end_reason():
    d = date(2026, 10, 10)
    end = T.replace(hour=17)
    assert judge.auto_end_reason(end + timedelta(minutes=119), d, "17:00", 120, None, 15) is None
    assert judge.auto_end_reason(end + timedelta(minutes=120), d, "17:00", 120, None, 15) == "plan_end"
    late = end + timedelta(minutes=150)
    assert judge.auto_end_reason(late, d, "17:00", 120, late - timedelta(minutes=15), 15) is None
    assert judge.auto_end_reason(late, d, "17:00", 120, late - timedelta(minutes=16), 15) == "plan_end"
    assert judge.auto_end_reason(late, d, "17:00", 120, late, 0) == "plan_end"  # 0 이면 미루지 않음
    assert judge.auto_end_reason(datetime(2026, 10, 11, 0, 1, tzinfo=KST), d, None, 120, late, 15) == "midnight"


def test_judge_situation():
    calm = {"zones": {"a": "safe", "b": "safe"}, "warnings": []}
    st, det = judge.judge_situation(calm, None, T, 1)
    assert det is None and st["abnormal"] is False and st["n"] == 0
    st, det = judge.judge_situation({"zones": {"a": "caution", "b": "safe"}, "warnings": []}, st, T, 1)
    assert det is None and (st["abnormal"], st["n"], st["zones"]) == (True, 1, ["a"])
    st, det = judge.judge_situation({"zones": {"a": "safe", "b": "danger"}, "warnings": ["호우주의보"]}, st, T, 1)
    assert det is None and (st["n"], st["zones"], st["warnings"]) == (1, ["a", "b"], ["호우주의보"])
    st, det = judge.judge_situation({"zones": {"a": "safe", "b": None}, "warnings": []}, st, T, 1)
    assert det is None and st["abnormal"] is True  # b 를 모름
    st, det = judge.judge_situation(calm, st, T, 2)
    assert det.type == "safety_eased" and det.level == 1 and det.cause_key == "safety_eased:situation:2:1"
    assert det.payload["reason"] == "situation_cleared" and st["abnormal"] is False
    st, det = judge.judge_situation(calm, st, T, 2)
    assert det is None


def test_pick_resume():
    q = [policy.Queued(1, "delay"), policy.Queued(2, "safety_raised", (5,)), policy.Queued(3, "delay"),
         policy.Queued(4, "safety_raised", (6,))]
    assert policy.pick_resume(q, {1, 2, 3, 4}) == (4, [1, 2, 3])  # 안전 > 지연, 가장 새것
    assert policy.pick_resume(q, {1, 3}) == (3, [1, 2, 4])
    assert policy.pick_resume(q, set()) == (None, [1, 2, 3, 4])


# ---------- 4. 여행이 끝나면 머무는 중 항목 정리 ----------

def statuses(env) -> list[tuple[str, str | None, str | None]]:
    return [(i["status"], i["arrived_at"], i["done_at"]) for i in env.planner.plans[env.plan_id]["items"]]


def test_user_end_closes_visiting(env):
    visited_until_port(env)
    env.start("14:10")
    env.stay(HOUSE, "14:10", 3)  # 가옥거리 visiting (14:10)
    env.clock.when = at("14:30")
    s = ok(env.c.post(env.url("/end"), headers=H))
    assert s["active"] is False
    assert statuses(env) == [("done", "12:30", "13:30"), ("done", "13:37", "14:00"), ("done", "14:10", "14:30"),
                             ("planned", None, None), ("planned", None, None)]  # 다른 항목은 그대로
    assert ("progress", item_id(env, 3), "done", "14:30") in env.planner.calls


def test_auto_end_closes_visiting(env):
    env.start("12:20")
    env.stay(LUNCH, "12:20", 2)
    assert env.tick("19:07")["state"]["active"] is False
    assert statuses(env)[0] == ("done", "12:20", "19:07")


def test_new_day_start_closes_previous_visiting(env):
    env.plan_id = env.planner.create(USER, {**ROOMY, "end_date": "2026-10-11"})
    env.start("12:25")
    env.stay(HOUSE, "12:30", 3)
    env.clock.when = at("09:00").replace(day=11)
    s = ok(env.c.post(env.url("/start"), headers=H))
    assert (s["day"], s["current"]["state"]) == (2, "idle")
    assert statuses(env)[0] == ("done", "12:30", "23:59")  # 그날 23:59 로 정리
    assert statuses(env)[1] == ("planned", None, None)


def test_end_works_when_planner_down(env):
    env.start("12:20")
    env.stay(LUNCH, "12:20", 2)
    env.planner.down = True
    s = ok(env.c.post(env.url("/end"), headers=H))
    assert s["active"] is False and s["ended_reason"] == "user"
    env.planner.down = False
    assert statuses(env)[0][0] == "visiting"  # 정리는 못 했지만 종료는 됐다


def test_reopen_soon_after_end_restores_visiting(env):
    """실수로 끝냄: 15분 안에 다시 열면 정리했던 머무는 곳을 되돌린다."""
    env.start("12:20")
    env.stay(LUNCH, "12:20", 2)
    env.clock.when = at("12:25")
    ok(env.c.post(env.url("/end"), headers=H))
    assert statuses(env)[0] == ("done", "12:20", "12:25")
    s = env.start("12:35")
    assert s["current"] == {"item_id": item_id(env, 1), "state": "visiting", "since": "12:20"}
    assert statuses(env)[0] == ("visiting", "12:20", None)
    assert env.stay(LUNCH, "12:36", 2) == []  # 도착을 다시 알리지 않는다


def test_reopen_later_starts_idle(env):
    env.start("12:20")
    env.stay(LUNCH, "12:20", 2)
    env.clock.when = at("12:25")
    ok(env.c.post(env.url("/end"), headers=H))
    s = env.start("13:00")  # 35분 뒤: 정말 끝냈던 것 -> 머무는 곳 없이
    assert s["current"]["state"] == "idle" and statuses(env)[0] == ("done", "12:20", "12:25")
    assert env.stay(LUNCH, "13:01", 2) == []  # 이미 다녀온 곳이라 도착이 나지 않는다
