"""타임라인 계산·위반 검사 단위 테스트 (DB 불필요).

    docker compose run --rm api pytest tests/planner
"""
from datetime import date, time

from app.planner.models import PlannerConstraint, PlannerItem, PlannerPlan
from app.planner.schemas import PlaceInfo
from app.planner.validation import Actual, OpenHours, build_schedule, closed_weekdays, open_hours_for

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
    """이 파일은 routing 결과(travel 표) 없이 직선거리 근사로 계산하므로 근사 경고(travel_estimated)는 뺀다."""
    return sorted(v.code for v in violations if v.code != "travel_estimated")


def test_timeline_chains_stay_and_travel():
    days, violations = build_schedule(make_plan({"place_id": "a"}, {"place_id": "b"}, {"place_id": "c"}), PLACES)
    items = days[0].items
    assert [i.arrive_at for i in items][0] == "12:30"
    assert items[0].end_at == "13:30"
    assert items[0].travel_minutes is None
    assert 5 <= items[1].travel_minutes <= 7  # 300m * 1.3 / 70m/min
    assert items[1].arrive_at == f"13:{30 + items[1].travel_minutes:02d}"
    assert days[0].end_at == items[2].end_at
    assert codes(violations) == []


def test_fixed_start_time_waits_or_is_missed():
    days, v = build_schedule(make_plan({"place_id": "a"}, {"place_id": "b", "start_time": time(14, 0)}), PLACES)
    assert days[0].items[1].start_at == "14:00"  # 일찍 도착하면 기다림
    assert codes(v) == []

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
    # 호미곶까지 도보 약 160분: 기본 검사(긴 도보 구간·하루 도보 합계)도 함께 나온다
    assert codes(v) == ["daily_walk_total", "end_before", "exclude", "long_walk", "max_travel_minutes", "must_include"]
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


TUE, SAT, SUN, JAN_TUE = date(2026, 10, 13), date(2026, 10, 17), date(2026, 10, 18), date(2027, 1, 12)


def oh(open_hours: str, day: date = TUE):
    """(영업 시작, 끝, [(쉬는 시작, 끝, 이름)]) 를 'HH:MM' 으로. 판단 보류면 None."""
    h = open_hours_for(open_hours, day)
    if h is None:
        return None
    f = lambda m: f"{m // 60:02d}:{m % 60:02d}"  # noqa: E731
    return f(h.open), f(h.close), [(f(a), f(b), n) for a, b, n in h.breaks]


def test_open_hours_single_range():
    assert open_hours_for("09:00~18:00", TUE) == OpenHours(540, 1080)
    assert open_hours_for("10:00-22:00", TUE) == OpenHours(600, 1320)
    assert open_hours_for("18:00~02:00", TUE) == OpenHours(1080, 1560)
    assert oh("09:00~18:00 (입장 마감 17:30)") == ("09:00", "18:00", [])
    assert oh("상시 개방") is None
    assert oh("- 입실 15:00- 퇴실 11:00") is None


def test_open_hours_breaks():
    """준비시간·브레이크타임·휴게시간 뒤의 범위는 쉬는 구간. 마지막 주문·매표 마감 같은 시각 하나는 보지 않는다 (원문은 로컬 DB 그대로)."""
    assert oh("11:30~18:00 (준비시간 14:30~16:00)") == ("11:30", "18:00", [("14:30", "16:00", "준비시간")])  # 유림식당
    assert oh("11:30~21:30브레이크타임 16:00~17:00라스트 오더 18:40") == (
        "11:30", "21:30", [("16:00", "17:00", "브레이크타임")])  # 고향숯불갈비
    assert oh("10:00~18:00 (매표 마감 16:30)휴게시간 12:00~13:00※인터넷 사전 예약 후 관람 가능") == (
        "10:00", "18:00", [("12:00", "13:00", "휴게시간")])  # 로보라이프 뮤지엄
    assert oh("10:30~21:00 (준비 시간 15:30~17:00 / 마지막 주문 20:00)") == (
        "10:30", "21:00", [("15:30", "17:00", "준비시간")])  # 마라도회식당
    assert oh("- 11:00~21:20\n- 점심 마지막 주문 14:50\n- 준비시간 15:30~16:30\n- 저녁 마지막 주문 19:50") == (
        "11:00", "21:20", [("15:30", "16:30", "준비시간")])  # 바다원해
    assert oh("- 09:30~20:00- 오전 준비시간 10:20~11:00- 오후 준비시간 15:00~16:00- 마지막 주문 19:30") == (
        "09:30", "20:00", [("10:20", "11:00", "준비시간"), ("15:00", "16:00", "준비시간")])  # 환여횟집


def test_open_hours_weekend_without_break_note():
    """※ 주말은 준비시간 없이: 평일만 쉬는 구간. 그 밖의 쉬는 구간 단서는 쉬는 구간만 무시한다."""
    text = "- 11:00~21:00\n- 준비시간 15:00~17:00\n- 마지막 주문 20:40\n※ 주말은 준비시간 없이 영업"  # 아쿠아춘천닭갈비
    assert oh(text, TUE) == ("11:00", "21:00", [("15:00", "17:00", "준비시간")])
    assert oh(text, SAT) == oh(text, SUN) == ("11:00", "21:00", [])
    assert oh("11:00~23:00 (준비시간 14:30~16:30)\n※ 준비시간은 바뀔 수 있음", TUE) == ("11:00", "23:00", [])


def test_open_hours_season():
    """하절기·동절기(월 범위) 는 날짜의 달로 고른다. 달이 없으면 판단 보류."""
    arbor = "- 하절기(3월~10월) 10:00~17:00\n- 동절기(11월~2월) 10:00~16:00\n※ 입장은 폐장 30분 전까지 가능"  # 경상북도수목원
    assert oh(arbor, TUE) == ("10:00", "17:00", [])
    assert oh(arbor, JAN_TUE) == ("10:00", "16:00", [])
    garden = "- 하절기(4월~10월) 09:00~18:00 (입장 마감 17:30)- 동절기(11월~3월) 09:00~17:00 (입장 마감 16:30)"  # 기청산식물원
    assert oh(garden, TUE) == ("09:00", "18:00", [])
    assert oh(garden, JAN_TUE) == ("09:00", "17:00", [])
    assert oh("[하절기(3~10월)] - 09:00~17:00 (입장 마감 16:00) [동절기(11~2월)] - 09:00~17:00 (입장 마감 16:00)",
              JAN_TUE) == ("09:00", "17:00", [])  # 환호공원식물원
    space = ("[하절기(4월~10월)]- 평일 10:00~20:00- 주말 10:00~21:00"
             "[동절기(11월~3월)]- 평일 10:00~17:00- 주말 10:00~18:00")  # 환호공원 스페이스워크
    assert (oh(space, TUE), oh(space, SAT), oh(space, JAN_TUE)) == (
        ("10:00", "20:00", []), ("10:00", "21:00", []), ("10:00", "17:00", []))
    assert oh("- 하절기 09:30~17:30- 동절기 10:00~16:30") is None  # 학도의용군 전승기념관: 달 범위가 없다


def test_open_hours_weekdays():
    """평일·주말·요일 표기는 요일로 고른다. 그날 맞는 범위가 없으면 판단 보류."""
    gobau = "- 평일 16:00~23:00 (마지막 주문 22:15)\n- 주말 11:30~23:00 (마지막 주문 22:15)"  # 고바우식당
    assert (oh(gobau, TUE), oh(gobau, SUN)) == (("16:00", "23:00", []), ("11:30", "23:00", []))
    posco = "- 월요일~금요일 09:00~17:30\n- 토요일 10:00~17:00"  # Park1538 포스코역사박물관
    assert (oh(posco, TUE), oh(posco, SAT), oh(posco, SUN)) == (("09:00", "17:30", []), ("10:00", "17:00", []), None)
    camellia = "- 월요일 9:00~19:00 - 화~금 10:00~18:00 - 토요일 10:00~21:00 - 일요일 9:00~19:00"  # 까멜리아인구룡포
    assert (oh(camellia, TUE), oh(camellia, SAT), oh(camellia, SUN)) == (
        ("10:00", "18:00", []), ("10:00", "21:00", []), ("09:00", "19:00", []))
    pyeongnam = ("[화~금요일]\n-08:00~19:00\n- 준비 시간 14:00~15:30\n- 마지막 주문 18:40\n[토요일]\n-08:00~19:00\n"
                 "- 준비 시간 14:00~15:30\n- 마지막 주문 18:40\n[일요일]\n-08:00~15:00\n- 마지막 주문 14:40")  # 평남식당
    assert oh(pyeongnam, TUE) == ("08:00", "19:00", [("14:00", "15:30", "준비시간")])
    assert oh(pyeongnam, SUN) == ("08:00", "15:00", [])
    assert oh(pyeongnam, date(2026, 10, 12)) is None  # 월요일: 표기 없음
    jangi = "- 평일 08:00~20:00 (준비시간 14:00~15:00)\n- 주말 08:00~19:30 (준비시간 14:00~16:00)"  # 장기식당
    assert oh(jangi, SAT) == ("08:00", "19:30", [("14:00", "16:00", "준비시간")])
    noodle = "[평일]- 11:00~15:00- 마지막 주문 14:30[주말]- 11:00~20:00- 준비시간 15:00~16:00- 마지막 주문 19:30"  # 육지고기국수
    assert (oh(noodle, TUE), oh(noodle, SAT)) == (("11:00", "15:00", []), ("11:00", "20:00", [("15:00", "16:00", "준비시간")]))
    cafe = "10:00~19:00\n토요일 09:00~20:00"  # 더자리카페: 조건 없는 범위 + 토요일만 다름
    assert (oh(cafe, TUE), oh(cafe, SAT)) == (("10:00", "19:00", []), ("09:00", "20:00", []))


def test_open_hours_undecided():
    """확실히 해석되지 않으면 판단하지 않는다 (잘못된 경고보다 보류)."""
    assert oh("카페,안내동 9:00~18:00\n매점 9:00~22:00 (무인운영)") is None  # 비손농장: 시설별 시간
    assert oh("- 11:30~21:00\n- 15:00~17:30\n- 마지막 주문 20:30") is None  # 식탐: 두 번째 범위가 무엇인지 모른다
    assert oh("- 월요일 13:30~18:00- 금요일 13:00~18:59- 토요일 13:00~17:00- 일요일 11:30~18:59"
              "※ 영업시간은 상시적으로 변동될 수 있으므로 방문 전 전화 문의 요망", TUE) is None  # 가배원: 화요일 표기 없음
    assert oh("09:00~18:00\n※ 주말 10:00~17:00") is None  # 참고 문구에 시각 범위


# 유림식당 원문 그대로 (11:30~18:00, 준비시간 14:30~16:00). 가옥거리(a)에서 약 300m (도보 6분)
RESTO = PlaceInfo(id="r", title="유림식당", lat=35.9907, lng=129.5570, open_hours="11:30~18:00 (준비시간 14:30~16:00)")
RPLACES = PLACES | {"r": RESTO}


def test_break_overlap_is_outside_open_hours():
    """가옥거리 12:30~13:30 -> 유림식당 13:36 도착. 60분이면 준비시간(14:30~)과 겹치고, 50분이면 통과."""
    _, v = build_schedule(make_plan({"place_id": "a"}, {"place_id": "r"}), RPLACES)
    hit = [x for x in v if x.code == "outside_open_hours"]
    assert [(x.item_id, x.severity) for x in hit] == [(2, "warning")]
    assert hit[0].message == "'유림식당' 방문 13:36~14:36 이 준비시간(14:30~16:00)과 겹칩니다."
    _, v = build_schedule(make_plan({"place_id": "a"}, {"place_id": "r", "stay_minutes": 50}), RPLACES)
    assert codes(v) == []


def test_visit_after_closing_with_break_text():
    """원문에 범위가 둘(영업 + 준비시간)이어도 영업 종료 뒤 방문은 운영시간 밖 (기존에는 판단하지 않았다)."""
    plan = make_plan({"place_id": "a", "stay_minutes": 300}, {"place_id": "r"}, day_end=time(23, 0))
    _, v = build_schedule(plan, RPLACES)
    assert [x.message for x in v if x.code == "outside_open_hours"] == [
        "'유림식당' 방문 17:36~18:36 이 운영시간(11:30~18:00) 밖입니다."]


def test_progress_waits_for_opening_and_break_end():
    """진행 중 기준: 문 열기 전 도착은 열 때까지, 준비시간 중 도착은 끝날 때까지 기다린다 (위반 아님). 계획 기준은 위반."""
    plan = make_plan({"place_id": "r"}, {"place_id": "a"})
    days, v = build_schedule(plan, RPLACES, actual=Actual(1, 11 * 60 + 5, anchor_id=1, anchor_arrive=11 * 60))
    assert days[0].items[0].start_at == "11:30" and codes(v) == []
    # 가옥거리 -> 유림식당 이동 중, 14:40 (준비시간 중) 도착 예정 -> 16:00 시작, 위반 없음
    plan = make_plan({"place_id": "a"}, {"place_id": "r", "stay_minutes": 50})
    days, v = build_schedule(plan, RPLACES, actual=Actual(1, 14 * 60 + 35, anchor_id=2, anchor_arrive=14 * 60 + 40, moving=True))
    r = days[0].items[1]
    assert (r.arrive_at, r.start_at, r.end_at) == ("14:40", "16:00", "16:50")
    assert "outside_open_hours" not in codes(v)
    # 이미 와 있는 경우도 같다 (머무는 항목)
    days, v = build_schedule(plan, RPLACES, actual=Actual(1, 14 * 60 + 45, anchor_id=2, anchor_arrive=14 * 60 + 40))
    assert (days[0].items[1].start_at, days[0].items[1].end_at) == ("16:00", "16:50")
    assert "outside_open_hours" not in codes(v)
    # 준비시간 전에 도착해도 머무는 동안 준비시간에 걸리면 위반 (기다려서 풀리는 것이 아니다)
    days, v = build_schedule(plan, RPLACES, actual=Actual(1, 13 * 60 + 55, anchor_id=2, anchor_arrive=14 * 60, moving=True))
    assert days[0].items[1].start_at == "14:00" and "outside_open_hours" in codes(v)


# ---------- 로직 버그 점검 2026-10-12 (docs/audit/logic-bugs-2026-10-12.md) ----------

MON, FRI, AUG_SAT, JUL_TUE = date(2026, 10, 12), date(2026, 10, 16), date(2026, 8, 22), date(2026, 7, 14)


def test_closed_weekdays_range_without_first_yoil():
    """P2: 앞쪽 '요일' 을 생략한 범위 (피어라계단 미디어아트 `매주 월~목요일`). 숫자 뒤 '월'(달)은 요일이 아니다."""
    assert closed_weekdays("매주 월~목요일") == {0, 1, 2, 3}
    assert closed_weekdays("매주 토~일요일") == {5, 6}
    assert closed_weekdays("1월~2월 일요일") == {6}  # '1월' 은 달


def test_closed_weekdays_monthly_comma_list():
    """P3: `매월 셋째 토요일, 일요일` 의 일요일도 매월 조건 (THE 신촌s 덮죽). '매주' 가 다시 나오면 매주 휴무."""
    assert closed_weekdays("매주 월요일 / 매월 셋째 토요일, 일요일") == {0}
    assert closed_weekdays("매주 월요일, 매월 셋째 화요일") == {0}
    assert closed_weekdays("매월 둘째 화요일, 매주 수요일") == {2}
    assert closed_weekdays("매주 토요일, 일요일") == {5, 6}


def test_open_hours_note_with_period_or_season():
    """P4: ※ 문구가 운영 기간·계절 변경을 말하면 판단 보류. '폐장 30분 전'·요일 운영일·'주말 준비시간 없이' 는 아니다."""
    market = "금요일~일요일 18:00~22:00\n※ 매년 8월 중순~9월 말까지 개장"  # 영일만친구야시장
    assert oh(market, SAT) is None and oh(market, AUG_SAT) is None
    assert oh("09:00~18:00\n※ 하절기 (6~8월)는 20:00 까지\n※ 태풍 등 기상특보 시 운영 중단", JUL_TUE) is None  # 이가리 닻 전망대
    assert oh("10:00~18:00\n※ 7~8월에만 운영") is None
    # 판단하던 것은 그대로
    assert oh("- 하절기(3월~10월) 10:00~17:00\n- 동절기(11월~2월) 10:00~16:00\n※ 입장은 폐장 30분 전까지 가능") == (
        "10:00", "17:00", [])
    assert oh("19:00 -  20:50\n※ 매주 금요일~일요일에만 운영\n내부 사정에 따라 시간이 변경될 수 있음", FRI) == (
        "19:00", "20:50", [])  # 피어라계단: 운영 요일은 휴무(closed_weekdays)가 본다
    text = "- 11:00~21:00\n- 준비시간 15:00~17:00\n- 마지막 주문 20:40\n※ 주말은 준비시간 없이 영업"
    assert oh(text, TUE) == ("11:00", "21:00", [("15:00", "17:00", "준비시간")]) and oh(text, SAT)[2] == []


def test_open_hours_day_off_token_is_not_condition():
    """P8: `(월요일 휴무)` 의 월요일은 뒤 범위(쉬는 구간)의 조건이 아니다 — 모든 날에 쉬는 구간을 지킨다."""
    text = "11:00~21:00 (월요일 휴무)\n브레이크타임 15:00~17:00"
    assert oh(text, TUE) == ("11:00", "21:00", [("15:00", "17:00", "브레이크타임")])
    assert oh(text, MON) == ("11:00", "21:00", [("15:00", "17:00", "브레이크타임")])  # 휴무는 closed_weekdays 가 본다
    assert oh("월~금 09:00~18:00 (토요일 휴관)", TUE) == ("09:00", "18:00", [])


def test_first_item_fixed_before_day_start():
    """P7: 그날 첫 항목의 고정 시각이 하루 시작(12:30)보다 이르면 그때 시작한다 (앞 일정이 없다)."""
    days, v = build_schedule(make_plan({"place_id": "a", "start_time": time(10, 0)}, {"place_id": "c"}), PLACES)
    assert days[0].items[0].start_at == "10:00" and "fixed_time_missed" not in codes(v)
    # 두 번째 항목이 앞 일정 때문에 늦는 것은 그대로
    plan = make_plan({"place_id": "a", "stay_minutes": 120}, {"place_id": "c", "start_time": time(13, 0)})
    _, v = build_schedule(plan, PLACES)
    assert codes(v) == ["fixed_time_missed"] and "앞 일정 때문에" in v[0].message
    # 지금 기준(시작 전): 지금보다 이르게는 시작하지 않는다
    plan = make_plan({"place_id": "a", "start_time": time(10, 0)}, {"place_id": "c"})
    days, v = build_schedule(plan, PLACES, actual=Actual(1, 9 * 60))
    assert days[0].items[0].start_at == "10:00" and "fixed_time_missed" not in codes(v)
    _, v = build_schedule(plan, PLACES, actual=Actual(1, 11 * 60))
    assert [x.message for x in v if x.code == "fixed_time_missed"] == [
        "'가옥거리' 는 10:00 시작으로 고정돼 있지만 지금 기준으로 11:00 에 도착합니다 (60분 늦음)."]
