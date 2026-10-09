"""좁은 범위(구룡포 몇 km)의 거리 계산. 평면 근사로 충분하다."""
import math

M_PER_DEG = 111_320


def distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    k = M_PER_DEG * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot((lat1 - lat2) * M_PER_DEG, (lng1 - lng2) * k)


def _to_segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    if dx == dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def polygon_distance_m(lat: float, lng: float, polygon: list[tuple[float, float]] | list[list[float]]) -> float:
    """점에서 다각형까지 거리(m). 안이면 0. polygon 은 [lng, lat] (GeoJSON 순서, safety /avoid 형식)."""
    if len(polygon) < 2:
        return math.inf
    k = M_PER_DEG * math.cos(math.radians(lat))
    pts = [((p[0] - lng) * k, (p[1] - lat) * M_PER_DEG) for p in polygon]  # 점을 원점으로 한 평면 좌표 (x=동, y=북)
    inside = False
    for (ax, ay), (bx, by) in zip(pts, pts[1:] + pts[:1]):
        if (ay > 0) != (by > 0) and 0 < ax + (0 - ay) * (bx - ax) / (by - ay):
            inside = not inside
    if inside:
        return 0.0
    return min(_to_segment(0, 0, ax, ay, bx, by) for (ax, ay), (bx, by) in zip(pts, pts[1:] + pts[:1]))
