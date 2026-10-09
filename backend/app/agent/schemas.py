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

StepKind = Literal["search", "place", "plan", "edit", "rule", "review", "recommend", "proposal", "safety", "route"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionCreate(Strict):
    plan_id: int


class ChatRequest(Strict):
    message: str = Field(min_length=1, max_length=4000, pattern=r"\S", description="공백만 있는 메시지는 거부")


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


class AgentInfo(BaseModel):
    """디버깅용. 앱은 쓰지 않아도 된다."""

    provider: str
