"""Planner API. 앱·CLI·Agent 가 모두 이 API 로만 일정을 조회·수정한다.

- 직접 편집(사용자): items/constraints/PATCH 엔드포인트 -> 즉시 반영
- 제안(LLM·이벤트): proposals 로 ops 를 저장 -> 사용자가 accept 해야 반영
- 미리보기: preview 로 ops 적용 결과만 계산 (저장 안 함)

일정을 바꾸는 요청은 모두 바뀐 뒤의 전체 계획(PlanDetail: 계산된 타임라인 + 위반 목록)을 돌려준다.
"""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.planner import ops as engine
from app.planner.models import PlannerItem, PlannerPlan, PlannerProposal
from app.planner.places import PlaceLookup, get_place_lookup
from app.planner.schemas import (
    AcceptResult,
    AddConstraintOp,
    AddItemOp,
    ConstraintCreate,
    ConstraintView,
    ItemCreate,
    ItemUpdate,
    OpsRequest,
    PlanCreate,
    PlanDetail,
    PlanOp,
    PlanSummary,
    PlanUpdate,
    PreviewResult,
    ProposalCreate,
    ProposalDetail,
    ProposalStatus,
    ProposalView,
    RemoveConstraintOp,
    RemoveItemOp,
    UpdateItemOp,
    UpdatePlanOp,
    ValidationResult,
)

router = APIRouter(prefix="/planner", tags=["planner"])

DbSession = Annotated[Session, Depends(get_db)]
Places = Annotated[PlaceLookup, Depends(get_place_lookup)]
# 로그인 모듈이 생기기 전까지는 헤더로 소유자만 구분한다.
UserId = Annotated[str, Header(alias="X-User-Id", min_length=1, max_length=64, description="사용자 ID")]

_ops_adapter = TypeAdapter(list[PlanOp])


# ---------- 공통 ----------

def _get_plan(db: Session, user_id: str, plan_id: int) -> PlannerPlan:
    plan = db.get(PlannerPlan, plan_id)
    if plan is None or plan.user_id != user_id:
        raise HTTPException(404, f"계획 {plan_id} 을 찾을 수 없습니다.")
    return plan


def _apply(db: Session, plan: PlannerPlan, ops: list, places: PlaceLookup) -> PlanDetail:
    """ops 를 실제 계획에 적용하고 commit. 하나라도 실패하면 아무것도 반영하지 않는다."""
    try:
        engine.Applier(plan, places, persist=True).apply_all(ops)
    except engine.OpError as e:
        db.rollback()
        raise HTTPException(e.status, e.message) from e
    db.commit()
    return engine.detail(plan, places)


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
    return engine.detail(plan, places)


@router.get("/plans/{plan_id}", response_model=PlanDetail)
def get_plan(plan_id: int, db: DbSession, user_id: UserId, places: Places) -> PlanDetail:
    return engine.detail(_get_plan(db, user_id, plan_id), places)


@router.patch("/plans/{plan_id}", response_model=PlanDetail)
def update_plan(plan_id: int, body: PlanUpdate, db: DbSession, user_id: UserId, places: Places) -> PlanDetail:
    op = UpdatePlanOp(op="update_plan", **body.model_dump(exclude_unset=True))
    return _apply(db, _get_plan(db, user_id, plan_id), [op], places)


@router.delete("/plans/{plan_id}", status_code=204)
def delete_plan(plan_id: int, db: DbSession, user_id: UserId) -> Response:
    db.delete(_get_plan(db, user_id, plan_id))
    db.commit()
    return Response(status_code=204)


@router.get("/plans/{plan_id}/validate", response_model=ValidationResult)
def validate_plan(plan_id: int, db: DbSession, user_id: UserId, places: Places) -> ValidationResult:
    violations = engine.detail(_get_plan(db, user_id, plan_id), places).violations
    return ValidationResult(ok=not any(v.severity == "error" for v in violations), violations=violations)


# ---------- 일정 항목 (직접 편집) ----------

@router.post("/plans/{plan_id}/items", response_model=PlanDetail, status_code=201)
def add_item(plan_id: int, body: ItemCreate, db: DbSession, user_id: UserId, places: Places) -> PlanDetail:
    op = AddItemOp(op="add_item", **body.model_dump())
    return _apply(db, _get_plan(db, user_id, plan_id), [op], places)


@router.patch("/plans/{plan_id}/items/{item_id}", response_model=PlanDetail)
def update_item(plan_id: int, item_id: int, body: ItemUpdate, db: DbSession, user_id: UserId, places: Places) -> PlanDetail:
    op = UpdateItemOp(op="update_item", item_id=item_id, **body.model_dump(exclude_unset=True))
    return _apply(db, _get_plan(db, user_id, plan_id), [op], places)


@router.delete("/plans/{plan_id}/items/{item_id}", response_model=PlanDetail)
def delete_item(plan_id: int, item_id: int, db: DbSession, user_id: UserId, places: Places) -> PlanDetail:
    op = RemoveItemOp(op="remove_item", item_id=item_id)
    return _apply(db, _get_plan(db, user_id, plan_id), [op], places)


# ---------- 규칙 ----------

@router.get("/plans/{plan_id}/constraints", response_model=list[ConstraintView])
def list_constraints(plan_id: int, db: DbSession, user_id: UserId) -> list[ConstraintView]:
    return [ConstraintView.model_validate(c) for c in _get_plan(db, user_id, plan_id).constraints]


@router.post("/plans/{plan_id}/constraints", response_model=PlanDetail, status_code=201)
def add_constraint(plan_id: int, body: ConstraintCreate, db: DbSession, user_id: UserId, places: Places) -> PlanDetail:
    op = AddConstraintOp(op="add_constraint", **body.model_dump())
    return _apply(db, _get_plan(db, user_id, plan_id), [op], places)


@router.delete("/plans/{plan_id}/constraints/{constraint_id}", response_model=PlanDetail)
def delete_constraint(plan_id: int, constraint_id: int, db: DbSession, user_id: UserId, places: Places) -> PlanDetail:
    op = RemoveConstraintOp(op="remove_constraint", constraint_id=constraint_id)
    return _apply(db, _get_plan(db, user_id, plan_id), [op], places)


# ---------- 여러 연산 한 번에 ----------

@router.post("/plans/{plan_id}/ops", response_model=PlanDetail)
def apply_ops(plan_id: int, body: OpsRequest, db: DbSession, user_id: UserId, places: Places) -> PlanDetail:
    """여러 변경을 원자적으로 즉시 적용 (사용자 직접 편집용)."""
    return _apply(db, _get_plan(db, user_id, plan_id), body.ops, places)


@router.post("/plans/{plan_id}/preview", response_model=PreviewResult)
def preview_ops(plan_id: int, body: OpsRequest, db: DbSession, user_id: UserId, places: Places) -> PreviewResult:
    """ops 를 적용하면 어떻게 되는지 계산만 한다 (저장 안 함)."""
    plan = _get_plan(db, user_id, plan_id)
    try:
        _, after, changes = engine.preview(plan, body.ops, places)
    except engine.OpError as e:
        raise HTTPException(e.status, e.message) from e
    return PreviewResult(plan=after, changes=changes)


# ---------- 제안 ----------

def _get_proposal(db: Session, plan: PlannerPlan, proposal_id: int) -> PlannerProposal:
    prop = db.get(PlannerProposal, proposal_id)
    if prop is None or prop.plan_id != plan.id:
        raise HTTPException(404, f"계획 {plan.id} 에 제안 {proposal_id} 이 없습니다.")
    return prop


def _proposal_detail(prop: PlannerProposal, plan: PlannerPlan, places: PlaceLookup) -> ProposalDetail:
    view = ProposalView.model_validate(prop)
    base = {k: getattr(view, k) for k in ProposalView.model_fields}  # ops 는 모델 그대로 (보낸 필드 정보 유지)
    if prop.status != "pending":
        return ProposalDetail(**base, changes=[])
    try:
        before, after, changes = engine.preview(plan, view.ops, places)
    except engine.OpError as e:
        return ProposalDetail(**base, changes=[], before=engine.detail(plan, places), applicable=False,
                              error=f"현재 계획에 적용할 수 없습니다: {e.message}")
    return ProposalDetail(**base, changes=changes, before=before, after=after)


def _decide(prop: PlannerProposal, status: str) -> None:
    prop.status = status
    prop.decided_at = datetime.now(timezone.utc)


@router.post("/plans/{plan_id}/proposals", response_model=ProposalDetail, status_code=201)
def create_proposal(plan_id: int, body: ProposalCreate, db: DbSession, user_id: UserId, places: Places) -> ProposalDetail:
    plan = _get_plan(db, user_id, plan_id)
    try:
        engine.preview(plan, body.ops, places)
    except engine.OpError as e:
        raise HTTPException(e.status, e.message) from e
    if body.supersedes is not None:
        old = _get_proposal(db, plan, body.supersedes)
        if old.status != "pending":
            # 대체하려던 제안이 그사이 승인·거절됐다. 그 ops 를 이어받은 새 제안을 만들면
            # 이미 반영된 변경이 두 번 들어가거나 거절한 변경이 되살아난다.
            raise HTTPException(409, f"대체하려던 제안 {old.id} 이 이미 {old.status} 상태입니다.")
        _decide(old, "superseded")
    prop = PlannerProposal(plan_id=plan.id, ops=_ops_adapter.dump_python(body.ops, mode="json", exclude_unset=True),
                           summary=body.summary, source=body.source, origin=body.origin, status="pending")
    db.add(prop)
    db.commit()
    return _proposal_detail(prop, plan, places)


@router.get("/plans/{plan_id}/proposals", response_model=list[ProposalView])
def list_proposals(plan_id: int, db: DbSession, user_id: UserId,
                   status: Annotated[ProposalStatus | None, Query()] = None) -> list[ProposalView]:
    plan = _get_plan(db, user_id, plan_id)
    q = select(PlannerProposal).where(PlannerProposal.plan_id == plan.id)
    if status:
        q = q.where(PlannerProposal.status == status)
    return [ProposalView.model_validate(p) for p in db.scalars(q.order_by(PlannerProposal.id.desc()))]


@router.get("/plans/{plan_id}/proposals/{proposal_id}", response_model=ProposalDetail)
def get_proposal(plan_id: int, proposal_id: int, db: DbSession, user_id: UserId, places: Places) -> ProposalDetail:
    plan = _get_plan(db, user_id, plan_id)
    return _proposal_detail(_get_proposal(db, plan, proposal_id), plan, places)


@router.post("/plans/{plan_id}/proposals/{proposal_id}/accept", response_model=AcceptResult)
def accept_proposal(plan_id: int, proposal_id: int, db: DbSession, user_id: UserId, places: Places) -> AcceptResult:
    plan = _get_plan(db, user_id, plan_id)
    prop = _get_proposal(db, plan, proposal_id)
    if prop.status != "pending":
        raise HTTPException(409, f"제안 {proposal_id} 은 이미 {prop.status} 상태입니다.")
    ops = _ops_adapter.validate_python(prop.ops)
    try:
        engine.Applier(plan, places, persist=True).apply_all(ops)
    except engine.OpError as e:
        db.rollback()
        raise HTTPException(409, f"현재 계획에 적용할 수 없습니다: {e.message}") from e
    _decide(prop, "accepted")
    db.commit()
    return AcceptResult(proposal=ProposalView.model_validate(prop), plan=engine.detail(plan, places))


@router.post("/plans/{plan_id}/proposals/{proposal_id}/reject", response_model=ProposalView)
def reject_proposal(plan_id: int, proposal_id: int, db: DbSession, user_id: UserId) -> ProposalView:
    plan = _get_plan(db, user_id, plan_id)
    prop = _get_proposal(db, plan, proposal_id)
    if prop.status != "pending":
        raise HTTPException(409, f"제안 {proposal_id} 은 이미 {prop.status} 상태입니다.")
    _decide(prop, "rejected")
    db.commit()
    return ProposalView.model_validate(prop)
