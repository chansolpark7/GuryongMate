"""구룡포 안전 정보 API. 디지털트윈(E-UM) 실측과 기상청·해양조사원 예보를 해석해 위험도로 돌려준다.

- GET  /safety/status          지금 구역별 위험도 (지도·현재 상황)
- GET  /safety/outlook?date=   특정 날짜 전망 (계획 단계에서 '그날 어때?')
- POST /safety/assess          일정 항목(장소+시각)별 위험도와 행동 지침 (계획 Agent 용)
- GET  /safety/zones           방파제·침수 감시 구역 위치 (지도 표시용)

여행일까지 많이 남았으면 날씨를 판단하지 않고 risk=unknown 과 '바꾸지 말라'는 지침을 돌려준다 (risk.py).
"""
from datetime import date, datetime, time
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.safety import geo
from app.safety.config import settings
from app.safety.places import PlaceLookup, get_place_lookup
from app.safety.schemas import AssessRequest, AssessResult, Outlook, Status, ZoneView
from app.safety.service import SafetyService
from app.safety.sources import Sources, build_sources

router = APIRouter(prefix="/safety", tags=["safety"])


@lru_cache
def _sources() -> Sources:
    return build_sources(settings)


def get_sources() -> Sources:
    """FastAPI 의존성. 테스트에서는 dependency_overrides 로 가짜 소스를 넣는다."""
    return _sources()


def get_service(sources: Annotated[Sources, Depends(get_sources)],
                places: Annotated[PlaceLookup, Depends(get_place_lookup)]) -> SafetyService:
    return SafetyService(sources, places)


Service = Annotated[SafetyService, Depends(get_service)]
NowParam = Annotated[datetime | None, Query(description="기준 시각 덮어쓰기 (테스트·시연용, 예: 2026-10-10T14:00:00+09:00)")]


@router.get("/status", response_model=Status)
def status(svc: Service, now: NowParam = None) -> Status:
    return svc.status(now)


@router.get("/outlook", response_model=Outlook)
def outlook(
    svc: Service,
    date_: Annotated[date, Query(alias="date", description="조회할 날짜 YYYY-MM-DD")],
    start: Annotated[time, Query(description="볼 시간대 시작")] = time(9),
    end: Annotated[time, Query(description="볼 시간대 끝")] = time(18),
    detail: Annotated[bool, Query(description="시간별 예보 포함")] = False,
    now: NowParam = None,
) -> Outlook:
    if end <= start:
        raise HTTPException(422, "end 는 start 보다 뒤여야 합니다.")
    return svc.outlook(date_, now, start, end, detail)


@router.post("/assess", response_model=AssessResult)
def assess(body: AssessRequest, svc: Service) -> AssessResult:
    return svc.assess(body)


@router.get("/zones", response_model=list[ZoneView])
def zones() -> list[ZoneView]:
    return [ZoneView(id=z.id, name=z.name, kind=z.kind, path=list(z.path), radius_m=z.radius_m)
            for z in (*geo.BREAKWATERS, *geo.STATIC_FLOOD_ZONES)]
