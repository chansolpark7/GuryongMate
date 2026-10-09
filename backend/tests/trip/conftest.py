"""trip 테스트 공용 fixture. 실제 DB 를 쓰고, planner·safety·agent 는 가짜 클라이언트(fakes.py), 시각은 FixedClock.

    docker compose run --rm trip pytest tests/trip
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.db import SessionLocal, init_db
from app.main import app
from app.trip import service
from app.trip.clients import Clients, get_clients
from app.trip.clock import KST, FixedClock, get_clock
from app.trip.models import TripDay

from .fakes import PLACES, FakeAgent, FakePlanner, FakeSafety, fakes

USER = "test-trip-user"
H = {"X-User-Id": USER}
DAY = date(2026, 10, 10)

# 장면 ③·④ 재생 파일의 계획과 같다 (12:30~18:00, 도보)
#   짬홍 12:30~13:30 -> 구룡포항 13:36~14:06 -> 가옥거리 14:14~15:14 -> 과메기문화관 15:19~16:19 -> 근대문화역사관 16:24~17:04
PLAN = {"title": "엄마랑 구룡포", "start_date": "2026-10-10", "day_start": "12:30", "day_end": "18:00",
        "items": [{"place_id": "kto-2834330", "stay_minutes": 60}, {"place_id": "kto-2610501", "stay_minutes": 30},
                  {"place_id": "kto-1909659", "stay_minutes": 60}, {"place_id": "kto-2660046", "stay_minutes": 60},
                  {"place_id": "kto-2603493", "stay_minutes": 40}]}


def at(hhmm: str, day: date = DAY) -> datetime:
    return datetime.combine(day, time.fromisoformat(hhmm), tzinfo=KST)


def ok(resp, status=200):
    assert resp.status_code == status, resp.text
    return resp.json()


def err(resp, status: int, code: str) -> dict:
    assert resp.status_code == status, resp.text
    detail = resp.json()["detail"]
    assert isinstance(detail, dict) and detail["code"] == code, resp.text
    assert detail["message"]
    return detail


def _cleanup():
    with SessionLocal.begin() as s:
        s.execute(delete(TripDay).where(TripDay.user_id.like("test-trip-%")))


@pytest.fixture(scope="module")
def client():
    init_db()
    _cleanup()
    with TestClient(app) as c:
        yield c
    _cleanup()


@dataclass
class Env:
    c: TestClient
    deps: Clients
    clock: FixedClock
    plan_id: int

    @property
    def planner(self) -> FakePlanner:
        return self.deps.planner

    @property
    def safety(self) -> FakeSafety:
        return self.deps.safety

    @property
    def agent(self) -> FakeAgent:
        return self.deps.agent

    def url(self, path: str = "") -> str:
        return f"/trip/plans/{self.plan_id}{path}"

    def start(self, hhmm: str = "12:20", **body) -> dict:
        self.clock.when = at(hhmm)
        return ok(self.c.post(self.url("/start"), json=body or None, headers=H))

    def loc(self, place_or_latlng, hhmm: str, acc: float | None = 10, **kw) -> dict:
        """장소 id 또는 (lat, lng) 에 hh:mm(:ss) 시각의 점 하나. 시계도 그 시각으로 옮긴다."""
        lat, lng = self.coords(place_or_latlng)
        self.clock.when = at(hhmm)
        body = {"lat": lat, "lng": lng, "recorded_at": at(hhmm).isoformat(), "source": "cli"} | kw
        if acc is not None:
            body["accuracy"] = acc
        return ok(self.c.post(self.url("/location"), json=body, headers=H))

    def stay(self, place, start: str, minutes: int, every_sec: int = 30) -> list[dict]:
        """start 부터 minutes 동안 every_sec 마다 같은 곳의 점. 생긴 이벤트를 모아 돌려준다."""
        events = []
        t0 = at(start)
        for s in range(0, minutes * 60 + 1, every_sec):
            events += self.loc(place, (t0 + timedelta(seconds=s)).strftime("%H:%M:%S"))["events"]
        return events

    def tick(self, hhmm: str) -> dict:
        self.clock.when = at(hhmm)
        return ok(self.c.post(self.url("/tick"), headers=H))

    def events(self, after: int = 0) -> list[dict]:
        return ok(self.c.get(self.url(f"/events?after={after}&limit=200"), headers=H))

    def types(self) -> list[str]:
        return [e["type"] for e in self.events()]

    def state(self) -> dict:
        return ok(self.c.get(self.url("/state"), headers=H))

    @staticmethod
    def coords(place_or_latlng) -> tuple[float, float]:
        if isinstance(place_or_latlng, str):
            p = PLACES[place_or_latlng]
            return p["lat"], p["lng"]
        return place_or_latlng


@pytest.fixture
def env(client):
    """테스트마다 새 가짜 서비스·시계·계획. 이 사용자의 여행 상태는 지운다."""
    _cleanup()
    deps = fakes()
    clk = FixedClock(at("12:00"))
    app.dependency_overrides[get_clients] = lambda: deps
    app.dependency_overrides[get_clock] = lambda: clk
    service.hazards.ttl = 0
    service.hazards.reset()
    yield Env(client, deps, clk, deps.planner.create(USER, PLAN))
    app.dependency_overrides.pop(get_clients, None)
    app.dependency_overrides.pop(get_clock, None)
