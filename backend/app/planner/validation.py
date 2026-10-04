"""일정 타임라인 계산과 결정론적 위반 검사. DB·HTTP 에 의존하지 않는 순수 함수.

시간은 모두 '자정 기준 분(int)' 으로 계산하고, 출력할 때만 "HH:MM" 으로 바꾼다.
"""
import re
from datetime import time, timedelta

from app.planner.models import PlannerConstraint, PlannerItem, PlannerPlan
from app.planner.schemas import DayView, ItemView, PlaceInfo, Violation
from app.planner.travel import TravelLeg, estimate_leg

WEEKDAYS = "월화수목금토일"


def to_min(t: time) -> int:
    return t.hour * 60 + t.minute


def fmt(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def closed_weekdays(rest_date: str) -> set[int]:
    """휴무일 원문에서 '매주 반복되는' 휴무 요일(0=월)만 뽑는다. 격주·명절 등 판단 불가한 것은 무시."""
    text = re.sub(r"\([^)]*\)", "", rest_date).split("※")[0]  # 단서 조항·참고 문구 제거
    days: set[int] = set()
    for seg in re.split(r"[/\n,]", text):
        if re.search(r"매월|매달|째|격주", seg):
            continue
        if "주말" in seg:
            days |= {5, 6}
        for a, b in re.findall(r"([월화수목금토일])요일\s*[~\-]\s*([월화수목금토일])요일", seg):
            i, j = WEEKDAYS.index(a), WEEKDAYS.index(b)
            days |= {(i + k) % 7 for k in range((j - i) % 7 + 1)}
        seg = re.sub(r"[월화수목금토일]요일\s*[~\-]\s*[월화수목금토일]요일", "", seg)
        days |= {WEEKDAYS.index(c) for c in re.findall(r"([월화수목금토일])요일", seg)}
    return days


def open_range(open_hours: str) -> tuple[int, int] | None:
    """운영시간 원문에 시간 범위가 정확히 하나일 때만 (시작분, 종료분). 그 외에는 판단하지 않는다."""
    if "입실" in open_hours or "퇴실" in open_hours:
        return None
    ranges = re.findall(r"(\d{1,2}):(\d{2})\s*[~\-]\s*(\d{1,2}):(\d{2})", open_hours)
    if len(ranges) != 1:
        return None
    h1, m1, h2, m2 = map(int, ranges[0])
    start, end = h1 * 60 + m1, h2 * 60 + m2
    if end <= start:  # 자정 넘어 영업
        end += 24 * 60
    return start, end


def _name(place: PlaceInfo | None, place_id: str) -> str:
    return place.title if place else place_id


def _timeline(plan: PlannerPlan, d: int) -> list[PlannerItem]:
    return sorted((i for i in plan.items if i.day == d), key=lambda i: i.position)


def travel_pairs(plan: PlannerPlan, places: dict[str, PlaceInfo | None]) -> list[tuple[PlaceInfo, PlaceInfo]]:
    """build_schedule 이 이동시간을 계산할 구간 (직전에 알려진 장소 → 다음 장소). 건너뜀·정보 없는 장소는 뺀다."""
    pairs = []
    for d in range(1, plan.num_days + 1):
        prev = None
        for it in _timeline(plan, d):
            place = places.get(it.place_id)
            if it.status == "skipped" or place is None:
                continue
            if prev is not None:
                pairs.append((prev, place))
            prev = place
    return pairs


TravelTable = dict[tuple[str, str], TravelLeg]  # (출발 place_id, 도착 place_id) -> 이동


def build_schedule(
    plan: PlannerPlan, places: dict[str, PlaceInfo | None], travel: TravelTable | None = None,
) -> tuple[list[DayView], list[Violation]]:
    """plan.items 를 날짜별로 배치해 도착·시작·종료 시각을 계산하고 위반 목록을 만든다.
    이동시간은 travel (travel_pairs 의 구간을 routing 에 물은 결과) 에서 찾고, 없으면 직선거리로 근사한다."""
    travel = travel or {}
    violations: list[Violation] = []
    days: list[DayView] = []
    day_start, day_end = to_min(plan.day_start), to_min(plan.day_end)
    legs: list[tuple[PlannerItem, int]] = []  # (도착 항목, 이동분) — 이동시간 규칙 검사용

    for d in range(1, plan.num_days + 1):
        date = plan.start_date + timedelta(days=d - 1)
        cursor = day_start
        prev: PlaceInfo | None = None
        last_end: int | None = None
        views: list[ItemView] = []

        for it in _timeline(plan, d):
            place = places.get(it.place_id)
            name = _name(place, it.place_id)
            if place is None:
                violations.append(Violation(code="place_unavailable", severity="warning", day=d, item_id=it.id,
                                            message=f"장소 '{it.place_id}' 정보를 불러오지 못해 이동시간을 계산하지 않았습니다."))
            if it.status == "skipped":
                views.append(_view(it, place, None, None, None, None))
                continue

            leg = None
            if prev is not None and place is not None:
                leg = travel.get((prev.id, place.id)) or estimate_leg(prev, place, plan.travel_mode)
                legs.append((it, leg.minutes))
            arrive = cursor + (leg.minutes if leg else 0)
            start = arrive
            if it.start_time is not None:
                fixed = to_min(it.start_time)
                if arrive > fixed:
                    violations.append(Violation(
                        code="fixed_time_missed", severity="error", day=d, item_id=it.id,
                        message=f"'{name}' 는 {fmt(fixed)} 시작으로 고정돼 있지만 앞 일정 때문에 {fmt(arrive)} 에 도착합니다 ({arrive - fixed}분 늦음)."))
                start = max(arrive, fixed)
            end = start + it.stay_minutes

            if place is not None:
                if date.weekday() in closed_weekdays(place.rest_date):
                    violations.append(Violation(
                        code="closed_day", severity="warning", day=d, item_id=it.id,
                        message=f"'{name}' 는 {WEEKDAYS[date.weekday()]}요일 휴무일 수 있습니다 (휴무: {place.rest_date.strip()})."))
                hours = open_range(place.open_hours)
                if hours and (start < hours[0] or end > hours[1]):
                    violations.append(Violation(
                        code="outside_open_hours", severity="warning", day=d, item_id=it.id,
                        message=f"'{name}' 방문 {fmt(start)}~{fmt(end)} 이 운영시간({fmt(hours[0])}~{fmt(hours[1])}) 밖입니다."))

            views.append(_view(it, place, leg, arrive, start, end))
            cursor = last_end = end
            prev = place if place is not None else prev

        if last_end is not None and last_end > day_end:
            violations.append(Violation(
                code="day_overflow", severity="error", day=d, item_id=views[-1].id if views else None,
                message=f"{d}일차 일정이 {fmt(last_end)} 에 끝나 목표 종료 시각 {fmt(day_end)} 을 {last_end - day_end}분 넘습니다."))
        days.append(DayView(day=d, date=date, weekday=WEEKDAYS[date.weekday()],
                            end_at=fmt(last_end) if last_end is not None else None, items=views))

    for c in plan.constraints:
        violations.extend(_check_constraint(c, plan, days, legs, places))
    return days, violations


def _view(it: PlannerItem, place, leg: TravelLeg | None, arrive, start, end) -> ItemView:
    return ItemView(
        id=it.id, day=it.day, order=it.position, place_id=it.place_id, place=place,
        stay_minutes=it.stay_minutes, start_time=it.start_time, memo=it.memo, status=it.status,
        travel_minutes=leg and leg.minutes, travel_distance_m=leg and leg.distance_m,
        travel_source=leg and leg.source,
        arrive_at=None if arrive is None else fmt(arrive),
        start_at=None if start is None else fmt(start),
        end_at=None if end is None else fmt(end),
    )


def _check_constraint(
    c: PlannerConstraint, plan: PlannerPlan, days: list[DayView],
    legs: list[tuple[PlannerItem, int]], places: dict[str, PlaceInfo | None],
) -> list[Violation]:
    sev = "error" if c.hard else "warning"
    p = c.params
    active = [i for i in plan.items if i.status != "skipped"]

    def v(message: str, **kw) -> Violation:
        return Violation(code=c.type, severity=sev, message=message, constraint_id=c.id, **kw)

    if c.type == "must_include":
        if not any(i.place_id == p["place_id"] for i in active):
            return [v(f"'{_name(places.get(p['place_id']), p['place_id'])}' 를 반드시 포함해야 하지만 일정에 없습니다.")]
    elif c.type == "exclude":
        return [v(f"'{_name(places.get(i.place_id), i.place_id)}' 는 제외하기로 한 장소입니다.", day=i.day, item_id=i.id)
                for i in active if i.place_id == p["place_id"]]
    elif c.type == "end_before":
        limit = to_min(time.fromisoformat(p["time"]))
        out = []
        for d in days:
            if p.get("day") not in (None, d.day) or d.end_at is None:
                continue
            h, m = map(int, d.end_at.split(":"))
            if h * 60 + m > limit:
                out.append(v(f"{d.day}일차 일정이 {d.end_at} 에 끝나 {p['time']} 전 종료 규칙을 어깁니다.", day=d.day))
        return out
    elif c.type == "max_travel_minutes":
        return [v(f"'{_name(places.get(i.place_id), i.place_id)}' 까지 이동 {m}분으로 상한 {p['minutes']}분을 넘습니다.",
                  day=i.day, item_id=i.id)
                for i, m in legs if m > p["minutes"]]
    return []
