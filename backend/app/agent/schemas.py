"""agent 공개 스키마 (앱·CLI 계약). 명세: docs/agent/README.md

채팅 스트림(SSE) 이벤트 — `event: <이름>` / `data: <JSON>`
    start          {"session_id"}
    status         {"text"}                                진행 영역 맨 위 한 줄
    step           Step                                    같은 id 면 그 줄을 갱신
    thinking       {"delta"}                               모델 추론 (지원 모델만)
    text           {"delta"}                               답변 조각
    proposal       ProposalDetail (planner)                변경 제안 카드
    recommendation RecommendationView (planner)            장소 추천 카드
    steps_done     {"count", "summary"}                    진행 영역을 한 줄로 접기
    done           {"text", "cards": [{"type", "id"}], "usage", "message_id", "mode"}  턴 종료
                   (usage: LLM 토큰 사용량, message_id: 저장된 답변 메시지, mode: plan | guide)
    error          {"code", "message"}                     오류 (이 뒤로 이벤트 없음)
"""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

StepKind = Literal["search", "place", "plan", "edit", "rule", "review", "recommend", "proposal", "safety", "route",
                   "memory", "forget", "recall", "research", "guide"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionCreate(Strict):
    plan_id: int


class ChatRequest(Strict):
    message: str = Field(min_length=1, max_length=4000, pattern=r"\S", description="공백만 있는 메시지는 거부")
    place_id: str | None = Field(None, min_length=1, max_length=64,
                                 description="앱 가이드 노트 화면에서 그 장소에 대해 묻는 질문. 그 장소의 노트·근거부터 찾아 짧게 답한다. "
                                             "메시지(사용자·답)에 남아 MessageView.place_id 로 돌아온다")


class TripEvent(BaseModel):
    """trip 의 Event (docs/trip/README.md §3.3). agent 는 type·message·item_ids·payload 만 쓰고 나머지는 그대로 둔다."""

    model_config = ConfigDict(extra="allow")

    type: str = Field(min_length=1, description="delay | safety_raised | ...")
    id: int | None = None
    message: str = ""
    item_ids: list[int] = []
    payload: dict[str, Any] = {}


class EventRequest(Strict):
    """선제 턴 (trip 이 부른다). trip 은 TripState 그대로."""

    event: TripEvent
    trip: dict[str, Any] = Field(description="TripState (docs/trip/README.md §1.3). active 면 동행 모드")


class AcceptRequest(Strict):
    mode: Literal["auto", "overwrite"] = "auto"


class Step(BaseModel):
    id: str
    kind: StepKind
    status: Literal["running", "done", "error"]
    title: str
    detail: str = ""


class SessionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    plan_id: int
    created_at: datetime
    updated_at: datetime


class MessageView(BaseModel):
    """화면에 보일 메시지 (도구 실행 기록·숨김 알림은 제외)."""

    id: int
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime
    place_id: str | None = Field(None, description="장소 맥락이 있는 턴(ChatRequest.place_id)의 메시지면 그 장소. 앱이 장소별로 걸러 보여준다")
    steps: list[Step] = []
    steps_summary: str | None = None
    cards: list[dict[str, Any]] = Field(
        [], description='[{"type": "proposal", "proposal": ProposalDetail} | {"type": "recommendation", '
                        '"recommendation": RecommendationView}] — 조회 시점의 상태')


class SessionDetail(SessionView):
    messages: list[MessageView]


class ChatResult(BaseModel):
    """stream=false 일 때의 응답 (스트림 이벤트를 모은 것)."""

    text: str
    steps: list[Step]
    steps_summary: str | None
    cards: list[dict[str, Any]]
    error: dict[str, str] | None = None
    usage: dict[str, int] | None = Field(None, description="이 턴의 LLM 토큰 사용량 (input, cached, output, reasoning, calls)")
    message_id: int | None = Field(None, description="저장된 답변 메시지 id (선제 턴 결과를 trip 이 이벤트에 기록)")
    mode: Literal["plan", "guide"] | None = Field(None, description="plan: 계획 모드 / guide: 동행 모드 (여행 중)")


class GuidePrepareRequest(Strict):
    """가이드 준비 (docs/docent/README.md §3.3). 본문 생략 = 일정의 장소 중 준비되지 않은(ready 가 아닌) 곳."""

    place_ids: list[str] | None = Field(None, max_length=50, description="이 장소만 (일정에 있어야 한다)")
    force: bool = Field(False, description="true 면 ready 노트도 다시 만든다")
    auto: bool = Field(False, description="자동 준비 (trip 이 부른다): 노트가 ready·pending·preparing·failed 인 장소는 건너뛰고, "
                                          "일정에 없는 place_ids 는 무시한다. AGENT_GUIDE_AUTO=false 면 아무것도 하지 않는다")


class GuidePlaceProgress(BaseModel):
    place_id: str
    title: str
    status: Literal["pending", "preparing", "ready", "failed"]
    error: str = ""
    sources: int = Field(0, description="저장한 출처 수")
    web_searches: int = Field(0, description="이 장소에서 쓴 웹 검색 수")
    seconds: float | None = Field(None, description="이 장소에 걸린 시간")
    usage: dict[str, int] | None = Field(None, description="이 장소의 LLM 토큰 사용량")


class GuideJobView(BaseModel):
    job_id: str
    plan_id: int
    status: Literal["running", "done"]
    total: int = Field(description="준비할 장소 수")
    finished: int = Field(description="끝난 장소 수 (ready + failed)")
    ready: int
    failed: int
    current: str | None = Field(None, description="지금 준비 중인 place_id")
    eta_sec: int | None = Field(None, description="남은 시간 어림 (끝난 장소 평균, 아직 없으면 장소당 20초)")
    places: list[GuidePlaceProgress]
    started_at: datetime
    finished_at: datetime | None = None
    usage: dict[str, int] = Field(description="작업 전체 LLM 토큰 사용량 (input, cached, output, reasoning, calls)")
    queued: list[str] = Field([], description="이 작업이 끝난 뒤 이어서 준비할 place_id (작업 중에 들어온 요청)")


class AgentInfo(BaseModel):
    """디버깅용. 앱은 쓰지 않아도 된다."""

    provider: str
