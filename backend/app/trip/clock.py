"""trip 의 '지금'. trip·agent·planner·safety 가 같은 시각을 보도록 safety 의 시각을 따른다 (docs/trip/README.md §5).

TRIP_CLOCK=safety 이면 GET /safety/sim 의 now(설정한 뒤 실제 시간만큼 흘러감)와 시스템 시각의 차이를
TRIP_CLOCK_CACHE_SEC 동안 기억해 쓴다. sim 이 꺼져 있으면(404) 시스템 시각, 접근할 수 없으면 20초 동안 시스템 시각.
(planner 의 clock.py 와 같은 규칙. 모듈 간 import 금지라 따로 둔다)
"""
import time as _time
from datetime import datetime, timedelta, timezone
from typing import Protocol

import httpx

from app.trip.config import settings

KST = timezone(timedelta(hours=9))
RETRY_SEC = 20


class Clock(Protocol):
    def now(self) -> datetime:
        """지금 (KST, tz 포함). 실패하지 않는다."""
        ...

    @property
    def kind(self) -> str:
        """system | sim (TripState.clock)"""
        ...


class SystemClock:
    kind = "system"

    def now(self) -> datetime:
        return datetime.now(KST)


class FixedClock:
    """정해 둔 시각 (테스트). when 을 바꿔 시간을 흘린다."""
    kind = "sim"

    def __init__(self, when: datetime):
        self.when = when

    def now(self) -> datetime:
        return self.when


class SafetyClock:
    def __init__(self, base_url: str, cache_sec: float, timeout: float = 2):
        self._client = httpx.Client(base_url=base_url, timeout=timeout)
        self._cache_sec = cache_sec
        self._offset = timedelta(0)
        self._sim = False
        self._until = 0.0  # monotonic. 이 전까지는 기억한 차이를 쓴다

    @property
    def kind(self) -> str:
        return "sim" if self._sim else "system"

    def now(self) -> datetime:
        if _time.monotonic() >= self._until:
            self._refresh()
        return datetime.now(KST) + self._offset

    def _refresh(self) -> None:
        try:
            resp = self._client.get("/safety/sim")
        except httpx.HTTPError:
            self._offset, self._sim, self._until = timedelta(0), False, _time.monotonic() + RETRY_SEC
            return
        offset, sim = timedelta(0), False
        if resp.is_success:  # 404 sim_disabled: 가상 시각이 아님 -> 시스템 시각
            try:
                when = datetime.fromisoformat(resp.json()["now"])
                offset = (when if when.tzinfo else when.replace(tzinfo=KST)) - datetime.now(KST)
                sim = True
            except (KeyError, ValueError, TypeError):
                pass
        self._offset, self._sim, self._until = offset, sim, _time.monotonic() + self._cache_sec


_clock: Clock = (SafetyClock(settings.safety_base_url, settings.clock_cache_sec)
                 if settings.clock == "safety" else SystemClock())


def get_clock() -> Clock:
    """FastAPI 의존성이자 일반 함수. 테스트에서는 이 모듈의 _clock 을 바꾼다."""
    return _clock
