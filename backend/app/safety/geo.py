"""구룡포 안전 구역의 고정 정보. 좌표는 E-UM API 실제 응답(2026-10-04)에서 가져왔다.

- 방파제: AI CCTV·스피커 좌표를 이은 선 + 버퍼. 위험구역 폴리곤은 E-UM 이 제공하지 않아 우리가 정의했다.
- 침수 감시 구역: 수위 센서(맨홀·지표면·하천) 주변 반경. 실시간 센서 응답이 오면 그 좌표를 쓰고,
  여기 값은 예보만 보는 경우(먼 날짜)와 센서 API 장애 때 쓴다.
"""
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Zone:
    id: str
    name: str
    kind: str  # breakwater | flood
    path: tuple[tuple[float, float], ...]  # (lat, lng). 점 하나면 원, 여러 개면 선
    radius_m: float


BREAKWATER_NORTH = Zone("breakwater_north", "구룡포 북방파제", "breakwater", (
    (35.985730, 129.557888), (35.987397, 129.558444), (35.988078, 129.559279),
    (35.988738, 129.560098), (35.989056, 129.560639)), 60)
BREAKWATER_SOUTH = Zone("breakwater_south", "구룡포 남방파제", "breakwater", (
    (35.982560, 129.557068), (35.984143, 129.558226), (35.984879, 129.559033),
    (35.985344, 129.559608), (35.985583, 129.560139)), 60)
BREAKWATERS = (BREAKWATER_NORTH, BREAKWATER_SOUTH)
# 이상행동 통계(S06_006)의 south/north 키
BREAKWATER_SIDE = {"north": BREAKWATER_NORTH, "south": BREAKWATER_SOUTH}

FLOOD_RADIUS_M = 200
# (E-UM 실시간 수위 API 의 장비 id, 종류, 이름, 위도, 경도). 강우량계는 구역이 아니라 강수 참고값
WATER_SENSORS = (
    (1, "RIVER", "구룡포교_하천수위계", 35.987244, 129.550467),
    (2, "HOLE", "하나과메기_스마트맨홀", 35.985929, 129.549278),
    (3, "HOLE", "로터리종합건재_스마트맨홀", 35.987373, 129.551115),
    (4, "HOLE", "구룡포수협_스마트맨홀", 35.991235, 129.557327),
    (5, "RAIN", "구룡포읍행정복지센터_강우량계", 35.984987, 129.546115),
    (7, "ROAD", "하나과메기_지표면 수위계", 35.986115, 129.549245),
    (8, "ROAD", "구룡포파출소_지표면 수위계", 35.988698, 129.553056),
    (9, "ROAD", "해양경찰서_지표면 수위계", 35.989470, 129.554646),
    (10, "ROAD", "구룡포환승센터_지표면 수위계", 35.990690, 129.556057),
    (11, "ROAD", "구룡포수협_지표면 수위계", 35.991109, 129.557354),
)
FLOOD_SENSOR_TYPES = {"HOLE", "ROAD", "RIVER"}


def flood_zone(sensor_id: int, name: str, lat: float, lng: float) -> Zone:
    return Zone(f"flood_{sensor_id}", f"{name} 주변", "flood", ((lat, lng),), FLOOD_RADIUS_M)


STATIC_FLOOD_ZONES = tuple(flood_zone(i, n, la, lo) for i, t, n, la, lo in WATER_SENSORS if t in FLOOD_SENSOR_TYPES)


def distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat = math.radians((a[0] + b[0]) / 2)
    dy = (a[0] - b[0]) * 111_320
    dx = (a[1] - b[1]) * 111_320 * math.cos(lat)
    return math.hypot(dx, dy)


def _to_segment(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    """점 p 에서 선분 ab 까지 거리(m). 좁은 범위라 평면 근사."""
    k = 111_320 * math.cos(math.radians(p[0]))
    ax, ay = (a[1] - p[1]) * k, (a[0] - p[0]) * 111_320
    bx, by = (b[1] - p[1]) * k, (b[0] - p[0]) * 111_320
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / (dx * dx + dy * dy)))
    return math.hypot(ax + t * dx, ay + t * dy)


def distance_to_zone(p: tuple[float, float], zone: Zone) -> float:
    if len(zone.path) == 1:
        return distance_m(p, zone.path[0])
    return min(_to_segment(p, a, b) for a, b in zip(zone.path, zone.path[1:]))


def _circle(c: tuple[float, float], radius_m: float, n: int = 16) -> list[tuple[float, float]]:
    """(lat, lng) 중심 원을 n각형 꼭짓점 [(lng, lat)] 으로."""
    k = 111_320 * math.cos(math.radians(c[0]))
    return [(c[1] + radius_m * math.cos(a) / k, c[0] + radius_m * math.sin(a) / 111_320)
            for a in (2 * math.pi * i / n for i in range(n))]


def _hull(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """볼록 껍질 (반시계 방향)."""
    pts = sorted(set(pts))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list = []
    upper: list = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def zone_polygon(zone: Zone, radius_m: float | None = None) -> list[tuple[float, float]]:
    """길찾기 우회용 닫힌 다각형 [(lng, lat), ..., 첫 점]. 점 구역은 원, 선 구역은 선을 감싼 볼록 다각형."""
    r = zone.radius_m if radius_m is None else radius_m
    ring = _hull([v for c in zone.path for v in _circle(c, r)])
    return ring + ring[:1]


def zones_at(p: tuple[float, float], zones) -> list[Zone]:
    return [z for z in zones if distance_to_zone(p, z) <= z.radius_m]
