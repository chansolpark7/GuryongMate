from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class DocentMemory(Base):
    """사용자 메모리 항목 1개 (§1). scope=user 는 계정 단위, scope=plan 은 그 여행(plan_id) 단위."""

    __tablename__ = "docent_memory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    scope: Mapped[str] = mapped_column(String(8))  # user | plan
    plan_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)  # planner 계획 id (scope=plan)
    category: Mapped[str] = mapped_column(String(16))  # interest | style | companion | food | avoid | known | feedback
    text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(100))  # 대화 메시지 id 또는 user_edit
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocentMemoryVersion(Base):
    """사용자 메모리가 바뀔 때마다 1 오르는 번호. 가이드 노트가 어느 메모리로 만들어졌는지 비교한다."""

    __tablename__ = "docent_memory_version"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=0)


class DocentGuideNote(Base):
    """계획의 장소 하나에 대한 가이드 노트 (§3.1). 본문은 agent 가 만들어 PUT 한다."""

    __tablename__ = "docent_guide_note"
    __table_args__ = (UniqueConstraint("plan_id", "place_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    plan_id: Mapped[int] = mapped_column(Integer, index=True)
    place_id: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(10))  # pending | preparing | ready | failed | stale
    content: Mapped[dict] = mapped_column(JSONB, default=dict)  # short·long·for_you·highlights·…·sources
    error: Mapped[str] = mapped_column(Text, default="")
    memory_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model: Mapped[str] = mapped_column(String(80), default="")
    prepared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocentStaleMark(Base):
    """노트를 다시 만들어야 하는 메모리 변경(interest·style·companion)이 마지막으로 있었던 memory_version (§3.2).
    scope=user 변경은 plan_id 0, scope=plan 변경은 그 계획. 준비 중에 메모리가 바뀌면, 바뀌기 전 메모리(memory_version)로
    만든 노트가 ready 로 들어올 때 stale 로 저장하려고 비교한다."""

    __tablename__ = "docent_stale_mark"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    plan_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)  # 0 = user 범위
    version: Mapped[int] = mapped_column(Integer)


class DocentUsage(Base):
    """사용자별 외부 검색·페이지 읽기 기록 (호출 수 제한용, app/docent/limits.py). 이틀 넘게 지난 기록은 지운다."""

    __tablename__ = "docent_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(8))  # search | page
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class DocentSearchCache(Base):
    """외부 검색·페이지 읽기 결과 캐시 (사용자와 무관, 같은 질의·URL 은 하루)."""

    __tablename__ = "docent_search_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256(kind·공급자·질의)
    kind: Mapped[str] = mapped_column(String(8))  # search | page
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
