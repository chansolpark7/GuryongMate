"""장소 성질 '해안' (항구·해수욕장 등 바다와 맞닿은 야외). docs/safety/README.md §3.

장소는 places DB 의 실제 구룡포 장소(좌표·분류)를 쓴다. 구룡포항은 해안이면서 해양경찰서·환승센터 지표면 수위계
200m 안이라 침수 감시 구역이기도 하다 (두 성질의 근거를 합친다).
"""
from datetime import datetime

import pytest

from app.safety import geo, risk
from app.safety.places import PlacePoint
from app.safety.sources import KST, ActiveWarning, ForecastHour

from tests.safety.test_api import NOW, PLACES, CountingMock, assess, ok, use  # noqa: F401  (use 는 fixture)

HARBOR = ["자연관광", "자연경관(하천‧해양)", "항구/포구"]
BEACH = ["자연관광", "자연경관(하천‧해양)", "해변. 해수욕장"]
ALLEY = ["문화관광", "도시.지역문화관광", "골목길, 문화거리"]
CAFE_CAT = ["음식", "카페/ 찻집", "카페"]
COAST_PLACES = {
    "kto-2610501": PlacePoint("kto-2610501", "구룡포항", 35.989305, 129.555642, "관광지", tuple(HARBOR)),
    "kto-127214": PlacePoint("kto-127214", "구룡포해수욕장", 35.996832, 129.566285, "관광지", tuple(BEACH)),
    "kto-1909659": PlacePoint("kto-1909659", "구룡포 일본인 가옥거리", 35.990621, 129.560455, "관광지", tuple(ALLEY)),
    # 바다 바로 앞(해안선 약 26m) 카페: 실내라 해안 규칙을 쓰지 않는다
    "kto-2834344": PlacePoint("kto-2834344", "카페파도", 35.976834, 129.551490, "음식점", tuple(CAFE_CAT)),
}
PORT, HOUSE, CAFE, BEACH_ID = "kto-2610501", "kto-1909659", "kto-2834344", "kto-127214"


@pytest.fixture(autouse=True)
def coast_places():
    PLACES.update(COAST_PLACES)
    yield
    for k in COAST_PLACES:
        PLACES.pop(k, None)


def visit(place_id, d, start, end=None, key=None):
    v = {"place_id": place_id, "date": d, "start": start, "key": key or place_id}
    return v | ({"end": end} if end else {})


# ---------- 판정 ----------

def test_coast_kind_from_category_then_title():
    assert risk.coast_kind(HARBOR, "구룡포항") == "항구"
    assert risk.coast_kind(BEACH, None) == "해수욕장"
    assert risk.coast_kind(["문화관광", "랜드마크관광", "등대"], "호미곶 등대") == "등대"
    assert risk.coast_kind(["문화관광", "도시공원", "주제공원"], "장길리 복합 낚시공원") == "바다낚시공원"  # 이름으로
    assert risk.coast_kind(ALLEY, "구룡포 일본인 가옥거리") is None
    assert risk.coast_kind(["축제/공연/행사", "축제", "기타축제"], "포항 해뜨면 국수축제") is None  # '포항' 은 항구가 아니다
    assert risk.coast_kind(None, None) is None


def test_coordinates_near_coastline():
    assert geo.near_coast((35.989262, 129.555611))  # 국수축제 (구룡포항 부두, 해안선 약 19m)
    assert geo.near_coast((35.996832, 129.566285))  # 구룡포해수욕장
    assert not geo.near_coast((35.990621, 129.560455))  # 가옥거리 골목 (약 100m)
    assert not geo.near_coast((35.990500, 129.561600))  # 근대문화역사관
    assert not geo.near_coast((35.9750, 129.5300))  # 내륙
    assert geo.coast_distance_m((35.9750, 129.5300)) is None


def test_settle_priority():
    assert risk.settle({"breakwater", "coast"}) == {"breakwater"}  # 방파제가 더 엄격
    assert risk.settle({"flood", "coast"}) == {"flood", "coast"}  # 둘 다의 근거를 합친다
    assert risk.settle({"coast"}, indoor=True) == set()  # 바다 보이는 카페 = 실내
    assert risk.settle({"flood", "coast"}, indoor=True) == {"flood"}


# ---------- 규칙 (경계값) ----------

def hour(**kw) -> ForecastHour:
    return ForecastHour(datetime(2026, 10, 5, 14, tzinfo=KST), **kw)


@pytest.mark.parametrize("wsd,coast,outdoor", [
    (8.9, "none", "none"), (9.0, "caution", "info"), (13.9, "caution", "info"), (14.0, "danger", "caution")])
def test_wind_is_not_lowered_on_coast(wsd, coast, outdoor):
    assert risk.hour_level(hour(wsd=wsd), {"coast"}) == coast
    assert risk.hour_level(hour(wsd=wsd), set()) == outdoor
    assert risk.hour_level(hour(wsd=wsd), {"breakwater"}) == coast  # 방파제와 같은 기준


@pytest.mark.parametrize("wav,coast", [(1.4, "none"), (1.5, "caution"), (2.9, "caution"), (3.0, "danger")])
def test_wave_applies_on_coast_only(wav, coast):
    assert risk.hour_level(hour(wav=wav), {"coast"}) == coast
    assert risk.hour_level(hour(wav=wav), set()) == "none"  # 그 밖의 야외는 파고를 보지 않는다


def test_rain_on_coast_is_like_outdoor():
    for mm in (5.0, 12.0, 35.0):
        assert risk.hour_level(hour(pcp_mm=mm, pty=1, pop=90), {"coast"}) == risk.hour_level(
            hour(pcp_mm=mm, pty=1, pop=90), set())


def test_coast_reason_shows_place_kind():
    hz = risk.forecast_hazards([hour(wsd=16.0, wav=3.5, pcp_mm=8.0)], {"coast"}, coast=risk.coast_label("항구"))
    by = {h.type: h for h in hz}
    assert by["wind"].reason.startswith("해안(항구) — ") and "16m/s" in by["wind"].reason
    assert by["wave"].reason.startswith("해안(항구) — ") and "3.5m" in by["wave"].reason
    assert not by["rain"].reason.startswith("해안")  # 비는 그 밖의 야외와 같아 성질을 붙이지 않는다
    assert risk.forecast_hazards([hour(wsd=16.0)], {"coast"})[0].reason.startswith("해안 — ")  # 종류를 모를 때


@pytest.mark.parametrize("name,coast,outdoor", [
    ("풍랑주의보", "caution", None), ("풍랑경보", "danger", None),
    ("강풍주의보", "caution", "info"), ("강풍경보", "danger", "caution"),
    ("호우주의보", "info", "info"), ("호우경보", "caution", "caution")])
def test_warnings_on_coast(name, coast, outdoor):
    ws = [ActiveWarning(name, "경북남부앞바다")]
    hz = risk.warning_hazards(ws, {"coast"}, coast="해안(항구)")
    assert [h.level for h in hz] == [coast]
    if name[:2] in ("풍랑", "강풍"):
        assert hz[0].reason.startswith("해안(항구) — ")
    assert [h.level for h in risk.warning_hazards(ws, set())] == ([outdoor] if outdoor else [])


# ---------- assess ----------

def test_storm_harbor_is_danger_but_alley_stays_caution(use):
    r = assess(use(CountingMock("storm")), visit(PORT, "2026-10-04", "11:00"), visit(HOUSE, "2026-10-04", "11:00"),
               visit(CAFE, "2026-10-04", "11:00"), visit(BEACH_ID, "2026-10-04", "11:00"))
    by = {v["key"]: v for v in r["visits"]}
    port = by[PORT]
    assert port["risk"] == "danger"
    assert port["sensitivity"] == ["침수 감시 구역", "해안"]  # 해경 앞 지표면 수위계 200m 안 + 항구
    reasons = [h["reason"] for h in port["hazards"]]
    assert any(x.startswith("해안(항구) — ") and "풍속" in x and "16m/s" in x for x in reasons), reasons
    assert any(x.startswith("해안(항구) — ") and "파고" in x for x in reasons), reasons
    assert any(x.startswith("해안(항구) — 풍랑주의보") for x in reasons), reasons
    assert any(x.startswith("호우주의보") for x in reasons)  # 침수 감시 구역 근거(호우특보 주의)도 함께
    # 바닷가에서 한 블록 안쪽 골목은 그 밖의 야외 그대로 (바람 한 단계 낮춤, 파고·풍랑 무시)
    house = by[HOUSE]
    assert house["risk"] == "caution" and house["sensitivity"] == []
    assert not any(h["type"] == "wave" for h in house["hazards"])
    # 바다 바로 앞 카페도 실내
    cafe = by[CAFE]
    assert cafe["risk"] == "safe" and cafe["sensitivity"] == ["실내"]
    assert by[BEACH_ID]["risk"] == "danger" and by[BEACH_ID]["sensitivity"] == ["해안"]


def test_wind_tomorrow_harbor_danger_calm_safe(use):
    r = assess(use(CountingMock("wind_tomorrow")), visit(PORT, "2026-10-05", "14:00"),
               visit(HOUSE, "2026-10-05", "14:00"), visit(PORT, "2026-10-05", "10:00", key="am"))
    by = {v["key"]: v for v in r["visits"]}
    port = by[PORT]
    assert port["risk"] == "danger"
    assert {(h["type"], h["level"]) for h in port["hazards"]} >= {("wind", "danger"), ("wave", "danger")}
    assert port["better_times"] and port["better_times"][0]["start"].startswith("2026-10-04")
    assert by[HOUSE]["risk"] == "caution"
    assert by["am"]["risk"] == "safe"
    calm = assess(use(CountingMock()), visit(PORT, "2026-10-05", "14:00"))["visits"][0]
    assert calm["risk"] == "safe" and calm["hazards"] == []


def test_categories_given_with_coordinates_skip_lookup(use):
    """planner 처럼 좌표·종류·분류를 함께 주면 places 를 다시 조회하지 않는다."""
    v = {"key": "x", "lat": 35.9905, "lng": 129.5640, "content_type": "관광지", "categories": BEACH,
         "label": "어딘가", "date": "2026-10-05", "start": "14:00"}
    out = assess(use(CountingMock("wind_tomorrow")), v)["visits"][0]
    assert out["risk"] == "danger" and "해안" in out["sensitivity"]
    assert any(h["reason"].startswith("해안(해수욕장) — ") for h in out["hazards"])
    # 분류가 없으면 좌표로: 해안선 30m 안이면 '해안(바닷가)'
    v = {"key": "y", "lat": 35.989262, "lng": 129.555611, "content_type": "축제공연행사",
         "date": "2026-10-05", "start": "14:00"}
    out = assess(use(CountingMock("wind_tomorrow")), v)["visits"][0]
    assert out["risk"] == "danger" and any(h["reason"].startswith("해안(바닷가) — ") for h in out["hazards"])
    # 실내로 지정하면 해안이어도 실내 규칙
    out = assess(use(CountingMock("wind_tomorrow")), v | {"indoor": True})["visits"][0]
    assert out["risk"] == "safe" and "해안" not in out["sensitivity"]


HARBOR_ROAD = [[35.9898, 129.5546], [35.98935, 129.5556], [35.9897, 129.5566]]  # 구룡포항 부두 앞길


def test_walk_along_harbor_is_coast_car_is_not(use):
    client = use(CountingMock("wind_tomorrow"))
    walk = {"key": "w", "path": HARBOR_ROAD, "mode": "walk", "date": "2026-10-05", "start": "14:00", "end": "14:10"}
    r = assess(client, walk, walk | {"key": "c", "mode": "car"})
    by = {v["key"]: v for v in r["visits"]}
    assert "해안" in by["w"]["sensitivity"] and by["w"]["risk"] == "danger"
    assert any(h["reason"].startswith("해안(바닷가 길) — ") for h in by["w"]["hazards"])
    assert "해안" not in by["c"]["sensitivity"] and by["c"]["risk"] == "safe"  # 차 안: 바람은 참고까지


# ---------- outlook · situation ----------

def test_outlook_and_situation_have_coast_kind(use):
    client = use(CountingMock("wind_tomorrow"))
    o = ok(client.get("/safety/outlook", params={"date": "2026-10-05", "now": NOW.isoformat()}))
    coast = [(w["start"][11:16], w["end"][11:16]) for w in o["safe_windows"] if w["place_kind"] == "coast"]
    bw = [(w["start"][11:16], w["end"][11:16]) for w in o["safe_windows"] if w["place_kind"] == "breakwater"]
    assert coast == bw == [("09:00", "12:00")]  # 바람·파고 기준이 방파제와 같다
    s = ok(client.get("/safety/situation", params={"now": NOW.isoformat()}))
    h14 = next(h for h in s["hours"] if h["time"].startswith("2026-10-05T14"))
    assert h14["levels"] == {"breakwater": "danger", "flood": "caution", "coast": "danger", "outdoor": "caution",
                             "indoor": "safe"}
