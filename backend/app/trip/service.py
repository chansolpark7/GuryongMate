"""trip 처리 흐름: 여행일 시작·종료, 위치 → 판정 → 이벤트, 백그라운드 틱, 단계 3 큐 전송.
판정은 judge.py, 이벤트 정책은 policy.py (순수 함수). 여기서는 DB·다른 서비스 호출만 엮는다.
"""
import logging
import threading
import time as _time
from datetime import date, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.auth import issue_token
from app.db import SessionLocal
from app.trip import judge, policy
from app.trip.clients import Clients, UpstreamError
from app.trip.clock import KST, Clock
from app.trip.config import settings
from app.trip.errors import TripError
from app.trip.models import TripDay, TripEvent
from app.trip.schemas import Current, EventView, Location, LocationIn, LocationResult, Next, TripState

log = logging.getLogger("uvicorn.error")
BACKGROUND_TOKEN_TTL = timedelta(minutes=5)


def background_auth(user_id: str) -> dict[str, str]:
    """사용자 요청 없이(틱·큐 전송) 다른 서비스를 부를 때: 그 사용자의 짧은 토큰 (모든 서비스가 같은 JWT_SECRET)."""
    return {"Authorization": f"Bearer {issue_token(user_id, ttl=BACKGROUND_TOKEN_TTL)}"}


def track_config() -> judge.TrackConfig:
    return judge.TrackConfig(arrive_radius_m=settings.arrive_radius_m, max_radius_m=settings.max_radius_m,
                             arrive_dwell_sec=settings.arrive_dwell_sec, depart_dwell_sec=settings.depart_dwell_sec,
                             max_accuracy_m=settings.max_accuracy_m)


def _kst(t: datetime | None) -> datetime | None:
    if t is None:
        return None
    return (t if t.tzinfo else t.replace(tzinfo=KST)).astimezone(KST)


def _planner_error(e: UpstreamError) -> TripError:
    if e.status == 404:
        return TripError(404, e.code if e.code.endswith("_not_found") else "plan_not_found", "계획을 찾을 수 없습니다.")
    return TripError(503, "planner_unavailable", "일정 서비스에 접근할 수 없습니다.", reason=str(e)[:200])


# ---------- 보기 ----------

def state_view(td: TripDay | None, plan_id: int, clock: Clock, at_least: datetime | None = None) -> TripState:
    """at_least: 지금이 이 시각보다 이르면 이 시각으로 본다 (선제 턴: 이벤트 판정 시각. 가상 시각을 크게 옮긴 직후
    trip 의 시계가 캐시 때문에 몇 초 늦을 때도 Agent 가 이벤트보다 이른 '지금'을 보지 않게)."""
    now = clock.now()
    if at_least is not None and at_least > now:
        now = at_least
    if td is None:
        return TripState(plan_id=plan_id, active=False, day=None, date=None, started_at=None, ended_at=None,
                         ended_reason=None, now=now, clock=clock.kind, location=None, current=None, next=None,
                         delay_minutes=None, session_id=None)
    loc = None
    if td.lat is not None and td.recorded_at is not None:
        loc = Location(lat=td.lat, lng=td.lng, accuracy=td.accuracy, recorded_at=_kst(td.recorded_at),
                       source=td.source or "app")
    since = _kst(td.current_since)
    return TripState(
        plan_id=td.plan_id, active=td.ended_at is None, day=td.day, date=td.date, started_at=_kst(td.started_at),
        ended_at=_kst(td.ended_at), ended_reason=td.ended_reason, now=now, clock=clock.kind, location=loc,
        current=Current(item_id=td.current_item_id, state=td.current_state, since=judge.hhmm(since) if since else None),
        next=Next(**td.next) if td.next else None, delay_minutes=td.delay_minutes, session_id=td.session_id)


def event_view(e: TripEvent) -> EventView:
    return EventView(id=e.id, plan_id=e.plan_id, day=e.day, type=e.type, level=e.level, severity=e.severity,
                     created_at=e.created_at, now=_kst(e.now), item_ids=e.item_ids, message=e.message,
                     payload=e.payload, status=e.status, agent=e.agent, card=e.card, seen_at=e.seen_at,
                     attempts=e.attempts, last_error=e.last_error)


# ---------- 조회 ----------

def latest_trip(db: Session, user_id: str, plan_id: int, lock: bool = False) -> TripDay | None:
    """진행 중인 여행일, 없으면 가장 최근 여행일."""
    q = (select(TripDay).where(TripDay.user_id == user_id, TripDay.plan_id == plan_id)
         .order_by(TripDay.ended_at.is_(None).desc(), TripDay.started_at.desc(), TripDay.id.desc()).limit(1))
    if lock:
        q = q.with_for_update()
    return db.scalars(q).first()


def active_trip(db: Session, user_id: str, plan_id: int) -> TripDay:
    td = latest_trip(db, user_id, plan_id, lock=True)
    if td is None or td.ended_at is not None:
        raise TripError(409, "trip_not_active", "진행 중인 여행일이 없습니다. 먼저 여행을 시작하세요.")
    return td


def _ensure_plan(clients: Clients, plan_id: int, auth: dict) -> dict:
    try:
        return clients.planner.get_plan(plan_id, auth)
    except UpstreamError as e:
        raise _planner_error(e) from e


def get_state(db: Session, user_id: str, plan_id: int, auth: dict, clients: Clients, clock: Clock) -> TripState:
    td = latest_trip(db, user_id, plan_id)
    if td is None:
        _ensure_plan(clients, plan_id, auth)  # 계획이 없거나 남의 것이면 404
    return state_view(td, plan_id, clock)


def list_events(db: Session, user_id: str, plan_id: int, after: int, limit: int) -> list[EventView]:
    q = (select(TripEvent).where(TripEvent.user_id == user_id, TripEvent.plan_id == plan_id, TripEvent.id > after)
         .order_by(TripEvent.id).limit(limit))
    return [event_view(e) for e in db.scalars(q)]


def mark_seen(db: Session, user_id: str, plan_id: int, event_id: int, clock: Clock) -> EventView:
    e = db.get(TripEvent, event_id)
    if e is None or e.user_id != user_id or e.plan_id != plan_id:
        raise TripError(404, "event_not_found", f"이벤트 {event_id} 을(를) 찾을 수 없습니다.")
    if e.seen_at is None:
        e.seen_at = clock.now()
    if e.status == "delivered":
        e.status = "handled"
    db.commit()
    return event_view(e)


# ---------- 이벤트 기록 ----------

def _known(db: Session, td: TripDay) -> set[str]:
    return policy.known_causes(list(db.scalars(select(TripEvent.cause_key).where(TripEvent.trip_day_id == td.id))))


def _record(db: Session, td: TripDay, dets: list[judge.Detection], known: set[str]) -> list[TripEvent]:
    out = []
    for d in policy.fresh(dets, known):
        e = TripEvent(trip_day_id=td.id, plan_id=td.plan_id, user_id=td.user_id, day=td.day, type=d.type,
                      level=d.level, severity=d.severity, now=d.at, item_ids=d.item_ids, message=d.message,
                      payload=d.payload, status=policy.initial_status(d), cause_key=d.cause_key, card=d.card)
        db.add(e)
        out.append(e)
    db.flush()
    return out


def _mark(td: TripDay, kind: str, now: datetime) -> judge.Detection:
    text = {"day_started": f"{td.day}일차 여행을 시작했어요.", "day_ended": f"{td.day}일차 여행을 마쳤어요."}[kind]
    return judge.Detection(kind, now, message=text, cause_key=f"{kind}:{now.isoformat()}",
                           payload={"reason": td.ended_reason} if kind == "day_ended" else {})


def _finish(db: Session, td: TripDay, reason: str, now: datetime) -> list[TripEvent]:
    """여행일 종료. 아직 못 보낸 단계 3은 더 이상 의미가 없어 suppressed (단계 1~3은 여행 중에만)."""
    td.ended_at, td.ended_reason = now, reason
    db.execute(update(TripEvent).where(TripEvent.trip_day_id == td.id, TripEvent.status == "queued")
               .values(status="suppressed"))
    return _record(db, td, [_mark(td, "day_ended", now)], set())


# ---------- 시작·종료 ----------

def _resolve_session(clients: Clients, plan_id: int, auth: dict) -> str | None:
    """그 계획의 가장 최근 Agent 세션, 없으면 새로 만든다. agent 에 접근할 수 없으면 None (큐 전송 때 다시)."""
    try:
        return clients.agent.latest_session(plan_id, auth) or clients.agent.create_session(plan_id, auth)
    except UpstreamError as e:
        log.warning("trip: agent 세션을 정하지 못함 (계획 %s): %s", plan_id, e)
        return None


def start(db: Session, user_id: str, plan_id: int, session_id: str | None, auth: dict, clients: Clients,
          clock: Clock) -> TripState:
    plan = _ensure_plan(clients, plan_id, auth)
    now = clock.now()
    today = now.date()
    start_date, end_date = date.fromisoformat(plan["start_date"]), date.fromisoformat(plan["end_date"])
    if not start_date <= today <= end_date:
        raise TripError(409, "not_trip_day", f"오늘({today})은 여행 기간({start_date} ~ {end_date})이 아닙니다.",
                        start_date=str(start_date), end_date=str(end_date), today=str(today))
    day = (today - start_date).days + 1

    current = latest_trip(db, user_id, plan_id, lock=True)
    if current is not None and current.ended_at is None:
        if current.date == today:  # 멱등
            if session_id and session_id != current.session_id:
                current.session_id = session_id
                db.commit()
            return state_view(current, plan_id, clock)
        _finish(db, current, "auto", now)  # 다른 날 여행일이 진행 중 -> 끝내고 새로 시작

    td = db.scalars(select(TripDay).where(TripDay.user_id == user_id, TripDay.plan_id == plan_id,
                                          TripDay.date == today).order_by(TripDay.id.desc())).first()
    if td is None:
        td = TripDay(plan_id=plan_id, user_id=user_id, day=day, date=today, started_at=now)
        db.add(td)
        # planner 에 이미 기록된 진행 상태(머무는 곳)에서 시작한다
        spots = judge.spots_from_plan(plan, day)
        items = {i["id"]: i for d in plan["days"] for i in d["items"]}
        arrived = {s.item_id: datetime.combine(today, judge.time_of(items[s.item_id].get("arrived_at")), tzinfo=KST)
                   for s in spots if items[s.item_id].get("arrived_at")}
        _save_track(td, judge.track_from_spots(spots, arrived))
    else:  # 같은 날 종료했던 여행일을 다시 연다 (이미 알린 원인은 그대로 기억)
        td.ended_at, td.ended_reason, td.started_at = None, None, now
    td.session_id = session_id or td.session_id or _resolve_session(clients, plan_id, auth)
    db.flush()
    _record(db, td, [_mark(td, "day_started", now)], set())
    db.commit()
    return state_view(td, plan_id, clock)


def end(db: Session, user_id: str, plan_id: int, auth: dict, clients: Clients, clock: Clock) -> TripState:
    td = latest_trip(db, user_id, plan_id, lock=True)
    if td is None:
        _ensure_plan(clients, plan_id, auth)
        return state_view(None, plan_id, clock)
    if td.ended_at is None:
        _finish(db, td, "user", clock.now())
        db.commit()
    return state_view(td, plan_id, clock)


def delete_user(db: Session, user_id: str) -> None:
    for td in db.scalars(select(TripDay).where(TripDay.user_id == user_id)):
        db.delete(td)  # 이벤트는 ON DELETE CASCADE
    db.commit()


# ---------- 판정 상태 저장 ----------

def _load_track(td: TripDay) -> judge.Track:
    return judge.Track(state=td.current_state, item_id=td.current_item_id, since=_kst(td.current_since),
                       cand_item_id=td.cand_item_id, cand_since=_kst(td.cand_since),
                       outside_since=_kst(td.outside_since), last_at=_kst(td.recorded_at))


def _save_track(td: TripDay, t: judge.Track) -> None:
    td.current_state, td.current_item_id, td.current_since = t.state, t.item_id, t.since
    td.cand_item_id, td.cand_since, td.outside_since = t.cand_item_id, t.cand_since, t.outside_since


# ---------- 재계산 (planner projection) ----------

def _project(clients: Clients, td: TripDay, track: judge.Track, now: datetime, auth: dict) -> dict | None:
    """지금 위치·시각 기준으로 그날 남은 일정을 다시 계산. 실패해도 판정을 멈추지 않는다 (None)."""
    if now.date() != td.date:
        return None
    body: dict = {"day": td.day, "now": judge.hhmm(now)}
    if track.state == "visiting" and track.item_id is not None:
        body["position"] = {"state": "visiting", "item_id": track.item_id}
        if track.since is not None:
            body["position"]["arrived_at"] = judge.hhmm(track.since)
    elif track.state == "moving" and td.lat is not None:
        body["position"] = {"state": "moving", "lat": td.lat, "lng": td.lng}
    try:
        return clients.planner.projection(td.plan_id, body, auth)
    except UpstreamError as e:
        log.warning("trip: 재계산 실패 (계획 %s): %s", td.plan_id, e)
        return None


def _apply_projection(td: TripDay, proj: dict) -> None:
    td.delay_minutes = proj.get("delay_minutes")
    titles = {i["id"]: (i.get("place") or {}).get("title") or i["place_id"]
              for d in proj["plan"]["days"] for i in d["items"]}
    nxt = proj.get("next")
    td.next = None if not nxt else {"item_id": nxt["item_id"], "title": titles.get(nxt["item_id"]),
                                    "planned_start": nxt.get("planned_start"), "eta": nxt.get("eta")}


class HazardCache:
    """safety /avoid 구역. 상황(fingerprint)이 바뀔 때만 다시 받는다. ttl 동안은 상황도 다시 묻지 않는다."""

    def __init__(self, ttl_sec: float):
        self.ttl = ttl_sec
        self._fp: str | None = None
        self._zones: list[dict] = []
        self._until = 0.0
        self._lock = threading.Lock()

    def zones(self, clients: Clients) -> list[dict]:
        with self._lock:
            if _time.monotonic() < self._until:
                return self._zones
            try:
                fp = clients.safety.situation().get("fingerprint")
                if fp != self._fp or fp is None:
                    self._zones = clients.safety.avoid().get("zones", [])
                    self._fp = fp
            except UpstreamError as e:
                log.warning("trip: 위험 구역을 받지 못함: %s", e)  # 지난 구역을 계속 쓴다
            self._until = _time.monotonic() + self.ttl
            return self._zones

    def reset(self) -> None:
        with self._lock:
            self._fp, self._zones, self._until = None, [], 0.0


hazards = HazardCache(settings.hazard_cache_sec)


# ---------- 위치 ----------

def record_location(db: Session, user_id: str, plan_id: int, points: list[LocationIn], auth: dict, clients: Clients,
                    clock: Clock) -> LocationResult:
    td = active_trip(db, user_id, plan_id)
    plan = _ensure_plan(clients, plan_id, auth)
    spots = judge.spots_from_plan(plan, td.day)
    cfg = track_config()
    track = _load_track(td)
    dets: list[judge.Detection] = []
    accepted = ignored = 0
    last: judge.Point | None = None

    for p in points:
        pt = judge.Point(p.lat, p.lng, settings.default_accuracy_m if p.accuracy is None else p.accuracy,
                         _kst(p.recorded_at) or clock.now(), p.source)
        if judge.ignore_reason(track, pt, cfg):
            ignored += 1
            continue
        track, found = judge.step(track, spots, pt, cfg)
        for d in found:  # 도착·출발은 planner 진행 상태로 (version 은 그대로, progress_version 만 오른다)
            status = "visiting" if d.type == "arrived" else "done"
            try:
                clients.planner.progress(plan_id, d.item_ids[0], status, judge.hhmm(d.at), auth)
            except UpstreamError as e:
                if e.status not in (404, 409, 422):  # 그 사이 항목이 빠졌거나 건너뜀 -> 기록만 못 함
                    raise TripError(503, "planner_unavailable", "일정 서비스에 접근할 수 없습니다.",
                                    reason=str(e)[:200]) from e
                log.warning("trip: 진행 상태 반영 실패 (계획 %s 항목 %s): %s", plan_id, d.item_ids[0], e)
        spots = judge.apply_progress(spots, found)
        dets += found
        accepted += 1
        last = pt

    _save_track(td, track)
    known = _known(db, td)
    if last is not None:
        td.lat, td.lng, td.accuracy, td.recorded_at, td.source = (last.lat, last.lng, last.accuracy,
                                                                  last.recorded_at, last.source)
        proj = _project(clients, td, track, last.recorded_at, auth)
        if proj is not None:
            _apply_projection(td, proj)
            d = judge.judge_delay(proj, last.recorded_at, known)
            if d:
                dets.append(d)
        by_id = {s.item_id: s for s in spots}
        for d in dets:
            if d.type == "departed":
                nxt = td.next
                target = by_id.get(nxt["item_id"]) if nxt else None
                d.message = judge.departed_message(d.payload["title"], nxt, target.title if target else None,
                                                   target.leg_mode if target else None, d.at)
        hz = judge.judge_hazard(last.lat, last.lng, last.recorded_at, hazards.zones(clients), settings.hazard_near_m)
        if hz:
            dets.append(hz)

    events = _record(db, td, dets, known)
    views = [event_view(e) for e in events]
    db.commit()
    return LocationResult(state=state_view(td, plan_id, clock), events=views, accepted=accepted, ignored=ignored)


# ---------- 백그라운드 틱 (§4.3) ----------

def tick_trip(db: Session, td: TripDay, clients: Clients, clock: Clock, now: datetime | None = None) -> list[EventView]:
    """진행 중인 여행일 하나: 지연 재판정 → 안전 경고 수집 → 자동 종료. (큐 전송은 dispatch)
    now: 판정 시각 (생략하면 trip 의 지금. 재생기는 재생 시각을 넘긴다)."""
    if td.ended_at is not None:
        return []
    hazards.reset()  # 틱마다 위험 구역도 새로 본다 (상황이 바뀐 직후 재생에서도 바로 반영)
    auth = background_auth(td.user_id)
    now = _kst(now) or clock.now()
    known = _known(db, td)
    dets: list[judge.Detection] = []
    try:
        plan = clients.planner.get_plan(td.plan_id, auth)
    except UpstreamError as e:
        log.warning("trip 틱: 계획 %s 을 받지 못함: %s", td.plan_id, e)
        return []
    spots = judge.spots_from_plan(plan, td.day)

    # 1. 위치가 안 들어와도 시간이 흐르면 지연이 생긴다
    proj = _project(clients, td, _load_track(td), now, auth)
    if proj is not None:
        _apply_projection(td, proj)
        d = judge.judge_delay(proj, now, known)
        if d:
            dets.append(d)

    # 2. planner 안전 경고 (오늘 남은 항목에 해당하는 것만 가져가고 seen 처리)
    try:
        alerts = clients.planner.safety_alerts(td.plan_id, auth)
    except UpstreamError as e:
        log.warning("trip 틱: 안전 경고를 받지 못함 (계획 %s): %s", td.plan_id, e)
        alerts = []
    remaining = judge.remaining_ids(spots)
    for a in sorted(alerts, key=lambda a: a["id"]):
        found = judge.judge_alert(a, td.date, remaining, now)
        if not found:
            continue
        dets += found
        try:
            clients.planner.alert_seen(td.plan_id, a["id"], auth)
        except UpstreamError as e:
            log.warning("trip 틱: 경고 %s seen 처리 실패: %s", a["id"], e)  # 원인 키(경고 id)로 다시 알리지는 않는다

    events = _record(db, td, dets, known)

    # 3. 자동 종료 (그날 마지막 일정 종료 + TRIP_AUTO_END_MIN, 또는 자정)
    if judge.should_auto_end(now, td.date, judge.day_end_at(plan, td.day), settings.auto_end_min):
        events += _finish(db, td, "auto", now)
    views = [event_view(e) for e in events]
    db.commit()
    return views


def tick_plan(db: Session, user_id: str, plan_id: int, clients: Clients, clock: Clock,
              now: datetime | None = None) -> list[EventView]:
    return tick_trip(db, active_trip(db, user_id, plan_id), clients, clock, now)


def tick_all(clients: Clients, clock: Clock, users: list[str] | None = None) -> int:
    """모든 진행 중인 여행일을 한 번씩. 돌린 여행일 수. users: 이 사용자들만 (테스트용, None 이면 전체)."""
    q = select(TripDay.id).where(TripDay.ended_at.is_(None)).order_by(TripDay.id)
    if users is not None:
        q = q.where(TripDay.user_id.in_(users))
    with SessionLocal() as db:
        ids = list(db.scalars(q))
    for trip_id in ids:
        try:
            with SessionLocal() as db:
                td = db.get(TripDay, trip_id, with_for_update=True, populate_existing=True)
                if td is not None:
                    tick_trip(db, td, clients, clock)
            dispatch(trip_id, clients, clock)
        except Exception:
            log.exception("trip 틱 실패 (여행일 %s)", trip_id)
    return len(ids)


# ---------- 단계 3 큐 전송 (§4.1, §4.2) ----------

_sending: dict[int, threading.Lock] = {}
_sending_guard = threading.Lock()
# 실패한 이벤트를 다시 보낼 수 있는 때: 이벤트 id -> (실패 시각, 다시 보낼 시각). 프로세스 메모리에만 둔다
# (다시 시작하면 한 번 바로 보내 볼 뿐, 실패 횟수 attempts 는 DB 에 있어 상한은 그대로 지킨다)
_retry_at: dict[int, tuple[datetime, datetime]] = {}


def _waiting_retry(event_id: int, now: datetime) -> bool:
    """간격을 늘려 다시 보내는 중이라 아직 때가 아님. 가상 시각을 되돌렸으면(실패 시각보다 이름) 기다리지 않는다."""
    t = _retry_at.get(event_id)
    return t is not None and t[0] <= now < t[1]


def _fail_attempt(ev: TripEvent, error: str, now: datetime) -> None:
    """전송 실패 한 번. 상한(TRIP_PROACTIVE_MAX_ATTEMPTS)에 닿으면 failed 로 남기고(앱이 이벤트 목록에서 본다) 더 보내지 않는다.
    아니면 다음 시도까지 TRIP_PROACTIVE_RETRY_SEC 에서 실패마다 두 배로 기다린다."""
    ev.attempts += 1
    ev.last_error = error[:500]
    if ev.attempts >= settings.proactive_max_attempts:
        ev.status = "failed"
        _retry_at.pop(ev.id, None)
        log.warning("trip: 선제 턴 %s번 실패, 이벤트 %s 를 failed 로 남김: %s", ev.attempts, ev.id, error)
        return
    wait = timedelta(seconds=settings.proactive_retry_sec * 2 ** (ev.attempts - 1))
    _retry_at[ev.id] = (now, now + wait)


def _proposal_id(result: dict) -> int | None:
    for c in result.get("cards") or []:
        if c.get("type") == "proposal":
            return c.get("id") or (c.get("proposal") or {}).get("id")
    return None


def dispatch(trip_id: int, clients: Clients, clock: Clock) -> int:
    """큐에 있는 단계 3 이벤트를 하나씩 Agent 선제 턴으로 보낸다. 보낸 수.
    한 여행일에 한 번에 하나만 (다른 전송이 진행 중이면 바로 돌아간다 — 그쪽이 끝난 뒤 이어서 보낸다).
    Agent 가 응답 중(409 agent_busy)이면 queued 로 두고 다음 틱에 다시 (실패로 세지 않는다).
    접근할 수 없거나 오류면 간격을 늘려 다시 보내고, TRIP_PROACTIVE_MAX_ATTEMPTS 번 실패하면 failed (_fail_attempt)."""
    with _sending_guard:
        lock = _sending.setdefault(trip_id, threading.Lock())
    if not lock.acquire(blocking=False):
        return 0
    sent = 0
    try:
        while True:
            with SessionLocal.begin() as db:
                td = db.get(TripDay, trip_id, populate_existing=True)
                if td is None or td.ended_at is not None:
                    return sent
                rows = list(db.scalars(select(TripEvent).where(TripEvent.trip_day_id == trip_id,
                                                               TripEvent.status == "queued", TripEvent.level == 3)))
                last = db.scalars(select(TripEvent.delivered_at).where(
                    TripEvent.trip_day_id == trip_id, TripEvent.level == 3, TripEvent.delivered_at.is_not(None))
                    .order_by(TripEvent.delivered_at.desc()).limit(1)).first()
                pick, suppress = policy.pick_next(
                    [policy.Queued(e.id, e.type, tuple(e.item_ids)) for e in rows], _kst(last), clock.now(),
                    timedelta(minutes=settings.proactive_cooldown_min))
                for e in rows:
                    if e.id in suppress:
                        e.status = "suppressed"
                if pick is None or _waiting_retry(pick.id, clock.now()):
                    return sent
                ev = next(e for e in rows if e.id == pick.id)
                auth = background_auth(td.user_id)
                if td.session_id is None:
                    td.session_id = _resolve_session(clients, td.plan_id, auth)
                if td.session_id is None:
                    _fail_attempt(ev, "agent 세션을 정하지 못함", clock.now())
                    if ev.status == "failed":
                        continue  # 큐의 다음 이벤트
                    return sent
                session_id, event_json = td.session_id, event_view(ev).model_dump(mode="json")
                trip_json = state_view(td, td.plan_id, clock, at_least=_kst(ev.now)).model_dump(mode="json")

            try:  # LLM 응답을 기다리는 동안 DB 연결·잠금을 잡지 않는다
                result = clients.agent.send_event(session_id, event_json, trip_json, auth)
            except UpstreamError as e:
                with SessionLocal.begin() as db:
                    ev = db.get(TripEvent, pick.id)
                    if e.code == "agent_busy":  # 사용자와 대화 중 -> 끝난 뒤 보낸다
                        ev.last_error = str(e)[:500]
                    else:
                        _fail_attempt(ev, str(e), clock.now())
                    if e.code == "session_not_found":  # 세션이 지워짐 -> 다음에 다시 정한다
                        db.get(TripDay, trip_id).session_id = None
                log.info("trip: 선제 턴 보류 (이벤트 %s): %s", pick.id, e)
                return sent

            _retry_at.pop(pick.id, None)
            with SessionLocal.begin() as db:
                ev = db.get(TripEvent, pick.id)
                ev.attempts += 1
                ev.last_error = None
                ev.delivered_at = clock.now()
                if ev.status == "queued":
                    ev.status = "delivered"
                ev.agent = {"session_id": session_id, "message_id": result.get("message_id"),
                            "proposal_id": _proposal_id(result)}
            sent += 1
    finally:
        lock.release()
