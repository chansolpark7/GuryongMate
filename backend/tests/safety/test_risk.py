"""시점 분류·위험 해석 (외부 호출 없음)."""
from datetime import date, datetime, timedelta

from app.safety import geo, risk
from app.safety.sources import KST, ActiveWarning, ForecastHour, SensorReading

NOW = datetime(2026, 10, 4, 10, 0, tzinfo=KST)
FC_LAST = datetime(2026, 10, 7, 0, 0, tzinfo=KST)  # 단기예보가 10/7 0시까지 있음
BREAKWATER = (35.9871, 129.5583)  # 북방파제 위
FLOOD = (35.9887, 129.5531)  # 구룡포파출소 지표면 수위계 옆
INLAND = (35.9750, 129.5300)


def at(d: datetime, hours: int = 1):
    return d, d + timedelta(hours=hours)


def test_classify_by_lead_time():
    assert risk.classify(*at(NOW - timedelta(hours=3)), NOW, FC_LAST, True).horizon == "past"
    t = risk.classify(*at(NOW + timedelta(hours=2)), NOW, FC_LAST, True)
    assert (t.horizon, t.confidence, t.forecast_available) == ("now", "high", True)
    t = risk.classify(*at(NOW + timedelta(hours=20)), NOW, FC_LAST, True)
    assert (t.horizon, t.confidence) == ("short_range", "high")
    t = risk.classify(*at(NOW + timedelta(hours=50)), NOW, FC_LAST, True)
    assert (t.horizon, t.confidence) == ("short_range", "medium")
    t = risk.classify(*at(datetime(2026, 10, 9, 13, tzinfo=KST)), NOW, FC_LAST, True)
    assert (t.horizon, t.forecast_available, t.recheck_from) == ("medium_range", False, date(2026, 10, 6))
    t = risk.classify(*at(datetime(2027, 1, 15, 13, tzinfo=KST)), NOW, FC_LAST, True)
    assert (t.horizon, t.confidence, t.recheck_from) == ("long_range", "none", date(2027, 1, 12))


def test_classify_ongoing_visit_is_now():
    assert risk.classify(NOW - timedelta(minutes=30), NOW + timedelta(minutes=30), NOW, FC_LAST, True).horizon == "now"


def test_classify_when_forecast_failed_keeps_short_range_but_no_forecast():
    t = risk.classify(*at(NOW + timedelta(hours=30)), NOW, None, False)
    assert (t.horizon, t.forecast_available) == ("short_range", False)


def test_sensitivity():
    assert risk.sensitivity(*BREAKWATER) == {"breakwater"}
    assert risk.sensitivity(*FLOOD) == {"flood"}
    assert risk.sensitivity(*INLAND) == set()
    assert risk.sensitivity(None, None) == set()


def test_zone_distance():
    assert geo.distance_to_zone(BREAKWATER, geo.BREAKWATER_NORTH) < 30
    assert geo.distance_to_zone(BREAKWATER, geo.BREAKWATER_SOUTH) > 100


def hour(h: int, **kw) -> ForecastHour:
    return ForecastHour(datetime(2026, 10, 5, h, tzinfo=KST), **kw)


def test_forecast_hazards_depend_on_place():
    hours = [hour(14, wsd=15.0, wav=3.2, pcp_mm=0.0), hour(15, wsd=16.0, wav=3.5, pcp_mm=0.0)]
    bw = risk.forecast_hazards(hours, {"breakwater"})
    assert {(h.type, h.level) for h in bw} == {("wind", "danger"), ("wave", "danger")}
    wind = next(h for h in bw if h.type == "wind")
    assert "14~16시" in wind.reason and "최대 16" in wind.reason
    inland = risk.forecast_hazards(hours, set())
    assert {(h.type, h.level) for h in inland} == {("wind", "caution")}  # 파고는 방파제만, 바람은 한 단계 낮게


def test_heavy_rain_is_flood_hazard_in_flood_zone():
    hz = risk.forecast_hazards([hour(14, pcp_mm=35.0, pty=1, pop=90)], {"flood"})
    assert [(h.type, h.level) for h in hz] == [("flood", "danger")]
    assert [(h.type, h.level) for h in risk.forecast_hazards([hour(14, pcp_mm=35.0)], set())] == [("rain", "caution")]
    # 강수량은 적어도 비 예보가 확실하면 참고로 알린다
    assert [h.level for h in risk.forecast_hazards([hour(14, pcp_mm=0.5, pty=1, pop=80)], {"flood"})] == ["low"]


def test_warning_impact_depends_on_place():
    ws = [ActiveWarning("풍랑주의보", "경북남부앞바다")]
    assert [h.level for h in risk.warning_hazards(ws, {"breakwater"})] == ["danger"]
    assert risk.warning_hazards(ws, set()) == []
    hw = [ActiveWarning("호우경보", "경상북도(포항)")]
    assert [h.level for h in risk.warning_hazards(hw, {"flood"})] == ["danger"]
    assert [h.level for h in risk.warning_hazards(hw, set())] == ["caution"]
    assert [h.level for h in risk.warning_hazards([ActiveWarning("건조주의보", "포항")], set())] == ["low"]


def test_sensor_levels():
    r = SensorReading(8, "ROAD", "구룡포파출소_지표면 수위계", *FLOOD, 0.0, 1)
    assert risk.sensor_risk(r) == ("none", None)
    assert risk.sensor_risk(SensorReading(8, "ROAD", "x", *FLOOD, 12.0, 1))[0] == "low"
    assert risk.sensor_risk(SensorReading(8, "ROAD", "x", *FLOOD, 12.0, 2))[0] == "caution"
    assert risk.sensor_risk(SensorReading(4, "HOLE", "x", *FLOOD, 1650.0, 3))[0] == "danger"
    assert risk.sensor_risk(SensorReading(5, "RAIN", "x", *FLOOD, 40.0, 3))[0] == "none"  # 강우량계는 구역이 아님
    near = risk.sensor_hazards([SensorReading(8, "ROAD", "x", *FLOOD, 12.0, 2)], *FLOOD, NOW)
    far = risk.sensor_hazards([SensorReading(8, "ROAD", "x", *FLOOD, 12.0, 2)], *INLAND, NOW)
    assert [h.level for h in near] == ["caution"] and far == []


def test_far_future_advice_says_do_not_change():
    t = risk.classify(*at(datetime(2027, 1, 15, 13, tzinfo=KST)), NOW, FC_LAST, True)
    text = risk.visit_advice(t, "unknown", {"breakwater"}, NOW, datetime(2027, 1, 15, 13, tzinfo=KST), [], False)
    assert "예측할 수 없다" in text and "바꾸거나 빼지 마라" in text and "2027-01-12" in text and "방파제" in text


def test_seasonal_notes_are_marked_as_tendency():
    assert all("예보가 아님" in n for n in risk.seasonal_notes(date(2027, 8, 1)))
    assert risk.seasonal_notes(date(2027, 5, 1)) == []
