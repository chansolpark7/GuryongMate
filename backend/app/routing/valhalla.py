"""Valhalla HTTP 클라이언트 (services/valhalla). 응답을 우리 단위(초·m)로 바꾸는 것까지만 한다."""
import time as _time

import httpx

from app.routing.config import settings
from app.routing.schemas import Mode, Point


class EngineUnavailable(RuntimeError):
    """엔진에 접근할 수 없음 (꺼짐·타임아웃·5xx)."""


class NoRoute(RuntimeError):
    """엔진은 살아 있지만 이 요청의 길을 찾지 못함 (도로에서 너무 멂, 막힘 등)."""


def costing(mode: Mode, walking_speed_kmh: float | None, easy_walk: bool) -> tuple[str, dict]:
    if mode == "car":
        return "auto", {}
    opts: dict = {"walking_speed": walking_speed_kmh or (
        settings.easy_walking_speed_kmh if easy_walk else settings.walking_speed_kmh)}
    if easy_walk:
        opts |= {"step_penalty": settings.easy_step_penalty_sec, "use_hills": settings.easy_use_hills}
    return "pedestrian", opts


def decode_polyline6(encoded: str) -> list[tuple[float, float]]:
    """Valhalla 경로 모양 (정밀도 6자리 encoded polyline) -> [(lng, lat)]."""
    coords, index, lat, lng = [], 0, 0, 0
    while index < len(encoded):
        for is_lng in (False, True):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if is_lng:
                lng += delta
            else:
                lat += delta
        coords.append((lng / 1e6, lat / 1e6))
    return coords


def _loc(p: Point) -> dict:
    return {"lat": p.lat, "lon": p.lng}


class ValhallaClient:
    def __init__(self, base_url: str, timeout: float, retry_sec: float):
        self._client = httpx.Client(base_url=base_url, timeout=timeout)
        self._retry = retry_sec
        self._down_until = 0.0
        self.last_error: str | None = None

    def _post(self, path: str, body: dict) -> dict:
        if _time.monotonic() < self._down_until:
            raise EngineUnavailable(self.last_error or "길찾기 엔진 응답 없음")
        try:
            resp = self._client.post(path, json=body)
        except httpx.HTTPError as e:
            self._fail(f"{type(e).__name__}: {e}")
        if resp.status_code == 400:
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            raise NoRoute(data.get("error") or resp.text[:200])
        if not resp.is_success:
            self._fail(f"{path} -> {resp.status_code}")
        self.last_error = None
        return resp.json()

    def _fail(self, error: str):
        self.last_error = error
        self._down_until = _time.monotonic() + self._retry
        raise EngineUnavailable(error)

    def status(self) -> dict:
        try:
            resp = self._client.get("/status", params={"verbose": "true"})
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            raise EngineUnavailable(str(e)) from e

    def matrix(self, sources: list[Point], targets: list[Point], cost: str, opts: dict
               ) -> list[list[tuple[float, float] | None]]:
        """[i][j] = (초, m) 또는 길이 없으면 None."""
        data = self._post("/sources_to_targets", {
            "sources": [_loc(p) for p in sources], "targets": [_loc(p) for p in targets],
            "costing": cost, "costing_options": {cost: opts}, "units": "kilometers"})
        rows = data["sources_to_targets"]
        if isinstance(rows, dict):  # verbose=false 형식
            return [[None if t is None or d is None else (t, d * 1000) for t, d in zip(tr, dr)]
                    for tr, dr in zip(rows["durations"], rows["distances"])]
        return [[None if c.get("time") is None or c.get("distance") is None else (c["time"], c["distance"] * 1000)
                 for c in row] for row in rows]

    def route(self, stops: list[Point], cost: str, opts: dict, exclude: list[list[tuple[float, float]]] | None = None
              ) -> dict:
        """Valhalla trip 객체. exclude 는 [lng, lat] 고리(닫힌 다각형) 목록."""
        body = {"locations": [_loc(p) for p in stops], "costing": cost, "costing_options": {cost: opts},
                "units": "kilometers", "language": "en-US", "directions_type": "maneuvers"}
        if exclude:
            body["exclude_polygons"] = [[list(c) for c in ring] for ring in exclude]
        return self._post("/route", body)["trip"]


client = ValhallaClient(settings.valhalla_url, settings.valhalla_timeout_sec, settings.valhalla_retry_sec)
