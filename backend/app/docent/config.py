from pydantic_settings import BaseSettings, SettingsConfigDict


class DocentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="DOCENT_", extra="ignore")

    # 다른 서비스(planner) 호출 주소. compose 는 게이트웨이(http://gateway)를 넣는다
    api_base_url: str = "http://127.0.0.1:8000"
    api_timeout_sec: float = 10

    # 외부 검색 (§2.1). 네이버 개발자센터 검색 API 키. 비어 있으면 /docent/search 는 503
    naver_client_id: str = ""
    naver_client_secret: str = ""
    naver_base_url: str = "https://openapi.naver.com"
    search_timeout_sec: float = 8

    # 사용자당 외부 호출 수 제한 (§2.4, app/docent/limits.py). 캐시에 있는 결과는 세지 않는다. 0 이면 제한 없음.
    # agent 가 사용자 대신 부르는 양(가이드 준비: 장소당 웹 검색 AGENT_WEB_SEARCH_PER_PLACE=3, 대화 한 턴 2)을 막지 않게 넉넉히
    limit_search_hour: int = 200
    limit_search_day: int = 1000
    limit_page_hour: int = 200
    limit_page_day: int = 1000

    # 검색·페이지 결과 캐시 (같은 질의·URL 은 하루)
    cache_ttl_sec: float = 86400

    # 페이지 읽기 안전 (§2.3)
    page_timeout_sec: float = 8       # 리다이렉트 포함 전체
    page_max_bytes: int = 2 * 1024 * 1024
    page_max_chars: int = 8000
    page_max_redirects: int = 5


settings = DocentSettings()
