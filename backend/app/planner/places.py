"""places 모듈 HTTP API 로 장소 정보를 조회한다 (모듈 간 규칙: 내부 코드 import 금지)."""
import time as _time
from typing import Protocol

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.planner.schemas import PlaceInfo


class PlannerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="PLANNER_", extra="ignore")

    # 같은 컨테이너의 API 서버. 컨테이너 안에서는 항상 8000 번 포트.
    places_base_url: str = "http://127.0.0.1:8000"
    places_cache_ttl_sec: float = 300


settings = PlannerSettings()


class PlacesUnavailable(RuntimeError):
    pass


class PlaceLookup(Protocol):
    def get(self, place_id: str) -> PlaceInfo | None:
        """없는 장소면 None, places API 에 접근할 수 없으면 PlacesUnavailable."""
        ...


class HttpPlaceLookup:
    def __init__(self, base_url: str, ttl_sec: float):
        self._client = httpx.Client(base_url=base_url, timeout=5)
        self._ttl = ttl_sec
        self._cache: dict[str, tuple[float, PlaceInfo | None]] = {}

    def get(self, place_id: str) -> PlaceInfo | None:
        hit = self._cache.get(place_id)
        if hit and _time.monotonic() - hit[0] < self._ttl:
            return hit[1]
        try:
            resp = self._client.get(f"/places/{place_id}")
        except httpx.HTTPError as e:
            raise PlacesUnavailable(str(e)) from e
        if resp.status_code == 404:
            info = None
        elif resp.is_success:
            info = PlaceInfo.model_validate(resp.json())
        else:
            raise PlacesUnavailable(f"GET /places/{place_id} -> {resp.status_code}")
        self._cache[place_id] = (_time.monotonic(), info)
        return info


_lookup = HttpPlaceLookup(settings.places_base_url, settings.places_cache_ttl_sec)


def get_place_lookup() -> PlaceLookup:
    """FastAPI 의존성. 테스트에서는 dependency_overrides 로 가짜 구현을 넣는다."""
    return _lookup
