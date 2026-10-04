"""planner 공개 요청·응답 스키마. 명세: docs/planner/README.md

요청 모델은 모두 엄격하다: 정의되지 않은 필드, 허용되지 않은 null, 바꿀 값이 없는 수정 요청은 422.
시각 입력·출력은 "HH:MM". 계산된 시각(arrive_at 등)은 자정을 넘으면 "24:30" 처럼 표기한다.
"""
from datetime import date, datetime, time
from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_serializer, model_validator

HHMM = Annotated[time, PlainSerializer(lambda t: t.strftime("%H:%M"), return_type=str, when_used="json")]

TravelMode = Literal["walk", "car"]
ItemStatus = Literal["planned", "visiting", "done", "skipped"]
RuleType = Literal["must_include", "exclude", "end_before", "max_travel_minutes"]
Severity = Literal["error", "warning"]
Source = Literal["llm", "user", "event"]

MAX_DAYS = 14
MAX_OPS = 100
MAX_CANDIDATES = 10

Title = Annotated[str, Field(min_length=1, max_length=200)]
Memo = Annotated[str, Field(max_length=500)]
Day = Annotated[int, Field(ge=1, le=MAX_DAYS)]
Order = Annotated[int, Field(ge=1)]
Stay = Annotated[int, Field(ge=5, le=720)]
PlaceId = Annotated[str, Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")]
Id = Annotated[int, Field(description="양수는 실제 id, 음수는 같은 ops 목록에서 새로 만든 항목·규칙의 임시 id")]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PartialUpdate(Strict):
    """보낸 필드만 바꾸는 요청. 바꿀 값이 없거나, null 을 허용하지 않는 필드에 null 이 오면 거부한다."""

    NULLABLE: ClassVar[set[str]] = set()
    KEYS: ClassVar[set[str]] = set()  # 바꿀 값이 아닌 식별 필드 (op, item_id 등)

    @model_validator(mode="after")
    def _check_partial(self):
        sent = self.model_fields_set - self.KEYS
        if not sent:
            raise ValueError("바꿀 값을 하나 이상 보내야 합니다.")
        nulls = sorted(f for f in sent if getattr(self, f) is None and f not in self.NULLABLE)
        if nulls:
            raise ValueError(f"null 일 수 없는 필드: {nulls}")
        return self


# ---------- 계획 ----------

class PlanCreate(Strict):
    title: Title
    start_date: date
    end_date: date | None = Field(None, description="생략하면 당일치기 (start_date 와 같음)")
    day_start: HHMM = Field(time(10, 0), description="매일 일정 시작 시각")
    day_end: HHMM = Field(time(18, 0), description="매일 일정 종료 목표 시각")
    travel_mode: TravelMode = "walk"
    memo: Memo = ""


class PlanUpdate(PartialUpdate):
    title: Title | None = None
    start_date: date | None = None
    end_date: date | None = None
    day_start: HHMM | None = None
    day_end: HHMM | None = None
    travel_mode: TravelMode | None = None
    memo: Memo | None = None


# ---------- 항목·규칙 ----------

class ItemCreate(Strict):
    place_id: PlaceId = Field(description="places 의 장소 ID (예: kto-126128)")
    day: Day = Field(1, description="n일차 (1부터)")
    order: Order | None = Field(None, description="그날 순서 (1부터). 생략하거나 항목 수보다 크면 맨 뒤")
    stay_minutes: Stay = 60
    start_time: HHMM | None = Field(None, description="이 시각에 시작하도록 고정 (생략하면 자동 계산)")
    memo: Memo = ""


class ItemUpdate(PartialUpdate):
    """보낸 필드만 바꾼다. start_time 에 null 을 보내면 시각 고정을 해제한다 (null 이 허용되는 유일한 필드)."""

    NULLABLE: ClassVar[set[str]] = {"start_time"}

    day: Day | None = Field(None, description="다른 날로 이동")
    order: Order | None = Field(None, description="순서 변경 (day 와 함께 주면 그 날의 해당 순서)")
    stay_minutes: Stay | None = None
    start_time: HHMM | None = None
    memo: Memo | None = None
    status: ItemStatus | None = None


class _PlaceRule(Strict):
    place_id: PlaceId


class _EndBefore(Strict):
    time: HHMM
    day: Day | None = None


class _MaxTravel(Strict):
    minutes: int = Field(ge=1, le=720)


RULE_PARAMS: dict[str, type[BaseModel]] = {
    "must_include": _PlaceRule,
    "exclude": _PlaceRule,
    "end_before": _EndBefore,
    "max_travel_minutes": _MaxTravel,
}


class ConstraintCreate(Strict):
    """규칙 종류별 params (정의되지 않은 키는 422)

    - must_include / exclude: {"place_id": "kto-..."}
    - end_before: {"time": "18:00", "day": 1 (생략 시 매일)}
    - max_travel_minutes: {"minutes": 20}
    """

    type: RuleType
    params: dict[str, Any] = {}
    hard: bool = Field(True, description="true 면 위반 시 error, false 면 warning")
    source: Literal["user", "llm"] = "user"
    note: str = Field("", max_length=300)

    @model_validator(mode="after")
    def _check_params(self):
        self.params = RULE_PARAMS[self.type].model_validate(self.params).model_dump(mode="json", exclude_none=True)
        return self


# ---------- 변경 연산 ----------

class AddItemOp(ItemCreate):
    op: Literal["add_item"]


class UpdateItemOp(ItemUpdate):
    KEYS: ClassVar[set[str]] = {"op", "item_id"}

    op: Literal["update_item"]
    item_id: Id


class RemoveItemOp(Strict):
    op: Literal["remove_item"]
    item_id: Id


class UpdatePlanOp(PlanUpdate):
    KEYS: ClassVar[set[str]] = {"op"}

    op: Literal["update_plan"]


class AddConstraintOp(ConstraintCreate):
    op: Literal["add_constraint"]


class RemoveConstraintOp(Strict):
    op: Literal["remove_constraint"]
    constraint_id: Id


PlanOp = Annotated[
    AddItemOp | UpdateItemOp | RemoveItemOp | UpdatePlanOp | AddConstraintOp | RemoveConstraintOp,
    Field(discriminator="op"),
]


class OpsRequest(Strict):
    ops: list[PlanOp] = Field(min_length=1, max_length=MAX_OPS)


# ---------- 응답: 계획 ----------

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
    arrive_at: str | None = Field(description="계산된 도착 시각 HH:MM (건너뜀은 null)")
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
    version: int = Field(description="일정이 바뀔 때마다 1 오른다")
    item_count: int = 0
    created_at: datetime
    updated_at: datetime


class PlanDetail(PlanSummary):
    days: list[DayView]
    constraints: list[ConstraintView]
    violations: list[Violation]


class ValidationResult(BaseModel):
    ok: bool = Field(description="error 수준 위반이 없으면 true")
    violations: list[Violation]


class Change(BaseModel):
    """사람이 읽는 변경 요약 한 줄."""

    kind: Literal["added", "removed", "moved", "updated", "plan", "rule_added", "rule_removed", "day_end"]
    message: str
    item_id: int | None = None
    day: int | None = None


class PreviewResult(BaseModel):
    plan: PlanDetail = Field(description="ops 를 적용했을 때의 계획 (저장 안 됨). 새 항목 id 는 음수 임시값")
    changes: list[Change]


# ---------- 변경 제안 ----------

ProposalStatus = Literal["pending", "accepted", "rejected", "superseded"]


class ProposalCreate(OpsRequest):
    summary: str = Field("", max_length=2000, description="카드 설명")
    request: str = Field("", max_length=4000, description="이 제안을 만든 사용자 요청 원문")
    source: Source = "llm"
    origin: str = Field("", max_length=64, description="출처 식별자 (예: agent 세션 id)")
    supersedes: int | None = Field(None, description="이 제안으로 대체할 대기 중 제안 id")
    expected_version: int | None = Field(None, description="현재 version 과 다르면 409 version_conflict")


class AcceptRequest(Strict):
    mode: Literal["auto", "overwrite"] = Field(
        "auto", description="auto: 제안 이후 일정이 바뀌었으면 409 base_changed. overwrite: 카드의 after 일정 그대로 반영")


class ProposalSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    plan_id: int
    status: ProposalStatus
    decision: Literal["applied", "overwritten"] | None
    summary: str
    source: str
    origin: str
    base_version: int
    changes: list[Change]
    created_at: datetime
    decided_at: datetime | None


class ProposalDetail(ProposalSummary):
    request: str
    ops: list[PlanOp]
    before: PlanDetail = Field(description="제안 당시 일정 (기록, 바뀌지 않음)")
    after: PlanDetail = Field(description="승인하면 될 일정 (기록, 새 항목 id 는 음수)")
    current_version: int
    base_changed: bool = Field(description="제안 이후 일정이 바뀌었는지 (current_version != base_version)")

    @field_serializer("ops")
    def _ops_as_sent(self, ops: list) -> list[dict]:
        # 보낸 필드만 내보낸다. update_item 의 start_time 처럼 "생략"과 "null(해제)"이 다른 뜻이라,
        # 생략된 필드를 null 로 채워 보내면 이 ops 를 다시 쓰는 쪽(Agent 초안)에서 의미가 바뀐다.
        return [op.model_dump(mode="json", exclude_unset=True) for op in ops]


class AcceptResult(BaseModel):
    proposal: ProposalDetail
    plan: PlanDetail


# ---------- 장소 추천 ----------

class CandidateCreate(Strict):
    place_id: PlaceId
    day: Day
    time_hint: HHMM | None = Field(None, description="이 시각 무렵에 넣고 싶음 (위치 계산·같은 날 중복 구분에만 사용)")
    stay_minutes: Stay = 60
    reason: str = Field("", max_length=300)


class RecommendationCreate(Strict):
    title: str = Field(min_length=1, max_length=100)
    source: Source = "llm"
    origin: str = Field("", max_length=64)
    candidates: list[CandidateCreate] = Field(min_length=1, max_length=MAX_CANDIDATES)


class CandidateItem(BaseModel):
    item_id: int
    day: int
    order: int
    start_at: str | None
    end_at: str | None


class CandidateView(BaseModel):
    id: int
    place_id: str
    place: PlaceInfo | None
    day: int
    time_hint: HHMM | None
    stay_minutes: int
    reason: str
    state: Literal["in_plan", "not_in_plan", "day_out_of_range"] = Field(
        description="지금 일정 기준: 그날 같은 장소가 있으면 in_plan")
    item: CandidateItem | None = Field(description="in_plan 일 때 이 후보에 해당하는 항목")
    other_days: list[int] = Field(description="같은 장소가 있는 다른 날 (참고)")


class RecommendationView(BaseModel):
    id: int
    plan_id: int
    title: str
    source: str
    origin: str
    created_at: datetime
    candidates: list[CandidateView]


class RecommendationActionResult(BaseModel):
    recommendation: RecommendationView
    plan: PlanDetail


# ---------- 안전 점검 (safety.py) ----------

SafetyLevel = Literal["safe", "caution", "danger"]
SAFETY_LEVEL_DOC = ("위험 3단계. safe 안전 / caution 주의 (알리고 시간 조정·대안 고려) / danger 위험 (그 시간대 방문 피하기). "
                    "null 은 판단하지 않음 (unassessed_reason). 안전하다는 뜻이 아니다")


class SafetyHazard(BaseModel):
    type: str = Field(description="flood | breakwater | rain | wind | wave | warning")
    level: Literal["info", "caution", "danger"] = Field(description="info 는 참고 (단계를 올리지 않음)")
    reason: str
    source: str


class SafetyWindow(BaseModel):
    start: datetime
    end: datetime
    has_info: bool = Field(description="참고 신호(약한 비 등)가 있는 구간인지")


class SafetyItem(BaseModel):
    """일정 항목 하나의 안전 점검 결과 (safety /safety/assess 결과를 일정 기준으로 정리)."""

    item_id: int
    day: int
    date: date
    place_id: str
    title: str
    start_at: str
    end_at: str
    risk: SafetyLevel | None = Field(description=SAFETY_LEVEL_DOC)
    unassessed_reason: Literal["past", "beyond_forecast", "data_missing"] | None = None
    data_missing: bool = Field(False, description="근거 일부를 받지 못함. risk 가 있으면 실제로는 더 높을 수 있다")
    horizon: str = Field(description="now | short_range | medium_range | long_range | past")
    hazards: list[SafetyHazard] = Field(description="근거. safe 여도 참고(info) 신호가 있으면 들어 있다")
    advice: str
    better_times: list[SafetyWindow] = Field(description="caution·danger 일 때 같은 장소에 신호가 없는 시간대")
    sensitivity: list[str] = Field(description="방파제, 침수 감시 구역, 실내 등 장소 성질")
    notes: list[str] = Field(description="참고 정보 (조위·계절). 일정 변경 근거가 아니다")
    recheck_from: date | None = None


class SafetyCounts(BaseModel):
    safe: int = 0
    caution: int = 0
    danger: int = 0
    unassessed: int = 0
    info: int = Field(0, description="safe 지만 참고 신호가 있는 항목 수")


class SafetyCheck(BaseModel):
    """계획의 안전 점검 결과. 검증(validate)과 별개로, 방문 장소·시각만으로 위험 단계를 매긴다."""

    plan_id: int
    plan_version: int
    checked_at: datetime = Field(description="safety 의 기준 시각 (가상 상황이면 가상 시각)")
    overall_risk: SafetyLevel | None = Field(
        description="주의·위험 항목이 있으면 그중 최고. 없는데 판단하지 않은 미래 항목이 있으면 null")
    counts: SafetyCounts
    guidance: str = Field(description="결과를 어떻게 다뤄야 하는지 (Agent 용)")
    items: list[SafetyItem] = Field(description="방문 순서대로. 건너뜀·완료 항목과 시각을 계산할 수 없는 항목은 뺀다")
    data_ok: dict[str, bool] = Field(description="safety 가 쓴 데이터 소스별 수신 여부")


SafetyChangeKind = Literal["raised", "lowered", "cleared"]


class SafetyChange(BaseModel):
    kind: SafetyChangeKind = Field(description="raised 위험 상승 / lowered 낮아졌지만 아직 주의 / cleared 안전으로 돌아옴")
    from_risk: SafetyLevel = Field(description="지난 점검 단계 (처음이면 safe)")
    to_risk: SafetyLevel
    item: SafetyItem


class SafetyAlertView(BaseModel):
    """상황이 바뀌어 일정의 위험 단계가 달라졌을 때 남는 경고. 나중에 Guide Agent 를 부르는 기준이 된다."""

    id: int
    plan_id: int
    created_at: datetime
    level: SafetyLevel = Field(description="이 경고의 단계 = raised·lowered 항목의 최고 단계 (해제만 있으면 safe)")
    needs_action: bool = Field(description="raised 가 있으면 true (일정 조정이 필요할 수 있음)")
    status: Literal["new", "seen"]
    message: str
    plan_version: int
    situation: str = Field(description="경고를 만든 safety 상황 fingerprint")
    checked_at: datetime
    changes: list[SafetyChange]


class SafetyScanReport(BaseModel):
    ran: bool = Field(description="점검을 실제로 했는지 (상황이 같고 재점검 주기 전이면 false)")
    reason: str
    situation: str | None = None
    checked_at: datetime | None = None
    plans: int = Field(0, description="점검한 계획 수")
    alerts: list[SafetyAlertView] = Field(default_factory=list)
