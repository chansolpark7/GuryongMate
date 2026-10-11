"""LLM 비용 보호: 사용자당 호출 수 제한 (공개 서버, docs/deploy/README.md §3.3).

종류별로 1시간·하루 상한을 둔다 (AGENT_LIMIT_<종류>_HOUR / _DAY, 0 이면 제한 없음 — 개발 기본값).
  chat   대화 턴 (POST /agent/sessions/{id}/chat, 다시 제안 repropose)
  event  선제 턴 (POST /agent/sessions/{id}/events, trip 이 사용자 대신 부른다)
  guide  가이드 준비 작업 (POST /agent/plans/{id}/guide/prepare, 새 작업을 시작할 때만. 장소 여러 곳이 한 번)
넘으면 429 rate_limited (retry_after 초). 공급자와 상관없이 센다 (각본 LLM 도). 시각은 실제 시각이다 (가상 시각 아님).
"""
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.agent.config import settings
from app.agent.errors import AgentError
from app.agent.models import AgentUsage

KINDS = {"chat": "대화", "event": "Agent 선제 안내", "guide": "가이드 준비"}
WINDOWS = (("hour", timedelta(hours=1), "1시간"), ("day", timedelta(days=1), "하루"))


def limits(kind: str) -> list[tuple[timedelta, int, str]]:
    return [(span, n, label) for key, span, label in WINDOWS if (n := getattr(settings, f"limit_{kind}_{key}")) > 0]


def consume(db: Session, user_id: str, kind: str) -> None:
    """한도 안이면 한 번 쓴 것으로 기록하고, 넘었으면 429 rate_limited."""
    active = limits(kind)
    if not active:
        return
    now = datetime.now(UTC)
    for span, n, label in active:
        count, oldest = db.execute(select(func.count(), func.min(AgentUsage.created_at)).where(
            AgentUsage.user_id == user_id, AgentUsage.kind == kind, AgentUsage.created_at > now - span)).one()
        if count >= n:
            retry = max(1, int((oldest + span - now).total_seconds()) + 1)
            raise AgentError(429, "rate_limited",
                             f"{label} {KINDS[kind]} 한도({n}회)를 넘었습니다. {_wait_text(retry)} 뒤에 다시 시도하세요.",
                             kind=kind, limit=n, window=label, retry_after=retry)
    db.execute(delete(AgentUsage).where(AgentUsage.user_id == user_id, AgentUsage.created_at < now - timedelta(days=2)))
    db.add(AgentUsage(user_id=user_id, kind=kind, created_at=now))
    db.commit()


def usage(db: Session, user_id: str) -> dict[str, dict]:
    """종류별 지금 쓴 수와 한도 (GET /agent/limits)."""
    now = datetime.now(UTC)
    out = {}
    for kind in KINDS:
        row = {}
        for key, span, _ in WINDOWS:
            used = db.scalar(select(func.count()).where(AgentUsage.user_id == user_id, AgentUsage.kind == kind,
                                                        AgentUsage.created_at > now - span))
            row[key] = {"used": used, "limit": getattr(settings, f"limit_{kind}_{key}") or None}
        out[kind] = row
    return out


def _wait_text(sec: int) -> str:
    return f"{sec // 3600}시간 {sec % 3600 // 60}분" if sec >= 3600 else f"{max(1, (sec + 59) // 60)}분"
