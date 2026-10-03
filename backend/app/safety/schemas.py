"""safety API 요청·응답. 계획 Agent 가 그대로 읽어도 오해하지 않도록 판단 근거와 한계를 함께 돌려준다."""
from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Risk = Literal["none", "low", "caution", "danger", "unknown"]
Horizon = Literal["past", "now", "short_range", "medium_range", "long_range"]
Confidence = Literal["high", "medium", "low", "none"]

HORIZON_DOC = (
    "now: 지금~3시간 이내(실측+특보+예보) / short_range: 기상청 단기예보가 있는 약 3일 이내 / "
    "medium_range: 10일 이내지만 시간별 예보 없음 / long_range: 10일 넘게 남음 (날씨 예측 불가) / past: 이미 지남"
)
RISK_DOC = ("none: 위험 신호 없음 / low: 참고 수준 / caution: 사용자에게 알리고 시간 조정·대안 고려 / "
            "danger: 이 시간대 방문 피하고 대안 제시 / unknown: 판단 근거 없음 (안전하다는 뜻이 아님)")


class SourceStatus(BaseModel):
    ok: bool
    fetched_at: datetime | None = None
    error: str | None = None
    note: str | None = None


class Hazard(BaseModel):
    type: Literal["flood", "breakwater", "rain", "wind", "wave", "warning"]
    level: Risk
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


class VisitAssessment(BaseModel):
    key: str | None
    place_id: str | None
    label: str | None
    start: datetime
    end: datetime
    horizon: Horizon = Field(description=HORIZON_DOC)
    forecast_available: bool = Field(description="이 시각의 기상 예보가 있는지. false 면 날씨 위험은 판단하지 않은 것")
    confidence: Confidence
    risk: Risk = Field(description=RISK_DOC)
    hazards: list[Hazard]
    sensitivity: list[str] = Field(description="날씨에 따라 위험해지는 장소 특성 (방파제, 침수 감시 구역). 위험이 아니라 성질")
    notes: list[str] = Field(description="참고 정보 (조위, 계절 등). 일정 변경 근거로 쓰지 않는다")
    advice: str = Field(description="계획 Agent 에게 주는 행동 지침")
    recheck_from: date | None = Field(None, description="이 날짜부터 다시 조회하면 예보 기반 판단이 가능")


class AssessResult(BaseModel):
    now: datetime
    overall_risk: Risk
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


class Outlook(BaseModel):
    date: date
    now: datetime
    horizon: Horizon = Field(description=HORIZON_DOC)
    forecast_available: bool
    confidence: Confidence
    summary: DaySummary | None = Field(None, description="예보가 있을 때만")
    hazards: list[Hazard] = Field(description="그날 시간대별 위험 신호 (방파제·침수 구역 기준)")
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
    risk: Risk


class ZoneStatus(BaseModel):
    id: str
    name: str
    kind: Literal["breakwater", "flood"]
    risk: Risk
    reasons: list[str]


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
    overall_risk: Risk
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
