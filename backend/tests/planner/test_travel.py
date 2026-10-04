"""planner → routing 이동시간 조회 (HttpTravelLookup). routing 은 가짜 HTTP 응답으로 바꾼다."""
import httpx
import pytest

from app.planner.schemas import PlaceInfo
from app.planner.travel import CAR_OVERHEAD_MIN, HttpTravelLookup, estimate_leg

A = PlaceInfo(id="a", title="가옥거리", lat=35.9880, lng=129.5570)
B = PlaceInfo(id="b", title="물회집", lat=35.9907, lng=129.5570)
C = PlaceInfo(id="c", title="문화관", lat=35.9934, lng=129.5570)


class FakeRouting:
    def __init__(self):
        self.requests: list[dict] = []
        self.status = 200
        self.source = "road"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        import json
        body = json.loads(request.content)
        self.requests.append(body)
        if self.status != 200:
            return httpx.Response(self.status)
        return httpx.Response(200, json={"mode": body["mode"], "engine": {"ok": True}, "legs": [
            {"minutes": 7, "distance_m": 450, "source": self.source} for _ in body["legs"]]})


@pytest.fixture
def routing():
    fake = FakeRouting()
    lookup = HttpTravelLookup("http://gateway", 1, retry_sec=60, cache_size=100, ttl_sec=3600)
    lookup._client = httpx.Client(base_url="http://gateway", transport=httpx.MockTransport(fake))
    return fake, lookup


def test_asks_routing_once_and_caches(routing):
    fake, lookup = routing
    legs = lookup.legs([(A, B), (B, C), (A, B)], "walk")
    assert [(leg.minutes, leg.distance_m, leg.source) for leg in legs] == [(7, 450, "road")] * 3
    assert len(fake.requests) == 1 and len(fake.requests[0]["legs"]) == 2  # 같은 구간은 한 번만
    lookup.legs([(B, C)], "walk")
    assert len(fake.requests) == 1  # 캐시
    lookup.legs([(B, C)], "car")
    assert len(fake.requests) == 2  # 수단이 다르면 다시


def test_car_adds_parking_time(routing):
    _, lookup = routing
    assert lookup.legs([(A, B)], "car")[0].minutes == 7 + CAR_OVERHEAD_MIN


def test_routing_down_uses_estimate_and_backs_off(routing):
    fake, lookup = routing
    fake.status = 503
    assert lookup.legs([(A, B)], "walk") == [estimate_leg(A, B, "walk")]
    assert lookup.legs([(B, C)], "walk") == [estimate_leg(B, C, "walk")]
    assert len(fake.requests) == 1  # retry_sec 동안은 묻지 않는다


def test_routing_estimate_is_replaced_and_not_cached(routing):
    fake, lookup = routing
    fake.source = "estimate"
    assert lookup.legs([(A, B)], "car") == [estimate_leg(A, B, "car")]  # 주차 시간이 들어간 우리 근사
    fake.source = "road"
    assert lookup.legs([(A, B)], "car")[0].source == "road"
