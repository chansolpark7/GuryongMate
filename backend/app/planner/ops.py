"""변경 연산(ops) 엔진. 직접 편집·미리보기·제안 승인이 모두 이 코드로 계획을 바꾼다.

- persist=True : DB 에 붙은 계획을 직접 수정하고 version 을 1 올린다 (호출 쪽에서 commit)
- preview(...)  : 계획을 메모리에 복제해 적용한 결과만 계산 (DB 변경 없음)
- overwrite(...): 제안의 after 스냅샷으로 계획을 통째로 맞춘다 (항목·규칙 id 는 가능한 한 유지, 진행 상태는 지금 값)
- project(...)  : 여행 중 현재 기준으로 그날 남은 일정을 다시 계산 (저장 안 함)

한 ops 목록 안에서 새로 만든 항목·규칙은 음수 임시 id(-1, -2, ...)로 다시 참조할 수 있다.
"""
from datetime import date, datetime, time, timezone

from sqlalchemy import inspect

from app.planner.errors import PlannerError
from app.planner.models import PlannerConstraint, PlannerItem, PlannerPlan
from app.planner.places import PlaceLookup, PlacesUnavailable
from app.planner.schemas import (
    AddConstraintOp,
    AddItemOp,
    Change,
    ConstraintView,
    MAX_DAYS,
    PlaceInfo,
    PlanDetail,
    PlanOp,
    PlanSummary,
    PositionMoving,
    PositionVisiting,
    ProjectionNext,
    ProjectionResult,
    RemoveConstraintOp,
    RemoveItemOp,
    UpdateItemOp,
    UpdatePlanOp,
)
from app.planner import validation
from app.planner.clock import KST
from app.planner.travel import get_travel_lookup
from app.planner.validation import Actual, build_schedule, leg_mode, to_min, travel_pairs


PLAN_FIELDS = ("title", "start_date", "end_date", "day_start", "day_end", "travel_mode", "memo")
PROGRESS = ("planned", "visiting", "done")  # 진행 상태. 이 안에서 바뀌는 것은 일정 변경이 아니다 (skipped 는 일정 변경)


def check_range(start_date: date, end_date: date, day_start: time, day_end: time) -> None:
    """기간·하루 시간대 조건. 계획 생성과 수정(병합 후)에 같이 쓴다."""
    if end_date < start_date:
        raise PlannerError(422, "invalid_range", "end_date 는 start_date 와 같거나 이후여야 합니다.")
    if (end_date - start_date).days + 1 > MAX_DAYS:
        raise PlannerError(422, "invalid_range", f"여행 기간은 최대 {MAX_DAYS}일입니다.")
    if day_end <= day_start:
        raise PlannerError(422, "invalid_range", "day_end 는 day_start 보다 늦어야 합니다.")


class Applier:
    def __init__(self, plan: PlannerPlan, places: PlaceLookup, persist: bool):
        self.plan = plan
        self.places = places
        self.persist = persist
        self._new_items: dict[int, PlannerItem] = {}
        self._new_rules: dict[int, PlannerConstraint] = {}

    def apply_all(self, ops: list[PlanOp], with_index: bool = False) -> None:
        """하나라도 실패하면 PlannerError (호출 쪽에서 rollback). with_index 면 실패한 연산 위치(op_index)를 붙인다."""
        for n, op in enumerate(ops):
            try:
                self.apply(op)
            except PlannerError as e:
                if with_index:
                    e.extra["op_index"] = n
                    e.message = f"ops[{n}] ({op.op}): {e.message}"
                raise
        self.plan.updated_at = datetime.now(timezone.utc)
        if self.persist:
            self.plan.version += 1

    def apply(self, op: PlanOp) -> None:
        match op:
            case AddItemOp():
                self._check_day(op.day)
                self._require_place(op.place_id)
                item = PlannerItem(place_id=op.place_id, stay_minutes=op.stay_minutes, start_time=op.start_time,
                                   memo=op.memo, status="planned", travel_mode=op.travel_mode)
                temp_id = -(len(self._new_items) + 1)
                if not self.persist:
                    item.id = temp_id
                self._new_items[temp_id] = item
                self.plan.items.append(item)
                self._insert(item, op.day, op.order)
            case UpdateItemOp():
                item = self._item(op.item_id)
                sent = op.model_fields_set
                if op.day is not None or op.order is not None:
                    new_day = op.day or item.day
                    self._check_day(new_day)
                    old_day = item.day
                    self._insert(item, new_day, op.order)
                    if old_day != new_day:
                        self._renumber(old_day)
                if op.stay_minutes is not None:
                    item.stay_minutes = op.stay_minutes
                if "start_time" in sent:
                    item.start_time = op.start_time
                if op.memo is not None:
                    item.memo = op.memo
                if op.status is not None and op.status != item.status:
                    set_status(item, op.status)
                if "travel_mode" in sent:
                    item.travel_mode = op.travel_mode
            case RemoveItemOp():
                item = self._item(op.item_id)
                self.plan.items.remove(item)
                self._renumber(item.day)
            case UpdatePlanOp():
                changes = op.model_dump(exclude_unset=True, exclude={"op"})
                merged = {k: changes.get(k, getattr(self.plan, k)) for k in ("start_date", "end_date", "day_start", "day_end")}
                check_range(**merged)
                num_days = (merged["end_date"] - merged["start_date"]).days + 1
                if any(i.day > num_days for i in self.plan.items):
                    raise PlannerError(409, "range_conflict", f"{num_days}일차 이후에 일정이 남아 있어 기간을 줄일 수 없습니다. "
                                       "먼저 옮기거나 삭제하세요.", num_days=num_days)
                for k, v in changes.items():
                    setattr(self.plan, k, v)
            case AddConstraintOp():
                if "place_id" in op.params:
                    self._require_place(op.params["place_id"])
                if "day" in op.params:
                    self._check_day(op.params["day"])
                rule = PlannerConstraint(**op.model_dump(exclude={"op"}))
                temp_id = -(len(self._new_rules) + 1)
                if not self.persist:
                    rule.id = temp_id
                self._new_rules[temp_id] = rule
                self.plan.constraints.append(rule)
            case RemoveConstraintOp():
                rule = self._new_rules.get(op.constraint_id) if op.constraint_id < 0 else next(
                    (c for c in self.plan.constraints if c.id == op.constraint_id), None)
                if rule is None or rule not in self.plan.constraints:
                    raise PlannerError(404, "constraint_not_found", f"규칙 {op.constraint_id} 이 없습니다.")
                self.plan.constraints.remove(rule)

    # ----- 보조 -----
    def _item(self, item_id: int) -> PlannerItem:
        item = self._new_items.get(item_id) if item_id < 0 else next((i for i in self.plan.items if i.id == item_id), None)
        if item is None or item not in self.plan.items:
            raise PlannerError(404, "item_not_found", f"일정 항목 {item_id} 이 없습니다.")
        return item

    def _check_day(self, day: int) -> None:
        check_day(self.plan, day)

    def _require_place(self, place_id: str) -> None:
        require_place(self.places, place_id)

    def _insert(self, item: PlannerItem, day: int, order: int | None) -> None:
        """item 을 day 의 order 번째(1부터, None 이면 맨 뒤)에 넣고 그날 순서를 1..n 으로 다시 매긴다."""
        others = sorted((i for i in self.plan.items if i.day == day and i is not item), key=lambda i: i.position)
        idx = len(others) if order is None else min(order - 1, len(others))
        others.insert(idx, item)
        item.day = day
        for n, it in enumerate(others, start=1):
            it.position = n

    def _renumber(self, day: int) -> None:
        for n, it in enumerate(sorted((i for i in self.plan.items if i.day == day), key=lambda i: i.position), start=1):
            it.position = n


def set_status(item: PlannerItem, status: str, at: time | None = None) -> None:
    """상태를 바꾸고 실제 도착·완료 기록을 맞춘다. at 은 visiting 이면 도착, done 이면 완료 시각."""
    item.status = status
    if status in ("planned", "skipped"):
        item.arrived_at = item.done_at = None
    elif status == "visiting":
        item.done_at = None
        if at is not None:
            item.arrived_at = at
    elif at is not None:  # done
        item.done_at = at


def check_day(plan: PlannerPlan, day: int) -> None:
    if not 1 <= day <= plan.num_days:
        raise PlannerError(422, "day_out_of_range", f"day 는 1~{plan.num_days} 사이여야 합니다 (여행 {plan.num_days}일).",
                           day=day, num_days=plan.num_days)


def require_place(places: PlaceLookup, place_id: str) -> PlaceInfo:
    try:
        found = places.get(place_id)
    except PlacesUnavailable as e:
        raise PlannerError(503, "places_unavailable", f"장소 정보를 조회할 수 없습니다: {e}") from e
    if found is None:
        raise PlannerError(422, "place_not_found", f"장소 '{place_id}' 가 없습니다. 장소 검색으로 찾은 id 를 쓰세요.",
                           place_id=place_id)
    return found


# ---------- 스냅샷 덮어쓰기 ----------

def overwrite(plan: PlannerPlan, after: PlanDetail) -> None:
    """계획을 after 스냅샷과 같게 맞춘다. 남아 있는 항목·규칙은 id 를 유지하고, 없어진 것은 새로 만든다.
    남아 있는 항목의 진행 상태(planned/visiting/done 과 도착·완료 시각)는 지금 값을 유지한다. 스냅샷은 제안 당시라
    그 뒤의 도착·완료를 모르기 때문이다. 건너뜀(skipped)으로 바꾸거나 되돌리는 것은 일정 변경이라 스냅샷을 따른다."""
    for f in PLAN_FIELDS:
        setattr(plan, f, getattr(after, f))

    current = {i.id: i for i in plan.items}
    keep: set[int] = set()
    for d in after.days:
        for v in d.items:
            item = current.get(v.id) if v.id > 0 else None
            if item is None:
                item = PlannerItem()
                plan.items.append(item)
                item.status, item.arrived_at, item.done_at = v.status, v.arrived_at, v.done_at
            else:
                keep.add(v.id)
                if v.status == "skipped" or item.status == "skipped":
                    item.status, item.arrived_at, item.done_at = v.status, v.arrived_at, v.done_at
            item.place_id, item.day, item.position = v.place_id, v.day, v.order
            item.stay_minutes, item.start_time, item.memo = v.stay_minutes, v.start_time, v.memo
            item.travel_mode = v.travel_mode
    for item_id, item in current.items():
        if item_id not in keep:
            plan.items.remove(item)

    rules = {c.id: c for c in plan.constraints}
    keep = set()
    for v in after.constraints:
        rule = rules.get(v.id) if v.id > 0 else None
        if rule is None:
            rule = PlannerConstraint()
            plan.constraints.append(rule)
        else:
            keep.add(v.id)
        rule.type, rule.params, rule.hard, rule.source, rule.note = v.type, v.params, v.hard, v.source, v.note
    for rule_id, rule in rules.items():
        if rule_id not in keep:
            plan.constraints.remove(rule)

    plan.updated_at = datetime.now(timezone.utc)
    plan.version += 1


# ---------- 복제 · 상세 · 미리보기 ----------

def _columns(obj) -> dict:
    return {a.key: getattr(obj, a.key) for a in inspect(obj).mapper.column_attrs}


def clone(plan: PlannerPlan) -> PlannerPlan:
    """세션에 붙지 않은 사본. 미리보기 계산용."""
    copy = PlannerPlan(**_columns(plan))
    copy.items = [PlannerItem(**_columns(i)) for i in plan.items]
    copy.constraints = [PlannerConstraint(**_columns(c)) for c in plan.constraints]
    return copy


def detail(plan: PlannerPlan, places: PlaceLookup, actual: Actual | None = None) -> PlanDetail:
    """계획 + 계산한 일정. actual 이 있으면 그날을 현재 기준으로 다시 계산한다 (project)."""
    ids = {i.place_id for i in plan.items}
    ids |= {c.params["place_id"] for c in plan.constraints if "place_id" in c.params}
    infos: dict[str, PlaceInfo | None] = {}
    for pid in ids:
        try:
            infos[pid] = places.get(pid)
        except PlacesUnavailable:
            infos[pid] = None
    # 이동시간은 필요한 구간을 모아 수단별로 routing 에 한 번씩만 묻는다 (실패하면 구간별 직선거리 근사).
    # 구간 수단은 항목마다 다를 수 있고, 비교용으로 도보·차량을 모두 계산한다
    pairs = travel_pairs(plan, infos)
    table: dict = {}
    if pairs:
        lookup = get_travel_lookup()
        for mode in ("walk", "car"):
            for (a, b), leg in zip(pairs, lookup.legs(pairs, mode)):
                table[(a.id, b.id, mode)] = leg
    days, violations = build_schedule(plan, infos, table, actual)
    fields = PlanSummary.model_fields.keys() - {"item_count"}
    return PlanDetail(
        **{k: getattr(plan, k) for k in fields},
        item_count=len(plan.items),
        days=days,
        constraints=[ConstraintView.model_validate(c) for c in plan.constraints],
        violations=violations,
    )


def simulate(plan: PlannerPlan, ops: list[PlanOp], places: PlaceLookup, with_index: bool = False,
             now: datetime | None = None, started: bool = False) -> PlanDetail:
    """ops 를 사본에 적용한 결과 (DB 변경 없음). 적용할 수 없으면 PlannerError.
    now 를 주면 그날 진행 기록이 있을 때(started 면 기록이 없어도) 실제 진행 기준으로 계산한다 (live_actual)."""
    copy = clone(plan)
    Applier(copy, places, persist=False).apply_all(ops, with_index=with_index)
    return detail(copy, places, live_actual(copy, places, now, started))


def preview(plan: PlannerPlan, ops: list[PlanOp], places: PlaceLookup, with_index: bool = True,
            now: datetime | None = None, started: bool = False) -> tuple[PlanDetail, PlanDetail, list[Change]]:
    """(현재, 적용 후, 변경 요약). 적용할 수 없으면 PlannerError (op_index 포함).
    now 를 주면 여행 중인 날은 둘 다 실제 진행 + now 기준이다 (변경 요약의 종료 시각도 현실 기준). started 는 live_actual."""
    after = simulate(plan, ops, places, with_index, now, started)
    before = detail(plan, places, live_actual(plan, places, now, started))
    return before, after, diff(before, after)


def live_actual(plan: PlannerPlan, places: PlaceLookup, now: datetime | None, started: bool = False) -> Actual | None:
    """now 가 계획 기간 안이고 그날 진행 기록(visiting·done)이 있으면 그날을 실제 진행 기준으로 다시 계산할 기준점
    (projection 과 같은 기준, 위치는 저장된 진행 상태). 아니면 None (계획 기준 그대로).
    started: 호출 쪽이 '오늘 여행을 시작했다'고 알 때 (Agent 동행 모드, 요청의 live). 그러면 진행 기록이 없어도
    projection 과 같이 지금 기준이다 (남은 첫 항목 지금 출발). planner 는 trip 을 모르므로 기본은 기록이 있을 때만 (§8.4)."""
    if now is None:
        return None
    if now.tzinfo is not None:
        now = now.astimezone(KST)
    day = (now.date() - plan.start_date).days + 1
    if not 1 <= day <= plan.num_days:
        return None
    if not started and not any(i.day == day and i.status in ("visiting", "done") for i in plan.items):
        return None
    return _anchor(plan, places, day, now.hour * 60 + now.minute, None)


def shown(plan: PlannerPlan, places: PlaceLookup, now: datetime | None) -> PlanDetail:
    """응답용 계획 (GET·편집·진행 기록 응답). 시각은 계획 기준 그대로, 위반(violations)은 여행 중인 날(live_actual)이면
    지금 기준(projection·preview·Agent 검토와 같은 기준)이다. 다녀온 항목의 계획 기준 시각 위반은 사라지고,
    지금 기준으로 생기는 위반(늦어서 하루 초과 등)이 보인다. 다른 날은 계획 기준과 같다 (§8.5)."""
    out = detail(plan, places)
    actual = live_actual(plan, places, now)
    if actual is not None:
        out.violations = detail(plan, places, actual).violations
    return out


def progress_changed(before: PlanDetail, after: PlanDetail, current: PlannerPlan) -> list[dict]:
    """제안 이후 다녀온(done) 항목 중 제안이 바꾸거나 지우는 것, 제안 이후 도착한(visiting) 항목 중 제안이 지우거나
    옮기는 것. 승인하면 지난 일·지금 머무는 곳을 바꾸게 된다 (409 progress_changed).
    머무는 곳의 체류·메모 변경은 막지 않는다 (머무는 동안에도 바꿀 수 있는 것)."""
    was = {i.id: i.status for d in before.days for i in d.items}
    done_now = {i.id: i for i in current.items if i.status == "done" and was.get(i.id) != "done"}
    here_now = {i.id: i for i in current.items if i.status == "visiting" and was.get(i.id) not in ("visiting", "done")}
    changes = diff(before, after)
    touched = {c.item_id for c in changes if c.kind in ("updated", "moved", "removed")}
    displaced = {c.item_id for c in changes if c.kind in ("moved", "removed")}
    hit = {**{i: here_now[i] for i in displaced & here_now.keys()}, **{i: done_now[i] for i in touched & done_now.keys()}}
    titles = {i.id: _title(i) for d in before.days for i in d.items}
    return [{"item_id": i, "title": titles.get(i, hit[i].place_id),
             "done_at": hit[i].done_at.strftime("%H:%M") if hit[i].done_at else None}
            for i in sorted(hit)]


# ---------- 변경 요약 ----------

def _title(item) -> str:
    return item.place.title if item.place else item.place_id


def _moved_ids(before: PlanDetail, after: PlanDetail) -> set[int]:
    """실제로 옮긴 항목. 다른 날로 간 항목 + 같은 날에 남은 항목 중 상대 순서가 바뀐 것.
    다른 항목이 추가·삭제돼 번호만 밀린 항목은 넣지 않는다. 같은 날 남은 항목의 예전 순서열에서
    가장 긴 증가 부분수열(LIS)에 든 항목은 제자리, 나머지가 옮긴 항목이다."""
    old_pos = {i.id: (d.day, n) for d in before.days for n, i in enumerate(d.items)}
    moved: set[int] = set()
    for d in after.days:
        stay = [i.id for i in d.items if i.id in old_pos and old_pos[i.id][0] == d.day]
        moved |= {i.id for i in d.items if i.id in old_pos and old_pos[i.id][0] != d.day}
        seq = [old_pos[i][1] for i in stay]
        # O(n^2) LIS (하루 항목 수는 수십 개 이하)
        best = [1] * len(seq)
        prev = [-1] * len(seq)
        for a in range(len(seq)):
            for b in range(a):
                if seq[b] < seq[a] and best[b] + 1 > best[a]:
                    best[a], prev[a] = best[b] + 1, b
        keep: set[int] = set()
        k = max(range(len(seq)), key=lambda x: best[x]) if seq else -1
        while k != -1:
            keep.add(stay[k])
            k = prev[k]
        moved |= set(stay) - keep
    return moved


def diff(before: PlanDetail, after: PlanDetail) -> list[Change]:
    out: list[Change] = []
    labels = {"title": "제목", "start_date": "시작일", "end_date": "종료일", "day_start": "하루 시작", "day_end": "하루 종료",
              "travel_mode": "이동 수단", "memo": "메모"}
    for key, label in labels.items():
        a, b = getattr(before, key), getattr(after, key)
        if a != b:
            fmt = (lambda t: t.strftime("%H:%M")) if key in ("day_start", "day_end") else str
            out.append(Change(kind="plan", message=f"{label}: {fmt(a)} → {fmt(b)}"))

    old = {i.id: i for d in before.days for i in d.items}
    new = {i.id: i for d in after.days for i in d.items}
    moved = _moved_ids(before, after)
    for i in (i for d in after.days for i in d.items):
        if i.id not in old:
            at = f" {i.start_time.strftime('%H:%M')} 고정" if i.start_time else ""
            out.append(Change(kind="added", item_id=i.id, day=i.day,
                              message=f"추가: {i.day}일차 {i.order}번째 '{_title(i)}' ({i.stay_minutes}분{at})"))
            continue
        o = old[i.id]
        if i.id in moved:
            out.append(Change(kind="moved", item_id=i.id, day=i.day,
                              message=f"이동: '{_title(i)}' {o.day}일차 {o.order}번째 → {i.day}일차 {i.order}번째"))
        parts = []
        if o.stay_minutes != i.stay_minutes:
            parts.append(f"체류 {o.stay_minutes}분 → {i.stay_minutes}분")
        if o.start_time != i.start_time:
            parts.append(f"시작 고정 {o.start_time.strftime('%H:%M') if o.start_time else '없음'} → "
                         f"{i.start_time.strftime('%H:%M') if i.start_time else '없음'}")
        if o.status != i.status and not (o.status in PROGRESS and i.status in PROGRESS):  # 진행 상태는 일정 변경이 아님
            parts.append(f"상태 {o.status} → {i.status}")
        if o.travel_mode != i.travel_mode:
            mode = {None: "계획 기본", "walk": "도보", "car": "차량"}
            parts.append(f"오는 수단 {mode[o.travel_mode]} → {mode[i.travel_mode]}")
        if o.memo != i.memo:
            parts.append(f"메모 '{o.memo}' → '{i.memo}'")
        if parts:
            out.append(Change(kind="updated", item_id=i.id, day=i.day, message=f"변경: '{_title(i)}' " + ", ".join(parts)))
    for o in (o for d in before.days for o in d.items):
        if o.id not in new:
            out.append(Change(kind="removed", item_id=o.id, day=o.day, message=f"삭제: {o.day}일차 '{_title(o)}'"))

    old_rules = {c.id: c for c in before.constraints}
    new_rules = {c.id: c for c in after.constraints}
    for c in after.constraints:
        if c.id not in old_rules:
            out.append(Change(kind="rule_added", message=f"규칙 추가: {c.type} {c.params}" + (f" ({c.note})" if c.note else "")))
    for c in before.constraints:
        if c.id not in new_rules:
            out.append(Change(kind="rule_removed", message=f"규칙 삭제: {c.type} {c.params}"))

    if out:  # 일정이 바뀌었으면 날짜별 종료 시각 변화도 알려준다
        ends = {d.day: d.end_at for d in before.days}
        for d in after.days:
            if d.day in ends and ends[d.day] != d.end_at:
                out.append(Change(kind="day_end", day=d.day,
                                  message=f"{d.day}일차 종료: {ends[d.day] or '없음'} → {d.end_at or '없음'}"))
    return out


# ---------- 현재 기준 재계산 (여행 중) ----------

POSITION_ID = "@position"  # 이동 중 현재 위치를 이동시간 조회에 넣을 때 쓰는 장소 id


def _place(places: PlaceLookup, place_id: str) -> PlaceInfo | None:
    try:
        return places.get(place_id)
    except PlacesUnavailable:
        return None


def _anchor(plan: PlannerPlan, places: PlaceLookup, day: int, now: int,
            position: PositionVisiting | PositionMoving | None) -> Actual:
    """지금 위치(없으면 저장된 진행 상태)로 그날 재계산의 기준점을 정한다."""
    items = sorted((i for i in plan.items if i.day == day and i.status != "skipped"), key=lambda i: i.position)
    if isinstance(position, PositionVisiting):
        item = next((i for i in plan.items if i.id == position.item_id), None)
        if item is None:
            raise PlannerError(404, "item_not_found", f"일정 항목 {position.item_id} 이 없습니다.")
        if item not in items:
            raise PlannerError(422, "invalid_position", f"항목 {item.id} 은 {day}일차의 (건너뛰지 않은) 항목이 아닙니다.",
                               item_id=item.id)
        at = position.arrived_at or item.arrived_at
        return Actual(day, now, item.id, to_min(at) if at else now)
    if isinstance(position, PositionMoving):
        # 향하는 항목: 마지막으로 다녀온(머문) 항목 뒤의 첫 planned 항목
        last = max((n for n, i in enumerate(items) if i.status in ("done", "visiting")), default=-1)
        target = next((i for i in items[last + 1:] if i.status == "planned"), None)
        if target is None:
            return Actual(day, now)
        dest = _place(places, target.place_id)
        minutes = 0
        if dest is not None:
            here = PlaceInfo(id=POSITION_ID, title="현재 위치", lat=position.lat, lng=position.lng)
            minutes = get_travel_lookup().legs([(here, dest)], leg_mode(plan, target))[0].minutes
        return Actual(day, now, target.id, now + minutes, moving=True)
    here = [i for i in items if i.status == "visiting"]
    if here:  # 저장된 진행 상태로: 머무는 항목 (여럿이면 일정상 마지막)
        return Actual(day, now, here[-1].id, to_min(here[-1].arrived_at) if here[-1].arrived_at else now)
    return Actual(day, now)


def project(plan: PlannerPlan, places: PlaceLookup, day: int, now: time,
            position: PositionVisiting | PositionMoving | None) -> ProjectionResult:
    """그날을 지금 기준으로 다시 계산해 기준 일정과 비교한다 (저장 안 함). docs/planner/README.md §8."""
    check_day(plan, day)
    baseline = detail(plan, places)
    actual = _anchor(plan, places, day, to_min(now), position)
    projected = detail(plan, places, actual)
    next_id = validation.next_item_id(plan, actual)
    nxt = None
    if next_id is not None:
        b = next(i for i in baseline.days[day - 1].items if i.id == next_id)
        p = next(i for i in projected.days[day - 1].items if i.id == next_id)
        nxt = ProjectionNext(item_id=next_id, planned_start=b.start_at, eta=p.arrive_at, start_at=p.start_at)
    return ProjectionResult(
        day=day, now=now.strftime("%H:%M"), plan=projected, baseline_violations=baseline.violations,
        new_violations=validation.new_violations(baseline.violations, projected.violations),
        delay_minutes=validation.delay_minutes(baseline.days, projected.days, day, next_id), next=nxt)
