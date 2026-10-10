"""trip 테이블 (docs/trip/README.md §7). 전체 이동 기록은 저장하지 않는다: 마지막 위치와 머무름 판정에 필요한 시각만 둔다."""
from datetime import date, datetime

from sqlalchemy import DDL, Date, DateTime, Float, ForeignKey, Integer, String, Text, event, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class TripDay(Base):
    """여행일 하루. 진행 중(ended_at 이 null)인 것이 있으면 그 계획은 동행 모드다.
    시각(started_at, recorded_at 등)은 trip 의 '지금'(safety 가상 시각을 따름) 기준이다."""

    __tablename__ = "trip_day"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(Integer, index=True)  # planner 계획 id (planner 테이블을 참조하지 않는다)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    day: Mapped[int] = mapped_column(Integer)  # n일차
    date: Mapped[date] = mapped_column(Date)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 선제 턴을 보낼 agent 세션
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_reason: Mapped[str | None] = mapped_column(String(8), nullable=True)  # user | auto

    # 마지막 위치
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str | None] = mapped_column(String(8), nullable=True)

    # 지금 상태: idle (시작 전·끝) | visiting (장소 안) | moving (떠난 뒤 이동 중, item_id = 떠난 항목)
    current_item_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_state: Mapped[str] = mapped_column(String(10), default="idle")
    current_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 머무름 판정: 반경 안에 처음 들어온 항목·시각 / 머무는 장소 반경 밖으로 처음 나간 시각
    cand_item_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cand_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outside_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # 마지막 재계산 (planner projection)
    delay_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # TripState.next

    # 앱 활동 (§4.1): 마지막 위치 전송·이벤트 조회 시각 / 마지막 위치 전송 시각 (trip 의 지금 기준, 위치는 recorded_at 과 큰 쪽)
    active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    located_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 이 여행일의 안전 상황 (§3.2 상황 해제): {"abnormal": bool, "n": 주의·위험 상황 번호, "zones": [...], "warnings": [...]}
    situation: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TripEvent(Base):
    """판정한 이벤트 (§3.3). cause_key 는 중복 억제용 원인 키."""

    __tablename__ = "trip_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trip_day_id: Mapped[int] = mapped_column(ForeignKey("trip_day.id", ondelete="CASCADE"), index=True)
    plan_id: Mapped[int] = mapped_column(Integer, index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    day: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(16))
    level: Mapped[int] = mapped_column(Integer)  # 개입 단계 0~3
    severity: Mapped[str] = mapped_column(String(8))  # info | warning | danger
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    now: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # 판정 시각 (trip 의 지금 / 위치의 recorded_at)
    item_ids: Mapped[list[int]] = mapped_column(JSONB, default=list)
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(10))  # queued | delivered | suppressed | handled | failed
    cause_key: Mapped[str] = mapped_column(Text)
    agent: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    card: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # trip 의 지금 기준
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)  # 단계 3 전송 시도 수
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


# 이미 만들어진 테이블에 새 컬럼 추가 (planner/models.py 와 같은 방식, Alembic 미사용). create_all 직후 매번, 있으면 건너뛴다
_UPGRADES = [
    "ALTER TABLE trip_day ADD COLUMN IF NOT EXISTS active_at TIMESTAMP WITH TIME ZONE",
    "ALTER TABLE trip_day ADD COLUMN IF NOT EXISTS located_at TIMESTAMP WITH TIME ZONE",
    "ALTER TABLE trip_day ADD COLUMN IF NOT EXISTS situation JSONB",
]
for _sql in _UPGRADES:
    event.listen(Base.metadata, "after_create", DDL(_sql).execute_if(dialect="postgresql"))
