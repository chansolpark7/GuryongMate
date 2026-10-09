"""Event Policy 순수 함수 (policy.py) 단위 테스트."""
from datetime import datetime, timedelta

from app.trip import policy
from app.trip.clock import KST
from app.trip.judge import Detection
from app.trip.policy import Queued, pick_next

T0 = datetime(2026, 10, 10, 15, 0, tzinfo=KST)
COOL = timedelta(minutes=10)


def det(type_: str, key: str) -> Detection:
    return Detection(type_, T0, cause_key=key)


def test_same_cause_is_not_repeated():
    d = det("delay", "delay:outside_open_hours:1:5:None")
    assert policy.fresh([d], set()) == [d]
    assert policy.fresh([d], policy.known_causes([d.cause_key])) == []


def test_worse_situation_is_notified_again():
    """제안을 거절해도 같은 위반이면 조용하고, 새 위반이 하나라도 생기면 다시 알린다."""
    known = policy.known_causes(["delay:outside_open_hours:1:5:None"])
    worse = det("delay", "delay:day_overflow:1:5:None|delay:outside_open_hours:1:5:None")
    assert policy.fresh([worse], known) == [worse]
    same_hazard_worse = det("near_hazard", "near_hazard:flood_4:danger")  # 주의 -> 위험
    assert policy.fresh([same_hazard_worse], {"near_hazard:flood_4:caution"}) == [same_hazard_worse]


def test_fresh_orders_by_priority_and_dedups_within_batch():
    a = det("arrived", "arrived:3")
    h = det("near_hazard", "near_hazard:flood_4:danger")
    d = det("delay", "delay:x")
    out = policy.fresh([a, d, h, det("arrived", "arrived:3")], set())
    assert [x.type for x in out] == ["near_hazard", "delay", "arrived"]


def test_initial_status():
    assert policy.initial_status(det("delay", "k")) == "queued"
    assert policy.initial_status(det("safety_raised", "k")) == "queued"
    assert policy.initial_status(det("arrived", "k")) == "delivered"
    assert policy.initial_status(det("near_hazard", "k")) == "delivered"


def test_pick_one_by_priority():
    q = [Queued(1, "delay"), Queued(2, "safety_raised", (3,))]
    pick, suppress = pick_next(q, None, T0, COOL)
    assert pick.id == 2 and suppress == []


def test_older_same_cause_is_suppressed():
    q = [Queued(1, "delay", (5,)), Queued(4, "delay", (4, 5)), Queued(2, "safety_raised", (3,)),
         Queued(3, "safety_raised", (3,)), Queued(5, "safety_raised", (2,))]
    pick, suppress = pick_next(q, None, T0, COOL)
    assert sorted(suppress) == [1, 2] and pick.id == 3


def test_cooldown_holds_delay_but_not_safety():
    q = [Queued(1, "delay")]
    assert pick_next(q, T0 - timedelta(minutes=5), T0, COOL) == (None, [])
    assert pick_next(q, T0 - timedelta(minutes=10), T0, COOL)[0].id == 1
    q.append(Queued(2, "safety_raised", (3,)))
    assert pick_next(q, T0 - timedelta(minutes=1), T0, COOL)[0].id == 2


def test_empty_queue():
    assert pick_next([], None, T0, COOL) == (None, [])
