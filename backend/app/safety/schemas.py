"""safety API 요청·응답. 계획 Agent 가 그대로 읽어도 오해하지 않도록 판단 근거와 한계를 함께 돌려준다."""
from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Level = Literal["safe", "caution", "danger"]  # 위험 3단계
HazardLevel = Literal["info", "caution", "danger"]  # 근거 등급. info 는 참고 (단계를 올리지 않음)
Unassessed = Literal["past", "beyond_forecast", "data_missing"]
Horizon = Literal["past", "now", "short_range", "medium_range", "long_range"]
Confidence = Literal["high", "medium", "low", "none"]

HORIZON_DOC = (
    "now: 지금~3시간 이내(실측+특보+예보) / short_range: 기상청 단기예보가 있는 약 3일 이내 / "
    "medium_range: 10일 이내지만 시간별 예보 없음 / long_range: 10일 넘게 남음 (날씨 예측 불가) / past: 이미 지남"
)
RISK_DOC = ("위험 3단계. safe: 주의·위험 신호 없음 (참고 신호는 hazards 에 info 로 있을 수 있음) / "
            "caution: 사용자에게 알리고 시간 조정·대안 고려 / danger: 이 시간대 방문 피하고 대안 제시 / "
            "null: 판단하지 않음 (unassessed_reason 참고. 안전하다는 뜻이 아님)")
UNASSESSED_DOC = ("risk 가 null 인 이유. past: 이미 지남 / beyond_forecast: 예보 범위 밖 (약 3일 뒤) / "
                  "data_missing: 필요한 데이터를 받지 못함")


class SourceStatus(BaseModel):
    ok: bool
    fetched_at: datetime | None = None
    error: str | None = None
    note: str | None = None


class Hazard(BaseModel):
    type: Literal["flood", "breakwater", "rain", "wind", "wave", "warning"]
    level: HazardLevel = Field(description="info: 참고 (단계를 올리지 않음, 일정 조정에 참고) / caution / danger")
    reason: str
    source: str = Field(description="근거 데이터 (예: E-UM 수위센서, 기상청 단기예보, 기상특보)")
    at: datetime | None = Field(None, description="측정 또는 예보 시각")


class Visit(BaseModel):
    key: str | None = Field(None, description="호출 측 식별자 (예: 일정 item_id). 결과에 그대로 돌려준다")
    place_id: str | None = Field(None, description="places 의 장소 id. 좌표를 모르면 이것만 주면 된다")
    lat: float | None = None
    lng: float | None = None
    label: str | None = Field(None, description="장소 이름 (없으면 places 에서 가져온다)")
    date: date
    start: time
    end: time | None = Field(None, description="생략하면 start + 60분")
    indoor: bool | None = Field(None, description="실내 여부. 생략하면 places 의 장소 종류로 추정 "
                                                  "(문화시설·음식점·쇼핑·숙박 = 실내)")

    @model_validator(mode="after")
    def _check(self):
        if self.place_id is None and (self.lat is None or self.lng is None):
            raise ValueError("place_id 또는 lat·lng 가 필요합니다.")
        if self.end is not None and self.end <= self.start:
            raise ValueError("end 는 start 보다 뒤여야 합니다.")
        return self


class AssessRequest(BaseModel):
    visits: list[Visit] = Field(min_length=1, max_length=60)
    now: datetime | None = Field(None, description="기준 시각 덮어쓰기 (테스트·시연용). 생략하면 현재 시각")


class TimeWindow(BaseModel):
    start: datetime
    end: datetime
    has_info: bool = Field(description="구간 안에 참고(info) 신호가 있는지 (예: 약한 비)")


class VisitAssessment(BaseModel):
    key: str | None
    place_id: str | None
    label: str | None
    start: datetime
    end: datetime
    indoor: bool | None = Field(description="실내 여부 (요청값 또는 장소 종류로 추정, 모르면 null = 야외로 판단)")
    horizon: Horizon = Field(description=HORIZON_DOC)
    forecast_available: bool = Field(description="이 시각의 기상 예보가 있는지. false 면 날씨 위험은 판단하지 않은 것")
    confidence: Confidence
    risk: Level | None = Field(description=RISK_DOC)
    unassessed_reason: Unassessed | None = Field(None, description=UNASSESSED_DOC)
    hazards: list[Hazard] = Field(description="근거. 위험 단계가 safe 여도 참고(info) 신호가 있으면 들어 있다")
    sensitivity: list[str] = Field(description="날씨에 따라 위험해지는 장소 특성 (방파제, 침수 감시 구역). 위험이 아니라 성질")
    notes: list[str] = Field(description="참고 정보 (조위, 계절 등). 일정 변경 근거로 쓰지 않는다")
    advice: str = Field(description="계획 Agent 에게 주는 행동 지침")
    better_times: list[TimeWindow] = Field(
        default_factory=list,
        description="risk 가 caution/danger 일 때, 예보 범위 안에서 같은 장소에 주의·위험 신호가 없는 시간대 "
                    "(08~19시, 같은 체류 시간, 이른 순 최대 3개). 비어 있으면 대체 장소를 찾는다")
    recheck_from: date | None = Field(None, description="이 날짜부터 다시 조회하면 예보 기반 판단이 가능")


class AssessResult(BaseModel):
    now: datetime
    overall_risk: Level | None = Field(description="주의·위험이 있으면 그중 최고. 없는데 판단하지 않은 미래 항목이 있으면 null")
    guidance: str = Field(description="전체 결과를 어떻게 다뤄야 하는지 (계획 Agent 용)")
    visits: list[VisitAssessment]
    data_status: dict[str, SourceStatus]
    assumptions: list[str]


class HourForecastView(BaseModel):
    time: datetime
    pop: int | None = Field(None, description="강수확률 %")
    pcp_mm: float | None = Field(None, description="1시간 강수량 mm")
    sky: str | None = None
    precip: str | None = Field(None, description="강수 형태")
    wind_ms: float | None = None
    wave_m: float | None = None
    temp: float | None = None


class TideView(BaseModel):
    time: datetime
    kind: Literal["high", "low"]
    level_cm: float


class DaySummary(BaseModel):
    max_pop: int | None = None
    total_pcp_mm: float | None = None
    max_wind_ms: float | None = None
    max_wave_m: float | None = None
    temp_min: float | None = None
    temp_max: float | None = None
    sky: str | None = Field(None, description="대표 하늘 상태 (가장 흔한 값)")


class SafeWindow(BaseModel):
    place_kind: Literal["breakwater", "flood", "outdoor"] = Field(
        description="breakwater 방파제 / flood 해안 저지대(침수 감시 구역) / outdoor 그 밖의 야외")
    start: datetime
    end: datetime
    has_info: bool = Field(description="구간 안에 참고(info) 신호가 있는지")


class Outlook(BaseModel):
    date: date
    now: datetime
    horizon: Horizon = Field(description=HORIZON_DOC)
    forecast_available: bool
    confidence: Confidence
    summary: DaySummary | None = Field(None, description="예보가 있을 때만")
    hazards: list[Hazard] = Field(description="그날 시간대별 위험 신호 (방파제·침수 구역 기준)")
    safe_windows: list[SafeWindow] = Field(
        description="조회 시간대 안에서 장소 종류별로 주의·위험 신호가 없는 구간 (예보가 있을 때만). 실내는 대개 항상 괜찮다")
    hourly: list[HourForecastView] = Field(description="detail=true 일 때만 채운다")
    tides: list[TideView] = Field(description="만조·간조 (천문 계산이라 먼 날짜도 정확, 구룡포 조차는 수십 cm)")
    warnings: list[str] = Field(description="현재 발효 중인 구룡포 해당 특보 (오늘·내일 조회일 때만)")
    seasonal_notes: list[str]
    guidance: str
    recheck_from: date | None = None
    data_status: dict[str, SourceStatus]


class SensorView(BaseModel):
    id: int
    name: str
    type: str = Field(description="HOLE 맨홀 | ROAD 지표면 | RIVER 하천 | RAIN 강우")
    lat: float
    lng: float
    value: float | None = Field(None, description="측정값 (단위 미공개)")
    level: int | None = Field(None, description="E-UM 이 매긴 등급 (1 = 평상시)")
    risk: Level | None = Field(description="이 센서 값으로 본 단계. 등급 값이 없으면 null")
    note: str | None = Field(None, description="참고 신호 (예: 노면 수위 감지)")


class ZoneStatus(BaseModel):
    id: str
    name: str
    kind: Literal["breakwater", "flood"]
    risk: Level | None = Field(description="null: 데이터를 받지 못해 판단하지 않음")
    reasons: list[str] = Field(description="주의·위험 사유와 참고 신호")


class BreakwaterActivity(BaseModel):
    side: Literal["north", "south"]
    name: str
    hour: datetime
    fall: int = Field(description="위험구역 실족 감지")
    intrusion: int = Field(description="위험구역 침입 감지")
    approach: int = Field(description="위험구역 접근 경고")
    abnormal: int = Field(description="보행자 이상 감지")


class UvView(BaseModel):
    index: float
    at: datetime


class Status(BaseModel):
    now: datetime
    overall_risk: Level | None
    zones: list[ZoneStatus]
    sensors: list[SensorView]
    breakwater_activity: list[BreakwaterActivity] = Field(description="방파제별 가장 최근 1시간 집계")
    uv: UvView | None
    warnings: list[str] = Field(description="발효 중인 구룡포 해당 특보")
    preliminary_warnings: list[str] = Field(description="예비특보")
    next_hours: list[Hazard] = Field(description="앞으로 12시간 예보상 위험 신호")
    guidance: str
    data_status: dict[str, SourceStatus]


class ZoneView(BaseModel):
    id: str
    name: str
    kind: Literal["breakwater", "flood"]
    path: list[tuple[float, float]] = Field(description="(lat, lng). 점 하나면 원의 중심, 여러 개면 선")
    radius_m: float


PlaceKind = Literal["breakwater", "flood", "outdoor", "indoor"]


class SituationHour(BaseModel):
    time: datetime
    levels: dict[PlaceKind, Level] = Field(description="장소 종류별 그 시각 단계 (예보 + 발효 특보)")
    info: list[PlaceKind] = Field(description="safe 지만 참고 신호가 있는 장소 종류")


class Situation(BaseModel):
    """지금 안전 상황 요약. planner 같은 이용자가 fingerprint 를 저장해 두었다가 바뀌었을 때만 일정을 다시 점검한다."""

    now: datetime = Field(description="safety 의 기준 시각 (시뮬레이션 중이면 가상 시각)")
    fingerprint: str = Field(description="아래 내용(참고 신호·데이터 장애 포함)의 해시. 상황이 바뀌면 달라진다")
    forecast_until: datetime | None = Field(description="단기예보 마지막 시각. 이 시각까지 방문 위험을 판단할 수 있다")
    zones: dict[str, Level | None] = Field(description="구역별 지금 단계 (status 와 같음)")
    warnings: list[str] = Field(description="발효 중인 구룡포 해당 특보")
    hours: list[SituationHour] = Field(description="예보가 있는 시각별 장소 종류 단계")
    data_ok: dict[str, bool]


class SimState(BaseModel):
    scenario: str
    now: datetime = Field(description="지금 가상 시각 (시뮬레이션 시각이 아니면 실제 시각)")
    offset_minutes: int = Field(description="실제 시각과의 차이")
    scenarios: dict[str, str] = Field(description="쓸 수 있는 시나리오와 설명")


class SimUpdate(BaseModel):
    model_config = {"extra": "forbid"}

    scenario: str | None = Field(None, description="바꿀 시나리오 (생략하면 그대로)")
    now: datetime | None = Field(None, description="가상 시각. 이후 실제 시간만큼 흘러간다. 생략하면 그대로")
    reset_clock: bool = Field(False, description="true 면 실제 시각으로 되돌린다")
