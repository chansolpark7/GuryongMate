"""로직 버그 점검 (docs/audit/logic-bugs-2026-10-12.md) trip 쪽 재현 테스트 — 큐·틱·전송 (실제 DB + 가짜 서비스).
판정 순수 함수(T1·T5 가·T6·T7 판정)는 test_judge.py, T4 는 test_time_audit.py 에 있다."""
from datetime import timedelta

from app.trip import policy, service
from app.trip.policy import Queued

from .conftest import DAY, H, at, ok
from .test_api import FIXED, HOUSE, PORT, _inject, item_id, roomy


def test_t3_queued_delay_refreshed_while_known_violation_continues(env, monkeypatch):
    """T3: 이미 알린 위반이 이어지는 동안(감지는 나오지만 policy.fresh 가 버림)에도 큐의 delay 를 지금 상황으로 고친다.
    가옥거리에서 위반 delay 가 큐에 남은 사이 구룡포항에 도착 -> 나중에 보내는 선제 턴의 '다음'은 과메기문화관이어야 한다."""
    monkeypatch.setattr(service.settings, "behind_min", 0)  # 위반만 본다
    roomy(env)
    _inject(env, [FIXED])  # 계속 이어지는 위반
    env.agent.busy = True
    [d] = env.tick("12:40")["events"]
    assert d["payload"]["reason"] == "violations" and d["payload"]["next"]["item_id"] == item_id(env, 2)
    walk = env.stay(PORT, "12:50", 5)
    assert [e["type"] for e in walk] == ["departed", "arrived"]
    env.agent.busy = False
    assert env.tick("12:56")["events"] == []  # 같은 위반이라 새 이벤트는 없다
    [(_, sent, _)] = env.agent.received
    assert sent["id"] == d["id"] and sent["payload"]["next"]["item_id"] == item_id(env, 3)


def test_t5_rewound_clock_location_and_cooldown(env):
    """T5: 같은 날 가상 시각을 되돌리면 (가) 위치를 버리지 않고 (나) 지난 전송이 '미래'라 쿨다운에 막히지 않는다."""
    roomy(env)
    env.loc(HOUSE, "14:00")
    r = env.loc(HOUSE, "12:40")  # 1시간 20분 되돌림
    assert (r["accepted"], r["ignored"]) == (1, 0)
    assert r["state"]["location"]["recorded_at"].startswith("2026-10-10T12:40")
    assert env.loc(HOUSE, "12:38")["ignored"] == 1  # 조금(10분 미만) 이른 점은 그대로 순서 뒤바뀜
    cool = timedelta(minutes=10)
    q = [Queued(5, "delay", (2,))]
    assert policy.pick_next(q, at("14:00"), at("12:40"), cool)[0] == q[0]  # 되돌린 시각: 쿨다운 아님
    assert policy.pick_next(q, at("12:40:05"), at("12:40"), cool)[0] is None  # 몇 초 차이(시계 캐시)는 쿨다운
    assert policy.pick_next(q, at("12:35"), at("12:40"), cool)[0] is None


def test_t7_raise_and_clear_in_same_tick(env):
    """T7: 같은 틱에 안전 상승과 그 뒤 해제가 함께 오면 이미 풀린 위험이라 safety_raised(선제 턴)를 내지 않는다."""
    roomy(env)
    port = item_id(env, 2)
    env.planner.add_alert(env.plan_id, "raised", [(port, "safe", "danger")])
    env.planner.add_alert(env.plan_id, "cleared", [(port, "danger", "safe")])
    events = env.tick("12:40")["events"]
    assert [e["type"] for e in events] == ["safety_eased"]
    assert env.agent.received == []
    assert [c for c in env.planner.calls if c[0] == "seen"] == [("seen", 1), ("seen", 2)]  # 둘 다 가져갔다


def test_t7_queued_raise_lifted_before_dispatch(env):
    """T7: 큐에서 기다리던 safety_raised 가 보내기 전에 해제되면 보내지 않는다 (dispatch 직전 재검사)."""
    roomy(env)
    port = item_id(env, 2)
    env.agent.busy = True
    env.planner.add_alert(env.plan_id, "raised", [(port, "safe", "danger")])
    [raised] = env.tick("12:40")["events"]
    env.agent.busy = False
    env.planner.add_alert(env.plan_id, "cleared", [(port, "danger", "safe")])
    assert [e["type"] for e in env.tick("12:41")["events"]] == ["safety_eased"]  # 틱 뒤 큐 전송
    assert env.agent.received == []
    assert next(e for e in env.events() if e["id"] == raised["id"])["status"] == "suppressed"


def test_t8_plan_deleted_ends_trip(env):
    """T8: 여행 중 계획을 지우면 틱에서 여행일을 끝낸다 (사유 auto, detail plan_deleted)."""
    roomy(env)
    del env.planner.plans[env.plan_id]
    r = env.tick("13:00")
    assert r["state"]["active"] is False and r["state"]["ended_reason"] == "auto"
    assert r["events"][-1]["type"] == "day_ended" and r["events"][-1]["payload"]["detail"] == "plan_deleted"


def test_t8_midnight_ends_even_without_plan(env):
    """T8: 자정이 지나면 계획을 받지 못해도(planner 접근 불가) 여행일을 끝낸다."""
    roomy(env)
    env.planner.down = True
    env.clock.when = at("00:05", DAY + timedelta(days=1))
    r = ok(env.c.post(env.url("/tick"), headers=H))
    assert r["state"]["active"] is False
    assert [e["type"] for e in r["events"]] == ["day_ended"] and r["events"][0]["payload"]["detail"] == "midnight"
