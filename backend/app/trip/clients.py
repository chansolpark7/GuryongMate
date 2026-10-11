"""다른 서비스 HTTP 클라이언트 (모듈 간 규칙: 내부 코드 import 금지, 게이트웨이 경유). 명세: docs/trip/README.md §6

    planner  GET /planner/plans/{id}, POST …/items/{item_id}/progress, POST …/projection,
             GET …/safety/alerts?status=new, POST …/safety/alerts/{id}/seen
    safety   GET /safety/situation (fingerprint), GET /safety/avoid
    agent    GET·POST /agent/sessions, POST /agent/sessions/{sid}/events (선제 턴),
             POST /agent/plans/{id}/guide/prepare (가이드 자동 준비: 여행 시작·도착·틱의 stale 노트)
    docent   GET /docent/plans/{id}/notes (틱: 오늘 남은 장소의 노트가 stale 인지)

모든 메서드는 auth(요청 헤더)를 받는다: 사용자 요청이면 받은 인증 그대로(ForwardAuth), 백그라운드면 그 사용자의 짧은 토큰.
테스트는 같은 메서드를 가진 가짜 클래스로 바꾼다 (get_clients).
"""
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.trip.config import settings


class UpstreamError(RuntimeError):
    """다른 서비스가 오류를 돌려주거나 접근할 수 없음. status=None 이면 접근 불가."""

    def __init__(self, service: str, status: int | None, detail: Any):
        self.service, self.status = service, status
        self.detail = detail if isinstance(detail, dict) else {"code": "upstream_error", "message": str(detail)[:300]}
        super().__init__(f"{service} {status}: {self.code} {self.detail.get('message', '')}")

    @property
    def code(self) -> str:
        return self.detail.get("code", "upstream_error")


class _Http:
    def __init__(self, service: str, base_url: str, timeout: float, http: httpx.Client | None = None):
        self.service = service
        self._http = http or httpx.Client(base_url=base_url, timeout=timeout)

    def call(self, method: str, path: str, auth: dict[str, str], **kw) -> Any:
        try:
            resp = self._http.request(method, path, headers=auth, **kw)
        except httpx.HTTPError as e:
            raise UpstreamError(self.service, None, f"{path}: {e}") from e
        if resp.is_error:
            try:
                body = resp.json()
                detail = body.get("detail", body) if isinstance(body, dict) else body
            except ValueError:
                detail = resp.text
            raise UpstreamError(self.service, resp.status_code, detail)
        return None if resp.status_code == 204 else resp.json()


class PlannerClient(Protocol):
    def get_plan(self, plan_id: int, auth: dict) -> dict: ...
    def progress(self, plan_id: int, item_id: int, status: str, at: str | None, auth: dict) -> dict: ...
    def projection(self, plan_id: int, body: dict, auth: dict) -> dict: ...
    def safety_alerts(self, plan_id: int, auth: dict) -> list[dict]: ...
    def alert_seen(self, plan_id: int, alert_id: int, auth: dict) -> None: ...


class SafetyClient(Protocol):
    def situation(self) -> dict: ...
    def avoid(self) -> dict: ...


class AgentClient(Protocol):
    def latest_session(self, plan_id: int, auth: dict) -> str | None: ...
    def create_session(self, plan_id: int, auth: dict) -> str: ...
    def send_event(self, session_id: str, event: dict, trip: dict, auth: dict) -> dict: ...
    def prepare_guide(self, plan_id: int, place_ids: list[str] | None, auth: dict) -> dict: ...


class DocentClient(Protocol):
    def list_notes(self, plan_id: int, auth: dict) -> list[dict]: ...


class HttpPlannerClient(_Http):
    def __init__(self, base_url: str = settings.planner_base_url, timeout: float = settings.timeout_sec, **kw):
        super().__init__("planner", base_url, timeout, **kw)

    def get_plan(self, plan_id, auth):
        return self.call("GET", f"/planner/plans/{plan_id}", auth)

    def progress(self, plan_id, item_id, status, at, auth):
        body = {"status": status} | ({"at": at} if at else {})
        return self.call("POST", f"/planner/plans/{plan_id}/items/{item_id}/progress", auth, json=body)

    def projection(self, plan_id, body, auth):
        return self.call("POST", f"/planner/plans/{plan_id}/projection", auth, json=body)

    def safety_alerts(self, plan_id, auth):
        return self.call("GET", f"/planner/plans/{plan_id}/safety/alerts", auth, params={"status": "new"})

    def alert_seen(self, plan_id, alert_id, auth):
        self.call("POST", f"/planner/plans/{plan_id}/safety/alerts/{alert_id}/seen", auth)


class HttpSafetyClient(_Http):
    """safety 조회는 사용자와 상관없다 (인증 없음)."""

    def __init__(self, base_url: str = settings.safety_base_url, timeout: float = settings.timeout_sec, **kw):
        super().__init__("safety", base_url, timeout, **kw)

    def situation(self):
        return self.call("GET", "/safety/situation", {})

    def avoid(self):
        return self.call("GET", "/safety/avoid", {})


class HttpAgentClient(_Http):
    def __init__(self, base_url: str = settings.agent_base_url, timeout: float = settings.agent_timeout_sec, **kw):
        super().__init__("agent", base_url, timeout, **kw)

    def latest_session(self, plan_id, auth):
        sessions = self.call("GET", "/agent/sessions", auth, params={"plan_id": plan_id})
        return sessions[0]["id"] if sessions else None

    def create_session(self, plan_id, auth):
        return self.call("POST", "/agent/sessions", auth, json={"plan_id": plan_id})["id"]

    def send_event(self, session_id, event, trip, auth):
        """선제 턴 (§4.2) -> ChatResult. 응답 중이면 UpstreamError(409 agent_busy)."""
        return self.call("POST", f"/agent/sessions/{session_id}/events", auth, json={"event": event, "trip": trip})

    def prepare_guide(self, plan_id, place_ids, auth):
        """가이드 자동 준비 (docs/docent/README.md §3.2) -> GuideJobView. 노트가 없는 곳만, 바로 돌아온다 (작업은 agent 백그라운드)."""
        body = {"auto": True} | ({"place_ids": place_ids} if place_ids is not None else {})
        return self.call("POST", f"/agent/plans/{plan_id}/guide/prepare", auth, json=body)


class HttpDocentClient(_Http):
    def __init__(self, base_url: str = settings.docent_base_url, timeout: float = settings.timeout_sec, **kw):
        super().__init__("docent", base_url, timeout, **kw)

    def list_notes(self, plan_id, auth):
        """가이드 노트 목록 [{place_id, status, …}] (본문 없이)."""
        return self.call("GET", f"/docent/plans/{plan_id}/notes", auth)


@dataclass
class Clients:
    planner: PlannerClient
    safety: SafetyClient
    agent: AgentClient
    docent: DocentClient


_clients: Clients | None = None


def get_clients() -> Clients:
    """FastAPI 의존성이자 백그라운드 틱이 쓰는 클라이언트. 테스트에서는 dependency_overrides 와 이 모듈의 _clients 를 바꾼다."""
    global _clients
    if _clients is None:
        _clients = Clients(HttpPlannerClient(), HttpSafetyClient(), HttpAgentClient(), HttpDocentClient())
    return _clients
