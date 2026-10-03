"""타임라인 계산·위반 검사 단위 테스트 (DB 불필요).

    docker compose run --rm api pytest tests/planner
"""
from datetime import date, time

from app.planner.models import PlannerConstraint, PlannerItem, PlannerPlan
from app.planner.schemas import PlaceInfo
from app.planner.validation import build_schedule, closed_weekdays, open_range

# 약 300m 간격으로 북쪽으로 늘어선 장소들
A = PlaceInfo(id="a", title="가옥거리", lat=35.9880, lng=129.5570)
B = PlaceInfo(id="b", title="물회집", lat=35.9907, lng=129.5570, open_hours="11:00~20:00", rest_date="매주 월요일")
C = PlaceInfo(id="c", title="문화관", lat=35.9934, lng=129.5570)
FAR = PlaceInfo(id="far", title="호미곶", lat=36.0765, lng=129.5670)
PLACES = {p.id: p for p in (A, B, C, FAR)}


def make_plan(*items, constraints=(), start=date(2026, 10, 10), days=1, day_end=time(18, 0), mode="walk"):
    plan = PlannerPlan(id=1, user_id="u", title="t", start_date=start, end_date=date.fromordinal(start.toordinal() + days - 1),
                       day_start=time(12, 30), day_end=day_end, travel_mode=mode, memo="")
    for n, kw in enumerate(items, start=1):
        kw = {"day": 1, "position": n, "stay_minutes": 60, "start_time": None, "memo": "", "status": "planned"} | kw
        plan.items.append(PlannerItem(id=n, **kw))
    for n, kw in enumerate(constraints, start=1):
        plan.constraints.append(PlannerConstraint(id=n, **({"hard": True, "source": "user", "note": ""} | kw)))
    return plan


def codes(violations):
    return sorted(v.code for v in violations)


def test_timeline_chains_stay_and_travel():
    days, violations = build_schedule(make_plan({"place_id": "a"}, {"place_id": "b"}, {"place_id": "c"}), PLACES)
    items = days[0].items
    assert [i.arrive_at for i in items][0] == "12:30"
    assert items[0].end_at == "13:30"
    assert items[0].travel_minutes is None
    assert 5 <= items[1].travel_minutes <= 7  # 300m * 1.3 / 70m/min
    assert items[1].arrive_at == f"13:{30 + items[1].travel_minutes:02d}"
    assert days[0].end_at == items[2].end_at
    assert violations == []


def test_fixed_start_time_waits_or_is_missed():
    days, v = build_schedule(make_plan({"place_id": "a"}, {"place_id": "b", "start_time": time(14, 0)}), PLACES)
    assert days[0].items[1].start_at == "14:00"  # 일찍 도착하면 기다림
    assert v == []

    days, v = build_schedule(make_plan({"place_id": "a", "stay_minutes": 120}, {"place_id": "b", "start_time": time(14, 0)}), PLACES)
    assert codes(v) == ["fixed_time_missed"]
    assert v[0].item_id == 2


def test_day_overflow_and_skipped_items():
    plan = make_plan({"place_id": "a", "stay_minutes": 300}, {"place_id": "b", "stay_minutes": 60})
    _, v = build_schedule(plan, PLACES)
    assert "day_overflow" in codes(v)

    plan.items[1].status = "skipped"
    days, v = build_schedule(plan, PLACES)
    assert v == []
    assert days[0].items[1].arrive_at is None
    assert days[0].end_at == "17:30"


def test_closed_day_and_open_hours():
    monday = date(2026, 10, 12)
    _, v = build_schedule(make_plan({"place_id": "b"}, start=monday), PLACES)
    assert codes(v) == ["closed_day"]
    # 12:30 시작, 운영 11:00~20:00 이지만 체류가 길면 운영시간 밖
    _, v = build_schedule(make_plan({"place_id": "b", "stay_minutes": 600}, day_end=time(23, 0)), PLACES)
    assert codes(v) == ["outside_open_hours"]


def test_constraints():
    plan = make_plan(
        {"place_id": "a"}, {"place_id": "far"},
        constraints=[
            {"type": "must_include", "params": {"place_id": "c"}},
            {"type": "exclude", "params": {"place_id": "far"}},
            {"type": "end_before", "params": {"time": "13:00"}},
            {"type": "max_travel_minutes", "params": {"minutes": 20}, "hard": False},
        ],
        day_end=time(23, 59),
    )
    _, v = build_schedule(plan, PLACES)
    assert codes(v) == ["end_before", "exclude", "max_travel_minutes", "must_include"]
    assert next(x for x in v if x.code == "max_travel_minutes").severity == "warning"


def test_car_mode_is_faster():
    walk, _ = build_schedule(make_plan({"place_id": "a"}, {"place_id": "far"}, day_end=time(23, 59)), PLACES)
    car, _ = build_schedule(make_plan({"place_id": "a"}, {"place_id": "far"}, day_end=time(23, 59), mode="car"), PLACES)
    assert car[0].items[1].travel_minutes < 40 < walk[0].items[1].travel_minutes


def test_unknown_place_is_warning_not_crash():
    days, v = build_schedule(make_plan({"place_id": "a"}, {"place_id": "gone"}, {"place_id": "c"}), PLACES)
    assert codes(v) == ["place_unavailable"]
    assert days[0].items[2].travel_minutes is not None  # 직전에 알려진 장소(a) 기준으로 계산


def test_closed_weekdays_parsing():
    assert closed_weekdays("매주 월요일") == {0}
    assert closed_weekdays("매주 월요일~목요일") == {0, 1, 2, 3}
    assert closed_weekdays("매주 일요일~월요일 / 1월 1일 / 설·추석 연휴") == {6, 0}
    assert closed_weekdays("주말 / 공휴일") == {5, 6}
    assert closed_weekdays("매주 월요일 (단, 월요일이 공휴일인 경우 공휴일 다음의 첫 번째 평일 휴관) / 설·추석 당일") == {0}
    assert closed_weekdays("매달 둘째 주, 넷째 주 화요일") == set()
    assert closed_weekdays("연중무휴") == set()


def test_open_range_parsing():
    assert open_range("09:00~18:00") == (540, 1080)
    assert open_range("10:00-22:00") == (600, 1320)
    assert open_range("18:00~02:00") == (1080, 1560)
    assert open_range("상시 개방") is None
    assert open_range("평일 09:00~18:00 / 주말 10:00~17:00") is None
    assert open_range("- 입실 15:00- 퇴실 11:00") is None
