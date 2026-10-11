"""직선거리 근사. 길찾기 엔진이 없거나 길을 찾지 못했을 때만 쓴다."""
import math

from app.routing.config import settings
from app.routing.schemas import Mode, Point


def distance_m(a: Point, b: Point) -> float:
    lat1, lng1, lat2, lng2 = map(math.radians, (a.lat, a.lng, b.lat, b.lng))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def estimate(a: Point, b: Point, mode: Mode, walking_speed_kmh: float) -> tuple[float, float]:
    """(초, 경로거리 m)."""
    route_m = distance_m(a, b) * settings.detour
    kmh = settings.car_kmh if mode == "car" else walking_speed_kmh
    return route_m / (kmh * 1000 / 3600), route_m
