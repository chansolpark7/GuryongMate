"""agent 의 '지금'. trip·planner·safety 와 같은 시각을 보도록 safety 의 시각을 따른다 (docs/trip/README.md §5).

- 여행 중(동행 모드)이면 TripState.now 를 그대로 쓴다 (trip 도 safety 시각을 따른다).
- 아니면 AGENT_CLOCK=safety 일 때 GET /safety/sim 의 가상 시각 (SAFETY_SOURCE=mock 일 때만 있음, 404 면 시스템 시각).
  매번 묻지 않고 '시스템 시각과의 차이'를 AGENT_CLOCK_CACHE_SEC 동안 기억한다. 접근할 수 없으면 잠시 시스템 시각.
"""
import time as _time
from datetime import datetime, timedelta, timezone

from app.agent.api import ApiError, ServiceApi
from app.agent.config import settings

KST = timezone(timedelta(hours=9))
RETRY_SEC = 20  # safety 에 접근할 수 없으면 이 시간 동안 묻지 않고 시스템 시각

_offset: timedelta | None = None  # None: 가상 시각 아님
_until = 0.0  # monotonic. 이 전까지는 기억한 차이를 쓴다


def reset() -> None:
    """기억한 차이를 버린다 (테스트)."""
    global _offset, _until
    _offset, _until = None, 0.0


def parse(value: str | None) -> datetime | None:
    """ISO 시각 -> KST datetime (tz 없으면 KST 로 본다). 형식이 틀리면 None."""
    try:
        when = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return (when if when.tzinfo else when.replace(tzinfo=KST)).astimezone(KST)


async def now(api: ServiceApi) -> tuple[datetime, bool]:
    """(지금 KST, 가상 시각인지). 실패하지 않는다."""
    global _offset, _until
    if settings.clock != "safety":
        return datetime.now(KST), False
    if _time.monotonic() >= _until:
        try:
            sim = parse((await api.safety_sim()).get("now"))
            _offset = sim - datetime.now(KST) if sim else None
            _until = _time.monotonic() + settings.clock_cache_sec
        except ApiError as e:
            _offset = None  # 404 sim_disabled: 가상 시각이 아님. 그 밖의 오류는 잠시 뒤 다시 묻는다
            _until = _time.monotonic() + (settings.clock_cache_sec if e.status == 404 else RETRY_SEC)
    if _offset is None:
        return datetime.now(KST), False
    return datetime.now(KST) + _offset, True
