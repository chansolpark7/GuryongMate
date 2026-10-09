"""planner 의 '지금'. trip·agent·safety 와 같은 시각을 보도록 safety 의 시각을 따른다 (docs/trip/README.md §5).

safety 가 시각의 단일 원천이다. SAFETY_SOURCE=mock 이면 GET /safety/sim 의 가상 시각(설정한 뒤 실제 시간만큼 흘러감),
아니면 /safety/sim 이 404 라 시스템 시각을 쓴다 (safety 도 이때는 시스템 시각이다).
매번 묻지 않고 '시스템 시각과의 차이'를 PLANNER_CLOCK_CACHE_SEC 동안 기억한다.
"""
import time as _time
from datetime import datetime, timedelta, timezone
from typing import Protocol

import httpx

from app.planner.places import settings

KST = timezone(timedelta(hours=9))
RETRY_SEC = 20  # safety 에 접근할 수 없으면 이 시간 동안 묻지 않고 시스템 시각


class Clock(Protocol):
    def now(self) -> datetime:
        """지금 (KST, tz 포함). 실패하지 않는다."""
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(KST)


class FixedClock:
    """정해 둔 시각 (테스트)."""

    def __init__(self, when: datetime):
        self.when = when

    def now(self) -> datetime:
        return self.when


class SafetyClock:
    def __init__(self, base_url: str, cache_sec: float, timeout: float = 2):
        self._client = httpx.Client(base_url=base_url, timeout=timeout)
        self._cache_sec = cache_sec
        self._offset = timedelta(0)
        self._until = 0.0  # monotonic. 이 전까지는 기억한 차이를 쓴다

    def now(self) -> datetime:
        if _time.monotonic() >= self._until:
            self._refresh()
        return datetime.now(KST) + self._offset

    def _refresh(self) -> None:
        try:
            resp = self._client.get("/safety/sim")
        except httpx.HTTPError:
            self._offset, self._until = timedelta(0), _time.monotonic() + RETRY_SEC
            return
        offset = timedelta(0)
        if resp.is_success:  # 404 sim_disabled: 가상 시각이 아님 -> 시스템 시각
            try:
                sim = datetime.fromisoformat(resp.json()["now"])
                offset = (sim if sim.tzinfo else sim.replace(tzinfo=KST)) - datetime.now(KST)
            except (KeyError, ValueError, TypeError):
                pass
        self._offset, self._until = offset, _time.monotonic() + self._cache_sec


_clock: Clock = (SafetyClock(settings.safety_base_url, settings.clock_cache_sec)
                 if settings.clock == "safety" else SystemClock())


def get_clock() -> Clock:
    """FastAPI 의존성이자 일반 함수. 테스트에서는 dependency_overrides 나 이 모듈의 _clock 을 바꾼다."""
    return _clock
