"""safety API. 외부 API 대신 MockSources(시각 고정)와 가짜 장소 조회를 쓴다. DB 가 필요 없다.

    docker compose run --rm safety pytest tests/safety
"""
from datetime import date, datetime, timedelta

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


def test_far_future_is_not_assessed_and_says_do_not_change(use):
    src = CountingMock("storm")  # 지금 폭풍이어도 몇 달 뒤 일정에는 영향 없다
    r = assess(use(src), visit("p-breakwater", "2027-01-15", "14:00"))
    v = r["visits"][0]
    assert v["horizon"] == "long_range" and v["risk"] is None and v["forecast_available"] is False
    assert v["unassessed_reason"] == "beyond_forecast"
    assert v["confidence"] == "none" and v["hazards"] == []
    assert v["recheck_from"] == "2027-01-12" and "바꾸거나 빼지 마라" in v["advice"]
    assert v["sensitivity"] == ["방파제"]
    assert any("겨울철" in n for n in v["notes"])
    assert r["overall_risk"] is None and "안전을 이유로 바꾸지 마라" in r["guidance"]
    assert src.calls == []  # 실측·특보는 부르지 않는다
    assert "sensors" not in r["data_status"]


def test_medium_range_waits_for_forecast(use):
    v = assess(use(CountingMock("storm")), visit("p-breakwater", "2026-10-11", "14:00"))["visits"][0]
    assert v["horizon"] == "medium_range" and v["risk"] is None and v["recheck_from"] == "2026-10-08"


def test_calm_tomorrow_is_safe_with_forecast(use):
    r = assess(use(CountingMock()), visit("p-breakwater", "2026-10-05", "14:00", "15:00"))
    v = r["visits"][0]
    assert (v["horizon"], v["forecast_available"], v["risk"]) == ("short_range", True, "safe")
    assert v["label"] == "구룡포 북방파제" and "바뀔 수 있다" in v["advice"]
    assert v["unassessed_reason"] is None and v["hazards"] == []
    assert r["overall_risk"] == "safe" and r["data_status"]["forecast"]["note"].startswith("mock")


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
    assert by["p-street"]["risk"] == "caution"  # 방파제·침수 구역이 아닌 곳은 한 단계 낮게
    assert r["overall_risk"] == "danger" and "advice 를 따르고" in r["guidance"]


def test_indoor_place_is_an_alternative_not_a_problem(use):
    r = assess(use(CountingMock("storm")), visit("p-museum", "2026-10-04", "15:00"),
               visit("p-restaurant", "2026-10-04", "15:00"))
    museum, restaurant = r["visits"]
    assert museum["indoor"] is True and "실내" in museum["sensitivity"]
    assert museum["risk"] == "safe" and "방문 자체는 괜찮다" in museum["advice"]
    # 단계는 안전이지만 비·바람·특보는 참고(info)로 그대로 남는다
    assert museum["hazards"] and all(h["level"] == "info" for h in museum["hazards"]) and museum["better_times"] == []
    # 침수 감시 구역 안의 실내는 그대로 위험하다
    assert restaurant["indoor"] is True and restaurant["risk"] in ("caution", "danger")


def test_indoor_can_be_overridden(use):
    v = visit("p-museum", "2026-10-04", "15:00") | {"indoor": False}
    r = assess(use(CountingMock("storm")), v)["visits"][0]
    assert r["indoor"] is False and r["risk"] == "caution"
    r = assess(use(CountingMock("storm")), {"lat": 35.9821, "lng": 129.5421, "date": "2026-10-04", "start": "15:00",
                                           "indoor": True})["visits"][0]
    assert r["risk"] == "safe" and r["hazards"]


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
    assert v["risk"] == "safe" and v["hazards"] == []
    # 20시간 뒤는 특보를 참고로만 붙이고, 위험 판단은 예보가 한다
    v = assess(use(CountingMock("storm")), visit("p-breakwater", "2026-10-05", "06:00"))["visits"][0]
    warn = [h for h in v["hazards"] if h["type"] == "warning"]
    assert warn and all(h["level"] == "info" and "해제될 수 있음" in h["reason"] for h in warn)
    assert v["risk"] == "danger"  # 예보(풍속·파고) 때문


def test_forecast_failure_is_not_assessed_not_safe(use):
    r = assess(use(ForecastDown()), visit("p-breakwater", "2026-10-05", "14:00"))
    v = r["visits"][0]
    assert (v["horizon"], v["forecast_available"], v["risk"]) == ("short_range", False, None)
    assert v["unassessed_reason"] == "data_missing"
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
    assert all(h["level"] == "info" for h in r["hazards"] if h["type"] == "warning")  # 그날 위험은 예보로 판단
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
    assert calm["overall_risk"] == "safe" and len(calm["sensors"]) == 10 and calm["uv"]["index"] == 4.0
    assert {a["side"] for a in calm["breakwater_activity"]} == {"north", "south"}
    storm = ok(use(CountingMock("storm")).get("/safety/status", params={"now": NOW.isoformat()}))
    assert storm["overall_risk"] == "danger" and len(storm["warnings"]) == 3
    north = next(z for z in storm["zones"] if z["id"] == "breakwater_north")
    assert north["risk"] == "danger" and north["reasons"]
    assert next(z for z in storm["zones"] if z["id"] == "flood_4")["risk"] == "danger"
    assert storm["next_hours"]


def test_status_without_sensors_is_not_assessed(use):
    class SensorsDown(CountingMock):
        def sensors(self):
            return Fetched(None, NOW, "SourceError: 40104")

    r = ok(use(SensorsDown()).get("/safety/status", params={"now": NOW.isoformat()}))
    flood = [z for z in r["zones"] if z["kind"] == "flood"]
    assert flood and all(z["risk"] is None for z in flood)
    assert r["overall_risk"] is None and "단정하지 마라" in r["guidance"]
    assert r["data_status"]["sensors"]["ok"] is False and "침수 센서" in r["guidance"]


def test_overall_is_null_when_some_visits_not_assessed(use):
    r = assess(use(CountingMock()), visit("p-street", "2026-10-05", "14:00"), visit("p-street", "2027-01-15", "14:00"))
    assert [v["risk"] for v in r["visits"]] == ["safe", None] and r["overall_risk"] is None


def test_dates_are_kst(use):
    v = assess(use(CountingMock()), visit("p-street", "2026-10-05", "14:00"))["visits"][0]
    assert v["start"].endswith("+09:00") and date.fromisoformat(v["start"][:10]) == date(2026, 10, 5)


# ---------- 3단계 + 참고 정보 ----------

def test_rain_scenario_is_safe_but_keeps_info(use):
    # 약한 비: 단계는 안전이지만 비 소식은 참고(info)로 남아 사람·LLM 이 일정 조정에 쓸 수 있다
    v = assess(use(CountingMock("rain")), visit("p-street", "2026-10-04", "14:00"))["visits"][0]
    assert v["risk"] == "safe" and v["unassessed_reason"] is None
    assert [(h["type"], h["level"]) for h in v["hazards"]] == [("rain", "info")] and "5mm" in v["hazards"][0]["reason"]
    assert "참고할 것" in v["advice"] and v["better_times"] == []


def test_wind_tomorrow_scenario_depends_on_place(use):
    src = CountingMock("wind_tomorrow")
    r = assess(use(src), visit("p-breakwater", "2026-10-05", "14:00"), visit("p-street", "2026-10-05", "14:00"),
               visit("p-museum", "2026-10-05", "14:00"), visit("p-breakwater", "2026-10-05", "10:00", key="am"))
    by = {v["key"]: v for v in r["visits"]}
    assert by["p-breakwater"]["risk"] == "danger" and by["p-street"]["risk"] == "caution"
    assert by["p-museum"]["risk"] == "safe" and {h["level"] for h in by["p-museum"]["hazards"]} == {"info"}
    assert by["am"]["risk"] == "safe"  # 같은 장소라도 바람이 없는 오전은 안전
    # 같은 날 오전이 better_times 로 나온다
    assert by["p-breakwater"]["better_times"][0]["start"].startswith("2026-10-04")
    assert r["overall_risk"] == "danger"


def test_flood_scenario_only_flood_zone(use):
    r = assess(use(CountingMock("flood")), visit("p-police", "2026-10-04", "10:30"),
               visit("p-breakwater", "2026-10-04", "10:30"), visit("p-street", "2026-10-04", "10:30"))
    by = {v["key"]: v for v in r["visits"]}
    assert by["p-police"]["risk"] == "caution"  # 옆 지표면 수위계 등급 2
    assert any(h["source"].startswith("E-UM") for h in by["p-police"]["hazards"])
    assert by["p-street"]["risk"] == "safe"


# ---------- 상황 요약 ----------

def situation(client, now=NOW):
    return ok(client.get("/safety/situation", params={"now": now.isoformat()}))


def test_situation_fingerprint_changes_only_when_situation_changes(use):
    src = CountingMock()
    client = use(src)
    calm = situation(client)
    assert calm["zones"]["breakwater_north"] == "safe" and calm["warnings"] == []
    assert calm["hours"] and all(set(h["levels"].values()) == {"safe"} for h in calm["hours"])
    assert calm["forecast_until"] and all(calm["data_ok"].values())
    # 같은 상황이면 시간이 조금 흘러도 같다
    assert situation(client, NOW + timedelta(minutes=30))["fingerprint"] == calm["fingerprint"]
    src.scenario = "wind_tomorrow"
    wind = situation(client)
    assert wind["fingerprint"] != calm["fingerprint"]
    risky = [h for h in wind["hours"] if h["levels"]["breakwater"] == "danger"]
    assert risky and all(h["time"].startswith("2026-10-05") for h in risky)
    assert all(h["levels"]["outdoor"] == "caution" and "indoor" in h["info"] for h in risky)
    src.scenario = "rain"
    rain = situation(client)
    assert rain["fingerprint"] not in (calm["fingerprint"], wind["fingerprint"])
    assert any("outdoor" in h["info"] for h in rain["hours"])  # 참고 신호 변화도 감지한다
    src.scenario = "storm"
    storm = situation(client)
    assert storm["zones"]["breakwater_north"] == "danger" and len(storm["warnings"]) == 3


def test_situation_data_failure_changes_fingerprint(use):
    calm = situation(use(CountingMock()))
    down = situation(use(ForecastDown()))
    assert down["fingerprint"] != calm["fingerprint"] and down["data_ok"]["forecast"] is False and down["hours"] == []


# ---------- 가상 상황 ----------

def test_sim_changes_scenario_and_clock(use):
    src = CountingMock()
    client = use(src)
    st = ok(client.get("/safety/sim"))
    assert st["scenario"] == "calm" and st["offset_minutes"] == 0 and "storm" in st["scenarios"]
    st = ok(client.put("/safety/sim", json={"scenario": "storm", "now": "2026-10-05T09:00:00+09:00"}))
    assert st["scenario"] == "storm" and st["now"].startswith("2026-10-05T09:00") and st["offset_minutes"] == 23 * 60
    # now 를 주지 않은 조회는 가상 시각을 쓴다
    r = ok(client.post("/safety/assess", json={"visits": [visit("p-breakwater", "2026-10-05", "10:00")]}))
    assert r["now"].startswith("2026-10-05T09:00") and r["visits"][0]["horizon"] == "now"
    assert ok(client.get("/safety/situation"))["now"].startswith("2026-10-05T09:00")
    st = ok(client.put("/safety/sim", json={"reset_clock": True}))
    assert st["offset_minutes"] == 0 and st["scenario"] == "storm"


def test_sim_rejects_bad_requests(use):
    client = use(CountingMock())
    r = client.put("/safety/sim", json={"scenario": "tsunami"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "unknown_scenario"
    assert client.put("/safety/sim", json={"scenrio": "storm"}).status_code == 422  # 모르는 필드

    class Live:  # mock 이 아닌 소스
        pass

    r = use(Live()).put("/safety/sim", json={"scenario": "storm"})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "sim_disabled"



# ---------- 데이터 장애는 낮은 단계가 아니라 판단 보류 ----------

def test_places_outage_without_coords_is_not_assessed(use):
    fake_places.down = True
    client = use(CountingMock("wind_tomorrow"))
    v = assess(client, visit("p-breakwater", "2026-10-05", "14:00"))["visits"][0]
    # 방파제인지 몰라 야외 기준(바람 한 단계 낮게)으로만 확인된 단계. 실제로는 더 높을 수 있다고 표시
    assert v["risk"] == "caution" and v["data_missing"] is True and any("알 수 없다" in n for n in v["notes"])
    # 좌표·장소 종류를 함께 주면 places 없이 판단한다
    v = assess(client, {"place_id": "p-breakwater", "lat": 35.9871, "lng": 129.5583, "content_type": "관광지",
                        "date": "2026-10-05", "start": "14:00"})["visits"][0]
    assert v["risk"] == "danger" and v["data_missing"] is False
    v = assess(client, {"place_id": "p-museum", "lat": 35.9821, "lng": 129.5421, "content_type": "문화시설",
                        "date": "2026-10-05", "start": "14:00"})["visits"][0]
    assert v["indoor"] is True and v["risk"] == "safe"
    # 확인된 위험 근거가 없으면 safe 가 아니라 판단 보류
    calm = assess(use(CountingMock()), visit("p-breakwater", "2026-10-05", "14:00"))["visits"][0]
    assert calm["risk"] is None and calm["unassessed_reason"] == "data_missing"


def test_cctv_outage_near_breakwater_now_is_not_assessed(use):
    class CctvDown(CountingMock):
        def breakwater(self):
            return Fetched(None, NOW, "SourceError: down")

    r = assess(use(CctvDown()), visit("p-breakwater", "2026-10-04", "11:00"))
    assert r["visits"][0]["risk"] is None and r["visits"][0]["unassessed_reason"] == "data_missing"
    st = ok(use(CctvDown()).get("/safety/status", params={"now": NOW.isoformat()}))
    assert next(z for z in st["zones"] if z["id"] == "breakwater_north")["risk"] is None


def test_sensor_without_level_near_visit_is_not_assessed(use):
    class NoLevel(CountingMock):
        def sensors(self):
            f = super().sensors()
            for r in f.value:
                if r.id == 8:
                    r.level = None
            return f

    v = assess(use(NoLevel()), visit("p-police", "2026-10-04", "11:00"))["visits"][0]
    assert v["risk"] is None and v["unassessed_reason"] == "data_missing"


def test_naive_now_is_kst(use):
    r = ok(use(CountingMock()).get("/safety/situation", params={"now": "2026-10-04T14:00:00"}))
    assert r["now"].startswith("2026-10-04T14:00") and r["now"].endswith("+09:00")


def test_visit_past_forecast_end_is_not_assessed(use):
    src = CountingMock()
    last = src.forecast(NOW).value.last_hour
    v = assess(use(src), {"place_id": "p-street", "date": last.date().isoformat(), "start": f"{last:%H:%M}",
                          "end": f"{last + timedelta(minutes=90):%H:%M}"})["visits"][0]
    assert v["risk"] is None and v["unassessed_reason"] == "beyond_forecast"
# ---------- 길찾기 우회 구역 ----------

def avoid(client, **params):
    return ok(client.get("/safety/avoid", params={"now": NOW.isoformat()} | params))


def test_avoid_calm_has_no_zones(use):
    r = avoid(use(CountingMock()))
    assert r["zones"] == [] and "없다" in r["guidance"]


def test_avoid_storm_uses_each_sensor_not_regional_forecast(use):
    r = avoid(use(CountingMock("storm")))
    flood = {z["id"]: z for z in r["zones"] if z["kind"] == "flood"}
    # 호우 예보·특보는 모든 침수 구역에 같으므로 넣지 않는다. 실측 등급이 오른 센서(4 위험, 8 주의, 1 주의)만
    assert set(flood) == {"flood_4", "flood_8", "flood_1"}
    assert flood["flood_4"]["risk"] == "danger" and "등급 3" in flood["flood_4"]["reason"]
    assert any(z["kind"] == "breakwater" for z in r["zones"])  # 특보·강풍 예보로 방파제 위험

    only_danger = avoid(use(CountingMock("storm")), min_risk="danger")
    assert {z["id"] for z in only_danger["zones"] if z["kind"] == "flood"} == {"flood_4"}


def test_avoid_polygon_is_closed_ring_around_sensor(use):
    from app.safety import geo
    z = next(z for z in avoid(use(CountingMock("storm")))["zones"] if z["id"] == "flood_4")
    ring = z["polygon"]
    assert ring[0] == ring[-1] and len(ring) == 17
    lat, lng = 35.991235, 129.557327  # 구룡포수협 스마트맨홀
    radii = [geo.distance_m((lat, lng), (p[1], p[0])) for p in ring]
    assert all(75 < r < 85 for r in radii)  # 반경 80m (판단용 200m 가 아님)


def test_avoid_breakwater_polygon_covers_the_line(use):
    from app.safety import geo

    def inside(ring, x, y):
        hit = False
        for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
            if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
                hit = not hit
        return hit

    ring = geo.zone_polygon(geo.BREAKWATER_NORTH)
    assert ring[0] == ring[-1]
    assert all(inside(ring, lng, lat) for lat, lng in geo.BREAKWATER_NORTH.path)


def test_avoid_sensor_failure_is_reported(use):
    class SensorsDown(CountingMock):
        def sensors(self):
            return Fetched(None, NOW, "SourceError: 40104")

    r = avoid(use(SensorsDown("storm")))
    assert not any(z["kind"] == "flood" for z in r["zones"])
    assert r["data_status"]["sensors"]["ok"] is False and "침수 센서" in r["guidance"]
