"""TripState.expected — 재계산한 예상 시각 (audit-2026-10-12 D2, docs/trip/README.md §1.3).

앱 Today 의 남은 일정은 늦을 때 '기준 → 예상'을 보인다. trip 은 위치·틱의 planner projection 결과를 저장해 두고
GET state 에서는 planner 를 부르지 않고 마지막 값을 돌려준다.
기준 일정은 test_api.py 머리말 참고 (가옥거리 14:16~15:16 -> 과메기문화관 15:21~16:21 -> 근대문화역사관 16:26~17:06).
"""
from .conftest import H, ok
from .test_api import HOUSE, item_id, visited_until_port


def baseline(env) -> dict:
    """기준 일정의 그날 종료와 항목별 시작 (가짜 planner 의 GET plan)."""
    day = env.planner.get_plan(env.plan_id, {})["days"][0]
    return {"end": day["end_at"], "start": {i["id"]: i["start_at"] for i in day["items"]}}


def projections(env) -> int:
    return sum(1 for c in env.planner.calls if c[0] == "projection")


def test_expected_null_before_first_projection(env):
    assert env.state()["expected"] is None  # 시작 전
    s = env.start("12:20")
    assert s["expected"] is None and env.state()["expected"] is None


def test_expected_stored_after_location_and_returned_by_state(env):
    visited_until_port(env)
    env.start("14:10")
    env.stay(HOUSE, "14:10", 3)
    base = baseline(env)
    r = env.loc(HOUSE, "15:35")  # 가옥거리에 15:35 까지 머묾 -> 뒤 일정이 밀린다
    exp = r["state"]["expected"]
    house, museum, history = item_id(env, 3), item_id(env, 4), item_id(env, 5)
    # 남은 항목(머무는 곳 + planned)만, 다녀온 곳(done)은 빠진다
    assert set(exp["items"]) == {str(house), str(museum), str(history)}
    assert exp["items"][str(house)]["end"] == "15:35"
    # 다음 일정의 예상 시작은 next 와 같은 재계산, 기준보다 늦다
    nxt = r["state"]["next"]
    assert nxt["item_id"] == museum and exp["items"][str(museum)]["start"] >= nxt["eta"]
    assert exp["items"][str(museum)]["start"] > base["start"][museum]
    assert exp["end"] > base["end"]
    # GET state 는 planner 를 부르지 않고 마지막 값을 돌려준다
    n = projections(env)
    assert env.state()["expected"] == exp
    assert projections(env) == n


def test_expected_on_time_matches_baseline(env):
    visited_until_port(env)
    env.start("14:10")
    env.stay(HOUSE, "14:16", 3)
    base = baseline(env)
    exp = env.state()["expected"]
    museum = item_id(env, 4)
    assert exp["items"][str(museum)]["start"] == base["start"][museum] and exp["end"] == base["end"]


def test_expected_stored_by_tick(env):
    visited_until_port(env)
    env.start("14:10")
    env.stay(HOUSE, "14:10", 3)
    before = env.state()["expected"]
    env.tick("15:50")  # 위치 없이 시간만 흐름 -> 머무는 곳 종료가 지금으로 밀린다
    exp = env.state()["expected"]
    assert exp["items"][str(item_id(env, 3))]["end"] == "15:50"
    assert exp["end"] > before["end"]


def test_expected_cleared_on_end(env):
    visited_until_port(env)
    env.start("14:10")
    env.stay(HOUSE, "14:10", 3)
    assert env.state()["expected"] is not None
    s = ok(env.c.post(env.url("/end"), headers=H))
    assert s["active"] is False and s["expected"] is None
    assert env.state()["expected"] is None and env.trip().expected is None
