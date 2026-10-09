"""trip API 스키마. 명세: docs/trip/README.md §1.3 (TripState), §2.1 (LocationIn), §3.3 (Event)"""
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StartRequest(Strict):
    session_id: str | None = Field(None, max_length=64, description="선제 턴을 보낼 Agent 세션. 생략하면 그 계획의 최근 세션, 없으면 새로 만든다")


class LocationIn(Strict):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    accuracy: float | None = Field(None, ge=0, description="m. 없으면 30 으로 본다. 100 보다 크면 버린다")
    recorded_at: datetime | None = Field(None, description="판정에 쓰는 시각. 없으면 서버의 지금 (시간대가 없으면 KST)")
    source: Literal["app", "cli", "replay", "sim"] = "app"


class LocationBatch(Strict):
    points: list[LocationIn] = Field(min_length=1, max_length=100, description="오래된 순")


class Location(BaseModel):
    lat: float
    lng: float
    accuracy: float | None
    recorded_at: datetime
    source: str


class Current(BaseModel):
    item_id: int | None = Field(description="visiting: 머무는 항목 / moving: 떠나온 항목 / idle: null")
    state: Literal["idle", "visiting", "moving"]
    since: str | None = Field(description="HH:MM")


class Next(BaseModel):
    item_id: int
    title: str | None = None
    planned_start: str | None = Field(None, description="기준 일정의 시작 시각")
    eta: str | None = Field(None, description="지금 기준 예상 도착 시각 (planner projection)")


class TripState(BaseModel):
    plan_id: int
    active: bool = Field(description="진행 중인 여행일이 있으면 true (동행 모드)")
    day: int | None
    date: date | None
    started_at: datetime | None
    ended_at: datetime | None
    ended_reason: Literal["user", "auto"] | None
    now: datetime = Field(description="trip 의 지금 (safety 가상 시각을 따름)")
    clock: Literal["system", "sim"]
    location: Location | None = Field(description="마지막 위치. 없으면 null")
    current: Current | None
    next: Next | None
    delay_minutes: int | None = Field(description="다음 일정 기준 예상 지연 (음수 = 여유)")
    session_id: str | None


class EventView(BaseModel):
    id: int
    plan_id: int
    day: int
    type: str = Field(description="arrived | departed | delay | safety_raised | safety_eased | near_hazard | day_started | day_ended")
    level: int = Field(description="개입 단계 0~3 (§3.1)")
    severity: Literal["info", "warning", "danger"]
    created_at: datetime
    now: datetime = Field(description="판정 시각 (trip 의 지금, 위치면 recorded_at)")
    item_ids: list[int]
    message: str
    payload: dict[str, Any]
    status: Literal["queued", "delivered", "suppressed", "handled"]
    agent: dict[str, Any] | None = Field(description="단계 3: Agent 결과 {session_id, message_id, proposal_id}. 아니면 null")
    card: dict[str, Any] | None = Field(description="단계 2: 카드 정보. 아니면 null")
    seen_at: datetime | None = None
    attempts: int = Field(0, description="단계 3 전송 시도 수")
    last_error: str | None = Field(None, description="마지막 전송 실패 사유 (큐에 남아 다시 보낸다)")


class LocationResult(BaseModel):
    state: TripState
    events: list[EventView] = Field(description="이번 요청으로 생긴 이벤트")
    accepted: int = Field(description="판정에 쓴 점 수")
    ignored: int = Field(description="버린 점 수 (accuracy 초과, 순서 뒤바뀜)")


class TickResult(BaseModel):
    state: TripState
    events: list[EventView] = Field(description="이번 틱으로 생긴 이벤트")
