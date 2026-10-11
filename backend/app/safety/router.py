"""구룡포 안전 정보 API. 디지털트윈(E-UM) 실측과 기상청·해양조사원 예보를 해석해 위험도로 돌려준다.

- GET  /safety/status          지금 구역별 위험도 (지도·현재 상황)
- GET  /safety/outlook?date=   특정 날짜 전망 (계획 단계에서 '그날 어때?')
- POST /safety/assess          일정 항목(장소+시각)별 위험도와 행동 지침 (계획 Agent 용)
- GET  /safety/zones           방파제·침수 감시 구역 위치 (지도 표시용)
- GET  /safety/situation       지금 상황 요약 + fingerprint (planner 가 바뀌었을 때만 일정을 다시 점검)
- GET/PUT /safety/sim          가상 상황 (SAFETY_SOURCE=mock 또는 SAFETY_SIM_ENABLED=true): 날씨 시간표·가상 시각,
                               실제(live) <-> 가상(mock) 전환 (SAFETY_SIM_ENABLED 일 때)
- GET  /safety/avoid           지금 길찾기에서 피할 구역 다각형 (routing 서비스가 우회에 사용)

위험 단계는 safe / caution / danger 3단계. 여행일까지 많이 남았으면 판단하지 않고(risk=null) '바꾸지 말라'는 지침을
돌려준다 (risk.py).
"""
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.safety import geo
from app.safety.config import settings
from app.safety.places import PlaceLookup, get_place_lookup
from app.safety.schemas import (
    AssessRequest, AssessResult, AvoidResult, Outlook, SimSegmentView, SimState, SimUpdate, Situation, Status, ZoneView,
)
from app.safety.service import SafetyService
from app.safety.sources import (
    KST, MOCK_SCENARIOS, SIM_KINDS, MockSources, SimSegment, SimSources, Sources, build_sources, check_timeline,
    normalize_timeline, preset_timeline,
)

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
        raise HTTPException(422, {"code": "invalid_request", "message": "end 는 start 보다 뒤여야 합니다."})
    return svc.outlook(date_, now, start, end, detail)


@router.post("/assess", response_model=AssessResult)
def assess(body: AssessRequest, svc: Service) -> AssessResult:
    return svc.assess(body)


@router.get("/avoid", response_model=AvoidResult)
def avoid(
    svc: Service,
    min_risk: Annotated[Literal["caution", "danger"], Query(description="이 등급 이상인 구역을 피한다")] = "caution",
    now: NowParam = None,
) -> AvoidResult:
    return svc.avoid(now, min_risk)


@router.get("/zones", response_model=list[ZoneView])
def zones() -> list[ZoneView]:
    return [ZoneView(id=z.id, name=z.name, kind=z.kind, path=list(z.path), radius_m=z.radius_m)
            for z in (*geo.BREAKWATERS, *geo.STATIC_FLOOD_ZONES)]


@router.get("/situation", response_model=Situation)
def situation(svc: Service, now: NowParam = None) -> Situation:
    """지금 상황 요약. fingerprint 가 바뀌면 일정을 다시 점검한다 (planner 주기 점검 기준).
    같아도 시간이 흘러 실측(센서·CCTV)이 적용되는 3시간 안으로 들어온 방문은 판단이 달라질 수 있어,
    이용자는 일정 주기로도 다시 점검해야 한다."""
    return svc.situation(now)


# ---------- 가상 상황 (mock, 또는 실제 <-> 가상 전환) ----------

def _sim(sources: Sources) -> MockSources | SimSources:
    if not isinstance(sources, (MockSources, SimSources)):
        raise HTTPException(404, {"code": "sim_disabled",
                                  "message": "가상 상황은 SAFETY_SOURCE=mock 또는 SAFETY_SIM_ENABLED=true 일 때만 쓸 수 있습니다."})
    return sources


def _seg(seg: SimSegment) -> SimSegmentView:
    return SimSegmentView(kind=seg.kind, start=seg.start, end=seg.end)


def _kst(t: datetime) -> datetime:
    return t.replace(tzinfo=KST) if t.tzinfo is None else t.astimezone(KST)


def _sim_state(src: MockSources | SimSources) -> SimState:
    switchable = isinstance(src, SimSources)
    now = src.now()
    seg = src.segment_at(now)
    return SimState(mode=src.mode if switchable else "sim", scenario=seg.kind if seg else "calm",
                    switchable=switchable, now=now, offset_minutes=round(src.offset.total_seconds() / 60),
                    scenarios=MOCK_SCENARIOS, timeline=[_seg(x) for x in src.timeline],
                    segment=_seg(seg) if seg else None, kinds=SIM_KINDS)


@router.get("/sim", response_model=SimState)
def get_sim(sources: Annotated[Sources, Depends(get_sources)]) -> SimState:
    return _sim_state(_sim(sources))


def _bad(code: str, message: str, **extra) -> HTTPException:
    return HTTPException(422, {"code": code, "message": message, **extra})


@router.put("/sim", response_model=SimState)
def put_sim(body: SimUpdate, sources: Annotated[Sources, Depends(get_sources)]) -> SimState:
    """가상 시각·날씨 시간표·데이터 모드를 바꾼다 (서비스를 다시 띄우면 초기화).
    - 가상 시각은 설정한 뒤 실제 시간만큼 흘러가고 live·sim 모두에 쓴다.
    - 저장하기 전에 시간표를 정리한다 (normalize_timeline: 겹침은 센 종류가 차지, 같은 종류는 합침). GET 은 정리된 시간표.
    - timeline: 시간표 전체 교체. scenario: 시간표를 프리셋으로 교체 (설정한 가상 시각 기준 절대 구간, until 이면 그 시각에 끝).
    - scenario 를 주면 가상 상황(mock)으로 바뀐다 (source 를 같이 주면 source 를 따른다). timeline 은 모드를 바꾸지 않는다."""
    src = _sim(sources)
    if body.scenario is not None and body.scenario not in MOCK_SCENARIOS:
        raise _bad("unknown_scenario", f"알 수 없는 시나리오: {body.scenario}", scenarios=sorted(MOCK_SCENARIOS))
    if body.until is not None and body.scenario is None:
        raise _bad("invalid_request", "until 은 scenario 와 함께만 씁니다 (구간 끝은 timeline 의 end 로).")
    if body.timeline is not None and body.scenario is not None:
        raise _bad("invalid_request", "scenario(프리셋)와 timeline 은 함께 쓰지 않습니다.")
    source = body.source or ("mock" if body.scenario is not None else None)
    if source == "live" and not isinstance(src, SimSources):
        raise HTTPException(409, {"code": "switch_disabled",
                                  "message": "실제 데이터로 바꾸려면 SAFETY_SIM_ENABLED=true 로 띄워야 합니다."})
    offset = src.offset
    if body.reset_clock:
        offset = timedelta(0)
    if body.now is not None:
        offset = _kst(body.now) - src._clock()
    now = src._clock() + offset  # 바뀐 가상 시각. 프리셋은 이 시각 기준이다

    timeline = None
    if body.timeline is not None:
        if any(x.kind not in SIM_KINDS for x in body.timeline):
            raise _bad("unknown_kind", "알 수 없는 구간 종류가 있습니다.", kinds=sorted(SIM_KINDS))
        try:
            timeline = check_timeline([SimSegment(x.kind, _kst(x.start), _kst(x.end)) for x in body.timeline])
        except ValueError as e:
            raise _bad("invalid_timeline", str(e)) from None
    elif body.scenario is not None:
        try:
            timeline = normalize_timeline(preset_timeline(body.scenario, now, body.until and _kst(body.until)))
        except ValueError as e:
            raise _bad("invalid_until", str(e)) from None

    if isinstance(src, SimSources):
        src.set(offset, timeline, {"live": "live", "mock": "sim"}.get(source))
    else:
        src.set(offset, timeline)
    return _sim_state(src)
