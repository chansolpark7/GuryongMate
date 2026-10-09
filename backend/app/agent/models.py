from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AgentSession(Base):
    """한 계획에 대한 Agent 대화. 여행 전·중 맥락이 이 세션에 이어진다."""

    __tablename__ = "agent_session"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    plan_id: Mapped[int] = mapped_column(Integer, index=True)  # planner 계획 id (HTTP API 로만 접근)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentMessage(Base):
    """LLM 대화 기록 (OpenAI 메시지 형식). hidden 은 LLM 에만 주고 화면에는 안 보이는 알림."""

    __tablename__ = "agent_message"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("agent_session.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(10))  # user | assistant | tool
    content: Mapped[str] = mapped_column(Text, default="")
    tool_calls: Mapped[list[dict] | None] = mapped_column(JSONB, nullable=True)  # assistant 의 도구 호출
    tool_call_id: Mapped[str] = mapped_column(String(64), default="")  # tool 응답이 가리키는 호출
    tool_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    # 턴의 마지막 assistant 메시지에만: 거친 단계와 만들어진 카드 [{"type": "proposal"|"recommendation", "id"}]
    steps: Mapped[list[dict] | None] = mapped_column(JSONB, nullable=True)
    cards: Mapped[list[dict] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentEventTurn(Base):
    """선제 턴의 결과 (POST /sessions/{id}/events). trip 이 같은 이벤트를 다시 보내면 LLM 턴을 다시 돌리지 않고 이것을 돌려준다.
    오류 없이 끝난 턴만 남긴다 (오류로 끝난 이벤트는 다시 보내면 다시 돈다)."""

    __tablename__ = "agent_event_turn"

    session_id: Mapped[str] = mapped_column(ForeignKey("agent_session.id", ondelete="CASCADE"), primary_key=True)
    event_id: Mapped[int] = mapped_column(Integer, primary_key=True)  # trip 이벤트 id
    result: Mapped[dict] = mapped_column(JSONB)  # ChatResult 그대로
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
