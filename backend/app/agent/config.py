"""Agent 설정. 어떤 LLM 을 쓸지는 서버 환경변수로만 정하고, 앱은 알 필요가 없다."""
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="AGENT_", extra="ignore")

    provider: str = Field("openai", description="openai (기본, gpt-5.4-mini) | local (Ollama 등)")

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
    openai_api: str = Field("responses", description="responses (GPT-5.x 추론+도구) | chat (Chat Completions)")
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-5.4-mini"
    openai_api_key: str = Field("", validation_alias=AliasChoices("AGENT_OPENAI_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY"))
    openai_reasoning_effort: str | None = "low"

    temperature: float | None = None
    request_timeout_sec: float = 300
    max_steps: int = Field(20, description="한 턴에서 LLM 호출(도구 사용 반복) 최대 횟수")
    review_rounds: int = Field(2, description="일정을 바꾼 턴에서 답을 끝내려 할 때 검토 문제가 남아 있으면 다시 고치게 하는 최대 횟수. "
                                              "0 이면 review_plan 도구와 검토 관문을 끈다")
    history_messages: int = Field(40, description="LLM 에 넘길 최근 대화 메시지 수")

    # 같은 컨테이너의 API (planner, places). 모듈 간 규칙상 HTTP 로만 호출한다.
    api_base_url: str = "http://127.0.0.1:8000"

    # 여행 중(동행 모드) 판단과 시계 (docs/agent/README.md §11, docs/trip/README.md §5)
    clock: str = Field("safety", description="safety (GET /safety/sim 가상 시각을 따름) | system")
    clock_cache_sec: float = 5
    trip_timeout_sec: float = Field(3, description="매 턴 trip 상태 조회 제한 시간. 넘기면 계획 모드로")
    memory_timeout_sec: float = Field(3, description="매 턴 docent 메모리 요약 조회 제한 시간. 넘기면 메모리 없이")

    # 외부 웹 검색 (docs/docent/README.md §2.1). 공급자가 내장 검색을 지원하면(OpenAI Responses) 그것을,
    # 아니면 docent /docent/search·/docent/page 를 함수 도구로 쓴다. 비용을 막기 위해 호출 수 상한을 둔다
    # (내장 검색은 검색 호출, 함수 도구는 web_search·read_page 호출을 합쳐서 센다)
    web_search_per_place: int = Field(3, description="가이드 준비 작업에서 장소 하나당 웹 검색 상한. 0 이면 웹 검색 안 함")
    web_search_per_turn: int = Field(2, description="동행 모드 대화 한 턴의 웹 검색 상한. 0 이면 웹 검색 안 함")
    web_search_context: str = Field("low", description="OpenAI 내장 검색의 search_context_size (low | medium | high)")

    # 가이드 준비 작업 (docs/docent/README.md §3.3)
    guide_max_steps: int = Field(6, description="장소 하나의 LLM 호출 최대 횟수 (마지막 호출은 도구 없이 노트 JSON 강제)")
    guide_passages: int = Field(6, description="장소마다 미리 찾아 주는 근거 조각 수")


settings = AgentSettings()
