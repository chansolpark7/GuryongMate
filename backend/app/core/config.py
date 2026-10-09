from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 이 프로세스가 맡을 모듈 (쉼표 구분). compose 에서 서비스마다 지정한다. all = 전부 (테스트·단독 실행용)
    service: str = "all"

    database_url: str = "postgresql+psycopg://guryong:guryong@localhost:5432/guryongmate"
    data_go_kr_service_key: str = ""
    mobile_app: str = "GuryongMate"
    llm_api_key: str = ""
    kakao_rest_api_key: str = ""

    # 인증 (account 서비스가 발급한 JWT 를 각 서비스가 직접 검증). 기본은 로그인 없이도 동작
    jwt_secret: str = "dev-only-change-me"
    auth_required: bool = False
    # 관리 토큰 (공개 서버). 게이트웨이가 데모 조작 API 를 X-Admin-Token 으로 막고(infra/gateway/Caddyfile.prod),
    # 서비스는 AUTH_REQUIRED=true 여도 이 토큰이 맞으면 개발·시연용 API 를 연다 (planner 즉시 안전 점검). 비우면 없음
    admin_token: str = ""

    @property
    def services(self) -> set[str] | None:
        """None 이면 전체 모듈."""
        names = {s.strip() for s in self.service.split(",") if s.strip()}
        return None if not names or "all" in names else names


settings = Settings()
