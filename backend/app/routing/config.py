"""routing 설정. compose 가 ROUTING_VALHALLA_URL, ROUTING_SAFETY_BASE_URL 을 넣어 준다."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class RoutingSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="ROUTING_", extra="ignore")

    # 길찾기 엔진 (services/valhalla). 응답이 없으면 직선거리 근사로 답한다
    valhalla_url: str = "http://127.0.0.1:8002"
    valhalla_timeout_sec: float = 5
    # 엔진이 죽었을 때 요청마다 타임아웃을 기다리지 않도록 이 시간 동안은 바로 근사로 답한다
    valhalla_retry_sec: float = 20

    # 위험 구역 (safety 서비스 HTTP API)
    safety_base_url: str = "http://127.0.0.1:8000"
    safety_timeout_sec: float = 5

    # 걷는 속도 기본값. 4.2km/h = 70m/분 (어르신 동행 고려, Valhalla 기본 5.1 보다 느리게)
    walking_speed_kmh: float = 4.2
    # 걷기 편한 길 (easy_walk): 계단·언덕을 피하고 조금 더 천천히
    easy_walking_speed_kmh: float = 3.6
    easy_step_penalty_sec: float = 60
    easy_use_hills: float = 0.1

    # 직선거리 근사 (엔진이 없거나 길을 못 찾을 때)
    detour: float = 1.3
    car_kmh: float = 30

    # 행렬 결과 캐시 (도로는 자주 바뀌지 않는다)
    cache_size: int = 50_000
    cache_ttl_sec: float = 86_400


settings = RoutingSettings()
