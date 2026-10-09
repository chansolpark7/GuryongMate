"""trip 백그라운드 틱 (main.py 의 lifespan 이 running() 을 연다). 명세: docs/trip/README.md §4.3

TRIP_TICK_SEC 초마다 진행 중인 여행일마다: 지연 재판정 → planner 안전 경고 수집 → 자동 종료 → 큐에 있는 단계 3 전송.
0 이면 하지 않는다 (코드 기본값. compose 의 trip 서비스에서만 켠다 -> 테스트·다른 서비스에서는 돌지 않음).
"""
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from app.trip import service
from app.trip.clients import get_clients
from app.trip.clock import get_clock
from app.trip.config import settings

log = logging.getLogger("uvicorn.error")


@contextmanager
def running() -> Iterator[None]:
    interval = settings.tick_sec
    if interval <= 0:
        yield
        return
    stop = threading.Event()

    def loop():
        while not stop.wait(interval):
            try:
                service.tick_all(get_clients(), get_clock())
            except Exception:
                log.exception("trip 틱 실패")

    t = threading.Thread(target=loop, name="trip-tick", daemon=True)
    t.start()
    log.info("trip 틱 시작 (%g초마다)", interval)
    try:
        yield
    finally:
        stop.set()
        t.join(timeout=5)
