"""planner 모듈의 공개 요청/응답 스키마. CLI·앱·LLM tool 이 모두 같은 형태를 쓴다.

시각 입력은 "HH:MM", 출력도 "HH:MM" 이다. 계산된 시각(arrive_at 등)은 자정을 넘으면 "24:30" 처럼 표기한다.
"""
from datetime import date, datetime, time
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, model_validator

HHMM = Annotated[time, PlainSerializer(lambda t: t.strftime("%H:%M"), return_type=str)]

TravelMode = Literal["walk", "car"]
ItemStatus = Literal["planned", "visiting", "done", "skipped"]
RuleType = Literal["must_include", "exclude", "end_before", "max_travel_minutes"]
Severity = Literal["error", "warning"]

MAX_DAYS = 14


# ---------- 요청 ----------

class PlanCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    start_date: date
    end_date: date | None = Field(None, description="생략하면 당일치기 (start_date 와 같음)")
    day_start: time = Field(time(10, 0), description="매일 일정 시작 시각")
    day_end: time = Field(time(18, 0), description="매일 일정 종료 목표 시각")
    travel_mode: TravelMode = "walk"
    memo: str = ""

    @model_validator(mode="after")
    def _check(self):
        if self.end_date is None:
            self.end_date = self.start_date
        _check_range(self.start_date, self.end_date, self.day_start, self.day_end)
        return self


class PlanUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    start_date: date | None = None
    end_date: date | None = None
    day_start: time | None = None
    day_end: time | None = None
    travel_mode: TravelMode | None = None
    memo: str | None = None


class ItemCreate(BaseModel):
    place_id: str = Field(description="places 모듈의 장소 ID (예: kto-126128)")
    day: int = Field(1, ge=1, description="여행 n일차 (1부터)")
    order: int | None = Field(None, ge=1, description="그날 안에서의 순서 (1부터). 생략하면 맨 뒤")
    stay_minutes: int = Field(60, ge=5, le=720)
    start_time: time | None = Field(None, description="이 시각에 시작하도록 고정 (생략하면 앞 일정에 이어서 자동 계산)")
    memo: str = ""


class ItemUpdate(BaseModel):
    """보낸 필드만 바꾼다. start_time 에 null 을 보내면 시각 고정을 해제한다."""

    day: int | None = Field(None, ge=1, description="다른 날로 이동")
    order: int | None = Field(None, ge=1, description="순서 변경 (day 와 함께 주면 그 날의 해당 순서로)")
    stay_minutes: int | None = Field(None, ge=5, le=720)
    start_time: time | None = None
    memo: str | None = None
    status: ItemStatus | None = None


class ConstraintCreate(BaseModel):
    """규칙 종류별 params

    - must_include: {"place_id": "kto-..."} 해당 장소를 반드시 포함
    - exclude: {"place_id": "kto-..."} 해당 장소를 포함하지 않음
    - end_before: {"time": "18:00", "day": 1(생략 시 모든 날)} 그날 일정이 이 시각 전에 끝남
    - max_travel_minutes: {"minutes": 20} 장소 간 이동시간 상한
    """

    type: RuleType
    params: dict[str, Any] = {}
    hard: bool = Field(True, description="true 면 위반 시 error, false 면 warning")
    source: Literal["user", "llm"] = "user"
    note: str = ""

    @model_validator(mode="after")
    def _check_params(self):
        self.params = RULE_PARAMS[self.type].model_validate(self.params).model_dump(mode="json", exclude_none=True)
        return self


class _PlaceRule(BaseModel):
    place_id: str


class _EndBefore(BaseModel):
    time: HHMM
    day: int | None = Field(None, ge=1)


class _MaxTravel(BaseModel):
    minutes: int = Field(ge=1)


RULE_PARAMS: dict[str, type[BaseModel]] = {
    "must_include": _PlaceRule,
    "exclude": _PlaceRule,
    "end_before": _EndBefore,
    "max_travel_minutes": _MaxTravel,
}


def _check_range(start_date: date, end_date: date, day_start: time, day_end: time) -> None:
    if end_date < start_date:
        raise ValueError("end_date 는 start_date 이후여야 합니다.")
    if (end_date - start_date).days + 1 > MAX_DAYS:
        raise ValueError(f"여행 기간은 최대 {MAX_DAYS}일입니다.")
    if day_end <= day_start:
        raise ValueError("day_end 는 day_start 보다 늦어야 합니다.")


# ---------- 응답 ----------

class PlaceInfo(BaseModel):
    """일정 계산에 필요한 장소 정보 (places API 응답의 일부)."""

    id: str
    title: str
    content_type: str = ""
    lat: float
    lng: float
    addr: str = ""
    open_hours: str = ""
    rest_date: str = ""


class Violation(BaseModel):
    code: str
    severity: Severity
    message: str
    day: int | None = None
    item_id: int | None = None
    constraint_id: int | None = None


class ItemView(BaseModel):
    id: int
    day: int
    order: int
    place_id: str
    place: PlaceInfo | None = Field(description="장소 정보를 못 불러오면 null")
    stay_minutes: int
    start_time: HHMM | None = Field(description="고정 시작 시각 (없으면 자동 계산)")
    memo: str
    status: ItemStatus
    travel_minutes: int | None = Field(description="직전 장소에서 이동시간 (첫 일정·건너뜀은 null)")
    travel_distance_m: int | None
    arrive_at: str | None = Field(description="계산된 도착 시각 HH:MM (skipped 는 null)")
    start_at: str | None
    end_at: str | None


class DayView(BaseModel):
    day: int
    date: date
    weekday: str
    end_at: str | None = Field(description="그날 마지막 일정 종료 시각")
    items: list[ItemView]


class ConstraintView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    params: dict[str, Any]
    hard: bool
    source: str
    note: str


class PlanSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: str
    title: str
    start_date: date
    end_date: date
    day_start: HHMM
    day_end: HHMM
    travel_mode: TravelMode
    memo: str
    created_at: datetime
    updated_at: datetime
    item_count: int = 0


class PlanDetail(PlanSummary):
    days: list[DayView]
    constraints: list[ConstraintView]
    violations: list[Violation]


class ValidationResult(BaseModel):
    ok: bool = Field(description="error 수준 위반이 없으면 true")
    violations: list[Violation]


# ---------- 변경 연산(ops) · 미리보기 · 제안 ----------
# 직접 편집 API 와 제안(Proposal)이 같은 연산 엔진(ops.py)을 쓴다.
# 같은 ops 목록 안에서 새로 추가한 항목/규칙은 음수 임시 id(-1, -2, ... 추가한 순서)로 참조한다.

class AddItemOp(ItemCreate):
    op: Literal["add_item"]


class UpdateItemOp(ItemUpdate):
    op: Literal["update_item"]
    item_id: int


class RemoveItemOp(BaseModel):
    op: Literal["remove_item"]
    item_id: int


class UpdatePlanOp(PlanUpdate):
    op: Literal["update_plan"]


class AddConstraintOp(ConstraintCreate):
    op: Literal["add_constraint"]


class RemoveConstraintOp(BaseModel):
    op: Literal["remove_constraint"]
    constraint_id: int


PlanOp = Annotated[
    AddItemOp | UpdateItemOp | RemoveItemOp | UpdatePlanOp | AddConstraintOp | RemoveConstraintOp,
    Field(discriminator="op"),
]


class OpsRequest(BaseModel):
    ops: list[PlanOp] = Field(min_length=1)


class Change(BaseModel):
    """사람이 읽는 변경 요약 한 줄. 앱의 제안 카드에 그대로 표시한다."""

    kind: Literal["added", "removed", "moved", "updated", "plan", "rule_added", "rule_removed", "day_end"]
    message: str
    item_id: int | None = None
    day: int | None = None


class PreviewResult(BaseModel):
    plan: PlanDetail = Field(description="ops 를 적용했을 때의 계획 (저장 안 됨). 새 항목 id 는 음수 임시값")
    changes: list[Change]


ProposalStatus = Literal["pending", "accepted", "rejected", "superseded"]


class ProposalCreate(OpsRequest):
    summary: str = Field("", description="변경 이유·요약 (Agent 응답 등)")
    source: Literal["llm", "user", "event"] = "llm"
    origin: str = Field("", description="출처 식별자 (예: agent 세션 id)")
    supersedes: int | None = Field(None, description="이 제안으로 대체할 이전 대기 중 제안 id")


class ProposalView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    plan_id: int
    status: ProposalStatus
    summary: str
    source: str
    origin: str
    ops: list[PlanOp]
    created_at: datetime
    decided_at: datetime | None


class ProposalDetail(ProposalView):
    changes: list[Change] = Field(description="현재 계획 대비 변경 요약 (pending 일 때만)")
    before: PlanDetail | None = Field(None, description="현재 계획 (pending 일 때만)")
    after: PlanDetail | None = Field(None, description="승인 시 계획 미리보기 (pending 이고 적용 가능할 때만)")
    applicable: bool = Field(True, description="현재 계획에 그대로 적용할 수 있는지")
    error: str | None = None


class AcceptResult(BaseModel):
    proposal: ProposalView
    plan: PlanDetail
