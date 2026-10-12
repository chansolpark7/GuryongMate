"""일정 타임라인 계산과 결정론적 위반 검사. DB·HTTP 에 의존하지 않는 순수 함수.

시간은 모두 '자정 기준 분(int)' 으로 계산하고, 출력할 때만 "HH:MM" 으로 바꾼다.
"""
import re
from dataclasses import dataclass
from datetime import date, time, timedelta

from app.planner.models import PlannerConstraint, PlannerItem, PlannerPlan
from app.planner.schemas import DayView, ItemView, PlaceInfo, TravelOption, Violation
from app.planner.travel import TravelLeg, estimate_leg

WEEKDAYS = "월화수목금토일"
MODES = ("walk", "car")
MODE_LABEL = {"walk": "도보", "car": "차량"}
# 기본 검사 기준. 같은 종류의 사용자 규칙(max_walk_minutes, max_daily_walk_minutes)이 있으면 그 규칙으로만 본다
LONG_WALK_MIN = 30  # 도보 구간 하나
DAILY_WALK_MIN = 90  # 하루 도보 합계


def leg_mode(plan: PlannerPlan, item: PlannerItem) -> str:
    """직전 장소에서 이 항목까지 오는 수단. 항목에 정한 값, 없으면 계획 기본."""
    return item.travel_mode or plan.travel_mode


def to_min(t: time) -> int:
    return t.hour * 60 + t.minute


def fmt(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


# 요일 범위. 앞쪽 '요일' 은 생략할 수 있다 (`월~목요일`). 숫자 뒤의 '월'(달)은 요일로 보지 않는다
_DAY_RANGE = re.compile(r"(?<!\d)([월화수목금토일])(?:요일)?\s*[~\-]\s*([월화수목금토일])요일")


def closed_weekdays(rest_date: str) -> set[int]:
    """휴무일 원문에서 '매주 반복되는' 휴무 요일(0=월)만 뽑는다. 격주·명절 등 판단 불가한 것은 무시.

    - `/`·줄바꿈 구간 안에서 '매월·째·격주' 가 나오면 그 뒤 `,` 로 이어진 요일도 같은 조건으로 보고 무시한다
      (`매월 셋째 토요일, 일요일` 의 일요일). '매주' 가 다시 나오면 매주 휴무로 돌아간다.
    - 범위는 앞쪽 '요일' 을 생략해도 읽는다 (`매주 월~목요일`)."""
    text = re.sub(r"\([^)]*\)", "", rest_date).split("※")[0]  # 단서 조항·참고 문구 제거
    days: set[int] = set()
    for part in re.split(r"[/\n]", text):
        monthly = False
        for seg in part.split(","):
            if "매주" in seg:
                monthly = False
            if re.search(r"매월|매달|째|격주", seg):
                monthly = True
            if monthly:
                continue
            if "주말" in seg:
                days |= {5, 6}
            for a, b in _DAY_RANGE.findall(seg):
                i, j = WEEKDAYS.index(a), WEEKDAYS.index(b)
                days |= {(i + k) % 7 for k in range((j - i) % 7 + 1)}
            seg = _DAY_RANGE.sub("", seg)
            days |= {WEEKDAYS.index(c) for c in re.findall(r"([월화수목금토일])요일", seg)}
    return days


@dataclass(frozen=True)
class OpenHours:
    """그날의 영업 범위 하나 (분, 자정 넘으면 close > 24*60) 와 영업 중 쉬는 구간들 ((시작, 끝, 이름) — 이름은 '준비시간' 등)."""
    open: int
    close: int
    breaks: tuple[tuple[int, int, str], ...] = ()


_BREAK_NAMES = {"준비": "준비시간", "브레이크": "브레이크타임", "휴게": "휴게시간"}
_HOURS_TOKEN = re.compile(
    r"(?P<season>하절기|동절기)(?:\s*\(\s*(?P<m1>\d{1,2})월?\s*[~\-]\s*(?P<m2>\d{1,2})월\s*\))?"
    r"|(?P<h1>\d{1,2}):(?P<n1>\d{2})\s*[~\-]\s*(?P<h2>\d{1,2}):(?P<n2>\d{2})"
    r"|(?P<d1>[월화수목금토일])(?:요일)?\s*[~\-]\s*(?P<d2>[월화수목금토일])(?:요일)?"
    r"|(?P<d>[월화수목금토일])요일"
    r"|(?P<dw>평일|주말)"
    r"|(?P<brk>준비|브레이크|휴게)\s*(?:시간|타임)"
)
_BREAK_WORD = re.compile(r"준비\s*시간|브레이크\s*타임|휴게\s*시간")
_TIME_RANGE = re.compile(r"\d{1,2}:\d{2}\s*[~\-]\s*\d{1,2}:\d{2}")
# ※ 문구가 운영 기간·계절에 따라 시간이 바뀐다고 말하는지 (`매년 8월 중순~9월 말까지 개장`, `하절기 (6~8월)는 20:00 까지`,
# `7~8월에만 운영`). 본문 범위만으로는 그날 맞는지 알 수 없다. '폐장 30분 전'·'개장 시간' 같은 시각 문구와
# 요일로 정한 운영일(`금요일~일요일에만 운영` — 휴무는 closed_weekdays 가 본다)은 아니다
_PERIOD_NOTE = re.compile(
    r"하절기|동절기|하계|동계|여름|겨울|성수기|비수기|시즌|기간|개장(?!\s*(?:시간|시각|전|후|\d))|(?<!요일)(?<!평일)(?<!주말)에만\s*운영"
    r"|\d{1,2}\s*월[^~\-\n]{0,8}[~\-]|\d{1,2}\s*[~\-]\s*\d{1,2}\s*월")
# 요일 토큰 뒤에 이 말이 오면 쉬는 날을 말하는 것이라 뒤 범위의 조건으로 쓰지 않는다 (`(월요일 휴무)`)
_DAY_OFF = re.compile(r"\s*(?:은|는)?\s*(?:정기\s*)?(?:휴무|휴관|휴업|휴일|쉼|쉽니다)")


def open_hours_for(open_hours: str, day: date) -> OpenHours | None:
    """운영시간 원문에서 그 날짜(월·요일)의 영업 범위 하나와 쉬는 구간들. 확실히 해석되지 않으면 None (판단하지 않는다).

    - 시각 범위마다 앞에 나온 조건(하절기/동절기(M월~N월), 평일·주말·요일)을 붙인다. 계절이 바뀌면 요일 조건은 처음부터.
    - 준비시간·브레이크타임·휴게시간 바로 뒤의 범위는 쉬는 구간. 마지막 주문·입장 마감 같은 시각 하나는 보지 않는다.
    - 그날에 맞는 영업 범위가 요일 조건 붙은 것 중 하나, 없으면 조건 없는 것 중 하나일 때만 판단한다 (시설별 시간처럼 둘 이상이면 None).
    - 계절에 월 범위가 없거나 조건 없는 범위가 따로 섞이면 None. ※ 뒤 참고 문구에 시각 범위가 있거나
      운영 기간·계절 변경(월 범위, 개장, 하절기·동절기, '7~8월에만 운영' 등)을 말해도 None.
    - 뒤에 '휴무·휴관·쉼' 이 오는 요일(`(월요일 휴무)`)은 조건으로 쓰지 않는다 (휴무는 closed_weekdays 가 본다).
    - ※ 의 '주말은 준비시간 없이' 는 주말에 쉬는 구간을 뺀다. 그 밖에 쉬는 구간을 말하는 ※ 문구가 있으면 쉬는 구간만 무시한다."""
    if "입실" in open_hours or "퇴실" in open_hours:
        return None
    text, _, note = open_hours.partition("※")
    if _TIME_RANGE.search(note) or _PERIOD_NOTE.search(note):
        return None
    wd, month = day.weekday(), day.month
    season: set[int] | None = None
    days: frozenset[int] | None = None
    pending_break: str | None = None
    entries: list[tuple[set[int] | None, frozenset[int] | None, str | None, int, int]] = []
    for m in _HOURS_TOKEN.finditer(text):
        if m["season"]:
            if not m["m1"]:
                return None  # 하절기·동절기의 달을 모른다
            a, b = int(m["m1"]), int(m["m2"])
            if not (1 <= a <= 12 and 1 <= b <= 12):
                return None
            season = {(a - 1 + k) % 12 + 1 for k in range((b - a) % 12 + 1)}
            days, pending_break = None, None
        elif m["h1"]:
            s, e = int(m["h1"]) * 60 + int(m["n1"]), int(m["h2"]) * 60 + int(m["n2"])
            entries.append((season, days, pending_break, s, e))
            pending_break = None
        elif (m["d1"] or m["d"] or m["dw"]) and _DAY_OFF.match(text, m.end()):
            continue  # 쉬는 요일 (조건 아님)
        elif m["d1"]:
            i, j = WEEKDAYS.index(m["d1"]), WEEKDAYS.index(m["d2"])
            days, pending_break = frozenset((i + k) % 7 for k in range((j - i) % 7 + 1)), None
        elif m["d"]:
            days, pending_break = frozenset({WEEKDAYS.index(m["d"])}), None
        elif m["dw"]:
            days, pending_break = frozenset(range(5) if m["dw"] == "평일" else (5, 6)), None
        else:
            pending_break = _BREAK_NAMES[m["brk"]]
    opens = [e for e in entries if e[2] is None]
    if not opens:
        return None
    if any(e[0] is None for e in opens) and any(e[0] is not None for e in opens):
        return None  # 계절 구분과 아닌 것이 섞임
    today = [e for e in entries if e[0] is None or month in e[0]]
    ctx = [e for e in today if e[1] is not None and wd in e[1]] or [e for e in today if e[1] is None]
    ctx_opens = [e for e in ctx if e[2] is None]
    if len(ctx_opens) != 1:
        return None
    _, ctx_days, _, start, end = ctx_opens[0]
    if end <= start:  # 자정 넘어 영업
        end += 24 * 60
    breaks = [(s, e, name) for _, d, name, s, e in ctx if name is not None and d == ctx_days and start < s < e < end]
    if _BREAK_WORD.search(note):
        weekend_free = re.search(r"주말\S*\s*(준비\s*시간|브레이크\s*타임|휴게\s*시간)\s*없", note)
        if not weekend_free or wd >= 5:
            breaks = []
    return OpenHours(start, end, tuple(sorted(breaks)))


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


TravelTable = dict[tuple[str, str, str], TravelLeg]  # (출발 place_id, 도착 place_id, 수단) -> 이동


class Leg:
    """구간 하나 (직전 장소 -> item). 검증 규칙이 쓴다."""

    def __init__(self, item: PlannerItem, day: int, mode: str, options: dict[str, TravelLeg], name: str):
        self.item, self.day, self.mode, self.options, self.name = item, day, mode, options, name
        self.leg = options[mode]


@dataclass(frozen=True)
class Actual:
    """현재 기준 재계산(projection)의 입력. 그날 타임라인 앞부분을 실제 기록·현재 위치로 고정한다.

    - anchor: 지금 머무는 항목(moving=False, anchor_arrive = 실제 도착) 또는
      이동 중 향하는 항목(moving=True, anchor_arrive = now + 지금 위치에서 남은 이동시간).
    - anchor 가 없으면 마지막으로 다녀온 항목 뒤의 첫 항목부터 '지금 출발' 로 본다 (now 이전에는 도착하지 않음).
      그보다 앞의 다녀오지 않은 항목은 건너뛴 것으로 보고 지난 항목으로 둔다 (behind_last_visit).
    """
    day: int
    now: int  # 자정 기준 분
    anchor_id: int | None = None
    anchor_arrive: int | None = None
    moving: bool = False


def build_schedule(
    plan: PlannerPlan, places: dict[str, PlaceInfo | None], travel: TravelTable | None = None,
    actual: Actual | None = None,
) -> tuple[list[DayView], list[Violation]]:
    """plan.items 를 날짜별로 배치해 도착·시작·종료 시각을 계산하고 위반 목록을 만든다.
    이동시간은 travel (travel_pairs 의 구간을 수단별로 routing 에 물은 결과) 에서 찾고, 없으면 직선거리로 근사한다.
    구간 수단은 항목마다 다를 수 있다 (leg_mode). 비교용으로 도보·차량 시간을 모두 붙인다.

    actual 이 있으면 그날(actual.day)은 현재 기준으로 다시 계산한다 (projection, docs/planner/README.md §8):
    - 지난 항목(done, 또는 anchor 보다 앞의 항목): 기록된 도착·완료 시각 그대로 (없으면 계산값). 시각 위반은 따지지 않는다
    - 지금 머무는 항목: 실제 도착부터 (고정 시각·운영 시작까지는 기다림). 늦게 와서 '도착 + 체류' 가 기준 일정의 예정 종료를
      넘으면 예정 종료에 떠난다고 본다 (규칙 B, 2026-10-12). 어느 쪽이든 now 가 지났으면 now 에 끝난다. 고정 시각 놓침은 따지지 않는다
    - 이동 중 향하는 항목: anchor_arrive 에 도착. 그 뒤 항목은 평소처럼 이어서 계산한다"""
    travel = travel or {}
    violations: list[Violation] = []
    days: list[DayView] = []
    day_start, day_end = to_min(plan.day_start), to_min(plan.day_end)
    legs: list[Leg] = []  # 이동 규칙 검사용

    for d in range(1, plan.num_days + 1):
        date = plan.start_date + timedelta(days=d - 1)
        cursor = day_start
        prev: PlaceInfo | None = None
        last_end: int | None = None
        views: list[ItemView] = []

        now_day = actual if actual is not None and actual.day == d else None
        # 머무는 항목의 기준 일정 예정 종료 (규칙 B). 계획 기준 계산은 actual 없이 한 번 더 돌린다
        anchored = now_day is not None and now_day.anchor_id is not None and not now_day.moving
        planned_end = _planned_end(plan, places, travel, now_day) if anchored else {}
        seen_anchor = live = False  # live: 지금 이후 항목(anchor 또는 남은 항목)을 계산하기 시작함
        # 기준점이 없으면 마지막으로 다녀온 항목보다 앞의 항목은 지난 항목 (순서를 건너뛰고 다녀옴)
        behind = behind_last_visit(_timeline(plan, d)) if now_day is not None and now_day.anchor_id is None else set()
        for it in _timeline(plan, d):
            place = places.get(it.place_id)
            name = _name(place, it.place_id)
            if place is None:
                violations.append(Violation(code="place_unavailable", severity="warning", day=d, item_id=it.id,
                                            message=f"장소 '{it.place_id}' 정보를 불러오지 못해 이동시간을 계산하지 않았습니다."))
            if it.status == "skipped":
                views.append(_view(it, place, None, None, None, None, None, None))
                continue

            leg = mode = options = None
            if prev is not None and place is not None:
                options = {m: travel.get((prev.id, place.id, m)) or estimate_leg(prev, place, m) for m in MODES}
                mode = leg_mode(plan, it)
                leg = options[mode]
                legs.append(Leg(it, d, mode, options, name))
            fixed = to_min(it.start_time) if it.start_time is not None else None
            phase = "plan"
            if now_day is not None:
                if it.id == now_day.anchor_id:
                    phase, seen_anchor = ("moving" if now_day.moving else "here"), True
                elif it.status == "done" or (now_day.anchor_id is not None and not seen_anchor) or it.id in behind:
                    phase = "past"
                elif now_day.anchor_id is None and not live:
                    cursor = max(cursor, now_day.now)  # 지금 출발

            if phase == "past":
                arrive = to_min(it.arrived_at) if it.arrived_at else cursor + (leg.minutes if leg else 0)
                start = arrive if it.arrived_at or fixed is None else max(arrive, fixed)
                end = max(start, to_min(it.done_at) if it.done_at else start + it.stay_minutes)
                views.append(_view(it, place, leg, arrive, start, end, mode, options))
                cursor = last_end = end if not live else max(cursor, end)
                prev = place if place is not None else prev
                continue
            live = live or now_day is not None

            if phase == "here":
                arrive = now_day.anchor_arrive
                start = arrive if fixed is None else max(arrive, fixed)  # 이미 와 있다: 고정 시각 놓침은 따지지 않는다
                end = None  # 운영 시작 대기까지 본 뒤 정한다 (아래)
            else:
                if phase == "plan" and last_end is None and fixed is not None:
                    # 그날 첫 항목: 앞 일정이 없으니 고정 시각이 하루 시작보다 일러도 그때 시작한다
                    # (지금 기준이면 지금보다 이르게는 아님 — 지났으면 '지금 기준으로' 놓침)
                    cursor = min(cursor, max(fixed, now_day.now if now_day is not None else fixed))
                arrive = now_day.anchor_arrive if phase == "moving" else cursor + (leg.minutes if leg else 0)
                start = arrive
                if fixed is not None:
                    if arrive > fixed:
                        why = "지금 기준으로" if now_day is not None else "앞 일정 때문에"
                        violations.append(Violation(
                            code="fixed_time_missed", severity="error", day=d, item_id=it.id,
                            message=f"'{name}' 는 {fmt(fixed)} 시작으로 고정돼 있지만 {why} {fmt(arrive)} 에 도착합니다 ({arrive - fixed}분 늦음)."))
                    start = max(arrive, fixed)
                end = start + it.stay_minutes

            hours = open_hours_for(place.open_hours, date) if place is not None else None
            waited = None  # 진행 중 기준으로 기다려서 시작하는 시각 (운영 시작·쉬는 구간 끝)
            if now_day is not None and hours:
                # 실제 진행 기준: 문을 열기 전에 도착하면 열 때까지, 쉬는 구간(준비시간 등) 중에 도착하면 끝날 때까지 기다린다
                # (고정 시각과 같은 방식). 일찍 온 것을 '운영시간 밖' 새 위반(-> 지연 선제 턴)으로 보지 않는다
                # (audit-2026-10-12 B3). 계획 기준은 그대로 위반
                waited = hours.open if start < hours.open else None
                for bs, be, _ in hours.breaks:
                    if bs <= (start if waited is None else waited) < be:
                        waited = be
                if waited is not None:
                    start = waited
                    if phase != "here":
                        end = start + it.stay_minutes
            if phase == "here":
                # 기다려야 하는 시각(고정 시각·운영 시작·쉬는 구간 끝). 그만큼은 예정 종료가 늦어도 온전히 머문다
                floor = max((t for t in (fixed, hours.open if hours else None, waited) if t is not None), default=None)
                end = visiting_end(start, it.stay_minutes, now_day.now, planned_end.get(it.id), floor)

            if place is not None:
                if date.weekday() in closed_weekdays(place.rest_date):
                    violations.append(Violation(
                        code="closed_day", severity="warning", day=d, item_id=it.id,
                        message=f"'{name}' 는 {WEEKDAYS[date.weekday()]}요일 휴무일 수 있습니다 (휴무: {place.rest_date.strip()})."))
                if hours and (start < hours.open or end > hours.close):
                    violations.append(Violation(
                        code="outside_open_hours", severity="warning", day=d, item_id=it.id,
                        message=f"'{name}' 방문 {fmt(start)}~{fmt(end)} 이 운영시간({fmt(hours.open)}~{fmt(hours.close)}) 밖입니다."))
                elif hours and (brk := next((b for b in hours.breaks if start < b[1] and end > b[0]), None)):
                    violations.append(Violation(
                        code="outside_open_hours", severity="warning", day=d, item_id=it.id,
                        message=f"'{name}' 방문 {fmt(start)}~{fmt(end)} 이 {brk[2]}({fmt(brk[0])}~{fmt(brk[1])})과 겹칩니다."))

            views.append(_view(it, place, leg, arrive, start, end, mode, options))
            cursor = last_end = end
            prev = place if place is not None else prev

        if last_end is not None and last_end > day_end:
            violations.append(Violation(
                code="day_overflow", severity="error", day=d,
                item_id=next((v.id for v in reversed(views) if v.status != "skipped"), None),  # 마지막으로 다니는 항목
                message=f"{d}일차 일정이 {fmt(last_end)} 에 끝나 목표 종료 시각 {fmt(day_end)} 을 {last_end - day_end}분 넘습니다."))
        days.append(DayView(day=d, date=date, weekday=WEEKDAYS[date.weekday()],
                            end_at=fmt(last_end) if last_end is not None else None, items=views))

    violations.extend(_check_travel(plan, legs))
    for c in plan.constraints:
        violations.extend(_check_constraint(c, plan, days, legs, places))
    return days, violations


def visiting_end(start: int, stay: int, now: int, planned_end: int | None, floor: int | None = None) -> int:
    """지금 머무는 항목이 끝나는 시각 (규칙 B, docs/planner/README.md §8.2).

    - 제때·일찍 왔으면 start + 체류 (start 는 고정 시각·운영 시작 대기를 반영한 값).
    - 늦게 와서 start + 체류 가 기준 일정의 예정 종료(planned_end)를 넘으면 예정 종료에 떠난다고 본다
      (체류를 줄여 뒤 일정을 지킨다). 단 기다려야 하는 시각(floor) + 체류 보다 이르게 자르지는 않는다.
    - 어느 쪽이든 now 가 지났으면 now 에 끝난다 (그때부터 늦음이 생긴다). 예정 종료를 모르면 기존대로."""
    natural = start + stay
    if planned_end is not None:
        cap = planned_end if floor is None else max(planned_end, floor + stay)
        natural = max(start, min(natural, cap))
    return max(natural, now)


def _planned_end(plan: PlannerPlan, places: dict[str, PlaceInfo | None], travel: TravelTable,
                 actual: Actual) -> dict[int, int]:
    """기준 일정(actual 없이 계산)에서 그날 항목별 예정 종료 (분). 규칙 B 의 기준."""
    days, _ = build_schedule(plan, places, travel)
    return {i.id: parse_hhmm(i.end_at) for i in days[actual.day - 1].items if i.end_at}


def _check_travel(plan: PlannerPlan, legs: list[Leg]) -> list[Violation]:
    """이동 기본 검사: 긴 도보 구간, 하루 도보 합계, 직선거리로 추정한 구간. 이미 다녀온 항목으로 간 이동은 뺀다."""
    out: list[Violation] = []
    legs = [g for g in legs if g.item.status != "done"]
    rules = {c.type for c in plan.constraints}
    if "max_walk_minutes" not in rules:
        for g in legs:
            if g.mode == "walk" and g.leg.minutes > LONG_WALK_MIN:
                car = g.options["car"]
                out.append(Violation(
                    code="long_walk", severity="warning", day=g.day, item_id=g.item.id,
                    message=f"'{g.name}' 까지 도보 {g.leg.minutes}분입니다 ({LONG_WALK_MIN}분 초과). "
                            f"차량이면 약 {car.minutes}분이라 이 구간만 차량으로 바꿀 수 있습니다."))
    if "max_daily_walk_minutes" not in rules:
        for d, total in _daily_walk(legs).items():
            if total > DAILY_WALK_MIN:
                out.append(Violation(
                    code="daily_walk_total", severity="warning", day=d,
                    message=f"{d}일차 도보 이동이 모두 {total}분입니다 ({DAILY_WALK_MIN}분 초과). "
                            "먼 구간을 차량으로 바꾸거나 가까운 장소로 묶으세요."))
    estimated: dict[int, list[Leg]] = {}
    for g in legs:
        if g.leg.source == "estimate" and g.leg.minutes > 0:
            estimated.setdefault(g.day, []).append(g)
    for d, gs in estimated.items():
        names = ", ".join(f"'{g.name}'" for g in gs[:3]) + (" 등" if len(gs) > 3 else "")
        out.append(Violation(
            code="travel_estimated", severity="warning", day=d, item_id=gs[0].item.id,
            message=f"{d}일차 {names} 까지 이동 {len(gs)}구간은 길찾기를 쓰지 못해 직선거리로 추정했습니다. "
                    "실제 시간과 다를 수 있습니다."))
    return out


def _daily_walk(legs: list[Leg]) -> dict[int, int]:
    total: dict[int, int] = {}
    for g in legs:
        if g.mode == "walk":
            total[g.day] = total.get(g.day, 0) + g.leg.minutes
    return total


def _view(it: PlannerItem, place, leg: TravelLeg | None, arrive, start, end, mode, options) -> ItemView:
    return ItemView(
        id=it.id, day=it.day, order=it.position, place_id=it.place_id, place=place,
        stay_minutes=it.stay_minutes, start_time=it.start_time, memo=it.memo, status=it.status,
        travel_mode=it.travel_mode, leg_mode=mode,
        travel_minutes=leg and leg.minutes, travel_distance_m=leg and leg.distance_m,
        travel_source=leg and leg.source,
        travel_options={m: TravelOption(minutes=o.minutes, distance_m=o.distance_m, source=o.source)
                        for m, o in options.items()} if options else None,
        arrive_at=None if arrive is None else fmt(arrive),
        start_at=None if start is None else fmt(start),
        end_at=None if end is None else fmt(end),
        arrived_at=it.arrived_at, done_at=it.done_at,
    )


def _check_constraint(
    c: PlannerConstraint, plan: PlannerPlan, days: list[DayView], legs: list[Leg],
    places: dict[str, PlaceInfo | None],
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
        return [v(f"'{g.name}' 까지 이동 {g.leg.minutes}분({MODE_LABEL[g.mode]})으로 상한 {p['minutes']}분을 넘습니다.",
                  day=g.day, item_id=g.item.id)
                for g in legs if g.leg.minutes > p["minutes"]]
    elif c.type == "max_walk_minutes":
        legs = [g for g in legs if g.item.status != "done"]
        return [v(f"'{g.name}' 까지 도보 {g.leg.minutes}분으로 도보 상한 {p['minutes']}분을 넘습니다. "
                  f"차량이면 약 {g.options['car'].minutes}분입니다.", day=g.day, item_id=g.item.id)
                for g in legs if g.mode == "walk" and g.leg.minutes > p["minutes"]]
    elif c.type == "max_daily_walk_minutes":
        legs = [g for g in legs if g.item.status != "done"]
        return [v(f"{d}일차 도보 이동이 모두 {total}분으로 하루 도보 상한 {p['minutes']}분을 넘습니다.", day=d)
                for d, total in _daily_walk(legs).items() if total > p["minutes"]]
    return []


# ---------- 현재 기준 재계산 (projection) 비교 ----------

def parse_hhmm(s: str) -> int:
    """fmt 의 역. 자정을 넘은 "24:30" 도 받는다."""
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def behind_last_visit(timeline: list[PlannerItem]) -> set[int]:
    """마지막으로 다녀온·머문(done·visiting) 항목보다 앞의 항목 id (그 항목들은 순서를 건너뛰고 지나온 것)."""
    items = [i for i in timeline if i.status != "skipped"]
    last = max((n for n, i in enumerate(items) if i.status in ("done", "visiting")), default=0)
    return {i.id for i in items[:last]}


def next_item_id(plan: PlannerPlan, actual: Actual) -> int | None:
    """다음에 갈 항목: 이동 중이면 향하는 항목, 머무는 중이면 그 뒤 첫 미완료 항목,
    아니면 마지막으로 다녀온 항목 뒤의 첫 미완료 항목 (그 앞의 건너뛴 항목은 다음이 아니다)."""
    items = [i for i in _timeline(plan, actual.day) if i.status != "skipped"]
    if actual.anchor_id is None:
        behind = behind_last_visit(items)
        items = [i for i in items if i.id not in behind]
    else:
        if actual.moving:
            return actual.anchor_id
        n = next((k for k, i in enumerate(items) if i.id == actual.anchor_id), None)
        if n is not None:
            items = items[n + 1:]
    return next((i.id for i in items if i.status != "done"), None)


def _key(v: Violation) -> tuple:
    return v.code, v.day, v.item_id, v.constraint_id


def new_violations(baseline: list[Violation], projected: list[Violation]) -> list[Violation]:
    """재계산 위반 중 기준 일정에 없던 것 (코드 + 날짜 + 항목 + 규칙 기준)."""
    old = {_key(v) for v in baseline}
    return [v for v in projected if _key(v) not in old]


def delay_minutes(baseline: list[DayView], projected: list[DayView], day: int, next_id: int | None) -> int | None:
    """다음 항목의 시작 시각 차이 (재계산 - 기준). 다음 항목이 없으면 그날 종료 시각 차이. 비교할 수 없으면 None."""
    b, p = baseline[day - 1], projected[day - 1]
    if next_id is not None:
        bi = next((i for i in b.items if i.id == next_id), None)
        pi = next((i for i in p.items if i.id == next_id), None)
        if bi and pi and bi.start_at and pi.start_at:
            return parse_hhmm(pi.start_at) - parse_hhmm(bi.start_at)
    if b.end_at and p.end_at:
        return parse_hhmm(p.end_at) - parse_hhmm(b.end_at)
    return None
