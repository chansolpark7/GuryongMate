"""account 설정. JWT_SECRET·AUTH_REQUIRED 는 공용 설정(app.core.config)을 쓴다."""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AccountSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="ACCOUNT_", extra="ignore")

    google_client_ids: str = Field("", description="허용할 Google OAuth 클라이언트 ID (쉼표 구분). 비면 Google 로그인 불가")
    google_certs_url: str = "https://www.googleapis.com/oauth2/v3/certs"
    access_ttl_min: int = 60
    refresh_ttl_days: int = 30
    max_failures: int = 5
    lock_min: int = 15
    # 탈퇴 시 다른 서비스 데이터 삭제를 부를 주소 (compose 에서는 게이트웨이)
    api_base_url: str = "http://127.0.0.1:8000"

    @property
    def client_ids(self) -> list[str]:
        return [c.strip() for c in self.google_client_ids.split(",") if c.strip()]


settings = AccountSettings()
