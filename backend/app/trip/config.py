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

    # 일정 밀림 (§3.2): 다음 일정의 예상 시작이 계획보다 이만큼(분) 늦으면 delay(behind_schedule). 이 간격마다 다시 묻는다. 0 이면 끔
    behind_min: float = 15
    # 위반 해소 (§4.1): 알린 위반이 재계산에서 이만큼(분, trip 의 지금 기준) 이어서 사라지면 해소로 보고, 다시 생기면 또 알린다
    resolve_min: float = 10

    # Event Policy (§4)
    proactive_cooldown_min: float = 10
    # 선제 턴 전송 실패(agent 접근 불가·오류): 이 횟수만큼 실패하면 failed 로 남기고 더 보내지 않는다.
    # 다시 보내기 전 기다림은 retry_sec 에서 실패마다 두 배 (trip 의 지금 기준). agent_busy(응답 중)는 실패로 세지 않는다
    proactive_max_attempts: int = 3
    proactive_retry_sec: float = 30
    # 자동 종료 (§1.2): 계획 종료 + 이만큼(분) 지나면. 다만 최근 위치(active_min 안)가 있으면 미룬다
    auto_end_min: float = 120
    # 앱 활동 (§4.1): 마지막 위치 전송·이벤트 조회가 이보다(분, trip 의 지금 기준) 오래면 단계 3 전송을 보류한다.
    # 자동 종료를 미루는 '최근 위치'의 기준도 같다. 0 이면 끔 (늘 활동 중으로 본다)
    active_min: float = 15

    # 다른 서비스 (compose 가 게이트웨이 주소를 넣는다). 컨테이너 안에서는 항상 8000 번 포트
    planner_base_url: str = "http://127.0.0.1:8000"
    safety_base_url: str = "http://127.0.0.1:8000"
    agent_base_url: str = "http://127.0.0.1:8000"
    docent_base_url: str = "http://127.0.0.1:8000"
    timeout_sec: float = 15
    agent_timeout_sec: float = 300  # 선제 턴은 LLM 응답이 끝날 때까지 기다린다


settings = TripSettings()
