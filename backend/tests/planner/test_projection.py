"""현재 기준 재계산 (명세 §8) 단위 테스트: 순수 함수 build_schedule(actual=...) 와 비교 함수 (DB 불필요).

기준 일정 (12:30 시작, 18:00 종료, 약 300m 간격 도보 6분):
    가옥거리 12:30~13:30 -> 물회집 13:36~14:36 -> 문화관 14:42~15:42
"""
from datetime import date, time

from app.planner.models import PlannerItem, PlannerPlan
from app.planner.schemas import PlaceInfo
from app.planner.validation import Actual, build_schedule, delay_minutes, new_violations, next_item_id

A = PlaceInfo(id="a", title="가옥거리", lat=35.9880, lng=129.5570)
B = PlaceInfo(id="b", title="물회집", lat=35.9907, lng=129.5570)
C = PlaceInfo(id="c", title="문화관", lat=35.9934, lng=129.5570, open_hours="09:00~16:00")
PLACES = {p.id: p for p in (A, B, C)}


def hm(s: str) -> int:
    h, m = map(int, s.split(":"))
    return h * 60 + m


def make_plan(*statuses, fixed_c: time | None = None) -> PlannerPlan:
    """statuses: 가옥거리·물회집·문화관 순서의 (status, arrived_at, done_at)."""
    plan = PlannerPlan(id=1, user_id="u", title="t", start_date=date(2026, 10, 10), end_date=date(2026, 10, 10),
                       day_start=time(12, 30), day_end=time(18, 0), travel_mode="walk", memo="")
    for n, (pid, st) in enumerate(zip("abc", statuses), start=1):
        status, arrived, done = st
        plan.items.append(PlannerItem(
            id=n, place_id=pid, day=1, position=n, stay_minutes=60, memo="", status=status, travel_mode=None,
            start_time=fixed_c if pid == "c" else None,
            arrived_at=time.fromisoformat(arrived) if arrived else None,
            done_at=time.fromisoformat(done) if done else None))
    return plan


PLANNED = ("planned", None, None)


def run(plan: PlannerPlan, actual: Actual):
    """(재계산 일정, 새 위반 코드, 지연, 다음 항목). 이동 근사 경고(travel_estimated)는 뺀다."""
    base_days, base_v = build_schedule(plan, PLACES)
    days, v = build_schedule(plan, PLACES, actual=actual)
    new = [x for x in new_violations(base_v, v) if x.code != "travel_estimated"]
    nxt = next_item_id(plan, actual)
    return days[0].items, [(x.code, x.item_id) for x in new], delay_minutes(base_days, days, 1, nxt), nxt


def test_baseline_unchanged_without_actual():
    plan = make_plan(("done", "12:30", "13:30"), ("visiting", "15:00", None), PLANNED)
    days, _ = build_schedule(plan, PLACES)
    assert [(i.start_at, i.end_at) for i in days[0].items] == [("12:30", "13:30"), ("13:36", "14:36"), ("14:42", "15:42")]
    assert days[0].items[1].arrived_at == time(15, 0)  # 실제 기록은 ItemView 에 그대로 노출


def test_late_arrival_leaves_at_planned_end():
    """규칙 B: 14분 늦게 왔으면(13:50) 예정 종료(14:36)에 떠난다고 본다 -> 뒤 일정은 아직 제시간."""
    plan = make_plan(("done", "12:30", "13:30"), ("visiting", "13:50", None), PLANNED)
    items, new, delay, nxt = run(plan, Actual(1, hm("14:00"), anchor_id=2, anchor_arrive=hm("13:50")))
    assert [(i.start_at, i.end_at) for i in items] == [("12:30", "13:30"), ("13:50", "14:36"), ("14:42", "15:42")]
    assert new == [] and nxt == 3 and delay == 0


def test_late_arrival_overstay_past_planned_end_is_late():
    """예정 종료(14:36)를 넘겨 머물면 now 에 끝난다 -> 그때부터 늦음."""
    plan = make_plan(("done", "12:30", "13:30"), ("visiting", "13:50", None), PLANNED)
    items, new, delay, nxt = run(plan, Actual(1, hm("14:50"), anchor_id=2, anchor_arrive=hm("13:50")))
    assert [(i.start_at, i.end_at) for i in items][1:] == [("13:50", "14:50"), ("14:56", "15:56")]
    assert new == [] and nxt == 3 and delay == 14


def test_early_arrival_keeps_full_stay():
    """일찍 왔으면(13:20, 예정 13:36) 그대로 도착 + 체류 (규칙 B 와 무관). now 가 지나면 now."""
    plan = make_plan(("done", "12:30", "13:10"), ("visiting", "13:20", None), PLANNED)
    items, new, delay, _ = run(plan, Actual(1, hm("13:30"), anchor_id=2, anchor_arrive=hm("13:20")))
    assert (items[1].end_at, items[2].start_at) == ("14:20", "14:26") and delay == -16
    items, new, delay, _ = run(plan, Actual(1, hm("14:30"), anchor_id=2, anchor_arrive=hm("13:20")))
    assert (items[1].end_at, items[2].start_at) == ("14:30", "14:36") and delay == -6


def test_overstay_ends_now_and_overflows_day():
    """예정 체류(60분)가 지났으면 now 에 끝난다고 본다 -> 뒤 일정이 밀려 18:00 을 넘는다."""
    plan = make_plan(("done", "12:30", "13:30"), ("visiting", "15:00", None), PLANNED)
    items, new, delay, _ = run(plan, Actual(1, hm("17:10"), anchor_id=2, anchor_arrive=hm("15:00")))
    assert items[1].end_at == "17:10" and items[2].start_at == "17:16"
    # 기준에 없던 위반만: 하루 초과, 운영시간(16:00) 밖
    assert sorted(new) == [("day_overflow", 3), ("outside_open_hours", 3)]
    assert delay == hm("17:16") - hm("14:42")


def test_closure_is_a_new_violation():
    """늦게 와서(14:20) 예정 종료(14:36)를 넘겨 15:20 까지 머묾 -> 문화관이 운영시간(16:00) 밖."""
    plan = make_plan(("done", "12:30", "13:30"), ("visiting", "14:20", None), PLANNED)
    items, new, delay, _ = run(plan, Actual(1, hm("15:20"), anchor_id=2, anchor_arrive=hm("14:20")))
    assert (items[2].start_at, items[2].end_at) == ("15:26", "16:26")
    assert new == [("outside_open_hours", 3)] and delay == 44


def test_moving_misses_fixed_time():
    """이동 중: 향하는 항목에 now + 남은 이동시간에 도착. 문화관 15:00 고정을 놓친다."""
    plan = make_plan(("done", "12:30", "13:40"), PLANNED, PLANNED, fixed_c=time(15, 0))
    items, new, delay, nxt = run(plan, Actual(1, hm("14:30"), anchor_id=2, anchor_arrive=hm("14:40"), moving=True))
    assert (items[1].arrive_at, items[2].arrive_at) == ("14:40", "15:46")
    assert new == [("fixed_time_missed", 3), ("outside_open_hours", 3)]
    assert nxt == 2 and delay == hm("14:40") - hm("13:36")


def test_past_items_keep_recorded_times_without_violations():
    """다녀온 항목은 기록 그대로이고 시각 위반을 따지지 않는다. 머무는 항목은 고정 시각 놓침을 따지지 않는다."""
    plan = make_plan(("done", "12:30", "13:30"), ("done", "13:40", "14:30"), ("visiting", "15:20", None),
                     fixed_c=time(15, 0))
    items, new, delay, nxt = run(plan, Actual(1, hm("16:10"), anchor_id=3, anchor_arrive=hm("15:20")))
    assert [(i.arrive_at, i.end_at) for i in items] == [("12:30", "13:30"), ("13:40", "14:30"), ("15:20", "16:10")]
    assert ("fixed_time_missed", 3) not in new
    assert nxt is None and delay == hm("16:10") - hm("16:00")  # 다음 항목이 없으면 그날 종료 시각 차이 (기준: 15:00 고정)
    # 고정 시각보다 늦게 왔으면 예정 종료(16:00)에 떠난다고 본다 (규칙 B). 아직 그 전이면 늦음 없음
    items, new, delay, nxt = run(plan, Actual(1, hm("15:30"), anchor_id=3, anchor_arrive=hm("15:20")))
    assert items[2].end_at == "16:00" and delay == 0


def test_no_anchor_leaves_now():
    """머무는 곳이 없으면 아직 다녀오지 않은 첫 항목부터 지금 출발로 본다."""
    plan = make_plan(("done", "12:30", "13:30"), PLANNED, PLANNED)
    items, new, delay, nxt = run(plan, Actual(1, hm("13:45")))
    assert items[1].arrive_at == "13:51" and nxt == 2 and delay == 15 and new == []
    # 시작 전이면 기준 일정과 같다
    items, new, delay, nxt = run(make_plan(PLANNED, PLANNED, PLANNED), Actual(1, hm("11:00")))
    assert items[0].start_at == "12:30" and delay == 0 and nxt == 1


# ---------- 로직 버그 점검 2026-10-12 P1·T2: 앞 항목을 건너뛰고 다녀옴 ----------

def test_skipped_ahead_planned_item_is_past_without_anchor():
    """P1: 가옥거리(planned)를 건너뛰고 물회집을 다녀옴(done), 머무는 곳 없음 -> 가옥거리는 지난 항목, 다음은 문화관."""
    plan = make_plan(PLANNED, ("done", "12:40", "13:40"), PLANNED)
    items, new, delay, nxt = run(plan, Actual(1, hm("14:00")))
    assert nxt == 3 and new == []
    assert items[0].start_at == "12:30"  # 지금 시각에 끼어들지 않는다 (지난 항목, 계산값)
    assert items[2].arrive_at == "14:06"  # 물회집에서 지금 출발
    assert delay == hm("14:06") - hm("14:42")


def test_moving_after_last_with_skipped_ahead_has_no_next():
    """T2: 가옥거리(planned) 건너뜀 -> 물회집·문화관 done, 17:30 출발 (이동 중, 향할 planned 없음 -> ops._anchor 가
    기준점 없음으로 본다) -> 다음 없음, 새 위반 없음 (예전: 다음=가옥거리, 운영시간·하루 초과 새 위반)."""
    plan = make_plan(PLANNED, ("done", "12:40", "13:40"), ("done", "14:00", "17:30"))
    items, new, delay, nxt = run(plan, Actual(1, hm("17:30")))
    assert nxt is None and new == []
    assert delay == hm("17:30") - hm("15:42")  # 그날 종료 시각 차이 (문화관 실제 완료)
