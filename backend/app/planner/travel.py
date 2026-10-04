"""장소 간 이동시간. routing 서비스(OSM 기반 길찾기, docs/routing/README.md)에 묻고,
응답이 없으면 직선거리로 근사한다 (모듈 간 규칙: 내부 코드 import 금지 → HTTP 로만 호출).
"""
import math
import time as _time
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.planner.schemas import PlaceInfo

DETOUR = 1.3  # 직선거리 -> 실제 경로 보정계수
WALK_M_PER_MIN = 70  # 약 4.2km/h (어르신 동행 고려해 느리게). routing 의 기본 걷는 속도와 같다
CAR_M_PER_MIN = 500  # 약 30km/h (시내·해안도로 평균)
CAR_OVERHEAD_MIN = 5  # 주차·승하차


class TravelSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="PLANNER_", extra="ignore")

    routing_base_url: str = "http://127.0.0.1:8000"
    routing_timeout_sec: float = 5
    routing_retry_sec: float = 20  # routing 이 죽었을 때 이 시간 동안은 묻지 않고 바로 근사
    routing_cache_size: int = 20_000
    routing_cache_ttl_sec: float = 86_400


settings = TravelSettings()


@dataclass(frozen=True)
class TravelLeg:
    minutes: int
    distance_m: int
    source: Literal["road", "estimate"]  # road = 실제 길, estimate = 직선거리 근사


def distance_m(a: PlaceInfo, b: PlaceInfo) -> float:
    lat1, lng1, lat2, lng2 = map(math.radians, (a.lat, a.lng, b.lat, b.lng))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def estimate_minutes(a: PlaceInfo, b: PlaceInfo, mode: str) -> tuple[int, int]:
    """(이동시간 분, 경로거리 m)."""
    route_m = distance_m(a, b) * DETOUR
    if route_m < 1:
        return 0, 0
    if mode == "car":
        return math.ceil(route_m / CAR_M_PER_MIN) + CAR_OVERHEAD_MIN, round(route_m)
    return math.ceil(route_m / WALK_M_PER_MIN), round(route_m)


def estimate_leg(a: PlaceInfo, b: PlaceInfo, mode: str) -> TravelLeg:
    return TravelLeg(*estimate_minutes(a, b, mode), source="estimate")


class TravelLookup(Protocol):
    def legs(self, pairs: list[tuple[PlaceInfo, PlaceInfo]], mode: str) -> list[TravelLeg]:
        """구간마다 이동시간. 실패하지 않는다 (못 구하면 근사값)."""
        ...


class EstimateTravel:
    """직선거리 근사만 쓴다 (테스트·routing 없이 단독 실행)."""

    def legs(self, pairs, mode):
        return [estimate_leg(a, b, mode) for a, b in pairs]


class HttpTravelLookup:
    def __init__(self, base_url: str, timeout: float, retry_sec: float, cache_size: int, ttl_sec: float):
        self._client = httpx.Client(base_url=base_url, timeout=timeout)
        self._retry, self._size, self._ttl = retry_sec, cache_size, ttl_sec
        self._down_until = 0.0
        self._cache: dict[tuple, tuple[float, TravelLeg]] = {}

    @staticmethod
    def _key(a: PlaceInfo, b: PlaceInfo, mode: str) -> tuple:
        return mode, round(a.lat, 6), round(a.lng, 6), round(b.lat, 6), round(b.lng, 6)

    def legs(self, pairs, mode):
        now = _time.monotonic()
        out: list[TravelLeg | None] = []
        todo: dict[tuple, int] = {}
        for a, b in pairs:
            k = self._key(a, b, mode)
            hit = self._cache.get(k)
            out.append(hit[1] if hit and now - hit[0] < self._ttl else None)
            if out[-1] is None and k not in todo:
                todo[k] = len(out) - 1
        if todo:
            fetched = self._fetch([pairs[i] for i in todo.values()], mode)
            found = dict(zip(todo, fetched)) if fetched else {}
            for k, leg in found.items():
                if leg.source == "road":
                    self._put(k, leg, now)
            for n, (a, b) in enumerate(pairs):
                if out[n] is None:
                    out[n] = found.get(self._key(a, b, mode)) or estimate_leg(a, b, mode)
        return out

    def _fetch(self, pairs, mode) -> list[TravelLeg] | None:
        if _time.monotonic() < self._down_until:
            return None
        body = {"mode": mode, "legs": [{"from": {"lat": a.lat, "lng": a.lng}, "to": {"lat": b.lat, "lng": b.lng}}
                                       for a, b in pairs]}
        try:
            resp = self._client.post("/routing/legs", json=body)
            resp.raise_for_status()
            legs = resp.json()["legs"]
        except (httpx.HTTPError, KeyError, ValueError):
            self._down_until = _time.monotonic() + self._retry
            return None
        out = []
        for (a, b), leg in zip(pairs, legs):
            if leg["source"] != "road":  # routing 의 근사는 쓰지 않고 우리 근사(주차 시간 포함)를 쓴다
                out.append(estimate_leg(a, b, mode))
                continue
            extra = CAR_OVERHEAD_MIN if mode == "car" and leg["distance_m"] > 0 else 0
            out.append(TravelLeg(leg["minutes"] + extra, leg["distance_m"], "road"))
        return out

    def _put(self, key, leg, now):
        if len(self._cache) >= self._size:
            self._cache.clear()  # 하루 일정 수준에서는 거의 닿지 않는다. 닿으면 통째로 비운다
        self._cache[key] = (now, leg)


_lookup: TravelLookup = HttpTravelLookup(settings.routing_base_url, settings.routing_timeout_sec,
                                         settings.routing_retry_sec, settings.routing_cache_size,
                                         settings.routing_cache_ttl_sec)


def get_travel_lookup() -> TravelLookup:
    """일정 계산이 쓰는 이동시간 조회. 테스트에서는 이 모듈의 _lookup 을 바꿔 끼운다."""
    return _lookup
