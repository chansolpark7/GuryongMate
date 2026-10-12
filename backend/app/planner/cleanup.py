"""계획을 지운 뒤 다른 서비스에 남은 그 계획의 데이터를 지운다 (모듈 간 규칙: HTTP API 로만, docs/planner/README.md).

- DELETE /agent/plans/{id}   그 계획의 대화 세션·메시지 (진행 중인 가이드 준비 작업도 멈춘다)
- DELETE /docent/plans/{id}  그 계획의 가이드 노트·이번 여행(scope=plan) 메모리
사용자 인증을 그대로 붙여 부른다 (각 서비스는 그 사용자의 행만 지운다). 실패해도 계획 삭제는 되돌리지 않고 로그만 남긴다.
주소: PLANNER_SERVICES_BASE_URL (없으면 PLANNER_SAFETY_BASE_URL — compose 에서는 둘 다 게이트웨이).
"""
import logging
import os

import httpx

from app.planner.places import settings

log = logging.getLogger("app.planner")

SERVICES = ("agent", "docent")
TIMEOUT_SEC = 5


class PlanCleaner:
    def __init__(self, base_url: str, client: httpx.Client | None = None):
        self._client = client or httpx.Client(base_url=base_url, timeout=TIMEOUT_SEC)

    def after_delete(self, plan_id: int, auth: dict[str, str]) -> list[str]:
        """지우지 못한 서비스 이름 목록 (빈 목록이면 모두 지움)."""
        failed = []
        for name in SERVICES:
            try:
                r = self._client.delete(f"/{name}/plans/{plan_id}", headers=auth)
                why = None if r.status_code == 204 else f"HTTP {r.status_code} {r.text[:200]}"
            except httpx.HTTPError as e:
                why = f"{type(e).__name__}: {str(e)[:200]}"
            if why:
                failed.append(name)
                log.warning("planner: 계획 %s 삭제 뒤 %s 데이터를 지우지 못함: %s", plan_id, name, why)
        return failed


_cleaner: PlanCleaner | None = None


def get_plan_cleaner() -> PlanCleaner:
    """FastAPI 의존성. 테스트에서는 dependency_overrides 로 바꾼다."""
    global _cleaner
    if _cleaner is None:
        _cleaner = PlanCleaner(os.environ.get("PLANNER_SERVICES_BASE_URL") or settings.safety_base_url)
    return _cleaner
