"""장소 간 이동시간 추정. 지금은 직선거리 기반 근사이며, 나중에 Kakao 경로 API 로 교체한다."""
import math

from app.planner.schemas import PlaceInfo

DETOUR = 1.3  # 직선거리 -> 실제 경로 보정계수
WALK_M_PER_MIN = 70  # 약 4.2km/h (어르신 동행 고려해 느리게)
CAR_M_PER_MIN = 500  # 약 30km/h (시내·해안도로 평균)
CAR_OVERHEAD_MIN = 5  # 주차·승하차


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
