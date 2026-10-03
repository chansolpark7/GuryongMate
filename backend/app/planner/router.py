"""Planner API. 명세: docs/planner/README.md

- 직접 편집(사용자): items / constraints / PATCH / ops -> 즉시 반영
- 변경 제안(LLM·이벤트): proposals -> 사용자가 accept 해야 반영 (auto / overwrite)
- 장소 추천: recommendations -> 후보마다 add / remove 로 즉시 반영
일정을 바꾸는 요청은 모두 바뀐 뒤의 전체 계획(PlanDetail)을 돌려주고, 계획의 version 을 1 올린다.
오류는 {"detail": {"code", "message", ...}} 형식이다 (errors.py).
"""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, Query, Response
from pydantic import TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.planner import ops as engine
from app.planner import recommend
from app.planner.errors import PlannerError, PlannerRoute, not_found
from app.planner.models import (
    PlannerItem,
    PlannerPlan,
    PlannerProposal,
    PlannerRecommendation,
    PlannerRecommendationCandidate,
)
from app.planner.places import PlaceLookup, get_place_lookup
from app.planner.schemas import (
    AcceptRequest,
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
    ProposalSummary,
    RecommendationActionResult,
    RecommendationCreate,
    RecommendationView,
    RemoveConstraintOp,
    RemoveItemOp,
    UpdateItemOp,
    UpdatePlanOp,
    ValidationResult,
)

router = APIRouter(prefix="/planner", tags=["planner"], route_class=PlannerRoute)

DbSession = Annotated[Session, Depends(get_db)]
Places = Annotated[PlaceLookup, Depends(get_place_lookup)]
# 로그인(account) 서비스가 생기기 전까지는 헤더로 소유자만 구분한다.
UserId = Annotated[str, Header(alias="X-User-Id", min_length=1, max_length=64, description="사용자 ID")]

_ops_adapter = TypeAdapter(list[PlanOp])


# ---------- 공통 ----------

def _get_plan(db: Session, user_id: str, plan_id: int) -> PlannerPlan:
    plan = db.get(PlannerPlan, plan_id)
    if plan is None or plan.user_id != user_id:
        raise not_found("plan_not_found", f"계획 {plan_id}")
    return plan


def _apply(db: Session, plan: PlannerPlan, ops: list, places: PlaceLookup, with_index: bool = False) -> PlanDetail:
    """ops 를 실제 계획에 적용하고 commit. 하나라도 실패하면 아무것도 반영하지 않는다."""
    try:
        engine.Applier(plan, places, persist=True).apply_all(ops, with_index=with_index)
    except PlannerError:
        db.rollback()
        raise
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
    data = body.model_dump()
    data["end_date"] = data["end_date"] or data["start_date"]
    engine.check_range(data["start_date"], data["end_date"], data["day_start"], data["day_end"])
    plan = PlannerPlan(user_id=user_id, version=1, **data)
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
    db.delete(_get_plan(db, user_id, plan_id))  # 제안·추천은 DB 의 ON DELETE CASCADE 로 함께 삭제
    db.commit()
    return Response(status_code=204)


@router.get("/plans/{plan_id}/validate", response_model=ValidationResult)
def validate_plan(plan_id: int, db: DbSession, user_id: UserId, places: Places) -> ValidationResult:
    violations = engine.detail(_get_plan(db, user_id, plan_id), places).violations
    return ValidationResult(ok=not any(v.severity == "error" for v in violations), violations=violations)


# ---------- 직접 편집 ----------

@router.post("/plans/{plan_id}/items", response_model=PlanDetail, status_code=201)
def add_item(plan_id: int, body: ItemCreate, db: DbSession, user_id: UserId, places: Places) -> PlanDetail:
    op = AddItemOp(op="add_item", **body.model_dump())
    return _apply(db, _get_plan(db, user_id, plan_id), [op], places)


@router.patch("/plans/{plan_id}/items/{item_id}", response_model=PlanDetail)
def update_item(plan_id: int, item_id: int, body: ItemUpdate, db: DbSession, user_id: UserId,
                places: Places) -> PlanDetail:
    op = UpdateItemOp(op="update_item", item_id=item_id, **body.model_dump(exclude_unset=True))
    return _apply(db, _get_plan(db, user_id, plan_id), [op], places)


@router.delete("/plans/{plan_id}/items/{item_id}", response_model=PlanDetail)
def delete_item(plan_id: int, item_id: int, db: DbSession, user_id: UserId, places: Places) -> PlanDetail:
    op = RemoveItemOp(op="remove_item", item_id=item_id)
    return _apply(db, _get_plan(db, user_id, plan_id), [op], places)


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


@router.post("/plans/{plan_id}/ops", response_model=PlanDetail)
def apply_ops(plan_id: int, body: OpsRequest, db: DbSession, user_id: UserId, places: Places) -> PlanDetail:
    """여러 변경을 원자적으로 즉시 적용 (사용자 직접 편집용)."""
    return _apply(db, _get_plan(db, user_id, plan_id), body.ops, places, with_index=True)


@router.post("/plans/{plan_id}/preview", response_model=PreviewResult)
def preview_ops(plan_id: int, body: OpsRequest, db: DbSession, user_id: UserId, places: Places) -> PreviewResult:
    """ops 를 적용하면 어떻게 되는지 계산만 한다 (저장 안 함)."""
    _, after, changes = engine.preview(_get_plan(db, user_id, plan_id), body.ops, places)
    return PreviewResult(plan=after, changes=changes)


# ---------- 변경 제안 ----------

def _get_proposal(db: Session, plan: PlannerPlan, proposal_id: int) -> PlannerProposal:
    prop = db.get(PlannerProposal, proposal_id)
    if prop is None or prop.plan_id != plan.id:
        raise not_found("proposal_not_found", f"계획 {plan.id} 의 제안 {proposal_id}")
    return prop


def _proposal_detail(prop: PlannerProposal, plan: PlannerPlan) -> ProposalDetail:
    summary = ProposalSummary.model_validate(prop)
    return ProposalDetail(
        **{k: getattr(summary, k) for k in ProposalSummary.model_fields},
        request=prop.request,
        ops=_ops_adapter.validate_python(prop.ops),  # 저장된(보낸 필드만) 그대로 -> 직렬화도 보낸 필드만
        before=PlanDetail.model_validate(prop.before),
        after=PlanDetail.model_validate(prop.after),
        current_version=plan.version,
        base_changed=plan.version != prop.base_version,
    )


def _require_pending(prop: PlannerProposal) -> None:
    if prop.status != "pending":
        raise PlannerError(409, "proposal_not_pending", f"제안 {prop.id} 은 이미 {prop.status} 상태입니다.",
                           status=prop.status)


def _decide(prop: PlannerProposal, status: str, decision: str | None = None) -> None:
    prop.status = status
    prop.decision = decision
    prop.decided_at = datetime.now(timezone.utc)


@router.post("/plans/{plan_id}/proposals", response_model=ProposalDetail, status_code=201)
def create_proposal(plan_id: int, body: ProposalCreate, db: DbSession, user_id: UserId,
                    places: Places) -> ProposalDetail:
    plan = _get_plan(db, user_id, plan_id)
    if body.expected_version is not None and body.expected_version != plan.version:
        raise PlannerError(409, "version_conflict", "그사이 일정이 바뀌었습니다.",
                           expected_version=body.expected_version, current_version=plan.version)
    before, after, changes = engine.preview(plan, body.ops, places)
    if body.supersedes is not None:
        old = _get_proposal(db, plan, body.supersedes)
        if old.status != "pending":
            # 대체하려던 제안이 그사이 승인·거절됐다. 그 내용을 이어받은 새 제안을 만들면
            # 이미 반영된 변경이 두 번 들어가거나 거절한 변경이 되살아난다.
            raise PlannerError(409, "supersede_conflict", f"대체하려던 제안 {old.id} 이 이미 {old.status} 상태입니다.",
                               status=old.status)
        _decide(old, "superseded")
    prop = PlannerProposal(
        plan_id=plan.id, ops=_ops_adapter.dump_python(body.ops, mode="json", exclude_unset=True),
        summary=body.summary, request=body.request, source=body.source, origin=body.origin, status="pending",
        base_version=plan.version, before=before.model_dump(mode="json"), after=after.model_dump(mode="json"),
        changes=[c.model_dump(mode="json") for c in changes])
    db.add(prop)
    db.commit()
    return _proposal_detail(prop, plan)


@router.get("/plans/{plan_id}/proposals", response_model=list[ProposalSummary])
def list_proposals(plan_id: int, db: DbSession, user_id: UserId,
                   status: Annotated[ProposalStatus | None, Query()] = None) -> list[ProposalSummary]:
    plan = _get_plan(db, user_id, plan_id)
    q = select(PlannerProposal).where(PlannerProposal.plan_id == plan.id)
    if status:
        q = q.where(PlannerProposal.status == status)
    return [ProposalSummary.model_validate(p) for p in db.scalars(q.order_by(PlannerProposal.id.desc()))]


@router.get("/plans/{plan_id}/proposals/{proposal_id}", response_model=ProposalDetail)
def get_proposal(plan_id: int, proposal_id: int, db: DbSession, user_id: UserId) -> ProposalDetail:
    plan = _get_plan(db, user_id, plan_id)
    return _proposal_detail(_get_proposal(db, plan, proposal_id), plan)


@router.post("/plans/{plan_id}/proposals/{proposal_id}/accept", response_model=AcceptResult)
def accept_proposal(plan_id: int, proposal_id: int, db: DbSession, user_id: UserId, places: Places,
                    body: Annotated[AcceptRequest | None, Body()] = None) -> AcceptResult:
    mode = (body or AcceptRequest()).mode
    plan = _get_plan(db, user_id, plan_id)
    prop = _get_proposal(db, plan, proposal_id)
    _require_pending(prop)
    after = PlanDetail.model_validate(prop.after)

    if plan.version == prop.base_version and mode == "auto":
        try:
            engine.Applier(plan, places, persist=True).apply_all(_ops_adapter.validate_python(prop.ops))
        except PlannerError:
            db.rollback()
            raise
        decision = "applied"
    elif mode == "auto":
        current = engine.detail(plan, places)
        raise PlannerError(
            409, "base_changed", "이 제안 이후 일정이 바뀌었습니다. 카드의 일정 그대로 반영하거나 다시 제안받으세요.",
            base_version=prop.base_version, current_version=plan.version,
            since_proposal=[c.model_dump(mode="json") for c in engine.diff(PlanDetail.model_validate(prop.before), current)],
            overwrite_effect=[c.model_dump(mode="json") for c in engine.diff(current, after)])
    else:
        engine.overwrite(plan, after)
        decision = "overwritten"
    _decide(prop, "accepted", decision)
    db.commit()
    return AcceptResult(proposal=_proposal_detail(prop, plan), plan=engine.detail(plan, places))


@router.post("/plans/{plan_id}/proposals/{proposal_id}/reject", response_model=ProposalDetail)
def reject_proposal(plan_id: int, proposal_id: int, db: DbSession, user_id: UserId) -> ProposalDetail:
    plan = _get_plan(db, user_id, plan_id)
    prop = _get_proposal(db, plan, proposal_id)
    _require_pending(prop)
    _decide(prop, "rejected")
    db.commit()
    return _proposal_detail(prop, plan)


# ---------- 장소 추천 ----------

def _get_recommendation(db: Session, plan: PlannerPlan, rec_id: int) -> PlannerRecommendation:
    rec = db.get(PlannerRecommendation, rec_id)
    if rec is None or rec.plan_id != plan.id:
        raise not_found("recommendation_not_found", f"계획 {plan.id} 의 추천 {rec_id}")
    return rec


def _get_candidate(rec: PlannerRecommendation, cand_id: int) -> PlannerRecommendationCandidate:
    cand = next((c for c in rec.candidates if c.id == cand_id), None)
    if cand is None:
        raise not_found("candidate_not_found", f"추천 {rec.id} 의 후보 {cand_id}")
    return cand


@router.post("/plans/{plan_id}/recommendations", response_model=RecommendationView, status_code=201)
def create_recommendation(plan_id: int, body: RecommendationCreate, db: DbSession, user_id: UserId,
                          places: Places) -> RecommendationView:
    plan = _get_plan(db, user_id, plan_id)
    seen: set[tuple[str, int]] = set()
    for c in body.candidates:
        if (c.place_id, c.day) in seen:
            raise PlannerError(422, "duplicate_candidate", f"같은 장소·날짜 후보가 두 번 있습니다: {c.place_id} {c.day}일차",
                               place_id=c.place_id, day=c.day)
        seen.add((c.place_id, c.day))
        engine.check_day(plan, c.day)
        engine.require_place(places, c.place_id)
    rec = PlannerRecommendation(plan_id=plan.id, title=body.title, source=body.source, origin=body.origin)
    rec.candidates = [PlannerRecommendationCandidate(position=n, **c.model_dump())
                      for n, c in enumerate(body.candidates, start=1)]
    db.add(rec)
    db.commit()
    return recommend.recommendation_view(engine.detail(plan, places), rec, places)


@router.get("/plans/{plan_id}/recommendations", response_model=list[RecommendationView])
def list_recommendations(plan_id: int, db: DbSession, user_id: UserId, places: Places) -> list[RecommendationView]:
    plan = _get_plan(db, user_id, plan_id)
    detail = engine.detail(plan, places)
    recs = db.scalars(select(PlannerRecommendation).where(PlannerRecommendation.plan_id == plan.id)
                      .order_by(PlannerRecommendation.id.desc()))
    return [recommend.recommendation_view(detail, r, places) for r in recs]


@router.get("/plans/{plan_id}/recommendations/{recommendation_id}", response_model=RecommendationView)
def get_recommendation(plan_id: int, recommendation_id: int, db: DbSession, user_id: UserId,
                       places: Places) -> RecommendationView:
    plan = _get_plan(db, user_id, plan_id)
    rec = _get_recommendation(db, plan, recommendation_id)
    return recommend.recommendation_view(engine.detail(plan, places), rec, places)


@router.post("/plans/{plan_id}/recommendations/{recommendation_id}/candidates/{candidate_id}/add",
             response_model=RecommendationActionResult)
def add_candidate(plan_id: int, recommendation_id: int, candidate_id: int, db: DbSession, user_id: UserId,
                  places: Places) -> RecommendationActionResult:
    plan = _get_plan(db, user_id, plan_id)
    rec = _get_recommendation(db, plan, recommendation_id)
    cand = _get_candidate(rec, candidate_id)
    engine.check_day(plan, cand.day)
    detail = engine.detail(plan, places)
    matched = recommend.match_item(detail, cand)
    if matched:
        raise PlannerError(409, "already_in_plan", f"{cand.day}일차 일정에 이미 있습니다.", item_id=matched.id)
    order = recommend.best_order(plan, detail, cand, places)
    detail = _apply(db, plan, [recommend.add_op(cand, order)], places)
    return RecommendationActionResult(recommendation=recommend.recommendation_view(detail, rec, places), plan=detail)


@router.post("/plans/{plan_id}/recommendations/{recommendation_id}/candidates/{candidate_id}/remove",
             response_model=RecommendationActionResult)
def remove_candidate(plan_id: int, recommendation_id: int, candidate_id: int, db: DbSession, user_id: UserId,
                     places: Places) -> RecommendationActionResult:
    plan = _get_plan(db, user_id, plan_id)
    rec = _get_recommendation(db, plan, recommendation_id)
    cand = _get_candidate(rec, candidate_id)
    matched = recommend.match_item(engine.detail(plan, places), cand)
    if matched is None:
        raise PlannerError(409, "not_in_plan", f"{cand.day}일차 일정에 이 장소가 없습니다.")
    detail = _apply(db, plan, [RemoveItemOp(op="remove_item", item_id=matched.id)], places)
    return RecommendationActionResult(recommendation=recommend.recommendation_view(detail, rec, places), plan=detail)
