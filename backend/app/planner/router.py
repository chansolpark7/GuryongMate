"""Planner API. 앱·CLI·LLM tool 이 모두 이 API 로만 일정을 조회·수정한다.

일정을 바꾸는 요청은 모두 바뀐 뒤의 전체 계획(PlanDetail: 계산된 타임라인 + 위반 목록)을 돌려준다.
"""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.planner.models import PlannerConstraint, PlannerItem, PlannerPlan
from app.planner.places import PlaceLookup, PlacesUnavailable, get_place_lookup
from app.planner.schemas import (
    ConstraintCreate,
    ConstraintView,
    ItemCreate,
    ItemUpdate,
    PlaceInfo,
    PlanCreate,
    PlanDetail,
    PlanSummary,
    PlanUpdate,
    ValidationResult,
)
from app.planner.validation import build_schedule

router = APIRouter(prefix="/planner", tags=["planner"])

DbSession = Annotated[Session, Depends(get_db)]
Places = Annotated[PlaceLookup, Depends(get_place_lookup)]
# 로그인 모듈이 생기기 전까지는 헤더로 소유자만 구분한다.
UserId = Annotated[str, Header(alias="X-User-Id", min_length=1, max_length=64, description="사용자 ID")]


# ---------- 공통 ----------

def _get_plan(db: Session, user_id: str, plan_id: int) -> PlannerPlan:
    plan = db.get(PlannerPlan, plan_id)
    if plan is None or plan.user_id != user_id:
        raise HTTPException(404, f"계획 {plan_id} 을 찾을 수 없습니다.")
    return plan


def _get_item(plan: PlannerPlan, item_id: int) -> PlannerItem:
    for it in plan.items:
        if it.id == item_id:
            return it
    raise HTTPException(404, f"계획 {plan.id} 에 일정 항목 {item_id} 이 없습니다.")


def _summary(plan: PlannerPlan) -> dict:
    fields = PlanSummary.model_fields.keys() - {"item_count"}
    return {k: getattr(plan, k) for k in fields} | {"item_count": len(plan.items)}


def _detail(plan: PlannerPlan, places: PlaceLookup) -> PlanDetail:
    ids = {i.place_id for i in plan.items}
    ids |= {c.params["place_id"] for c in plan.constraints if "place_id" in c.params}
    infos: dict[str, PlaceInfo | None] = {}
    for pid in ids:
        try:
            infos[pid] = places.get(pid)
        except PlacesUnavailable:
            infos[pid] = None
    days, violations = build_schedule(plan, infos)
    return PlanDetail(
        **_summary(plan),
        days=days,
        constraints=[ConstraintView.model_validate(c) for c in plan.constraints],
        violations=violations,
    )


def _touch(plan: PlannerPlan) -> None:
    plan.updated_at = datetime.now(timezone.utc)


def _check_day(plan: PlannerPlan, day: int) -> None:
    if day > plan.num_days:
        raise HTTPException(422, f"day 는 1~{plan.num_days} 사이여야 합니다 (여행 {plan.num_days}일).")


def _insert(plan: PlannerPlan, item: PlannerItem, day: int, order: int | None) -> None:
    """item 을 day 의 order 번째(1부터, None 이면 맨 뒤)에 넣고 그날 순서를 1..n 으로 다시 매긴다."""
    others = sorted((i for i in plan.items if i.day == day and i is not item), key=lambda i: i.position)
    idx = len(others) if order is None else min(order - 1, len(others))
    others.insert(idx, item)
    item.day = day
    for n, it in enumerate(others, start=1):
        it.position = n


def _renumber(plan: PlannerPlan, day: int) -> None:
    for n, it in enumerate(sorted((i for i in plan.items if i.day == day), key=lambda i: i.position), start=1):
        it.position = n


def _require_place(places: PlaceLookup, place_id: str) -> None:
    try:
        found = places.get(place_id)
    except PlacesUnavailable as e:
        raise HTTPException(503, f"장소 정보를 조회할 수 없습니다: {e}") from e
    if found is None:
        raise HTTPException(422, f"장소 '{place_id}' 가 없습니다. GET /places 로 검색한 id 를 쓰세요.")


# ---------- 계획 ----------

@router.get("/plans", response_model=list[PlanSummary])
def list_plans(db: DbSession, user_id: UserId) -> list[PlanSummary]:
    count = select(func.count()).where(PlannerItem.plan_id == PlannerPlan.id).scalar_subquery()
    rows = db.execute(
        select(PlannerPlan, count).where(PlannerPlan.user_id == user_id).order_by(PlannerPlan.start_date, PlannerPlan.id)
    ).all()
    return [PlanSummary.model_validate(p).model_copy(update={"item_count": n}) for p, n in rows]


@router.post("/plans", response_model=PlanDetail, status_code=201)
def create_plan(body: PlanCreate, db: DbSession, user_id: UserId, places: Places) -> PlanDetail:
    plan = PlannerPlan(user_id=user_id, **body.model_dump())
    db.add(plan)
    db.commit()
    return _detail(plan, places)


@router.get("/plans/{plan_id}", response_model=PlanDetail)
def get_plan(plan_id: int, db: DbSession, user_id: UserId, places: Places) -> PlanDetail:
    return _detail(_get_plan(db, user_id, plan_id), places)


@router.patch("/plans/{plan_id}", response_model=PlanDetail)
def update_plan(plan_id: int, body: PlanUpdate, db: DbSession, user_id: UserId, places: Places) -> PlanDetail:
    plan = _get_plan(db, user_id, plan_id)
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    merged = {k: changes.get(k, getattr(plan, k)) for k in ("start_date", "end_date", "day_start", "day_end")}
    try:
        PlanCreate(title=plan.title, **merged)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    num_days = (merged["end_date"] - merged["start_date"]).days + 1
    if any(i.day > num_days for i in plan.items):
        raise HTTPException(409, f"{num_days}일차 이후에 일정이 남아 있어 기간을 줄일 수 없습니다. 먼저 옮기거나 삭제하세요.")
    for k, v in changes.items():
        setattr(plan, k, v)
    _touch(plan)
    db.commit()
    return _detail(plan, places)


@router.delete("/plans/{plan_id}", status_code=204)
def delete_plan(plan_id: int, db: DbSession, user_id: UserId) -> Response:
    db.delete(_get_plan(db, user_id, plan_id))
    db.commit()
    return Response(status_code=204)


@router.get("/plans/{plan_id}/validate", response_model=ValidationResult)
def validate_plan(plan_id: int, db: DbSession, user_id: UserId, places: Places) -> ValidationResult:
    violations = _detail(_get_plan(db, user_id, plan_id), places).violations
    return ValidationResult(ok=not any(v.severity == "error" for v in violations), violations=violations)


# ---------- 일정 항목 ----------

@router.post("/plans/{plan_id}/items", response_model=PlanDetail, status_code=201)
def add_item(plan_id: int, body: ItemCreate, db: DbSession, user_id: UserId, places: Places) -> PlanDetail:
    plan = _get_plan(db, user_id, plan_id)
    _check_day(plan, body.day)
    _require_place(places, body.place_id)
    item = PlannerItem(place_id=body.place_id, stay_minutes=body.stay_minutes, start_time=body.start_time,
                       memo=body.memo, status="planned")
    plan.items.append(item)
    _insert(plan, item, body.day, body.order)
    _touch(plan)
    db.commit()
    return _detail(plan, places)


@router.patch("/plans/{plan_id}/items/{item_id}", response_model=PlanDetail)
def update_item(plan_id: int, item_id: int, body: ItemUpdate, db: DbSession, user_id: UserId, places: Places) -> PlanDetail:
    plan = _get_plan(db, user_id, plan_id)
    item = _get_item(plan, item_id)
    sent = body.model_fields_set

    if body.day is not None or body.order is not None:
        new_day = body.day or item.day
        _check_day(plan, new_day)
        old_day = item.day
        _insert(plan, item, new_day, body.order)
        if old_day != new_day:
            _renumber(plan, old_day)
    if body.stay_minutes is not None:
        item.stay_minutes = body.stay_minutes
    if "start_time" in sent:
        item.start_time = body.start_time
    if body.memo is not None:
        item.memo = body.memo
    if body.status is not None:
        item.status = body.status
    _touch(plan)
    db.commit()
    return _detail(plan, places)


@router.delete("/plans/{plan_id}/items/{item_id}", response_model=PlanDetail)
def delete_item(plan_id: int, item_id: int, db: DbSession, user_id: UserId, places: Places) -> PlanDetail:
    plan = _get_plan(db, user_id, plan_id)
    item = _get_item(plan, item_id)
    plan.items.remove(item)
    _renumber(plan, item.day)
    _touch(plan)
    db.commit()
    return _detail(plan, places)


# ---------- 규칙 ----------

@router.get("/plans/{plan_id}/constraints", response_model=list[ConstraintView])
def list_constraints(plan_id: int, db: DbSession, user_id: UserId) -> list[ConstraintView]:
    return [ConstraintView.model_validate(c) for c in _get_plan(db, user_id, plan_id).constraints]


@router.post("/plans/{plan_id}/constraints", response_model=PlanDetail, status_code=201)
def add_constraint(plan_id: int, body: ConstraintCreate, db: DbSession, user_id: UserId, places: Places) -> PlanDetail:
    plan = _get_plan(db, user_id, plan_id)
    if "place_id" in body.params:
        _require_place(places, body.params["place_id"])
    if "day" in body.params:
        _check_day(plan, body.params["day"])
    plan.constraints.append(PlannerConstraint(**body.model_dump()))
    _touch(plan)
    db.commit()
    return _detail(plan, places)


@router.delete("/plans/{plan_id}/constraints/{constraint_id}", response_model=PlanDetail)
def delete_constraint(plan_id: int, constraint_id: int, db: DbSession, user_id: UserId, places: Places) -> PlanDetail:
    plan = _get_plan(db, user_id, plan_id)
    target = next((c for c in plan.constraints if c.id == constraint_id), None)
    if target is None:
        raise HTTPException(404, f"계획 {plan_id} 에 규칙 {constraint_id} 이 없습니다.")
    plan.constraints.remove(target)
    _touch(plan)
    db.commit()
    return _detail(plan, places)

