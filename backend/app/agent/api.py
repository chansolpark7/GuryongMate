"""planner·places·routing·safety·trip 서비스를 HTTP API 로 호출하는 비동기 클라이언트 (서비스 간 규칙: 내부 코드 import 금지).

Agent 는 앱·CLI 와 똑같은 공개 API 만 쓴다. 주소는 AGENT_API_BASE_URL (compose 에서는 게이트웨이).
"""
from typing import Any
from urllib.parse import quote

import httpx

from app.agent.config import settings


class ApiError(RuntimeError):
    """다른 서비스가 오류를 돌려줌. planner 오류는 detail = {"code", "message", ...}."""

    def __init__(self, status: int, detail: Any):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail

    @property
    def code(self) -> str:
        if isinstance(self.detail, dict) and "code" in self.detail:
            return self.detail["code"]
        return "upstream_unavailable" if self.status == 503 else "upstream_error"

    @property
    def message(self) -> str:
        if isinstance(self.detail, dict):
            return str(self.detail.get("message", self.detail))
        if isinstance(self.detail, list):  # FastAPI 기본 검증 오류 (places 등)
            return "; ".join(f"{'.'.join(str(x) for x in d.get('loc', [])[1:])}: {d.get('msg')}" for d in self.detail)
        return str(self.detail)

    def body(self) -> dict:
        """앱에 그대로 전달할 오류 본문 (planner 코드·추가 필드 유지)."""
        if isinstance(self.detail, dict) and "code" in self.detail:
            return {"detail": self.detail}
        return {"detail": {"code": self.code, "message": self.message}}


class ServiceApi:
    def __init__(self, http: httpx.AsyncClient, auth: dict[str, str]):
        """auth: 사용자의 인증 헤더 (받은 Authorization 그대로, 개발 모드면 X-User-Id) — app.core.auth.ForwardAuth"""
        self._http = http
        self._headers = auth

    async def _req(self, method: str, path: str, **kw) -> Any:
        try:
            resp = await self._http.request(method, path, headers=self._headers, **kw)
        except httpx.HTTPError as e:
            raise ApiError(503, {"code": "upstream_unavailable", "message": f"서비스에 연결할 수 없습니다: {e!r}"}) from e
        if resp.is_error:
            try:
                body = resp.json()
                detail = body.get("detail", body) if isinstance(body, dict) else body
            except ValueError:
                detail = resp.text
            if resp.status_code >= 500 and not (isinstance(detail, dict) and "code" in detail):
                # 게이트웨이 502·HTML 같은 형식 없는 서버 오류는 "서비스에 접근할 수 없음"으로 통일
                raise ApiError(503, {"code": "upstream_unavailable",
                                     "message": f"서비스가 응답하지 않습니다 (HTTP {resp.status_code})."})
            raise ApiError(resp.status_code, detail)
        return None if resp.status_code == 204 else resp.json()

    # ----- places -----
    async def search_places(self, **params) -> dict:
        return await self._req("GET", "/places", params={k: v for k, v in params.items() if v not in (None, "")})

    async def get_place(self, place_id: str) -> dict:
        return await self._req("GET", f"/places/{quote(place_id, safe='')}")

    # ----- planner: 계획 -----
    async def get_plan(self, plan_id: int) -> dict:
        return await self._req("GET", f"/planner/plans/{plan_id}")

    async def preview(self, plan_id: int, ops: list[dict]) -> dict:
        return await self._req("POST", f"/planner/plans/{plan_id}/preview", json={"ops": ops})

    async def projection(self, plan_id: int) -> dict:
        """여행 중 오늘을 실제 진행 + planner 의 지금 기준으로 다시 계산한 일정 (저장 안 함)."""
        return await self._req("POST", f"/planner/plans/{plan_id}/projection")

    # ----- routing (길찾기) -----
    async def routing_matrix(self, mode: str, sources: list[dict], targets: list[dict]) -> dict:
        return await self._req("POST", "/routing/matrix", json={"mode": mode, "sources": sources, "targets": targets})

    async def routing_route(self, mode: str, stops: list[dict], hazards: str = "avoid") -> dict:
        return await self._req("POST", "/routing/route",
                               json={"mode": mode, "stops": stops, "hazards": hazards, "steps": False})

    # ----- planner: 안전 점검 · safety -----
    async def check_safety(self, plan_id: int, ops: list[dict]) -> dict:
        """초안(ops)이 있으면 적용했다고 치고, 없으면 지금 일정의 안전 점검 (방문 + 이동 구간)."""
        if ops:
            return await self._req("POST", f"/planner/plans/{plan_id}/safety/preview", json={"ops": ops})
        return await self._req("GET", f"/planner/plans/{plan_id}/safety")

    async def safety_situation(self) -> dict:
        return await self._req("GET", "/safety/situation")

    async def safety_assess(self, visits: list[dict]) -> dict:
        return await self._req("POST", "/safety/assess", json={"visits": visits})

    async def safety_outlook(self, day: str) -> dict:
        return await self._req("GET", "/safety/outlook", params={"date": day})

    async def safety_sim(self) -> dict:
        """가상 상황·시각 (SAFETY_SOURCE=mock 일 때만, 아니면 404 sim_disabled). 시계용이라 짧게 기다린다."""
        return await self._req("GET", "/safety/sim", timeout=2)

    # ----- trip (여행 중 상태) -----
    async def trip_state(self, plan_id: int) -> dict:
        return await self._req("GET", f"/trip/plans/{plan_id}/state", timeout=settings.trip_timeout_sec)

    # ----- planner: 제안 -----
    async def list_proposals(self, plan_id: int, status: str | None = None) -> list[dict]:
        return await self._req("GET", f"/planner/plans/{plan_id}/proposals", params={"status": status} if status else None)

    async def get_proposal(self, plan_id: int, proposal_id: int) -> dict:
        return await self._req("GET", f"/planner/plans/{plan_id}/proposals/{proposal_id}")

    async def create_proposal(self, plan_id: int, ops: list[dict], *, summary: str, request: str, origin: str,
                              supersedes: int | None, expected_version: int | None) -> dict:
        body = {"ops": ops, "summary": summary[:2000], "request": request[:4000], "source": "llm", "origin": origin,
                "supersedes": supersedes, "expected_version": expected_version}
        return await self._req("POST", f"/planner/plans/{plan_id}/proposals", json=body)

    async def accept_proposal(self, plan_id: int, proposal_id: int, mode: str = "auto") -> dict:
        return await self._req("POST", f"/planner/plans/{plan_id}/proposals/{proposal_id}/accept", json={"mode": mode})

    async def reject_proposal(self, plan_id: int, proposal_id: int) -> dict:
        return await self._req("POST", f"/planner/plans/{plan_id}/proposals/{proposal_id}/reject")

    # ----- planner: 추천 -----
    async def create_recommendation(self, plan_id: int, title: str, candidates: list[dict], origin: str) -> dict:
        body = {"title": title, "source": "llm", "origin": origin, "candidates": candidates}
        return await self._req("POST", f"/planner/plans/{plan_id}/recommendations", json=body)

    async def get_recommendation(self, plan_id: int, rec_id: int) -> dict:
        return await self._req("GET", f"/planner/plans/{plan_id}/recommendations/{rec_id}")

    async def candidate_action(self, plan_id: int, rec_id: int, cand_id: int, action: str) -> dict:
        return await self._req("POST", f"/planner/plans/{plan_id}/recommendations/{rec_id}/candidates/{cand_id}/{action}")


_http: httpx.AsyncClient | None = None


def get_http() -> httpx.AsyncClient:
    """FastAPI 의존성. 테스트에서는 ASGITransport 클라이언트로 바꾼다."""
    global _http
    if _http is None:
        _http = httpx.AsyncClient(base_url=settings.api_base_url, timeout=30)
    return _http
