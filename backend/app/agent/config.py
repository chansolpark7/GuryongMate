"""Agent 설정. 어떤 LLM 을 쓸지는 서버 환경변수로만 정하고, 앱은 알 필요가 없다."""
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="AGENT_", extra="ignore")

    provider: str = Field("local", description="local | openai")

    # 로컬 LLM. 컨테이너에서 호스트의 Ollama 로 접속.
    #   local_api=ollama : Ollama 네이티브 API (컨텍스트 길이 지정 가능, 권장)
    #   local_api=openai : OpenAI 호환 서버 (LM Studio, vLLM, llama.cpp 등). base_url 은 .../v1 까지
    local_api: str = "ollama"
    local_base_url: str = "http://host.docker.internal:11434"
    local_model: str = "qwen3.6:35b"
    local_api_key: str = ""
    local_num_ctx: int = 16384  # Ollama 기본값 4096 은 시스템 프롬프트+도구 결과에 부족
    local_think: bool = False  # 추론(thinking) 사용. 켜면 도구 사용이 신중해지지만 느려진다

    # ChatGPT (OpenAI API)
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-5-mini"
    openai_api_key: str = Field("", validation_alias=AliasChoices("AGENT_OPENAI_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY"))
    openai_reasoning_effort: str | None = "low"

    temperature: float | None = None
    request_timeout_sec: float = 300
    max_steps: int = Field(8, description="한 턴에서 LLM 호출(도구 사용 반복) 최대 횟수")
    history_messages: int = Field(40, description="LLM 에 넘길 최근 대화 메시지 수")

    # 같은 컨테이너의 API (planner, places). 모듈 간 규칙상 HTTP 로만 호출한다.
    api_base_url: str = "http://127.0.0.1:8000"


settings = AgentSettings()
