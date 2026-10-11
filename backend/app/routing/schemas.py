"""routing 공개 요청·응답 스키마. 명세: docs/routing/README.md"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Mode = Literal["walk", "car"]
Source = Literal["road", "estimate"]  # road = 실제 길로 계산, estimate = 직선거리 근사


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Point(Strict):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class WalkOptions(Strict):
    walking_speed_kmh: float | None = Field(None, ge=0.5, le=10, description="걷는 속도. 생략 시 4.2 (easy_walk 면 3.6)")
    easy_walk: bool = Field(False, description="계단·언덕을 피하는 걷기 편한 길")


class Leg(Strict):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    from_: Point = Field(alias="from")
    to: Point


class LegsRequest(WalkOptions):
    mode: Mode = "walk"
    legs: list[Leg] = Field(min_length=1, max_length=300)


class MatrixRequest(WalkOptions):
    mode: Mode = "walk"
    sources: list[Point] = Field(min_length=1, max_length=50)
    targets: list[Point] = Field(min_length=1, max_length=50)


class RouteRequest(WalkOptions):
    mode: Mode = "walk"
    stops: list[Point] = Field(min_length=2, max_length=20, description="출발 → 경유 → 도착 순서")
    hazards: Literal["ignore", "report", "avoid"] = Field(
        "ignore", description="ignore: 위험 구역을 보지 않음 / report: 지나가는 위험 구역만 알림 / avoid: 우회")
    now: datetime | None = Field(None, description="위험 구역 판단 기준 시각 (테스트·시연용, safety 에 그대로 넘김)")
    steps: bool = Field(True, description="회전 안내 포함")


class EngineStatus(BaseModel):
    ok: bool = Field(description="길찾기 엔진 응답 여부. false 면 모든 값이 직선거리 근사")
    error: str | None = None


class LegResult(BaseModel):
    minutes: int = Field(description="이동시간 (분, 올림). 같은 지점이면 0")
    distance_m: int
    source: Source


class LegsResult(BaseModel):
    mode: Mode
    legs: list[LegResult]
    engine: EngineStatus


class MatrixResult(BaseModel):
    mode: Mode
    matrix: list[list[LegResult]] = Field(description="matrix[i][j] = sources[i] → targets[j]")
    engine: EngineStatus


class Step(BaseModel):
    type: str = Field(description="start · straight · left · right · slight_left · uturn · steps · arrive 등")
    instruction: str = Field(description="한국어 안내 문구")
    street: str | None = None
    distance_m: int
    seconds: int
    begin_index: int = Field(description="geometry.coordinates 에서 이 안내가 시작되는 위치")


class RouteLeg(BaseModel):
    minutes: int
    distance_m: int
    steps: list[Step]


class LineString(BaseModel):
    type: Literal["LineString"] = "LineString"
    coordinates: list[tuple[float, float]] = Field(description="[lng, lat] (GeoJSON 순서)")


class ZoneHit(BaseModel):
    id: str
    name: str
    kind: Literal["breakwater", "flood"]
    risk: str
    reason: str


class RouteSummary(BaseModel):
    minutes: int
    distance_m: int


class HazardReport(BaseModel):
    checked: bool = Field(description="위험 구역을 확인했는지. safety 장애·ignore 면 false")
    on_route: list[ZoneHit] = Field(description="돌려준 경로가 지나가는 위험 구역")
    avoided: list[ZoneHit] = Field(description="우회한 위험 구역 (hazards=avoid 이고 우회했을 때)")
    baseline: RouteSummary | None = Field(None, description="우회하지 않은 원래 경로 (우회했을 때만)")
    detour_minutes: int | None = Field(None, description="우회로 늘어난 시간")
    note: str | None = None


class RouteResult(BaseModel):
    mode: Mode
    source: Source
    minutes: int
    distance_m: int
    geometry: LineString
    legs: list[RouteLeg]
    hazards: HazardReport
    engine: EngineStatus
