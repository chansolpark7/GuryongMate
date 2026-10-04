"""safety 설정. 키는 backend/.env 에 둔다 (DT_SERVICE_KEY, DATA_GO_KR_SERVICE_KEY)."""
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SafetySettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SAFETY_", extra="ignore")

    # live: 실제 API 호출 / mock: 키 없이 고정 데이터 (mock_scenario 로 평상시·악천후 선택)
    source: str = "live"
    mock_scenario: str = Field("calm", description="calm | storm")

    # E-UM 디지털트윈 포항 인증키. URL 인코딩된 값 그대로 (디코딩하면 40102)
    dt_service_key: str = Field("", validation_alias=AliasChoices("SAFETY_DT_SERVICE_KEY", "DT_SERVICE_KEY"))
    dt_base_url: str = "https://genix.pohang-eum.kr/dpg"
    # 공공데이터포털 공통키 (기상청 단기예보·기상특보·조석예보)
    data_go_kr_service_key: str = Field(
        "", validation_alias=AliasChoices("SAFETY_DATA_GO_KR_SERVICE_KEY", "DATA_GO_KR_SERVICE_KEY"))

    # 구룡포 기준값 (docs/safety/README.md 참고)
    kma_nx: int = 106  # 기상청 단기예보 격자 (구룡포읍)
    kma_ny: int = 94
    # 특보 발효 현황에서 구룡포에 해당하는 구역 이름 (육상: 포항 / 앞바다: 경북남부앞바다 / 먼바다: 동해남부북쪽…)
    # "동해남부" 만 쓰면 부산·울산 쪽 동해남부남쪽 해역까지 걸린다
    warn_keywords: str = "포항,경북남부앞바다,동해남부북쪽"
    tide_obs_code: str = "SO_0573"  # 조석예보 지점: 양포항 (구룡포에서 12km, 같은 동쪽 해안)

    # 장소 좌표 조회 (places 서비스 HTTP API)
    places_base_url: str = "http://127.0.0.1:8000"

    # 길찾기 우회 구역 (/safety/avoid). 침수는 센서 주변 이 반경만 피한다.
    # 판단용 반경(geo.FLOOD_RADIUS_M=200m)을 그대로 쓰면 센서가 몰린 중심가 도로가 전부 막힌다
    avoid_flood_radius_m: float = 80

    timeout_sec: float = 10
    # 캐시 (초). 외부 API 호출 한도(개발계정 하루 10,000건)와 센서 갱신 간격을 고려
    ttl_sensors: float = 120
    ttl_breakwater: float = 600
    ttl_forecast: float = 1800
    ttl_warnings: float = 300
    ttl_tide: float = 86400
    ttl_error: float = 30  # 실패도 잠깐 기억해서 장애 시 매 요청마다 다시 부르지 않는다


settings = SafetySettings()
