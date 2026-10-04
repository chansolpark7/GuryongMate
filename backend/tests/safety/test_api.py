"""safety API. 외부 API 대신 MockSources(시각 고정)와 가짜 장소 조회를 쓴다. DB 가 필요 없다.

    docker compose run --rm safety pytest tests/safety
"""
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.safety.places import PlacePoint, PlacesUnavailable, get_place_lookup
from app.safety.router import get_sources
from app.safety.sources import KST, Fetched, MockSources

NOW = datetime(2026, 10, 4, 10, 0, tzinfo=KST)
PLACES = {
    "p-breakwater": PlacePoint("p-breakwater", "구룡포 북방파제", 35.9871, 129.5583, "관광지"),
    "p-street": PlacePoint("p-street", "구룡포 일본인가옥거리", 35.9820, 129.5420, "관광지"),
    "p-police": PlacePoint("p-police", "구룡포파출소 앞", 35.9887, 129.5531),
    "p-museum": PlacePoint("p-museum", "구룡포 근대문화역사관", 35.9821, 129.5421, "문화시설"),
    "p-restaurant": PlacePoint("p-restaurant", "파출소 옆 식당", 35.9886, 129.5532, "음식점"),
}


class FakeLookup:
    down = False

    def get(self, place_id):
        if self.down:
            raise PlacesUnavailable("down")
        return PLACES.get(place_id)


class CountingMock(MockSources):
    def __init__(self, scenario="calm"):
        super().__init__(scenario, clock=lambda: NOW)
        self.calls: list[str] = []

    def sensors(self):
        self.calls.append("sensors")
        return super().sensors()

    def warnings(self):
        self.calls.append("warnings")
        return super().warnings()


class ForecastDown(CountingMock):
    def forecast(self, now):
        return Fetched(None, NOW, "SourceError: SERVICETIMEOUT_ERROR")


fake_places = FakeLookup()


@pytest.fixture
def use():
    """use(sources) -> TestClient. lifespan(DB 초기화)을 돌리지 않도록 with 없이 쓴다."""
    app.dependency_overrides[get_place_lookup] = lambda: fake_places

    def _use(src):
        app.dependency_overrides[get_sources] = lambda: src
        return TestClient(app)

    yield _use
    app.dependency_overrides.pop(get_sources, None)
    app.dependency_overrides.pop(get_place_lookup, None)
    fake_places.down = False


def ok(resp, status=200):
    assert resp.status_code == status, resp.text
    return resp.json()


def assess(client, *visits, now=NOW):
    return ok(client.post("/safety/assess", json={"visits": list(visits), "now": now.isoformat()}))


def visit(place_id, d, start, end=None, key=None):
    v = {"place_id": place_id, "date": d, "start": start, "key": key or place_id}
    return v | ({"end": end} if end else {})


def test_zones(use):
    zones = ok(use(CountingMock()).get("/safety/zones"))
    kinds = [z["kind"] for z in zones]
    assert kinds.count("breakwater") == 2 and kinds.count("flood") == 9


def test_far_future_is_unknown_and_says_do_not_change(use):
    src = CountingMock("storm")  # 지금 폭풍이어도 몇 달 뒤 일정에는 영향 없다
    r = assess(use(src), visit("p-breakwater", "2027-01-15", "14:00"))
    v = r["visits"][0]
    assert v["horizon"] == "long_range" and v["risk"] == "unknown" and v["forecast_available"] is False
    assert v["confidence"] == "none" and v["hazards"] == []
    assert v["recheck_from"] == "2027-01-12" and "바꾸거나 빼지 마라" in v["advice"]
    assert v["sensitivity"] == ["방파제"]
    assert any("겨울철" in n for n in v["notes"])
    assert r["overall_risk"] == "unknown" and "안전을 이유로 바꾸지 마라" in r["guidance"]
    assert src.calls == []  # 실측·특보는 부르지 않는다
    assert "sensors" not in r["data_status"]


def test_medium_range_waits_for_forecast(use):
    v = assess(use(CountingMock("storm")), visit("p-breakwater", "2026-10-11", "14:00"))["visits"][0]
    assert v["horizon"] == "medium_range" and v["risk"] == "unknown" and v["recheck_from"] == "2026-10-08"


def test_calm_tomorrow_is_none_with_forecast(use):
    r = assess(use(CountingMock()), visit("p-breakwater", "2026-10-05", "14:00", "15:00"))
    v = r["visits"][0]
    assert (v["horizon"], v["forecast_available"], v["risk"]) == ("short_range", True, "none")
    assert v["label"] == "구룡포 북방파제" and "바뀔 수 있다" in v["advice"]
    assert r["overall_risk"] == "none" and r["data_status"]["forecast"]["note"].startswith("mock")


def test_storm_now_marks_breakwater_and_flood_zone_dangerous(use):
    r = assess(use(CountingMock("storm")),
               visit("p-breakwater", "2026-10-04", "11:00"), visit("p-police", "2026-10-04", "11:30"),
               visit("p-street", "2026-10-04", "12:00"))
    by = {v["key"]: v for v in r["visits"]}
    bw = by["p-breakwater"]
    assert bw["horizon"] == "now" and bw["risk"] == "danger"
    assert {"wind", "wave", "warning"} <= {h["type"] for h in bw["hazards"]}
    assert "피하는 것이 좋다" in bw["advice"]
    police = by["p-police"]  # 옆 지표면 수위계가 등급 2(주의) + 호우주의보
    assert police["risk"] == "caution" and police["sensitivity"] == ["침수 감시 구역"]
    assert any(h["source"].startswith("E-UM") and h["level"] == "caution" for h in police["hazards"])
    assert by["p-street"]["risk"] in ("low", "caution")  # 방파제·침수 구역이 아닌 곳은 낮게
    assert r["overall_risk"] == "danger" and "advice 를 따르고" in r["guidance"]


def test_indoor_place_is_an_alternative_not_a_problem(use):
    r = assess(use(CountingMock("storm")), visit("p-museum", "2026-10-04", "15:00"),
               visit("p-restaurant", "2026-10-04", "15:00"))
    museum, restaurant = r["visits"]
    assert museum["indoor"] is True and "실내" in museum["sensitivity"]
    assert museum["risk"] == "low" and "방문 자체는 괜찮다" in museum["advice"]
    assert all(h["level"] == "low" for h in museum["hazards"]) and museum["better_times"] == []
    # 침수 감시 구역 안의 실내는 그대로 위험하다
    assert restaurant["indoor"] is True and restaurant["risk"] in ("caution", "danger")


def test_indoor_can_be_overridden(use):
    v = visit("p-museum", "2026-10-04", "15:00") | {"indoor": False}
    r = assess(use(CountingMock("storm")), v)["visits"][0]
    assert r["indoor"] is False and r["risk"] == "caution"
    r = assess(use(CountingMock("storm")), {"lat": 35.9821, "lng": 129.5421, "date": "2026-10-04", "start": "15:00",
                                           "indoor": True})["visits"][0]
    assert r["risk"] == "low"


def test_risky_visit_suggests_better_times(use):
    # storm mock: 10/05 15시부터 잠잠, 예보는 10/07 06시까지
    v = assess(use(CountingMock("storm")), visit("p-breakwater", "2026-10-04", "14:00", "15:00"))["visits"][0]
    assert v["risk"] == "danger"
    wins = [(w["start"][5:16], w["end"][5:16]) for w in v["better_times"]]
    assert wins == [("10-05T15:00", "10-05T19:00"), ("10-06T08:00", "10-06T19:00")]
    assert "better_times" in v["advice"] and "10/05 15:00~19:00" in v["advice"]


def test_no_better_time_tells_to_find_another_place(use):
    class LongStorm(CountingMock):
        def forecast(self, now):
            f = MockSources("storm", clock=lambda: NOW).forecast(now)
            for h in f.value.hours.values():
                h.wsd, h.wav = 16.0, 3.5
            return f

    v = assess(use(LongStorm()), visit("p-breakwater", "2026-10-05", "14:00"))["visits"][0]
    assert v["risk"] == "danger" and v["better_times"] == [] and "대체 장소" in v["advice"]


def test_current_warning_does_not_decide_tomorrow(use):
    # 내일 오후(약 29시간 뒤)는 예보가 잠잠하므로, 지금 발효 중인 특보만으로 위험해지지 않는다
    v = assess(use(CountingMock("storm")), visit("p-breakwater", "2026-10-05", "15:00"))["visits"][0]
    assert v["risk"] == "none" and v["hazards"] == []
    # 20시간 뒤는 특보를 참고로만 붙이고, 위험 판단은 예보가 한다
    v = assess(use(CountingMock("storm")), visit("p-breakwater", "2026-10-05", "06:00"))["visits"][0]
    warn = [h for h in v["hazards"] if h["type"] == "warning"]
    assert warn and all(h["level"] == "low" and "해제될 수 있음" in h["reason"] for h in warn)
    assert v["risk"] == "danger"  # 예보(풍속·파고) 때문


def test_forecast_failure_is_unknown_not_safe(use):
    r = assess(use(ForecastDown()), visit("p-breakwater", "2026-10-05", "14:00"))
    v = r["visits"][0]
    assert (v["horizon"], v["forecast_available"], v["risk"]) == ("short_range", False, "unknown")
    assert "안전하다고 말하지 말고" in v["advice"]
    assert r["data_status"]["forecast"]["ok"] is False and "기상 예보" in r["guidance"]


def test_place_lookup_problems(use):
    client = use(CountingMock())
    v = assess(client, visit("p-nowhere", "2026-10-05", "14:00"))["visits"][0]
    assert v["sensitivity"] == [] and any("좌표" in n for n in v["notes"])
    fake_places.down = True
    r = assess(client, visit("p-breakwater", "2026-10-05", "14:00"))
    assert r["data_status"]["places"]["ok"] is False
    # 좌표를 직접 주면 places 없이도 된다
    r = assess(client, {"lat": 35.9871, "lng": 129.5583, "date": "2026-10-05", "start": "14:00", "label": "방파제"})
    assert r["visits"][0]["sensitivity"] == ["방파제"]


@pytest.mark.parametrize("body", [
    {"visits": []},
    {"visits": [{"date": "2026-10-05", "start": "14:00"}]},  # 장소 없음
    {"visits": [{"place_id": "p", "date": "2026-10-05", "start": "14:00", "end": "13:00"}]},
])
def test_assess_validation(use, body):
    assert use(CountingMock()).post("/safety/assess", json=body).status_code == 422


def test_outlook_far_future_only_tide_and_season(use):
    r = ok(use(CountingMock()).get("/safety/outlook", params={"date": "2027-08-01", "now": NOW.isoformat()}))
    assert r["horizon"] == "long_range" and r["summary"] is None and r["hazards"] == []
    assert r["tides"] and any("태풍" in n for n in r["seasonal_notes"])
    assert "바꾸지 마라" in r["guidance"] and r["recheck_from"] == "2027-07-29"


def test_outlook_storm_tomorrow(use):
    r = ok(use(CountingMock("storm")).get("/safety/outlook", params={
        "date": "2026-10-05", "start": "09:00", "end": "13:00", "detail": True, "now": NOW.isoformat()}))
    assert r["horizon"] == "short_range" and r["forecast_available"] is True
    assert r["summary"]["max_wave_m"] == 3.5 and len(r["hourly"]) == 4
    assert any(h["level"] == "danger" and "방파제 기준" in h["reason"] for h in r["hazards"])
    assert r["warnings"]  # 내일은 현재 특보도 함께 보여주되
    assert all(h["level"] == "low" for h in r["hazards"] if h["type"] == "warning")  # 그날 위험은 예보로 판단
    assert "대안" in r["guidance"] and "safe_windows" in r["guidance"]


def test_outlook_safe_windows(use):
    r = ok(use(CountingMock("storm")).get("/safety/outlook", params={"date": "2026-10-05", "now": NOW.isoformat()}))
    by = {}
    for w in r["safe_windows"]:
        by.setdefault(w["place_kind"], []).append((w["start"][11:16], w["end"][11:16]))
    assert by["breakwater"] == [("15:00", "18:00")]  # 폭풍이 15시에 끝난다
    assert by["outdoor"][-1][1] == "18:00"
    calm = ok(use(CountingMock()).get("/safety/outlook", params={"date": "2026-10-05", "now": NOW.isoformat()}))
    assert {(w["place_kind"], w["start"][11:16], w["end"][11:16]) for w in calm["safe_windows"]} == {
        ("breakwater", "09:00", "18:00"), ("flood", "09:00", "18:00"), ("outdoor", "09:00", "18:00")}
    far = ok(use(CountingMock()).get("/safety/outlook", params={"date": "2027-08-01", "now": NOW.isoformat()}))
    assert far["safe_windows"] == []  # 예보가 없으면 '괜찮은 시간'도 말하지 않는다


def test_outlook_rejects_bad_window(use):
    assert use(CountingMock()).get("/safety/outlook", params={"date": "2026-10-05", "start": "18:00",
                                                              "end": "09:00"}).status_code == 422


def test_status_calm_and_storm(use):
    calm = ok(use(CountingMock()).get("/safety/status", params={"now": NOW.isoformat()}))
    assert calm["overall_risk"] == "none" and len(calm["sensors"]) == 10 and calm["uv"]["index"] == 4.0
    assert {a["side"] for a in calm["breakwater_activity"]} == {"north", "south"}
    storm = ok(use(CountingMock("storm")).get("/safety/status", params={"now": NOW.isoformat()}))
    assert storm["overall_risk"] == "danger" and len(storm["warnings"]) == 3
    north = next(z for z in storm["zones"] if z["id"] == "breakwater_north")
    assert north["risk"] == "danger" and north["reasons"]
    assert next(z for z in storm["zones"] if z["id"] == "flood_4")["risk"] == "danger"
    assert storm["next_hours"]


def test_status_without_sensors_is_unknown(use):
    class SensorsDown(CountingMock):
        def sensors(self):
            return Fetched(None, NOW, "SourceError: 40104")

    r = ok(use(SensorsDown()).get("/safety/status", params={"now": NOW.isoformat()}))
    flood = [z for z in r["zones"] if z["kind"] == "flood"]
    assert flood and all(z["risk"] == "unknown" for z in flood)
    assert r["overall_risk"] == "unknown" and "단정하지 마라" in r["guidance"]
    assert r["data_status"]["sensors"]["ok"] is False and "침수 센서" in r["guidance"]


def test_overall_is_unknown_when_some_visits_not_assessed(use):
    r = assess(use(CountingMock()), visit("p-street", "2026-10-05", "14:00"), visit("p-street", "2027-01-15", "14:00"))
    assert [v["risk"] for v in r["visits"]] == ["none", "unknown"] and r["overall_risk"] == "unknown"


def test_dates_are_kst(use):
    v = assess(use(CountingMock()), visit("p-street", "2026-10-05", "14:00"))["visits"][0]
    assert v["start"].endswith("+09:00") and date.fromisoformat(v["start"][:10]) == date(2026, 10, 5)
