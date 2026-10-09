"""계획 소유 확인. planner HTTP API(GET /planner/plans/{id})를 사용자 인증 그대로 부른다 (모듈 간 규칙: 내부 코드 import 금지).
다른 사용자의 계획이면 planner 가 404 를 돌려주므로 docent 도 404 plan_not_found 로 답한다.
"""
import httpx

from app.docent.config import settings
from app.docent.errors import DocentError

_http: httpx.AsyncClient | None = None


def get_http() -> httpx.AsyncClient:
    """FastAPI 의존성. 테스트에서는 ASGITransport 클라이언트로 바꾼다."""
    global _http
    if _http is None:
        _http = httpx.AsyncClient(base_url=settings.api_base_url, timeout=settings.api_timeout_sec)
    return _http


async def ensure_plan(http: httpx.AsyncClient, plan_id: int, auth: dict[str, str]) -> None:
    try:
        resp = await http.get(f"/planner/plans/{plan_id}", headers=auth)
    except httpx.HTTPError as e:
        raise DocentError(503, "planner_unavailable", "계획 서비스에 접근할 수 없습니다.", reason=str(e)[:200]) from e
    if resp.status_code == 404:
        raise DocentError(404, "plan_not_found", f"계획 {plan_id} 을(를) 찾을 수 없습니다.")
    if not resp.is_success:
        raise DocentError(503, "planner_unavailable", "계획 서비스에 접근할 수 없습니다.",
                          reason=f"GET /planner/plans/{plan_id} -> {resp.status_code}")
