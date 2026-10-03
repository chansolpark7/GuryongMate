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
