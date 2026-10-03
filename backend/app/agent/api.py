"""planner·places 모듈을 HTTP API 로 호출하는 비동기 클라이언트 (모듈 간 규칙: 내부 코드 import 금지).

Agent 는 앱·CLI 와 똑같은 공개 API 만 쓴다.
"""
from typing import Any

import httpx

from app.agent.config import settings


class ApiError(RuntimeError):
    def __init__(self, status: int, detail: Any):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail

    @property
    def message(self) -> str:
        """pydantic 검증 오류(list)도 한 줄로 읽히게."""
        if isinstance(self.detail, list):
            return "; ".join(f"{'.'.join(str(x) for x in d.get('loc', [])[1:])}: {d.get('msg')}" for d in self.detail)
        return str(self.detail)


class ServiceApi:
    def __init__(self, http: httpx.AsyncClient, user_id: str):
        self._http = http
        self._headers = {"X-User-Id": user_id}

    async def _req(self, method: str, path: str, **kw) -> Any:
        try:
            resp = await self._http.request(method, path, headers=self._headers, **kw)
        except httpx.HTTPError as e:
            raise ApiError(503, f"API 연결 실패: {e!r}") from e
        if resp.is_error:
            try:
                detail = resp.json().get("detail", resp.text)
            except ValueError:
                detail = resp.text
            raise ApiError(resp.status_code, detail)
        return resp.json()

    # places
    async def search_places(self, **params) -> dict:
        return await self._req("GET", "/places", params={k: v for k, v in params.items() if v not in (None, "")})

    async def get_place(self, place_id: str) -> dict:
        return await self._req("GET", f"/places/{place_id}")

    # planner
    async def get_plan(self, plan_id: int) -> dict:
        return await self._req("GET", f"/planner/plans/{plan_id}")

    async def preview(self, plan_id: int, ops: list[dict]) -> dict:
        return await self._req("POST", f"/planner/plans/{plan_id}/preview", json={"ops": ops})

    async def list_proposals(self, plan_id: int, status: str | None = None) -> list[dict]:
        return await self._req("GET", f"/planner/plans/{plan_id}/proposals", params={"status": status} if status else None)

    async def get_proposal(self, plan_id: int, proposal_id: int) -> dict:
        return await self._req("GET", f"/planner/plans/{plan_id}/proposals/{proposal_id}")

    async def create_proposal(self, plan_id: int, ops: list[dict], summary: str, origin: str,
                              supersedes: int | None) -> dict:
        body = {"ops": ops, "summary": summary, "source": "llm", "origin": origin, "supersedes": supersedes}
        return await self._req("POST", f"/planner/plans/{plan_id}/proposals", json=body)

    async def accept_proposal(self, plan_id: int, proposal_id: int) -> dict:
        return await self._req("POST", f"/planner/plans/{plan_id}/proposals/{proposal_id}/accept")

    async def reject_proposal(self, plan_id: int, proposal_id: int) -> dict:
        return await self._req("POST", f"/planner/plans/{plan_id}/proposals/{proposal_id}/reject")


_http: httpx.AsyncClient | None = None


def get_http() -> httpx.AsyncClient:
    """FastAPI 의존성. 테스트에서는 ASGITransport 클라이언트로 바꾼다."""
    global _http
    if _http is None:
        _http = httpx.AsyncClient(base_url=settings.api_base_url, timeout=30)
    return _http
