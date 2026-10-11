"""trip 처리 흐름: 여행일 시작·종료, 위치 → 판정 → 이벤트, 백그라운드 틱, 단계 3 큐 전송, 앱 활동(보류·재개).
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


def _last_delay(db: Session, td: TripDay, not_queued: bool = False) -> tuple[int, float | None] | None:
    """이 여행일의 마지막 delay 이벤트 (id, 그때 delay_minutes). 일정 밀림을 다시 물을 기준 (judge.behind_ref).
    전송에 실패(failed)했거나 큐에서 대체된(suppressed) 것도 센다 — 원인 키와 같이, 같은 상황을 다시 만들지 않는다.
    not_queued: 아직 큐에 있는 것은 빼고 (보류된 delay 가 지금도 유효한지 볼 때, _revalidate)."""
    q = select(TripEvent.id, TripEvent.payload).where(TripEvent.trip_day_id == td.id, TripEvent.type == "delay")
    if not_queued:
        q = q.where(TripEvent.status != "queued")
    row = db.execute(q.order_by(TripEvent.id.desc()).limit(1)).first()
    return (row[0], (row[1] or {}).get("delay_minutes")) if row else None


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


def _mark(td: TripDay, kind: str, now: datetime, detail: str | None = None) -> judge.Detection:
    text = {"day_started": f"{td.day}일차 여행을 시작했어요.", "day_ended": f"{td.day}일차 여행을 마쳤어요."}[kind]
    payload = {}
    if kind == "day_ended":
        payload = {"reason": td.ended_reason} | ({"detail": detail} if detail else {})
    return judge.Detection(kind, now, message=text, cause_key=f"{kind}:{now.isoformat()}", payload=payload)


def _finish(db: Session, td: TripDay, reason: str, now: datetime, clients: Clients, auth: dict,
            detail: str | None = None) -> list[TripEvent]:
    """여행일 종료. 아직 못 보낸 단계 3은 더 이상 의미가 없어 suppressed (단계 1~3은 여행 중에만).
    그날 머무는 중(visiting)인 planner 항목은 done 으로 정리한다 (_close_visiting).
    detail: 자동 종료의 세부 사유 (midnight | plan_end | new_day, day_ended payload.detail)."""
    td.ended_at, td.ended_reason = now, reason
    db.execute(update(TripEvent).where(TripEvent.trip_day_id == td.id, TripEvent.status == "queued")
               .values(status="suppressed"))
    _close_visiting(clients, td, now, auth)
    return _record(db, td, [_mark(td, "day_ended", now, detail)], set())


def _close_at(td: TripDay, now: datetime) -> str:
    """종료로 정리하는 완료 시각 (HH:MM): 종료 시각. 자정이 지나 끝났거나 다른 날 시작으로 끝나면 그날 23:59."""
    return judge.hhmm(now) if now.date() == td.date else "23:59"


def _close_visiting(clients: Clients, td: TripDay, now: datetime, auth: dict) -> None:
    """여행일이 끝날 때 그날 visiting 항목을 planner 진행 상태 done 으로 (at = 종료 시각, version 은 그대로).
    그대로 두면 앱 플랜 탭에 '지금 여기'가 계속 보인다. planner 에 접근할 수 없으면 종료는 그대로 하고 로그만 남긴다."""
    try:
        spots = judge.spots_from_plan(clients.planner.get_plan(td.plan_id, auth), td.day)
    except UpstreamError as e:
        log.warning("trip: 종료 때 머무는 항목을 정리하지 못함 (계획 %s): %s", td.plan_id, e)
        return
    for s in spots:
        if s.status != "visiting":
            continue
        try:
            clients.planner.progress(td.plan_id, s.item_id, "done", _close_at(td, now), auth)
        except UpstreamError as e:
            log.warning("trip: 종료 때 항목 %s 을 done 으로 바꾸지 못함 (계획 %s): %s", s.item_id, td.plan_id, e)


def _reopen_track(clients: Clients, td: TripDay, plan: dict, now: datetime, auth: dict) -> None:
    """같은 날 여행일을 다시 열 때 판정 상태. 끝날 때 머무는 곳을 done 으로 정리했으므로:
    - 끝낸 지 TRIP_ACTIVE_MIN 안이고 그 항목이 정리한 그대로(done, 완료 시각 = 종료 시각)면 실수로 끝낸 것으로 보고 다시 머무는 중으로 되돌린다
      (planner 진행 상태 visiting, 처음 도착 시각).
    - 아니면 머무는 곳 없이(idle) 시작한다. 그 장소에 다시 가도 이미 다녀온(done) 항목이라 도착이 나지 않는다 — 이미 들른 곳이므로 괜찮다."""
    ended = _kst(td.ended_at)
    if td.current_state == "visiting" and td.current_item_id is not None and ended is not None:
        item = next((i for d in plan.get("days") or [] for i in d["items"] if i["id"] == td.current_item_id), None)
        recent = settings.active_min and timedelta(0) <= now - ended <= timedelta(minutes=settings.active_min)
        if (recent and item is not None and item.get("status") == "done"
                and (item.get("done_at") or "")[:5] == _close_at(td, ended)):
            since = _kst(td.current_since)
            try:
                clients.planner.progress(td.plan_id, td.current_item_id, "visiting",
                                         judge.hhmm(since) if since else None, auth)
                return  # 판정 상태(visiting)는 그대로 둔다
            except UpstreamError as e:
                log.warning("trip: 다시 열 때 항목 %s 을 되돌리지 못함 (계획 %s): %s", td.current_item_id, td.plan_id, e)
    if td.current_state == "visiting":
        _save_track(td, judge.Track())


# ---------- 시작·종료 ----------

def _resolve_session(clients: Clients, plan_id: int, auth: dict) -> str | None:
    """그 계획의 가장 최근 Agent 세션, 없으면 새로 만든다. agent 에 접근할 수 없으면 None (큐 전송 때 다시)."""
    try:
        return clients.agent.latest_session(plan_id, auth) or clients.agent.create_session(plan_id, auth)
    except UpstreamError as e:
        log.warning("trip: agent 세션을 정하지 못함 (계획 %s): %s", plan_id, e)
        return None


def prepare_guide(clients: Clients, user_id: str, plan_id: int, place_ids: list[str] | None = None) -> bool:
    """가이드 자동 준비 요청 (여행 시작 때 일정 전체, 도착 때 그 장소, 틱에서 stale 노트. docs/docent/README.md §3.2).
    응답 뒤 백그라운드로 부른다. 노트가 이미 있거나 준비 중·실패면 agent 가 건너뛴다.
    실패해도(agent 접근 불가·호출 수 제한 429) 여행에는 영향 없음 — 로그만. 요청이 받아들여졌는지 돌려준다."""
    try:
        job = clients.agent.prepare_guide(plan_id, place_ids, background_auth(user_id))
        log.info("trip: 가이드 자동 준비 요청 (계획 %s, %s): 작업 %s %s곳, 이어서 %s곳", plan_id, place_ids or "전체",
                 job.get("status"), job.get("total"), len(job.get("queued") or []))
        return True
    except UpstreamError as e:
        log.warning("trip: 가이드 자동 준비 요청 실패 (계획 %s, %s): %s", plan_id, place_ids or "전체", e)
    except Exception:
        log.exception("trip: 가이드 자동 준비 요청 중 오류 (계획 %s)", plan_id)
    return False


# 틱의 stale 노트 다시 준비가 실패하면(한도 초과·접근 불가) 이 시각(monotonic)까지 그 여행일은 다시 묻지 않는다 (틱마다 같은 실패 로그 방지)
_refresh_wait: dict[int, float] = {}
REFRESH_RETRY_SEC = 300


def refresh_guides(trip_id: int, clients: Clients, clock: Clock, now: datetime | None = None) -> list[str]:
    """관심사가 바뀐 뒤 오늘 남은 장소의 노트를 도착 전에 다시 준비 (틱, docs/trip/README.md §4.3, docs/docent/README.md §3.2).
    앱 활동 중일 때만(§4.1, 앱이 꺼져 있으면 LLM 비용을 쓰지 않는다), 오늘 남은 항목(planned·visiting) 중 노트가 stale 인 장소로
    agent 자동 준비를 부른다. agent 가 바로 pending 으로 표시하므로 다음 틱에는 다시 부르지 않는다. 요청한 place_id 들.
    now: 활동을 볼 기준 시각의 하한 (틱 API 의 판정 시각, dispatch 와 같다)."""
    if _time.monotonic() < _refresh_wait.get(trip_id, 0):
        return []
    with SessionLocal() as db:
        td = db.get(TripDay, trip_id)
        if td is None or td.ended_at is not None:
            return []
        ref = max(clock.now(), _kst(now)) if now is not None else clock.now()
        if not _is_active(td, ref):
            return []
        user_id, plan_id, day = td.user_id, td.plan_id, td.day
    auth = background_auth(user_id)
    try:
        spots = judge.spots_from_plan(clients.planner.get_plan(plan_id, auth), day)
        remaining = {s.place_id for s in spots if s.item_id in judge.remaining_ids(spots)}
        if not remaining:
            return []
        notes = clients.docent.list_notes(plan_id, auth)
    except UpstreamError as e:
        log.warning("trip 틱: 가이드 노트 상태를 보지 못함 (계획 %s): %s", plan_id, e)
        return []
    stale = [s.place_id for s in spots if s.place_id in remaining
             and any(n["place_id"] == s.place_id and n["status"] == "stale" for n in notes)]
    stale = list(dict.fromkeys(stale))  # 일정 순서, 같은 장소는 한 번
    if not stale:
        return []
    log.info("trip 틱: 남은 장소의 노트가 stale -> 다시 준비 (계획 %s): %s", plan_id, stale)
    if prepare_guide(clients, user_id, plan_id, stale):
        _refresh_wait.pop(trip_id, None)
    else:
        _refresh_wait[trip_id] = _time.monotonic() + REFRESH_RETRY_SEC
    return stale


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
        _finish(db, current, "auto", now, clients, auth, "new_day")  # 다른 날 여행일이 진행 중 -> 끝내고 새로 시작

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
        _reopen_track(clients, td, plan, now, auth)
        td.ended_at, td.ended_reason, td.started_at = None, None, now
    td.session_id = session_id or td.session_id or _resolve_session(clients, plan_id, auth)
    td.active_at = now  # 시작도 앱 활동이다 (§4.1)
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
        _finish(db, td, "user", clock.now(), clients, auth)
        db.commit()
    return state_view(td, plan_id, clock)


def delete_user(db: Session, user_id: str) -> None:
    for td in db.scalars(select(TripDay).where(TripDay.user_id == user_id)):
        db.delete(td)  # 이벤트는 ON DELETE CASCADE
    db.commit()


# ---------- 앱 활동 (§4.1 보류) ----------

HELD = "보류: 앱 활동 없음"  # 활동이 없어 보내지 않은 단계 3 의 last_error (활동이 돌아오면 _revalidate 가 지운다)


def _is_active(td: TripDay, now: datetime) -> bool:
    return judge.is_active(_kst(td.active_at), now, settings.active_min)


def _held(db: Session, td: TripDay) -> bool:
    return db.scalars(select(TripEvent.id).where(TripEvent.trip_day_id == td.id, TripEvent.status == "queued",
                                                 TripEvent.level == 3, TripEvent.last_error == HELD)
                      .limit(1)).first() is not None


def _activity(db: Session, td: TripDay, at: datetime, located: bool = False) -> bool:
    """앱 활동(위치 전송·이벤트 조회)을 기록한다. 활동 시각은 trip 의 지금 기준이다 (위치는 recorded_at 과 큰 쪽).
    -> 활동이 돌아왔으면(이전 활동이 TRIP_ACTIVE_MIN 보다 오래됐거나 보류된 단계 3 이 있음) True: 응답 뒤 resume."""
    back = not _is_active(td, at) or _held(db, td)
    td.active_at = judge.bump_active(_kst(td.active_at), at)
    if located:
        td.located_at = judge.bump_active(_kst(td.located_at), at)
    return back


def touch(db: Session, user_id: str, plan_id: int, clock: Clock) -> int | None:
    """이벤트 조회(GET events)를 앱 활동으로 기록. 활동이 돌아왔으면 그 여행일 id (응답 뒤 resume), 아니면 None."""
    td = latest_trip(db, user_id, plan_id)
    if td is None or td.ended_at is not None:
        return None
    back = _activity(db, td, clock.now())
    db.commit()
    return td.id if back else None


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
        self._sit: dict | None = None  # 마지막으로 받은 상황 (받지 못했으면 None)
        self._until = 0.0
        self._lock = threading.Lock()

    def zones(self, clients: Clients) -> list[dict]:
        with self._lock:
            if _time.monotonic() < self._until:
                return self._zones
            try:
                self._sit = None
                self._sit = clients.safety.situation()
                fp = self._sit.get("fingerprint")
                if fp != self._fp or fp is None:
                    self._zones = clients.safety.avoid().get("zones", [])
                    self._fp = fp
            except UpstreamError as e:
                log.warning("trip: 위험 구역을 받지 못함: %s", e)  # 지난 구역을 계속 쓴다
            self._until = _time.monotonic() + self.ttl
            return self._zones

    def situation(self, clients: Clients) -> dict | None:
        """safety 상황 (GET /safety/situation, 구역과 같은 캐시). 받지 못했으면 None."""
        self.zones(clients)
        return self._sit

    def reset(self) -> None:
        with self._lock:
            self._fp, self._zones, self._sit, self._until = None, [], None, 0.0


hazards = HazardCache(settings.hazard_cache_sec)


# ---------- 위치 ----------

def record_location(db: Session, user_id: str, plan_id: int, points: list[LocationIn], auth: dict, clients: Clients,
                    clock: Clock) -> tuple[LocationResult, bool]:
    """-> (결과, 앱 활동이 돌아왔는지: True 면 응답 뒤 resume)."""
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
    now = clock.now()
    back = _activity(db, td, max(now, last.recorded_at) if last is not None else now, located=last is not None)
    known = _known(db, td)
    if last is not None:
        td.lat, td.lng, td.accuracy, td.recorded_at, td.source = (last.lat, last.lng, last.accuracy,
                                                                  last.recorded_at, last.source)
        proj = _project(clients, td, track, last.recorded_at, auth)
        if proj is not None:
            _apply_projection(td, proj)
            d = judge.judge_delay(proj, last.recorded_at, known, behind_min=settings.behind_min, plan=plan,
                                  day=td.day, track=track, notice=_last_delay(db, td))
            if d:
                dets.append(d)
        by_id = {s.item_id: s for s in spots}
        for i, d in enumerate(dets):
            if d.type == "departed":
                # 같은 요청에서 다음 장소 도착까지 났으면(가까운 장소 전환 등) 다음 일정·예상 도착은 이미 지난 이야기라 빼고 떠남만
                nxt = None if any(x.type == "arrived" for x in dets[i + 1:]) else td.next
                target = by_id.get(nxt["item_id"]) if nxt else None
                d.message = judge.departed_message(d.payload["title"], nxt, target.title if target else None,
                                                   target.leg_mode if target else None, d.at)
        hz = judge.judge_hazard(last.lat, last.lng, last.recorded_at, hazards.zones(clients), settings.hazard_near_m)
        if hz:
            dets.append(hz)

    events = _record(db, td, dets, known)
    views = [event_view(e) for e in events]
    db.commit()
    return LocationResult(state=state_view(td, plan_id, clock), events=views, accepted=accepted, ignored=ignored), back


# ---------- 백그라운드 틱 (§4.3) ----------

def tick_trip(db: Session, td: TripDay, clients: Clients, clock: Clock, now: datetime | None = None,
              revalidate: bool = False) -> list[EventView]:
    """진행 중인 여행일 하나: 지연 재판정 → 안전 경고 수집 → 상황 해제 → 자동 종료. (큐 전송은 dispatch)
    now: 판정 시각 (생략하면 trip 의 지금. 재생기는 재생 시각을 넘긴다).
    revalidate: 앱 활동이 돌아왔을 때(resume) — 큐의 단계 3 중 지금도 유효한 최신 한 건만 남긴다 (_revalidate)."""
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
    track = _load_track(td)
    proj = _project(clients, td, track, now, auth)
    delay_valid: bool | None = None  # 큐의 delay 가 지금도 유효한지 (revalidate). None: 재계산 실패 -> 모름
    if proj is not None:
        _apply_projection(td, proj)
        if revalidate:  # 큐에 있는 것을 빼고 본 기준으로: 지금도 위반이나 밀림이 있나
            delay_valid = judge.judge_delay(proj, now, frozenset(), behind_min=settings.behind_min, plan=plan,
                                            day=td.day, track=track,
                                            notice=_last_delay(db, td, not_queued=True)) is not None
        d = judge.judge_delay(proj, now, known, behind_min=settings.behind_min, plan=plan, day=td.day,
                              track=track, notice=_last_delay(db, td))
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

    # 3. 상황 해제: 이 여행일에 주의·위험이던 safety 상황이 모두 평상시로 -> safety_eased (situation_cleared).
    #    같은 틱에 planner 경고 완화(safety_eased)가 있으면 그것 하나만 (원인 키를 합쳐 같은 해제를 다시 알리지 않는다)
    sit = hazards.situation(clients)
    if sit is not None:
        td.situation, cleared = judge.judge_situation(sit, td.situation, now, td.day)
        if cleared:
            eased = next((x for x in dets if x.type == "safety_eased"), None)
            if eased is not None:
                eased.cause_key += "|" + cleared.cause_key
                eased.payload["situation_cleared"] = True
            else:
                dets.append(cleared)

    events = _record(db, td, dets, known)
    if revalidate:
        _revalidate(db, td, delay_valid)

    # 4. 자동 종료 (그날 마지막 일정 종료 + TRIP_AUTO_END_MIN 이 지나고 최근 위치가 없음, 또는 자정)
    reason = judge.auto_end_reason(now, td.date, judge.day_end_at(plan, td.day), settings.auto_end_min,
                                   _kst(td.located_at), settings.active_min)
    if reason:
        events += _finish(db, td, "auto", now, clients, auth, reason)
    views = [event_view(e) for e in events]
    db.commit()
    return views


def _revalidate(db: Session, td: TripDay, delay_valid: bool | None) -> None:
    """앱 활동이 돌아왔을 때: 큐의 단계 3 중 지금도 유효한 것에서 최신 한 건만 남기고 (안전 > 지연) 나머지는 suppressed.
    - delay: 지금 재계산으로도 위반이나 밀림이 있음 (delay_valid. 재계산 실패면 유효로 본다)
    - safety_raised: 그 뒤에 상황 해제(situation_cleared)나, 그 항목들이 모두 safe 로 내려간 경고 완화가 없음"""
    db.flush()
    rows = list(db.scalars(select(TripEvent).where(TripEvent.trip_day_id == td.id, TripEvent.status == "queued",
                                                   TripEvent.level == 3)))
    if not rows:
        return
    eased = list(db.scalars(select(TripEvent).where(TripEvent.trip_day_id == td.id, TripEvent.type == "safety_eased")))

    def lifted(e: TripEvent) -> bool:
        for x in eased:
            p = x.payload or {}
            if x.id < e.id:
                continue
            if p.get("reason") == "situation_cleared" or p.get("situation_cleared"):
                return True
            safe = {c["item"]["item_id"] for c in p.get("changes") or [] if c.get("to_risk") == "safe"}
            if e.item_ids and set(e.item_ids) <= safe:
                return True
        return False

    valid = {e.id for e in rows if (delay_valid is not False if e.type == "delay" else not lifted(e))}
    keep, suppress = policy.pick_resume([policy.Queued(e.id, e.type, tuple(e.item_ids)) for e in rows], valid)
    for e in rows:
        if e.id in suppress:
            e.status = "suppressed"
        elif e.last_error == HELD:
            e.last_error = None
    if suppress:
        log.info("trip: 활동 복귀 (여행일 %s) — 보낼 것 %s, 억제 %s", td.id, keep, suppress)


def resume(trip_id: int, clients: Clients, clock: Clock) -> None:
    """앱 활동이 돌아옴 (응답 뒤 백그라운드): 틱과 같은 지연·안전 재판정을 한 번 하고, 큐의 단계 3 중 지금도 유효한 최신 한 건만 보낸다."""
    with SessionLocal() as db:
        td = db.get(TripDay, trip_id, with_for_update=True, populate_existing=True)
        if td is None or td.ended_at is not None:
            return
        tick_trip(db, td, clients, clock, revalidate=True)
    dispatch(trip_id, clients, clock)


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
            refresh_guides(trip_id, clients, clock)  # 선제 턴(LLM 응답을 기다림)보다 먼저 — 바로 돌아온다
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


def dispatch(trip_id: int, clients: Clients, clock: Clock, now: datetime | None = None) -> int:
    """큐에 있는 단계 3 이벤트를 하나씩 Agent 선제 턴으로 보낸다. 보낸 수.
    앱 활동이 TRIP_ACTIVE_MIN 넘게 없으면 보내지 않고 queued 로 둔다 (last_error=HELD, 시도 수는 세지 않음. §4.1).
    now: 활동을 볼 기준 시각의 하한 (틱 API 의 판정 시각 — 가상 시각을 옮긴 직후 trip 시계 캐시가 늦어도 맞게).
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
                ref = max(clock.now(), _kst(now)) if now is not None else clock.now()
                if pick is not None and not _is_active(td, ref):  # 앱이 꺼져 있음 -> 보류 (활동이 돌아오면 resume)
                    for e in rows:
                        if e.id not in suppress:
                            e.last_error = HELD
                    return sent
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
