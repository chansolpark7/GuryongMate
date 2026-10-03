"""변경 연산(ops) 엔진. 직접 편집·미리보기·제안 승인이 모두 이 코드로 계획을 바꾼다.

- persist=True : DB 에 붙은 계획을 직접 수정하고 version 을 1 올린다 (호출 쪽에서 commit)
- preview(...)  : 계획을 메모리에 복제해 적용한 결과만 계산 (DB 변경 없음)
- overwrite(...): 제안의 after 스냅샷으로 계획을 통째로 맞춘다 (항목·규칙 id 는 가능한 한 유지)

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
    RemoveConstraintOp,
    RemoveItemOp,
    UpdateItemOp,
    UpdatePlanOp,
)
from app.planner.validation import build_schedule


PLAN_FIELDS = ("title", "start_date", "end_date", "day_start", "day_end", "travel_mode", "memo")


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
                                   memo=op.memo, status="planned")
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
                if op.status is not None:
                    item.status = op.status
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
    """계획을 after 스냅샷과 같게 맞춘다. 남아 있는 항목·규칙은 id 를 유지하고, 없어진 것은 새로 만든다."""
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
            else:
                keep.add(v.id)
            item.place_id, item.day, item.position = v.place_id, v.day, v.order
            item.stay_minutes, item.start_time, item.memo, item.status = v.stay_minutes, v.start_time, v.memo, v.status
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


def detail(plan: PlannerPlan, places: PlaceLookup) -> PlanDetail:
    ids = {i.place_id for i in plan.items}
    ids |= {c.params["place_id"] for c in plan.constraints if "place_id" in c.params}
    infos: dict[str, PlaceInfo | None] = {}
    for pid in ids:
        try:
            infos[pid] = places.get(pid)
        except PlacesUnavailable:
            infos[pid] = None
    days, violations = build_schedule(plan, infos)
    fields = PlanSummary.model_fields.keys() - {"item_count"}
    return PlanDetail(
        **{k: getattr(plan, k) for k in fields},
        item_count=len(plan.items),
        days=days,
        constraints=[ConstraintView.model_validate(c) for c in plan.constraints],
        violations=violations,
    )


def simulate(plan: PlannerPlan, ops: list[PlanOp], places: PlaceLookup, with_index: bool = False) -> PlanDetail:
    """ops 를 사본에 적용한 결과 (DB 변경 없음). 적용할 수 없으면 PlannerError."""
    copy = clone(plan)
    Applier(copy, places, persist=False).apply_all(ops, with_index=with_index)
    return detail(copy, places)


def preview(plan: PlannerPlan, ops: list[PlanOp], places: PlaceLookup,
            with_index: bool = True) -> tuple[PlanDetail, PlanDetail, list[Change]]:
    """(현재, 적용 후, 변경 요약). 적용할 수 없으면 PlannerError (op_index 포함)."""
    after = simulate(plan, ops, places, with_index)
    before = detail(plan, places)
    return before, after, diff(before, after)


# ---------- 변경 요약 ----------

def _title(item) -> str:
    return item.place.title if item.place else item.place_id


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
    for i in (i for d in after.days for i in d.items):
        if i.id not in old:
            at = f" {i.start_time.strftime('%H:%M')} 고정" if i.start_time else ""
            out.append(Change(kind="added", item_id=i.id, day=i.day,
                              message=f"추가: {i.day}일차 {i.order}번째 '{_title(i)}' ({i.stay_minutes}분{at})"))
            continue
        o = old[i.id]
        if (o.day, o.order) != (i.day, i.order):
            out.append(Change(kind="moved", item_id=i.id, day=i.day,
                              message=f"이동: '{_title(i)}' {o.day}일차 {o.order}번째 → {i.day}일차 {i.order}번째"))
        parts = []
        if o.stay_minutes != i.stay_minutes:
            parts.append(f"체류 {o.stay_minutes}분 → {i.stay_minutes}분")
        if o.start_time != i.start_time:
            parts.append(f"시작 고정 {o.start_time.strftime('%H:%M') if o.start_time else '없음'} → "
                         f"{i.start_time.strftime('%H:%M') if i.start_time else '없음'}")
        if o.status != i.status:
            parts.append(f"상태 {o.status} → {i.status}")
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
