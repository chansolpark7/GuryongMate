"""planner 백그라운드 작업 (main.py 의 lifespan 이 running() 을 연다).

- 안전 점검: PLANNER_SAFETY_WATCH_SEC 초마다 safety 상황을 확인하고, 바뀌었을 때만 일정을 다시 점검한다 (safety_watch.py).
  0 이면 하지 않는다 (기본값. compose 의 planner 서비스에서만 켠다 -> 테스트·다른 서비스에서는 돌지 않음).
"""
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from app.planner.places import settings
from app.planner.safety_client import SafetyUnavailable
from app.planner.safety_watch import get_watcher

log = logging.getLogger("uvicorn.error")


@contextmanager
def running() -> Iterator[None]:
    interval = settings.safety_watch_sec
    if interval <= 0:
        yield
        return
    stop = threading.Event()

    def loop():
        while not stop.wait(interval):
            try:
                report = get_watcher().tick()
                if report.ran:
                    log.info("planner 안전 점검 (%s): 계획 %d개, 경고 %d건", report.reason, report.plans,
                             len(report.alerts))
            except SafetyUnavailable as e:
                log.warning("planner 안전 점검: safety 에 접근할 수 없음 (%s)", e)
            except Exception:
                log.exception("planner 안전 점검 실패")

    t = threading.Thread(target=loop, name="planner-safety-watch", daemon=True)
    t.start()
    log.info("planner 안전 점검 시작 (%g초마다 상황 확인)", interval)
    try:
        yield
    finally:
        stop.set()
        t.join(timeout=5)
