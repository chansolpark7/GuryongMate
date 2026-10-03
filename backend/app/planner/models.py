from datetime import date, datetime, time

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, Time, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class PlannerPlan(Base):
    """여행 계획 1건. 일정의 단일 진실 원천(Source of Truth)."""

    __tablename__ = "planner_plan"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(200))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    day_start: Mapped[time] = mapped_column(Time)  # 매일 일정 시작 시각
    day_end: Mapped[time] = mapped_column(Time)  # 매일 일정 종료 목표 시각
    travel_mode: Mapped[str] = mapped_column(String(10), default="walk")  # walk | car
    memo: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)  # 일정이 바뀔 때마다 1 오른다 (제안의 기준 비교용)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    items: Mapped[list["PlannerItem"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", order_by="(PlannerItem.day, PlannerItem.position)"
    )
    constraints: Mapped[list["PlannerConstraint"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", order_by="PlannerConstraint.id"
    )

    @property
    def num_days(self) -> int:
        return (self.end_date - self.start_date).days + 1


class PlannerItem(Base):
    """일정 항목. 장소는 place_id(places 모듈 공개 ID)로만 참조한다."""

    __tablename__ = "planner_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("planner_plan.id", ondelete="CASCADE"), index=True)
    place_id: Mapped[str] = mapped_column(String(40))
    day: Mapped[int] = mapped_column(Integer)  # 1부터
    position: Mapped[int] = mapped_column(Integer)  # 그날 안에서의 순서, 1부터 연속
    stay_minutes: Mapped[int] = mapped_column(Integer, default=60)
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)  # 지정 시 이 시각에 고정
    memo: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(10), default="planned")  # planned|visiting|done|skipped

    plan: Mapped[PlannerPlan] = relationship(back_populates="items")


class PlannerConstraint(Base):
    """사용자 또는 LLM이 정한 규칙. 검증은 validation.py 의 결정론적 알고리즘이 한다."""

    __tablename__ = "planner_constraint"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("planner_plan.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(30))
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    hard: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(10), default="user")  # user | llm
    note: Mapped[str] = mapped_column(Text, default="")

    plan: Mapped[PlannerPlan] = relationship(back_populates="constraints")


class PlannerProposal(Base):
    """일정 변경 제안. 만들 때의 일정(before)과 승인 시 일정(after)을 기록으로 남긴다 (이후 바뀌지 않음)."""

    __tablename__ = "planner_proposal"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("planner_plan.id", ondelete="CASCADE"), index=True)
    ops: Mapped[list[dict]] = mapped_column(JSONB)  # 보낸 필드만 (exclude_unset)
    summary: Mapped[str] = mapped_column(Text, default="")
    request: Mapped[str] = mapped_column(Text, default="")  # 이 제안을 만든 사용자 요청 원문
    source: Mapped[str] = mapped_column(String(10), default="llm")  # llm | user | event
    origin: Mapped[str] = mapped_column(String(64), default="")  # 예: agent 세션 id
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending|accepted|rejected|superseded
    decision: Mapped[str | None] = mapped_column(String(12), nullable=True)  # applied | overwritten
    base_version: Mapped[int] = mapped_column(Integer)
    before: Mapped[dict] = mapped_column(JSONB)  # PlanDetail
    after: Mapped[dict] = mapped_column(JSONB)  # PlanDetail
    changes: Mapped[list[dict]] = mapped_column(JSONB)  # Change[]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PlannerRecommendation(Base):
    """장소 추천 카드. 후보의 상태(일정에 있는지)는 저장하지 않고 조회할 때 지금 일정에서 계산한다."""

    __tablename__ = "planner_recommendation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("planner_plan.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(100))
    source: Mapped[str] = mapped_column(String(10), default="llm")
    origin: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    candidates: Mapped[list["PlannerRecommendationCandidate"]] = relationship(
        cascade="all, delete-orphan", order_by="PlannerRecommendationCandidate.position"
    )


class PlannerRecommendationCandidate(Base):
    __tablename__ = "planner_recommendation_candidate"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recommendation_id: Mapped[int] = mapped_column(
        ForeignKey("planner_recommendation.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)  # 카드 안 순서
    place_id: Mapped[str] = mapped_column(String(40))
    day: Mapped[int] = mapped_column(Integer)
    time_hint: Mapped[time | None] = mapped_column(Time, nullable=True)
    stay_minutes: Mapped[int] = mapped_column(Integer, default=60)
    reason: Mapped[str] = mapped_column(Text, default="")
