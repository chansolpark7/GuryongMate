"""주기 안전 점검. safety 의 상황 fingerprint 가 바뀌었을 때(또는 재점검 주기마다)만 일정을 다시 점검하고,
항목의 위험 단계가 지난번과 달라졌으면 계획에 경고(PlannerSafetyAlert)를 남긴다.

    safety (안전 정보만 관리)  <-- GET /safety/situation, POST /safety/assess --  planner (일정 점검 주도)

점검 대상: 예보로 판단할 수 있는 기간(지금 ~ 단기예보 마지막 날)에 방문이 있는 계획.
경고는 나중에 Guide Agent 를 부르는 기준이 된다 (지금은 저장·조회만).
"""
import logging
import threading
import time as _time
from datetime import date, datetime, timedelta

from sqlalchemy import delete, select

from app.db import SessionLocal
from app.planner import ops as engine
from app.planner import safety
from app.planner.models import PlannerPlan, PlannerSafetyAlert, PlannerSafetyState
from app.planner.places import PlaceLookup, get_place_lookup, settings
from app.planner.safety_client import SafetyClient, get_safety_client
from app.planner.schemas import PlanDetail, SafetyAlertView, SafetyChange, SafetyCheck, SafetyDetour, SafetyScanReport
from app.planner.travel import RouteLookup, get_route_lookup

log = logging.getLogger("uvicorn.error")
LOOKAHEAD = timedelta(days=3)  # 예보 마지막 시각을 모를 때 점검할 기간


def assess_plan(detail: PlanDetail, client: SafetyClient, routes: RouteLookup,
                start: date | None = None, end: date | None = None) -> SafetyCheck | None:
    """일정(방문 + 이동 구간)을 safety 에 물어 점검 결과를 만든다. 점검할 것이 없으면 None.
    이동 구간은 routing 경로선으로 보내고(못 구하면 직선), 3시간 안 이동이 주의·위험이면 우회 정보를 붙인다.
    safety 에 접근할 수 없으면 SafetyUnavailable."""
    legs = safety.travel_legs(detail, start, end)
    paths = {g.item_id: routes.path(g.origin, g.dest, g.mode) for g in legs}
    vs = safety.visits(detail, start, end, paths)
    if not vs:
        return None
    result = safety.check(detail, client.assess(vs))
    by_id = {g.item_id: g for g in legs}
    for it in result.items:
        g = by_id.get(it.item_id) if it.kind == "travel" else None
        if g is not None and it.horizon == "now" and it.risk in ("caution", "danger"):
            d = routes.detour(g.origin, g.dest, g.mode)
            # 길찾기는 센서 바로 주변(80m)만 막는다. 그 안을 지나지 않으면 우회할 것이 없어 붙이지 않는다
            if d is not None and (d["on_route"] or d["avoided"]):
                it.detour = SafetyDetour(**d)
    return result


def alert_view(a: PlannerSafetyAlert) -> SafetyAlertView:
    return SafetyAlertView(
        id=a.id, plan_id=a.plan_id, created_at=a.created_at, level=a.level, needs_action=a.needs_action,
        status=a.status, message=a.message, plan_version=a.plan_version, situation=a.situation,
        checked_at=a.checked_at, changes=[SafetyChange.model_validate(c) for c in a.changes])


class SafetyWatcher:
    def __init__(self, client: SafetyClient, places: PlaceLookup, session_factory=SessionLocal,
                 recheck: timedelta | None = None, users: list[str] | None = None, routes: RouteLookup | None = None):
        """users: 이 사용자들의 계획만 점검 (테스트용). None 이면 전체."""
        self.client = client
        self.routes = routes or get_route_lookup()
        self.users = users
        self.places = places
        self.sessions = session_factory
        self.recheck = recheck if recheck is not None else timedelta(minutes=settings.safety_recheck_min)
        self.last_situation: str | None = None
        self.last_run: float | None = None  # monotonic
        self._lock = threading.Lock()  # 백그라운드 점검과 수동 점검이 겹치지 않게

    def tick(self, force: bool = False) -> SafetyScanReport:
        """상황을 확인하고 필요할 때만 점검한다. safety 에 접근할 수 없으면 SafetyUnavailable."""
        with self._lock:
            sit = self.client.situation()
            fp = sit["fingerprint"]
            due = self.last_run is None or _time.monotonic() - self.last_run >= self.recheck.total_seconds()
            if force:
                reason = "요청에 의한 점검"
            elif fp != self.last_situation:
                reason = "안전 상황이 바뀜" if self.last_situation else "첫 점검"
            elif due:
                reason = "재점검 주기"
            else:
                return SafetyScanReport(ran=False, reason="상황이 같고 재점검 주기 전", situation=fp)
            report = self.scan(sit, reason)
            self.last_situation, self.last_run = fp, _time.monotonic()
            return report

    def scan(self, sit: dict, reason: str) -> SafetyScanReport:
        now = datetime.fromisoformat(sit["now"])
        until = datetime.fromisoformat(sit["forecast_until"]) if sit.get("forecast_until") else now + LOOKAHEAD
        q = select(PlannerPlan.id).where(PlannerPlan.start_date <= until.date(), PlannerPlan.end_date >= now.date())
        if self.users is not None:
            q = q.where(PlannerPlan.user_id.in_(self.users))
        with self.sessions() as db:
            ids = list(db.scalars(q.order_by(PlannerPlan.id)))
        alerts, checked = [], 0
        for plan_id in ids:
            done, alert = self.check_plan(plan_id, sit, now, until)
            checked += done
            if alert is not None:
                alerts.append(alert)
        return SafetyScanReport(ran=True, reason=reason, situation=sit["fingerprint"], checked_at=now,
                                plans=checked, alerts=alerts)

    def check_plan(self, plan_id: int, sit: dict, now: datetime, until: datetime
                   ) -> tuple[bool, SafetyAlertView | None]:
        """(점검했는지, 새 경고). safety 호출 동안에는 DB 연결·잠금을 잡지 않는다."""
        with self.sessions() as db:
            plan = db.get(PlannerPlan, plan_id)
            if plan is None:
                return False, None
            detail = engine.detail(plan, self.places)
        result = assess_plan(detail, self.client, self.routes, now.date(), until.date())
        if result is None:
            with self.sessions.begin() as db:  # 모두 건너뜀·완료 등: 지난 상태를 남기지 않는다
                db.execute(delete(PlannerSafetyState).where(PlannerSafetyState.plan_id == plan_id))
            return False, None

        with self.sessions.begin() as db:
            plan = db.get(PlannerPlan, plan_id, with_for_update=True, populate_existing=True)
            if plan is None or plan.version != detail.version:
                return False, None  # 점검하는 사이 일정이 바뀜 -> 다음 점검에서 본다
            prev = {s.key: s.risk for s in db.scalars(
                select(PlannerSafetyState).where(PlannerSafetyState.plan_id == plan_id))}
            changes, state = safety.diff(prev, result)
            if state != prev:
                db.execute(delete(PlannerSafetyState).where(PlannerSafetyState.plan_id == plan_id))
                db.add_all(PlannerSafetyState(plan_id=plan_id, key=k, risk=v) for k, v in state.items())
            if not changes:
                return True, None
            alert = PlannerSafetyAlert(
                plan_id=plan_id, level=safety.alert_level(changes),
                needs_action=any(c.kind == "raised" for c in changes), status="new",
                message=safety.alert_message(changes), plan_version=plan.version, situation=sit["fingerprint"],
                checked_at=result.checked_at, changes=[c.model_dump(mode="json") for c in changes])
            db.add(alert)
            db.flush()
            db.refresh(alert)
            view = alert_view(alert)
        log.info("planner 안전 경고: 계획 %s, %s", plan_id, view.message.splitlines()[0])
        return True, view


_watcher: SafetyWatcher | None = None


def get_watcher() -> SafetyWatcher:
    """백그라운드 점검과 수동 점검(POST /planner/safety/scan)이 같은 상태(마지막 fingerprint)를 쓴다."""
    global _watcher
    if _watcher is None:
        _watcher = SafetyWatcher(get_safety_client(), get_place_lookup())
    return _watcher
