"""trip 설정 (backend/.env, 접두사 TRIP_). 명세: docs/trip/README.md §9"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class TripSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="TRIP_", extra="ignore")

    # 백그라운드 틱(초): 지연 재판정·안전 경고 수집·큐 전송·자동 종료. 0 이면 끔 (코드 기본값·테스트. compose 의 trip 서비스만 켠다)
    tick_sec: float = 0

    # 지금 시각 (§5). safety: safety 의 가상 시각을 따른다 (실패하면 시스템 시각) / system: 시스템 시각
    clock: str = "safety"
    clock_cache_sec: float = 5

    # 판정 (§3.2)
    arrive_radius_m: float = 60  # + accuracy, 최대 max_radius_m
    max_radius_m: float = 150
    arrive_dwell_sec: float = 90
    depart_dwell_sec: float = 120
    max_accuracy_m: float = 100  # 이보다 부정확한 점은 버린다
    default_accuracy_m: float = 30  # accuracy 를 보내지 않은 점
    hazard_near_m: float = 50
    hazard_cache_sec: float = 5  # 이 시간 동안은 safety 상황(fingerprint)을 다시 묻지 않는다 (틱마다 새로 본다)

    # Event Policy (§4)
    proactive_cooldown_min: float = 10
    auto_end_min: float = 120

    # 다른 서비스 (compose 가 게이트웨이 주소를 넣는다). 컨테이너 안에서는 항상 8000 번 포트
    planner_base_url: str = "http://127.0.0.1:8000"
    safety_base_url: str = "http://127.0.0.1:8000"
    agent_base_url: str = "http://127.0.0.1:8000"
    timeout_sec: float = 15
    agent_timeout_sec: float = 300  # 선제 턴은 LLM 응답이 끝날 때까지 기다린다


settings = TripSettings()
