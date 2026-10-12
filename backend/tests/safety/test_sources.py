"""외부 응답 파싱. fixtures 는 2026-10-04 실제 API 응답이다."""
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.safety import sources as s
from app.safety.sources import KST

FIX = Path(__file__).parent / "fixtures"
KEYWORDS = ["포항", "경북남부앞바다", "동해남부북쪽"]  # config 기본값


def load(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def test_parse_sensors_real_response():
    rows = s.parse_sensors(load("eum_sensor_level.json")["data"])
    assert len(rows) == 10
    assert {r.type for r in rows} == {"HOLE", "ROAD", "RIVER", "RAIN"}
    assert all(r.level == 1 for r in rows)
    hole = next(r for r in rows if r.type == "HOLE")
    assert hole.value and hole.value > 100 and 35.9 < hole.lat < 36.0


def test_parse_breakwater_real_response():
    rows = s.parse_breakwater(load("eum_abnormal.json")["data"])
    assert {r.side for r in rows} == {"north", "south"}
    south_17 = next(r for r in rows if r.side == "south" and r.hour == datetime(2026, 10, 3, 17, tzinfo=KST))
    assert (south_17.intrusion, south_17.approach, south_17.abnormal) == (9, 7, 1)


def test_parse_forecast_real_response():
    raw = load("kma_vilagefcst.json")["response"]["body"]["items"]["item"]
    fc = s.parse_forecast(raw, datetime(2026, 10, 4, 5, tzinfo=KST))
    first = fc.hours[datetime(2026, 10, 4, 6, tzinfo=KST)]
    assert (first.tmp, first.wsd, first.sky, first.pop, first.pty, first.wav, first.pcp_mm) == (
        19.0, 5.3, 4, 30, 0, 0.5, 0.0)


@pytest.mark.parametrize("text,mm", [
    ("강수없음", 0.0), ("0", 0.0), ("1mm 미만", 0.5), ("1.0mm 미만", 0.5), ("2.0mm", 2.0),
    ("30.0~50.0mm", 30.0), ("50.0mm 이상", 50.0), ("", 0.0),
])
def test_parse_pcp(text, mm):
    assert s.parse_pcp(text) == mm


def test_parse_warnings_keeps_only_guryongpo_areas():
    item = {"tmFc": 202610031000, "t6": "o 건조주의보 : 서울(서울동남권 제외)\r\n"
                                        "o 강풍주의보 : 경상북도(경주, 영덕,\r\n 포항), 울산\r\n"
                                        "o 풍랑주의보 : 동해남부북쪽안쪽먼바다, 동해남부북쪽바깥먼바다\r\n"
                                        "o 호우주의보 : 경상북도(경주동부)\r\n",
            "t7": "o 없 음"}
    w = s.parse_warnings(item, KEYWORDS)
    assert [a.name for a in w.active] == ["강풍주의보", "풍랑주의보"]  # 줄바꿈된 '포항' 도 찾는다
    assert w.active[0].kind == "강풍" and w.active[0].grade == "주의보"
    assert w.issued_at == datetime(2026, 10, 3, 10, tzinfo=KST)
    assert w.preliminary == []


def test_parse_warnings_real_response_without_local_warning():
    item = load("kma_pwn_status.json")["response"]["body"]["items"]["item"][0]
    assert s.parse_warnings(item, KEYWORDS).active == []


def test_parse_warnings_excludes_southern_sea_and_spaced_none():
    item = {"tmFc": 202610011000, "t6": "o 풍랑주의보 : 동해남부남쪽안쪽먼바다, 부산앞바다", "t7": "o 없 음"}
    assert s.parse_warnings(item, KEYWORDS).active == []  # 부산·울산 쪽 해역은 구룡포가 아니다
    assert s.parse_warning_lines("o 없 음\r\n", KEYWORDS) == []


def test_parse_tide_real_response():
    items = load("khoa_tide.json")["body"]["items"]["item"]
    ev = s.parse_tide(items)
    assert [(e.kind, e.level_cm) for e in ev] == [("high", 33.0), ("low", 15.0)]


def test_portal_items_formats():
    assert s.portal_items(200, json.dumps(load("khoa_tide.json")))["item"]
    assert s.portal_items(200, '{"response":{"header":{"resultCode":"03","resultMsg":"NODATA_ERROR"}}}') == {}
    with pytest.raises(s.SourceError, match="SERVICETIMEOUT"):
        s.portal_items(200, json.dumps(load("portal_error.json")))
    with pytest.raises(s.SourceError):
        s.portal_items(500, "<OpenAPI_ServiceResponse>...")


@pytest.mark.parametrize("now,base", [
    (datetime(2026, 10, 4, 5, 20, tzinfo=KST), datetime(2026, 10, 4, 5, tzinfo=KST)),
    (datetime(2026, 10, 4, 5, 10, tzinfo=KST), datetime(2026, 10, 4, 2, tzinfo=KST)),  # 발표 직후엔 아직 없음
    (datetime(2026, 10, 4, 1, 0, tzinfo=KST), datetime(2026, 10, 3, 23, tzinfo=KST)),
    (datetime(2026, 10, 4, 23, 59, tzinfo=KST), datetime(2026, 10, 4, 23, tzinfo=KST)),
])
def test_kma_base(now, base):
    assert s.kma_base(now) == base


def test_key_param_keeps_encoded_key():
    assert s._key_param("ab%2Bcd%3D") == "ab%2Bcd%3D"
    assert s._key_param("ab+cd/=") == "ab%2Bcd%2F%3D"


def test_cache_returns_error_as_value():
    cache = s._Cache()
    calls = []

    def boom():
        calls.append(1)
        raise RuntimeError("down")

    first = cache.get(("x",), ttl=60, err_ttl=60, load=boom)
    second = cache.get(("x",), ttl=60, err_ttl=60, load=boom)
    assert not first.ok and "down" in first.error and second is first and len(calls) == 1


# ---------- 단기예보 받기 (LiveSources, HTTP 가짜) ----------

def _kma(base: datetime, hours: int, wind: dict[int, float] | None = None, code: str = "00") -> dict:
    """base 발표의 단기예보 응답 (base+1 시부터 hours 시간, 풍속만). wind: {시각(시): 풍속}."""
    items = []
    for k in range(1, hours + 1):
        t = base + timedelta(hours=k)
        items.append({"category": "WSD", "fcstDate": f"{t:%Y%m%d}", "fcstTime": f"{t:%H}00",
                      "fcstValue": str((wind or {}).get(t.hour, 3.0) if t.date() == base.date() else 3.0)})
    if code != "00":
        return {"response": {"header": {"resultCode": code, "resultMsg": "NODATA_ERROR"}}}
    return {"response": {"header": {"resultCode": "00"}, "body": {"items": {"item": items}}}}


def _live(responses: dict[str, dict]) -> tuple[s.LiveSources, list[str]]:
    """base_time(HHMM) -> 응답. 부른 base_time 목록을 함께 돌려준다."""
    import httpx
    from app.safety.config import SafetySettings
    asked: list[str] = []

    def handle(req: httpx.Request) -> httpx.Response:
        bt = req.url.params["base_time"]
        asked.append(bt)
        return httpx.Response(200, json=responses[bt])

    cfg = SafetySettings(data_go_kr_service_key="k", dt_service_key="k")
    return s.LiveSources(cfg, httpx.Client(transport=httpx.MockTransport(handle))), asked


def test_forecast_fills_current_hour_from_previous_issue():
    """14:20 에는 14시 발표(15시부터)가 최신이라 14시 예보가 빈다 -> 11시 발표의 14시 예보로 채운다 (S1)."""
    b14, b11 = datetime(2026, 10, 4, 14, tzinfo=KST), datetime(2026, 10, 4, 11, tzinfo=KST)
    src, asked = _live({"1400": _kma(b14, 60), "1100": _kma(b11, 60, {14: 16.0})})
    f = src.forecast(datetime(2026, 10, 4, 14, 20, tzinfo=KST))
    assert f.ok and f.value.first_hour == b14 and f.value.hours[b14].wsd == 16.0
    assert f.value.base == b14 and f.value.hours[b14 + timedelta(hours=1)].wsd == 3.0  # 15시는 최신 발표
    assert asked == ["1400", "1100"]
    # 15시가 지나면 최신 발표만으로 충분하다
    asked.clear()
    assert src.forecast(datetime(2026, 10, 4, 15, 10, tzinfo=KST)).value.first_hour == b14 + timedelta(hours=1)
    assert asked == []  # 캐시


def test_forecast_gap_stays_when_previous_issue_fails():
    b14, b11 = datetime(2026, 10, 4, 14, tzinfo=KST), datetime(2026, 10, 4, 11, tzinfo=KST)
    src, _ = _live({"1400": _kma(b14, 60), "1100": _kma(b11, 60, code="03")})
    f = src.forecast(datetime(2026, 10, 4, 14, 20, tzinfo=KST))
    assert f.ok and f.value.first_hour > datetime(2026, 10, 4, 14, 20, tzinfo=KST)


def test_forecast_nodata_or_short_is_failure():
    """NODATA(03)·너무 짧은 예보는 실패로 본다 (S4). 빈 예보를 정상으로 받으면 지도는 안전, 일정은 예보 범위 밖이 된다."""
    b14 = datetime(2026, 10, 4, 14, tzinfo=KST)
    now = datetime(2026, 10, 4, 15, 10, tzinfo=KST)
    src, _ = _live({"1400": _kma(b14, 60, code="03")})
    f = src.forecast(now)
    assert not f.ok and "NODATA" in f.error
    src, _ = _live({"1400": _kma(b14, 6)})
    f = src.forecast(now)
    assert not f.ok and "짧음" in f.error
