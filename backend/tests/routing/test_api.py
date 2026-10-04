"""routing API. 실제 Valhalla 대신 가짜 엔진, safety 대신 가짜 위험 구역을 쓴다. DB 가 필요 없다.

    docker compose run --rm routing pytest tests/routing
"""
import math

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routing import service
from app.routing.hazards import AvoidZone, HazardsUnavailable, get_hazard_lookup
from app.routing.router import get_engine
from app.routing.valhalla import EngineUnavailable, NoRoute

# 구룡포항 -> 일본인가옥거리 -> 과메기문화관
PORT = {"lat": 35.9893, "lng": 129.5556}
STREET = {"lat": 35.9906, "lng": 129.5605}
MUSEUM = {"lat": 35.9927, "lng": 129.5618}
OFF_ROAD = {"lat": 35.95, "lng": 129.70}  # 바다 한가운데


def encode6(coords):
    """[(lng, lat)] -> 정밀도 6자리 polyline (가짜 엔진 응답용)."""
    out, plat, plng = [], 0, 0
    for lng, lat in coords:
        ilat, ilng = round(lat * 1e6), round(lng * 1e6)
        for d in (ilat - plat, ilng - plng):
            v = ~(d << 1) if d < 0 else d << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        plat, plng = ilat, ilng
    return "".join(out)


def _dist_km(a, b):
    return (abs(a.lat - b.lat) * 111.32 + abs(a.lng - b.lng) * 90.2)


class FakeEngine:
    """맨해튼 거리로 시간을 내는 가짜 엔진. exclude 가 있으면 북쪽으로 돌아가는 길(+50%)을 낸다."""

    def __init__(self):
        self.down = False
        self.calls: list[str] = []
        self.no_detour = False
        self.last_error = None

    def _check(self, name):
        self.calls.append(name)
        if self.down:
            raise EngineUnavailable("ConnectError: refused")

    def matrix(self, sources, targets, cost, opts):
        self._check("matrix")
        if any(p.lng > 129.65 for p in (*sources, *targets)):
            raise NoRoute("No suitable edges near location")
        speed = 30 if cost == "auto" else opts["walking_speed"]
        return [[(_dist_km(a, b) / speed * 3600, _dist_km(a, b) * 1000) for b in targets] for a in sources]  # (초, m)

    def route(self, stops, cost, opts, exclude=None):
        self._check("route")
        if any(p.lng > 129.65 for p in stops):
            raise NoRoute("No suitable edges near location")
        if exclude and self.no_detour:
            raise NoRoute("No path could be found for input")
        speed = 30 if cost == "auto" else opts["walking_speed"]
        legs = []
        for a, b in zip(stops, stops[1:]):
            mid = (a.lng + b.lng) / 2, max(a.lat, b.lat) + (0.004 if exclude else 0)
            shape = [(a.lng, a.lat), mid, (b.lng, b.lat)]
            km = _dist_km(a, b) * (1.5 if exclude else 1)
            sec = km / speed * 3600
            legs.append({"shape": encode6(shape), "summary": {"time": sec, "length": km}, "maneuvers": [
                {"type": 1, "street_names": ["구룡포길"], "length": km / 2, "time": sec / 2, "begin_shape_index": 0},
                {"type": 15, "street_names": ["호미로"], "length": km / 2, "time": sec / 2, "begin_shape_index": 1},
                {"type": 4, "length": 0, "time": 0, "begin_shape_index": 2}]})
        return {"legs": legs, "summary": {"time": sum(leg["summary"]["time"] for leg in legs),
                                          "length": sum(leg["summary"]["length"] for leg in legs)}}

    def status(self):
        self._check("status")
        return {"version": "fake", "tileset_last_modified": 1_790_000_000}


def square(name, lat, lng, half=0.0008, **kw):
    ring = ((lng - half, lat - half), (lng + half, lat - half), (lng + half, lat + half), (lng - half, lat + half),
            (lng - half, lat - half))
    return AvoidZone(id=name, name=f"{name} 주변", kind=kw.get("kind", "flood"), risk=kw.get("risk", "danger"),
                     reason="수위 등급 3 (위험)", polygon=ring)


class FakeHazards:
    def __init__(self):
        self.items: list[AvoidZone] = []
        self.down = False

    def zones(self, now):
        if self.down:
            raise HazardsUnavailable("refused")
        return self.items


engine, hazards = FakeEngine(), FakeHazards()


@pytest.fixture
def client():
    engine.__init__()
    hazards.__init__()
    service.cache.clear()
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_hazard_lookup] = lambda: hazards
    yield TestClient(app)  # lifespan(DB 초기화)을 돌리지 않도록 with 없이 쓴다
    app.dependency_overrides.pop(get_engine, None)
    app.dependency_overrides.pop(get_hazard_lookup, None)
    service.cache.clear()


def ok(resp, status=200):
    assert resp.status_code == status, resp.text
    return resp.json()


def legs(client, *pairs, **kw):
    return ok(client.post("/routing/legs", json={"legs": [{"from": a, "to": b} for a, b in pairs]} | kw))


def route(client, *stops, **kw):
    return ok(client.post("/routing/route", json={"stops": list(stops)} | kw))


# ---------- 구간 ----------

def test_legs_use_engine_and_round_up(client):
    r = legs(client, (PORT, STREET), (STREET, MUSEUM))
    assert r["engine"]["ok"] and [leg["source"] for leg in r["legs"]] == ["road", "road"]
    first = r["legs"][0]
    km = abs(35.9893 - 35.9906) * 111.32 + abs(129.5556 - 129.5605) * 90.2
    assert first["distance_m"] == round(km * 1000)
    assert first["minutes"] == math.ceil(km / 4.2 * 60)  # 4.2km/h, 올림


def test_legs_one_engine_call_and_cache(client):
    legs(client, (PORT, STREET), (STREET, MUSEUM), (PORT, STREET))
    assert engine.calls == ["matrix"]
    legs(client, (PORT, STREET))
    assert engine.calls == ["matrix"]  # 캐시


def test_same_point_is_zero_without_engine(client):
    r = legs(client, (PORT, PORT))
    assert r["legs"][0] == {"minutes": 0, "distance_m": 0, "source": "road"} and engine.calls == []


def test_mode_and_walk_options_change_time(client):
    walk = legs(client, (PORT, MUSEUM))["legs"][0]["minutes"]
    easy = legs(client, (PORT, MUSEUM), easy_walk=True)["legs"][0]["minutes"]
    fast = legs(client, (PORT, MUSEUM), walking_speed_kmh=6)["legs"][0]["minutes"]
    car = legs(client, (PORT, MUSEUM), mode="car")["legs"][0]["minutes"]
    assert car < fast < walk < easy


def test_engine_down_falls_back_to_estimate_and_is_not_cached(client):
    engine.down = True
    r = legs(client, (PORT, STREET))
    assert r["engine"]["ok"] is False and r["legs"][0]["source"] == "estimate" and r["legs"][0]["minutes"] > 0
    engine.down = False
    assert legs(client, (PORT, STREET))["legs"][0]["source"] == "road"


def test_unroutable_point_only_affects_its_leg(client):
    r = legs(client, (PORT, STREET), (STREET, OFF_ROAD))
    assert [leg["source"] for leg in r["legs"]] == ["road", "estimate"]
    assert r["engine"]["ok"]


def test_matrix_shape(client):
    r = ok(client.post("/routing/matrix", json={"sources": [PORT, STREET], "targets": [STREET, MUSEUM, PORT]}))
    m = r["matrix"]
    assert len(m) == 2 and all(len(row) == 3 for row in m)
    assert m[1][0]["minutes"] == 0 and m[0][2]["minutes"] == 0


@pytest.mark.parametrize("body", [
    {"legs": []},
    {"legs": [{"from": PORT, "to": STREET}], "mode": "bus"},
    {"legs": [{"from": PORT, "to": STREET}], "walking_speed_kmh": 30},
    {"legs": [{"from": PORT, "to": STREET}], "speed": 3},
    {"legs": [{"from": {"lat": 99, "lng": 129}, "to": STREET}]},
])
def test_legs_validation(client, body):
    assert client.post("/routing/legs", json=body).status_code == 422


def test_matrix_size_limit(client):
    assert client.post("/routing/matrix", json={"sources": [PORT] * 51, "targets": [PORT]}).status_code == 422


# ---------- 경로 ----------

def test_route_geometry_legs_and_korean_steps(client):
    r = route(client, PORT, STREET, MUSEUM)
    assert r["source"] == "road" and len(r["legs"]) == 2
    coords = r["geometry"]["coordinates"]
    assert len(coords) == 5  # 구간마다 3점, 이어지는 점은 한 번만
    assert coords[0] == pytest.approx([PORT["lng"], PORT["lat"]]) and coords[-1] == pytest.approx(
        [MUSEUM["lng"], MUSEUM["lat"]])
    steps = r["legs"][1]["steps"]
    assert [s["type"] for s in steps] == ["start", "left", "arrive"]
    assert steps[0]["instruction"].startswith("구룡포길로 출발해")
    assert steps[1]["instruction"].startswith("호미로로 좌회전 후")
    assert [s["begin_index"] for s in steps] == [2, 3, 4]  # 전체 geometry 기준
    assert r["minutes"] >= max(leg["minutes"] for leg in r["legs"])
    assert r["hazards"]["checked"] is False  # 기본은 ignore


def test_route_without_steps(client):
    assert all(leg["steps"] == [] for leg in route(client, PORT, MUSEUM, steps=False)["legs"])


def test_route_engine_down_is_straight_line(client):
    engine.down = True
    r = route(client, PORT, STREET, MUSEUM, hazards="avoid")
    assert r["source"] == "estimate" and r["engine"]["ok"] is False
    assert len(r["geometry"]["coordinates"]) == 3 and r["hazards"]["checked"] is False
    assert "직선거리" in r["hazards"]["note"]


def test_route_no_road_is_estimate_but_engine_ok(client):
    r = route(client, PORT, OFF_ROAD)
    assert r["source"] == "estimate" and r["engine"]["ok"] is True and "길을 찾지 못해" in r["hazards"]["note"]


# ---------- 위험 구역 ----------
# 가짜 경로는 두 지점의 중간 (경도 평균, 위도 큰 쪽) 을 지난다. 우회하면 그 점이 북쪽으로 0.004도 올라간다.
# PORT -> STREET 의 중간점은 (35.9906, 129.55805) 이다.


def test_report_lists_zones_on_route_without_detour(client):
    hazards.items = [square("flood_8", 35.9906, 129.55805), square("far", 35.90, 129.40)]
    r = route(client, PORT, STREET, hazards="report")
    assert r["hazards"]["checked"] and [z["id"] for z in r["hazards"]["on_route"]] == ["flood_8"]
    assert r["hazards"]["avoided"] == [] and engine.calls == ["route"]


def test_avoid_detours_and_compares_with_baseline(client):
    hazards.items = [square("flood_8", 35.9906, 129.55805)]
    r = route(client, PORT, STREET, hazards="avoid")
    h = r["hazards"]
    assert [z["id"] for z in h["avoided"]] == ["flood_8"] and h["on_route"] == []
    assert h["baseline"]["minutes"] < r["minutes"] and h["detour_minutes"] == r["minutes"] - h["baseline"]["minutes"]
    assert engine.calls == ["route", "route"]


def test_avoid_not_needed_when_route_is_clear(client):
    hazards.items = [square("far", 35.90, 129.40)]
    r = route(client, PORT, STREET, hazards="avoid")
    assert r["hazards"]["checked"] and r["hazards"]["avoided"] == [] and r["hazards"]["baseline"] is None
    assert engine.calls == ["route"]


def test_zone_containing_a_stop_is_not_blocked(client):
    hazards.items = [square("flood_dest", STREET["lat"], STREET["lng"])]
    r = route(client, PORT, STREET, hazards="avoid")
    h = r["hazards"]
    assert [z["id"] for z in h["on_route"]] == ["flood_dest"] and h["avoided"] == []
    assert "우회할 수 없다" in h["note"] and engine.calls == ["route"]


def test_no_detour_path_returns_original_with_note(client):
    engine.no_detour = True
    hazards.items = [square("flood_8", 35.9906, 129.55805)]
    r = route(client, PORT, STREET, hazards="avoid")
    assert [z["id"] for z in r["hazards"]["on_route"]] == ["flood_8"] and "찾지 못해" in r["hazards"]["note"]


def test_safety_down_is_not_checked(client):
    hazards.down = True
    r = route(client, PORT, STREET, hazards="avoid")
    assert r["source"] == "road" and r["hazards"]["checked"] is False and "안전 정보" in r["hazards"]["note"]


def test_status(client):
    assert ok(client.get("/routing/status"))["engine"]["ok"] is True
    engine.down = True
    assert ok(client.get("/routing/status"))["engine"]["ok"] is False
