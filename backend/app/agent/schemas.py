"""agent 모듈 공개 스키마 (앱·CLI 계약).

채팅 스트림(SSE) 이벤트 — `event: <이름>` / `data: <JSON>`
    start        {"session_id"}
    thinking     {"delta"}                         모델의 추론 과정 (모델이 지원할 때만, 표시는 선택)
    text         {"delta"}                         답변 텍스트 조각. 오는 대로 이어 붙여 표시
    tool_call    {"id", "name", "arguments"}       도구 호출 시작 (예: "장소 검색 중…" 표시)
    tool_result  {"id", "name", "ok", "summary"}
    proposal     ProposalDetail (planner)          일정 변경 제안. changes/before/after 로 비교 카드 표시
    done         {"text", "proposal_id"}           턴 종료
    error        {"message"}                       오류 (이 뒤로 이벤트 없음)
"""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SessionCreate(BaseModel):
    plan_id: int


class SessionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    plan_id: int
    created_at: datetime
    updated_at: datetime


class MessageView(BaseModel):
    """화면에 보일 메시지 (도구 호출 내부 기록·숨김 알림은 제외)."""

    id: int
    role: Literal["user", "assistant"]
    content: str
    proposal_id: int | None = None
    created_at: datetime


class SessionDetail(SessionView):
    messages: list[MessageView]
    pending_proposal: dict[str, Any] | None = Field(None, description="승인 대기 중인 제안 (planner ProposalDetail)")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class ToolCallLog(BaseModel):
    id: str
    name: str
    arguments: str
    ok: bool
    summary: str


class ChatResult(BaseModel):
    """stream=false 일 때의 응답 (스트림 이벤트를 모은 것)."""

    text: str
    tool_calls: list[ToolCallLog]
    proposal: dict[str, Any] | None = None
    error: str | None = None


class AgentInfo(BaseModel):
    """디버깅용. 앱은 쓰지 않아도 된다."""

    provider: str
