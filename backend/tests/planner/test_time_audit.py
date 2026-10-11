"""여행 중 시간 판정 점검 (docs/trip/audit-2026-10-12.md) — planner 재계산(projection) 쪽 재현 테스트 (순수 함수, DB 불필요).

기준 일정 (10:00 시작, 18:00 종료, 약 300m 간격 도보 6분):
    가옥거리(10:00~18:00 운영) 10:00~11:00 -> 물회집(11:00~20:00) 11:06~12:06 -> 문화관(09:00~16:00) 12:12~13:12
"""
from datetime import date, time

from app.planner.models import PlannerItem, PlannerPlan
from app.planner.schemas import PlaceInfo
from app.planner.validation import Actual, build_schedule, delay_minutes, new_violations, next_item_id

A = PlaceInfo(id="a", title="가옥거리", lat=35.9880, lng=129.5570, open_hours="10:00~18:00")
B = PlaceInfo(id="b", title="물회집", lat=35.9907, lng=129.5570, open_hours="11:00~20:00")
C = PlaceInfo(id="c", title="문화관", lat=35.9934, lng=129.5570, open_hours="09:00~16:00")
PLACES = {p.id: p for p in (A, B, C)}
PLANNED = ("planned", None, None)


def hm(s: str) -> int:
    h, m = map(int, s.split(":"))
    return h * 60 + m


def make_plan(*statuses, fixed: dict[str, time] | None = None, day_start: time = time(10, 0)) -> PlannerPlan:
    """statuses: 가옥거리·물회집·문화관 순서의 (status, arrived_at, done_at)."""
    plan = PlannerPlan(id=1, user_id="u", title="t", start_date=date(2026, 10, 10), end_date=date(2026, 10, 10),
                       day_start=day_start, day_end=time(18, 0), travel_mode="walk", memo="")
    for n, (pid, st) in enumerate(zip("abc", statuses), start=1):
        status, arrived, done = st
        plan.items.append(PlannerItem(
            id=n, place_id=pid, day=1, position=n, stay_minutes=60, memo="", status=status, travel_mode=None,
            start_time=(fixed or {}).get(pid),
            arrived_at=time.fromisoformat(arrived) if arrived else None,
            done_at=time.fromisoformat(done) if done else None))
    return plan


def run(plan: PlannerPlan, actual: Actual):
    """(재계산 일정, 새 위반 (code, item_id), 지연, 다음 항목). 이동 근사 경고(travel_estimated)는 뺀다."""
    base_days, base_v = build_schedule(plan, PLACES)
    days, v = build_schedule(plan, PLACES, actual=actual)
    new = [x for x in new_violations(base_v, v) if x.code != "travel_estimated"]
    nxt = next_item_id(plan, actual)
    return days[0].items, [(x.code, x.item_id) for x in new], delay_minutes(base_days, days, 1, nxt), nxt


# ---------- 1. 일찍 도착 ----------

def test_early_30min_no_violation():
    """정상: 30분 일찍 떠나 다음 장소에 30분 일찍 도착 -> 뒤 일정이 당겨지고(여유) 새 위반 없음."""
    plan = make_plan(("done", "10:00", "10:30"), ("visiting", "11:00", None), PLANNED)
    items, new, delay, nxt = run(plan, Actual(1, hm("11:05"), anchor_id=2, anchor_arrive=hm("11:00")))
    assert (items[1].start_at, items[2].start_at) == ("11:00", "12:06")
    assert new == [] and nxt == 3 and delay == -6


def test_start_two_hours_early_without_position():
    """정상: 첫 일정 2시간 전에 여행을 시작(아직 아무 데도 도착 안 함) -> 계획 그대로, 지연 0."""
    items, new, delay, nxt = run(make_plan(PLANNED, PLANNED, PLANNED), Actual(1, hm("08:00")))
    assert items[0].start_at == "10:00" and new == [] and delay == 0 and nxt == 1


def test_arrive_before_opening_waits_not_violation():
    """가옥거리(10:00 개장)에 09:30 도착해 기다림 -> '운영시간 밖' 위반이 새로 생기면 안 된다 (지연이 아니다)."""
    plan = make_plan(("visiting", "09:30", None), PLANNED, PLANNED)
    items, new, delay, nxt = run(plan, Actual(1, hm("09:35"), anchor_id=1, anchor_arrive=hm("09:30")))
    assert new == []
    assert items[0].start_at == "10:00"  # 문을 열 때까지 기다린다 (고정 시각과 같은 방식)
    assert delay <= 0


def test_next_place_before_opening_after_leaving_early():
    """가옥거리를 일찍 떠나 물회집(11:00 개장)에 10:06 도착 예정 -> 기다리면 되므로 새 위반이 아니다."""
    plan = make_plan(("done", "09:30", "10:00"), PLANNED, PLANNED)
    items, new, delay, nxt = run(plan, Actual(1, hm("10:00"), anchor_id=2, anchor_arrive=hm("10:06"), moving=True))
    assert new == []
    assert (items[1].arrive_at, items[1].start_at) == ("10:06", "11:00")
    assert nxt == 2 and delay <= 0


def test_fixed_start_early_arrival_waits():
    """정상: 고정 시각(문화관 15:00) 항목에 12:06 일찍 도착 -> 15:00 까지 기다리고 16:00 에 끝남, 위반 없음."""
    plan = make_plan(("done", "10:00", "11:00"), ("done", "11:06", "12:00"), ("visiting", "12:06", None),
                     fixed={"c": time(15, 0)})
    items, new, delay, nxt = run(plan, Actual(1, hm("12:10"), anchor_id=3, anchor_arrive=hm("12:06")))
    assert (items[2].start_at, items[2].end_at) == ("15:00", "16:00")
    assert new == [] and nxt is None and delay == 0


def test_fixed_start_moving_early_waits():
    """정상: 고정 시각 항목으로 이동 중 일찍 도착 예정 -> 시작은 고정 시각, fixed_time_missed 없음."""
    plan = make_plan(("done", "10:00", "10:30"), PLANNED, PLANNED, fixed={"b": time(11, 30)})
    items, new, delay, nxt = run(plan, Actual(1, hm("10:30"), anchor_id=2, anchor_arrive=hm("10:36"), moving=True))
    assert (items[1].arrive_at, items[1].start_at) == ("10:36", "11:30")
    assert new == [] and delay == 0


# ---------- 2. 늦게 도착·오래 머묾 ----------

def test_arrive_after_closing_is_violation():
    """정상: 문화관(16:00 마감)에 16:10 도착 -> 지금 머무는 곳의 운영시간 종료는 따진다 (§8.2)."""
    plan = make_plan(("done", "10:00", "11:00"), ("done", "11:06", "16:00"), ("visiting", "16:10", None))
    items, new, delay, nxt = run(plan, Actual(1, hm("16:15"), anchor_id=3, anchor_arrive=hm("16:10")))
    assert ("outside_open_hours", 3) in new


def test_day_overflow_points_to_last_visited_item():
    plan = make_plan(("done", "10:00", "11:00"), ("visiting", "11:06", None), ("skipped", None, None))
    plan.day_end = time(12, 30)
    _, v = build_schedule(plan, PLACES, actual=Actual(1, hm("12:40"), anchor_id=2, anchor_arrive=hm("11:06")))
    over = next(x for x in v if x.code == "day_overflow")
    assert over.item_id == 2


# ---------- 3. 순서 바꿔 방문 ----------

def test_out_of_order_skipped_items_are_past():
    """정상: 문화관에 먼저 와 있음(가옥거리·물회집은 아직 planned) -> 앞 항목은 지난 것으로 보고 위반을 따지지 않는다. 다음 없음."""
    plan = make_plan(PLANNED, PLANNED, ("visiting", "10:10", None))
    items, new, delay, nxt = run(plan, Actual(1, hm("10:20"), anchor_id=3, anchor_arrive=hm("10:10")))
    assert new == [] and nxt is None
