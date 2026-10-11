"""travel_time 도구: routing 행렬로 도보·차량 이동시간, 위험 구역 확인 (places·routing API 는 가짜)."""
import asyncio
import json

from app.agent.api import ApiError
from app.agent.tools import Draft, Toolbox

PLACES = {
    "a": {"id": "a", "title": "구룡포항", "area": "구룡포읍", "lat": 35.9893, "lng": 129.5556},
    "b": {"id": "b", "title": "호미곶", "area": "호미곶면", "lat": 36.0765, "lng": 129.5670},
    "c": {"id": "c", "title": "가옥거리", "area": "구룡포읍", "lat": 35.9906, "lng": 129.5605},
}


class FakeApi:
    def __init__(self, hazards=None, road=True, risk="safe", safety_down=False):
        self.calls: list[tuple] = []
        self.hazards = hazards
        self.road = road
        self.risk = risk
        self.safety_down = safety_down
        self.assessed: list[dict] = []

    async def get_place(self, place_id):
        if place_id not in PLACES:
            raise ApiError(404, {"code": "not_found", "message": f"장소 {place_id} 없음"})
        return PLACES[place_id]

    async def routing_matrix(self, mode, sources, targets):
        self.calls.append(("matrix", mode, len(targets)))
        per_km = 15 if mode == "walk" else 2
        row = []
        for t in targets:
            km = abs(t["lat"] - sources[0]["lat"]) * 111
            row.append({"minutes": round(km * per_km), "distance_m": round(km * 1000),
                        "source": "road" if self.road else "estimate"})
        return {"mode": mode, "matrix": [row], "engine": {"ok": self.road}}

    async def routing_route(self, mode, stops, hazards="avoid"):
        self.calls.append(("route", mode, hazards))
        line = [[s["lng"], s["lat"]] for s in stops]
        return {"minutes": 8, "geometry": {"coordinates": line},
                "hazards": self.hazards or {"checked": True, "on_route": [], "avoided": [], "detour_minutes": None}}

    async def safety_situation(self):
        if self.safety_down:
            raise ApiError(503, {"code": "upstream_unavailable", "message": "down"})
        return {"now": "2026-10-06T10:30:00+09:00"}

    async def safety_assess(self, visits):
        self.assessed = visits
        level = {"safe": "info", "caution": "caution", "danger": "danger"}[self.risk]
        return {"visits": [{"key": v["key"], "risk": self.risk, "advice": "피하라",
                            "hazards": [{"level": level, "reason": "수협 맨홀 수위 등급 3"}]} for v in visits]}


def run(api, **args):
    box = Toolbox(api, plan_id=1, draft=Draft(), session_id="s")
    out = asyncio.run(box.call("travel_time", json.dumps(args)))
    return out, json.loads(out.content)


def test_walk_and_car_with_parking():
    api = FakeApi()
    out, body = run(api, from_place_id="a", to_place_ids=["b"])
    r = body["results"][0]
    assert body["from"] == "구룡포항" and r["to"] == "호미곶" and r["area"] == "호미곶면"
    assert r["walk"]["minutes"] > r["car"]["minutes"] and "approx" not in r["walk"]
    assert r["car"]["minutes"] == round(abs(36.0765 - 35.9893) * 111 * 2) + 5  # 주차·승하차 5분
    assert out.detail == f"호미곶 도보 {r['walk']['minutes']}분·차량 {r['car']['minutes']}분"
    assert sorted(c[1] for c in api.calls) == ["car", "walk"] and "safety_now" not in r and "route_zones" not in r


def test_one_mode_and_compare_many():
    api = FakeApi(road=False)
    out, body = run(api, from_place_id="a", to_place_ids=["b", "c"], mode="car")
    assert [r["to"] for r in body["results"]] == ["호미곶", "가옥거리"]
    assert all("walk" not in r and r["car"]["approx"] for r in body["results"])
    assert out.detail == "구룡포항에서 2곳" and api.calls == [("matrix", "car", 2)]


def test_check_hazards_route_zones():
    hz = {"checked": True, "on_route": [], "detour_minutes": 4,
          "avoided": [{"name": "구룡포파출소_지표면 수위계 주변", "reason": "수위 등급 2"}], "note": None}
    _, body = run(FakeApi(hazards=hz), from_place_id="a", to_place_ids=["c"], check_hazards=True)
    assert body["results"][0]["route_zones"] == {"avoided": ["구룡포파출소_지표면 수위계 주변"], "still_on_route": [],
                                                 "detour_minutes": 4, "note": None}
    _, body = run(FakeApi(), from_place_id="a", to_place_ids=["c"], check_hazards=True, mode="car")
    assert body["results"][0]["route_zones"] == "경로에 지금 막아야 할 구역 없음"
    _, body = run(FakeApi(hazards={"checked": False, "on_route": [], "avoided": []}), from_place_id="a",
                  to_place_ids=["c"], check_hazards=True)
    assert body["results"][0]["route_zones"].startswith("확인하지 못함")
    assert "그것만으로 안전하다고 말하지 마라" in body["note"]


def test_safety_now_judges_route_and_destination():
    """'지금 걸어가도 안전해?': 우회로 막을 구역이 없어도 도착지가 위험하면 위험으로 알려야 한다."""
    api = FakeApi(risk="danger")
    _, body = run(api, from_place_id="a", to_place_ids=["c"], check_hazards=True, mode="walk")
    now = body["results"][0]["safety_now"]
    assert now["depart"] == "10:30" and now["route"]["risk"] == "위험" and now["destination"]["risk"] == "위험"
    assert now["destination"]["advice"] == "피하라" and "수협" in now["destination"]["reasons"][0]
    leg, visit = api.assessed
    assert (leg["mode"], leg["start"], leg["end"], leg["date"]) == ("walk", "10:30", "10:38", "2026-10-06")
    assert leg["path"][0] == [PLACES["a"]["lat"], PLACES["a"]["lng"]] and visit["place_id"] == "c"
    assert visit["start"] == "10:38"  # 도착 시각부터 방문
    _, body = run(FakeApi(risk="safe"), from_place_id="a", to_place_ids=["c"], check_hazards=True)
    assert body["results"][0]["safety_now"]["route"] == {"risk": "안전", "reasons": ["[참고] 수협 맨홀 수위 등급 3"]}
    _, body = run(FakeApi(safety_down=True), from_place_id="a", to_place_ids=["c"], check_hazards=True)
    assert body["results"][0]["safety_now"].startswith("안전 정보를 확인하지 못함")


def test_hazard_routes_limited_to_three():
    api = FakeApi()
    PLACES.update({k: dict(PLACES["c"], id=k, title=k) for k in ("d", "e", "f")})
    try:
        run(api, from_place_id="a", to_place_ids=["b", "c", "d", "e", "f"], check_hazards=True, mode="walk")
        assert [c for c in api.calls if c[0] == "route"] == [("route", "walk", "avoid")] * 3
    finally:
        for k in ("d", "e", "f"):
            PLACES.pop(k)


def test_bad_arguments_are_tool_errors():
    out, body = run(FakeApi(), from_place_id="a", to_place_ids=[])
    assert not out.ok and "1~10" in body["error"]
    out, body = run(FakeApi(), from_place_id="a", to_place_ids=["b"], mode="bike")
    assert not out.ok
    out, body = run(FakeApi(), from_place_id="a", to_place_ids=["zzz"])
    assert not out.ok and "없음" in body["error"]
    out, body = run(FakeApi(), from_place_id="a", to_place_ids="b")  # 문자열 하나도 받아 준다
    assert out.ok and body["results"][0]["to"] == "호미곶"
