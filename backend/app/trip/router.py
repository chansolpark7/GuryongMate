"""trip API — 여행 중 상태·이벤트. 명세: docs/trip/README.md

- 여행일: start / end / state (진행 중인 여행일이 있으면 동행 모드)
- 위치: location -> 도착·출발·지연·위험 구역 판정 -> 이벤트 (판정은 서버에서만)
- 이벤트: events?after= (앱 폴링), seen
- 앱 활동: 위치 전송·이벤트 조회. 오래 없으면 단계 3 전송을 보류하고, 돌아오면 재판정 후 최신 한 건만 보낸다 (service.resume)
- 틱: tick (백그라운드 틱을 이 계획에 지금 한 번. CLI·재생기·시연용)
오류는 {"detail": {"code", "message", ...}} 형식이다 (errors.py).
"""
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser, ForwardAuth
from app.db import SessionLocal, get_db
from app.trip import service
from app.trip.clients import Clients, get_clients
from app.trip.clock import Clock, get_clock
from app.trip.errors import TripRoute
from app.trip.schemas import (
    EventView, LocationBatch, LocationIn, LocationResult, StartRequest, TickResult, TripState,
)

router = APIRouter(prefix="/trip", tags=["trip"], route_class=TripRoute)

DbSession = Annotated[Session, Depends(get_db)]
Deps = Annotated[Clients, Depends(get_clients)]
Now = Annotated[Clock, Depends(get_clock)]


def _send_queued(background: BackgroundTasks, state: TripState, events: list[EventView], user_id: str,
                 clients: Clients, clock: Clock) -> None:
    """단계 3 이벤트가 생겼으면 응답 뒤에 Agent 선제 턴을 보낸다 (LLM 응답을 기다리지 않게)."""
    if any(e.status == "queued" for e in events):
        background.add_task(_dispatch_plan, user_id, state.plan_id, clients, clock)


def _dispatch_plan(user_id: str, plan_id: int, clients: Clients, clock: Clock, now: datetime | None = None) -> None:
    with SessionLocal() as db:
        td = service.latest_trip(db, user_id, plan_id)
        trip_id = td.id if td is not None else None
    if trip_id is not None:
        service.dispatch(trip_id, clients, clock, now)


@router.post("/plans/{plan_id}/start", response_model=TripState)
def start_trip(plan_id: int, db: DbSession, user_id: CurrentUser, auth: ForwardAuth, clients: Deps, clock: Now,
               background: BackgroundTasks, body: Annotated[StartRequest | None, Body()] = None) -> TripState:
    """여행일 시작 (오늘이 계획 기간 안이어야 한다. 아니면 409 not_trip_day). 같은 날 다시 부르면 그대로 돌려준다.
    응답 뒤 아직 노트가 없는 장소의 가이드 준비를 agent 에 요청한다 (실패해도 시작에는 영향 없음)."""
    state = service.start(db, user_id, plan_id, (body or StartRequest()).session_id, auth, clients, clock)
    background.add_task(service.prepare_guide, clients, user_id, plan_id)
    return state


@router.post("/plans/{plan_id}/end", response_model=TripState)
def end_trip(plan_id: int, db: DbSession, user_id: CurrentUser, auth: ForwardAuth, clients: Deps,
             clock: Now) -> TripState:
    return service.end(db, user_id, plan_id, auth, clients, clock)


@router.get("/plans/{plan_id}/state", response_model=TripState)
def get_state(plan_id: int, db: DbSession, user_id: CurrentUser, auth: ForwardAuth, clients: Deps,
              clock: Now) -> TripState:
    """지금 상태. active=false 면 계획 모드 (여행일을 시작하지 않았거나 끝남)."""
    return service.get_state(db, user_id, plan_id, auth, clients, clock)


@router.post("/plans/{plan_id}/location", response_model=LocationResult)
def post_location(plan_id: int, body: Annotated[LocationIn | LocationBatch, Body()], db: DbSession,
                  user_id: CurrentUser, auth: ForwardAuth, clients: Deps, clock: Now,
                  background: BackgroundTasks) -> LocationResult:
    """위치 한 점 또는 {"points": [...]} (최대 100, 오래된 순). 진행 중인 여행일이 없으면 409 trip_not_active."""
    points = body.points if isinstance(body, LocationBatch) else [body]
    result, back = service.record_location(db, user_id, plan_id, points, auth, clients, clock)
    if back:  # 앱 활동이 돌아옴 -> 재판정 후 보류된 것 중 최신 한 건만 (응답 뒤)
        background.add_task(service.resume, service.latest_trip(db, user_id, plan_id).id, clients, clock)
    else:
        _send_queued(background, result.state, result.events, user_id, clients, clock)
    # 도착한 장소에 해설이 없으면 지금이라도 준비 (노트가 있거나 준비 중·실패면 agent 가 건너뛴다)
    arrived = list(dict.fromkeys(e.payload["place_id"] for e in result.events if e.type == "arrived"))
    if arrived:
        background.add_task(service.prepare_guide, clients, user_id, plan_id, arrived)
    return result


@router.get("/plans/{plan_id}/events", response_model=list[EventView])
def list_events(plan_id: int, db: DbSession, user_id: CurrentUser, clients: Deps, clock: Now,
                background: BackgroundTasks,
                after: Annotated[int, Query(ge=0, description="이 id 이후 이벤트 (오래된 순)")] = 0,
                limit: Annotated[int, Query(ge=1, le=200)] = 50) -> list[EventView]:
    """이벤트 조회는 앱 활동이다 (위치 전송과 함께). 활동이 오래 없다가 돌아오면 응답 뒤 재판정·전송 (docs/trip/README.md §4.1)."""
    trip_id = service.touch(db, user_id, plan_id, clock)
    if trip_id is not None:
        background.add_task(service.resume, trip_id, clients, clock)
    return service.list_events(db, user_id, plan_id, after, limit)


@router.post("/plans/{plan_id}/events/{event_id}/seen", response_model=EventView)
def mark_seen(plan_id: int, event_id: int, db: DbSession, user_id: CurrentUser, clock: Now) -> EventView:
    return service.mark_seen(db, user_id, plan_id, event_id, clock)


@router.post("/plans/{plan_id}/tick", response_model=TickResult)
def tick(plan_id: int, db: DbSession, user_id: CurrentUser, clients: Deps, clock: Now, background: BackgroundTasks,
         now: Annotated[datetime | None, Query(description="판정 시각 (생략하면 trip 의 지금). 재생기가 재생 시각을 넘긴다")] = None,
         ) -> TickResult:
    """백그라운드 틱(지연 재판정·안전 경고 수집·자동 종료)을 이 계획에 지금 한 번 돌리고, 큐에 있는 선제 턴을 보낸다.
    틱 주기를 기다리지 않고 확인할 때 쓴다 (CLI·재생기). 진행 중인 여행일이 없으면 409 trip_not_active."""
    events = service.tick_plan(db, user_id, plan_id, clients, clock, now)
    state = service.get_state(db, user_id, plan_id, {}, clients, clock)
    td = service.latest_trip(db, user_id, plan_id)
    if td is not None:  # 관심사가 바뀌어 stale 이 된 남은 장소 노트를 도착 전에 다시 준비 (앱 활동 중일 때만)
        background.add_task(service.refresh_guides, td.id, clients, clock, now)
    background.add_task(_dispatch_plan, user_id, plan_id, clients, clock, now)
    return TickResult(state=state, events=events)


@router.delete("/me", status_code=204)
def delete_my_data(db: DbSession, user_id: CurrentUser) -> Response:
    """이 사용자의 여행 상태·이벤트를 모두 지운다. account 탈퇴가 부른다."""
    service.delete_user(db, user_id)
    return Response(status_code=204)
