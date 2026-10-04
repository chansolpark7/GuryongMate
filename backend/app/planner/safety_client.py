"""safety 서비스 HTTP API 로 위험 판단을 받는다 (모듈 간 규칙: 내부 코드 import 금지).

- POST /safety/assess     방문(장소·날짜·시각)별 위험 단계
- GET  /safety/situation  지금 상황 요약 + fingerprint (바뀌었을 때만 일정을 다시 점검)
"""
from typing import Protocol

import httpx

from app.planner.places import settings


class SafetyUnavailable(RuntimeError):
    pass


class SafetyClient(Protocol):
    def assess(self, visits: list[dict]) -> dict:
        """AssessResult JSON. 접근할 수 없으면 SafetyUnavailable."""
        ...

    def situation(self) -> dict:
        """Situation JSON. 접근할 수 없으면 SafetyUnavailable."""
        ...


MAX_VISITS = 60  # /safety/assess 한 번에 보낼 수 있는 방문 수


class HttpSafetyClient:
    def __init__(self, base_url: str, timeout: float):
        self._client = httpx.Client(base_url=base_url, timeout=timeout)

    def _call(self, method: str, path: str, **kw) -> dict:
        try:
            resp = self._client.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise SafetyUnavailable(f"{path}: {e}") from e
        if not resp.is_success:
            raise SafetyUnavailable(f"{path} -> {resp.status_code} {resp.text[:200]}")
        return resp.json()

    def assess(self, visits: list[dict]) -> dict:
        # 많으면 나눠 보내고 합친다 (now·data_status 는 첫 결과 기준)
        merged: dict | None = None
        for i in range(0, len(visits), MAX_VISITS):
            r = self._call("POST", "/safety/assess", json={"visits": visits[i:i + MAX_VISITS]})
            if merged is None:
                merged = r
            else:
                merged["visits"] += r["visits"]
                for k, v in r["data_status"].items():
                    if not v.get("ok", True):
                        merged["data_status"][k] = v
        return merged or {"visits": [], "data_status": {}}

    def situation(self) -> dict:
        return self._call("GET", "/safety/situation")


_client = HttpSafetyClient(settings.safety_base_url, settings.safety_timeout_sec)


def get_safety_client() -> SafetyClient:
    """FastAPI 의존성. 테스트에서는 dependency_overrides 로 바꾼다."""
    return _client
