"""위험 구역 조회 (safety 서비스 HTTP API) 와 경로-구역 교차 판정."""
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import httpx

from app.routing.config import settings
from app.routing.schemas import Point, ZoneHit

Coord = tuple[float, float]  # (lng, lat)


class HazardsUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class AvoidZone:
    id: str
    name: str
    kind: str
    risk: str
    reason: str
    polygon: tuple[Coord, ...]  # 닫힌 고리 [lng, lat]

    def hit(self) -> ZoneHit:
        return ZoneHit(id=self.id, name=self.name, kind=self.kind, risk=self.risk, reason=self.reason)


class HazardLookup(Protocol):
    def zones(self, now: datetime | None) -> list[AvoidZone]:
        """지금 피할 구역. safety 에 접근할 수 없으면 HazardsUnavailable."""
        ...


class HttpHazardLookup:
    def __init__(self, base_url: str, timeout: float):
        self._client = httpx.Client(base_url=base_url, timeout=timeout)

    def zones(self, now: datetime | None) -> list[AvoidZone]:
        params = {"now": now.isoformat()} if now else {}
        try:
            resp = self._client.get("/safety/avoid", params=params)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise HazardsUnavailable(str(e)) from e
        return [AvoidZone(id=z["id"], name=z["name"], kind=z["kind"], risk=z["risk"], reason=z["reason"],
                          polygon=tuple(tuple(c) for c in z["polygon"])) for z in resp.json()["zones"]]


_lookup = HttpHazardLookup(settings.safety_base_url, settings.safety_timeout_sec)


def get_hazard_lookup() -> HazardLookup:
    """FastAPI 의존성. 테스트에서는 dependency_overrides 로 가짜 구현을 넣는다."""
    return _lookup


# ---------- 기하 (구룡포 규모라 평면 근사로 충분) ----------

def contains(poly: tuple[Coord, ...], p: Coord) -> bool:
    inside = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:]):
        if (y1 > p[1]) != (y2 > p[1]) and p[0] < (x2 - x1) * (p[1] - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def _cross(o: Coord, a: Coord, b: Coord) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _segments_cross(a: Coord, b: Coord, c: Coord, d: Coord) -> bool:
    return (_cross(a, b, c) * _cross(a, b, d) < 0) and (_cross(c, d, a) * _cross(c, d, b) < 0)


def line_hits(line: list[Coord], poly: tuple[Coord, ...]) -> bool:
    """경로선이 다각형 안을 지나는지. 꼭짓점이 밖에 있어도 변을 가로지르면 지난 것으로 본다."""
    if any(contains(poly, p) for p in line):
        return True
    edges = list(zip(poly, poly[1:]))
    return any(_segments_cross(a, b, c, d) for a, b in zip(line, line[1:]) for c, d in edges)


def point_in(zone: AvoidZone, p: Point) -> bool:
    return contains(zone.polygon, (p.lng, p.lat))
