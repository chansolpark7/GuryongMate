from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://guryong:guryong@localhost:5432/guryongmate"
    data_go_kr_service_key: str = ""
    mobile_app: str = "GuryongMate"
    llm_api_key: str = ""
    kakao_rest_api_key: str = ""


settings = Settings()
