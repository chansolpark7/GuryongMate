"""외부 검색·페이지 읽기의 사용자당 호출 수 제한 (공개 서버 비용·네이버 검색 일일 한도 보호, docs/docent/README.md §2.4).

종류별로 1시간·하루 상한을 둔다 (DOCENT_LIMIT_<종류>_HOUR / _DAY, 0 이면 제한 없음).
  search  GET /docent/search (외부 검색 공급자 호출)
  page    GET /docent/page   (외부 페이지 요청)
캐시에 있는 결과는 외부로 나가지 않으므로 세지 않는다. agent 가 사용자 대신 부르는 호출(가이드 준비·동행 모드 웹 검색)도
그 사용자의 토큰으로 오므로 그 사용자 몫으로 센다. 넘으면 429 rate_limited (retry_after 초). 시각은 실제 시각이다.
agent 의 app/agent/limits.py 와 같은 방식 (모듈 간 규칙상 코드는 따로 둔다).
"""
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.docent.config import settings
from app.docent.errors import DocentError
from app.docent.models import DocentUsage

KINDS = {"search": "외부 검색", "page": "페이지 읽기"}
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
        count, oldest = db.execute(select(func.count(), func.min(DocentUsage.created_at)).where(
            DocentUsage.user_id == user_id, DocentUsage.kind == kind, DocentUsage.created_at > now - span)).one()
        if count >= n:
            retry = max(1, int((oldest + span - now).total_seconds()) + 1)
            raise DocentError(429, "rate_limited",
                              f"{label} {KINDS[kind]} 한도({n}회)를 넘었습니다. {_wait_text(retry)} 뒤에 다시 시도하세요.",
                              kind=kind, limit=n, window=label, retry_after=retry)
    db.execute(delete(DocentUsage).where(DocentUsage.user_id == user_id, DocentUsage.created_at < now - timedelta(days=2)))
    db.add(DocentUsage(user_id=user_id, kind=kind, created_at=now))
    db.commit()


def _wait_text(sec: int) -> str:
    return f"{sec // 3600}시간 {sec % 3600 // 60}분" if sec >= 3600 else f"{max(1, (sec + 59) // 60)}분"
