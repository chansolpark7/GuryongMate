"""장소 추천: 후보 상태 계산과 [추가] 위치 계산. 명세: docs/planner/README.md §6

후보 상태는 저장하지 않는다. 지금 일정에서 "후보의 day 에 같은 place_id 항목이 있는가"로 정한다.
시간은 상태 판단에 쓰지 않는다 (자동 계산 시각은 앞 일정에 따라 움직이므로). 시간 힌트는 위치 계산과
같은 날 중복 항목 중 하나를 고를 때만 쓴다.
"""
from datetime import datetime

from app.planner.models import PlannerPlan, PlannerRecommendation, PlannerRecommendationCandidate
from app.planner.ops import simulate
from app.planner.places import PlaceLookup, PlacesUnavailable
from app.planner.schemas import (
    AddItemOp,
    CandidateItem,
    CandidateView,
    ItemView,
    PlanDetail,
    RecommendationView,
)
from app.planner.validation import to_min

FAR = 10 ** 6


def _minutes(hhmm: str | None) -> int | None:
    if hhmm is None:
        return None
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def match_item(detail: PlanDetail, cand: PlannerRecommendationCandidate) -> ItemView | None:
    """후보의 day 에 같은 장소 항목이 있으면 그중 time_hint 에 가장 가까운 것 (없으면 가장 앞 순서)."""
    if cand.day > len(detail.days):
        return None
    same = [i for i in detail.days[cand.day - 1].items if i.place_id == cand.place_id]
    if not same:
        return None
    if cand.time_hint is None:
        return same[0]
    hint = to_min(cand.time_hint)

    def distance(i: ItemView) -> int:
        start = _minutes(i.start_at)
        return FAR if start is None else abs(start - hint)

    return min(same, key=lambda i: (distance(i), i.order))


def candidate_view(detail: PlanDetail, cand: PlannerRecommendationCandidate, places: PlaceLookup) -> CandidateView:
    try:
        place = places.get(cand.place_id)
    except PlacesUnavailable:
        place = None
    if cand.day > len(detail.days):
        state, item = "day_out_of_range", None
    else:
        matched = match_item(detail, cand)
        state = "in_plan" if matched else "not_in_plan"
        item = CandidateItem(item_id=matched.id, day=matched.day, order=matched.order, start_at=matched.start_at,
                             end_at=matched.end_at) if matched else None
    other_days = sorted({d.day for d in detail.days if d.day != cand.day
                         for i in d.items if i.place_id == cand.place_id})
    return CandidateView(id=cand.id, place_id=cand.place_id, place=place, day=cand.day, time_hint=cand.time_hint,
                         stay_minutes=cand.stay_minutes, reason=cand.reason, state=state, item=item,
                         other_days=other_days)


def recommendation_view(detail: PlanDetail, rec: PlannerRecommendation, places: PlaceLookup) -> RecommendationView:
    return RecommendationView(id=rec.id, plan_id=rec.plan_id, title=rec.title, source=rec.source, origin=rec.origin,
                              created_at=rec.created_at,
                              candidates=[candidate_view(detail, c, places) for c in rec.candidates])


def add_op(cand: PlannerRecommendationCandidate, order: int) -> AddItemOp:
    return AddItemOp(op="add_item", place_id=cand.place_id, day=cand.day, order=order,
                     stay_minutes=cand.stay_minutes, memo=cand.reason[:500])


def _vkey(v) -> tuple:
    return v.code, v.day, v.item_id, v.constraint_id


def best_order(plan: PlannerPlan, detail: PlanDetail, cand: PlannerRecommendationCandidate,
               places: PlaceLookup, now: datetime | None = None) -> int:
    """그날 각 위치에 넣어 보고 고른다.
    - time_hint 있음: 계산된 시작 시각이 힌트에 가장 가까운 위치 (같으면 앞 위치)
    - time_hint 없음: 그날 총 이동시간이 가장 적게 늘어나는 위치 (같으면 뒤 위치)
    여행 중인 날(그날 다녀온·머무는 항목이 있음)은 마지막으로 다녀온·머무는 항목 뒤에만 넣는다 (지난 일정 앞에 넣지 않는다).
    이때 위반은 지금 기준(live_actual)으로 보고, 새 위반(운영시간·하루 초과 등)이 가장 적은 위치를 먼저 고른다.
    여행 전·진행 기록이 없는 날은 위 규칙 그대로."""
    items = detail.days[cand.day - 1].items
    n = len(items)
    last = max((k for k, i in enumerate(items, start=1) if i.status in ("done", "visiting")), default=0)
    trip = last > 0
    hint = to_min(cand.time_hint) if cand.time_hint is not None else None
    before: set[tuple] = set()
    if trip:
        before = {_vkey(v) for v in simulate(plan, [], places, now=now).violations}
    best: tuple[tuple, int] | None = None
    for order in range(last + 1, n + 2):
        after = simulate(plan, [add_op(cand, order)], places, now=now if trip else None)
        day = after.days[cand.day - 1]
        new = next(i for i in day.items if i.id < 0)
        if hint is not None:
            start = _minutes(new.start_at)
            key = (FAR if start is None else abs(start - hint), order)
        else:
            travel = sum(i.travel_minutes or 0 for i in day.items)
            key = (travel, -order)
        if trip:
            key = (len({_vkey(v) for v in after.violations} - before),) + key
        if best is None or key < best[0]:
            best = (key, order)
    return best[1]
