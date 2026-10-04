"""길찾기 API. OSM 포항 데이터로 직접 띄운 Valhalla 를 감싼다 (외부 지도 API 를 쓰지 않는다).

- POST /routing/legs     구간 목록의 이동시간·거리 (planner 일정 계산용, 한 번에 최대 300구간)
- POST /routing/matrix   출발지 × 도착지 이동시간 행렬 (추천·순서 비교용)
- POST /routing/route    경유지를 잇는 경로선 + 한국어 회전 안내 + 위험 구역 우회 (앱 지도·Agent 용)
- GET  /routing/status   길찾기 엔진 상태

엔진이 없거나 길을 못 찾은 값은 직선거리 근사로 채우고 source="estimate" 로 표시한다. 명세: docs/routing/README.md
"""
from typing import Annotated

from fastapi import APIRouter, Depends

from app.routing.hazards import HazardLookup, get_hazard_lookup
from app.routing.schemas import LegsRequest, LegsResult, MatrixRequest, MatrixResult, RouteRequest, RouteResult
from app.routing.service import RoutingService
from app.routing.valhalla import ValhallaClient, client

router = APIRouter(prefix="/routing", tags=["routing"])


def get_engine() -> ValhallaClient:
    """FastAPI 의존성. 테스트에서는 dependency_overrides 로 가짜 엔진을 넣는다."""
    return client


def get_service(engine: Annotated[ValhallaClient, Depends(get_engine)],
                hazards: Annotated[HazardLookup, Depends(get_hazard_lookup)]) -> RoutingService:
    return RoutingService(engine, hazards)


Service = Annotated[RoutingService, Depends(get_service)]


@router.post("/legs", response_model=LegsResult)
def legs(body: LegsRequest, svc: Service) -> LegsResult:
    return svc.legs([(leg.from_, leg.to) for leg in body.legs], body.mode, body)


@router.post("/matrix", response_model=MatrixResult)
def matrix(body: MatrixRequest, svc: Service) -> MatrixResult:
    return svc.matrix(body)


@router.post("/route", response_model=RouteResult)
def route(body: RouteRequest, svc: Service) -> RouteResult:
    return svc.route(body)


@router.get("/status")
def status(svc: Service) -> dict:
    return svc.status()
