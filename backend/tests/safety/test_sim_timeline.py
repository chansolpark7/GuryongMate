"""가상 날씨 시간표 (docs/console/README.md §7.5, docs/safety/README.md §1 /safety/sim).

구간 {kind, start, end} 은 가상 시각 기준 절대 시각이다 (start 포함·end 제외). 구간 밖은 평상시.
예보는 넣는 즉시 보이고, 지금 관측값(센서·특보·CCTV·자외선)은 가상 시각이 속한 구간의 값이다.
"""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.safety.places import PlacePoint, get_place_lookup
from app.safety.router import get_sources
from app.safety.sources import KST, MockSources, SimSegment, SimSources, normalize_timeline

NOW = datetime(2026, 10, 10, 15, 20, tzinfo=KST)
H = timedelta(hours=1)
BREAKWATER = PlacePoint("p-breakwater", "구룡포 북방파제", 35.9871, 129.5583, "관광지")


class Clock:
    """실제 시각 자리. 테스트가 직접 흘려보낸다."""

    def __init__(self, t: datetime = NOW):
        self.t = t

    def __call__(self) -> datetime:
        return self.t


class Places:
    def get(self, place_id):
        return BREAKWATER if place_id == BREAKWATER.id else None


@pytest.fixture
def use():
    app.dependency_overrides[get_place_lookup] = lambda: Places()

    def _use(src):
        app.dependency_overrides[get_sources] = lambda: src
        return TestClient(app)

    yield _use
    app.dependency_overrides.clear()


def ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def at(hh: int, mm: int = 0, day: int = 10) -> datetime:
    return datetime(2026, 10, day, hh, mm, tzinfo=KST)


def seg(kind: str, start: datetime, end: datetime) -> dict:
    return {"kind": kind, "start": start.isoformat(), "end": end.isoformat()}


def breakwater_risk(client, hh: int, mm: int = 0) -> str | None:
    """10/10 hh:mm 부터 1시간 북방파제 방문의 단계."""
    body = {"visits": [{"key": "bw", "place_id": "p-breakwater", "date": "2026-10-10", "start": f"{hh:02d}:{mm:02d}"}]}
    return ok(client.post("/safety/assess", json=body))["visits"][0]["risk"]


# ---------- 시간표 판정 (MockSources) ----------

def test_segment_boundary_start_inclusive_end_exclusive():
    clock = Clock(at(14, 59))
    src = MockSources(clock=clock)
    src.timeline = (SimSegment("storm", at(15), at(17)),)
    assert src.scenario == "calm" and src.warnings().value.active == []
    clock.t = at(15)
    assert src.scenario == "storm" and len(src.warnings().value.active) == 3
    clock.t = at(16, 59)
    assert src.scenario == "storm"
    clock.t = at(17)
    assert src.scenario == "calm" and src.segment_at(clock.t) is None
    # 예보: 그 시(정시~1시간)와 겹치는 구간. 14시·17시는 평상시, 15·16시는 폭풍
    hours = src.forecast(at(13)).value.hours
    assert [hours[at(h)].wsd for h in (14, 15, 16, 17)] == [3.0, 16.0, 16.0, 3.0]


def test_overlap_uses_the_worst_kind():
    clock = Clock(at(9))
    src = MockSources(clock=clock)
    src.timeline = (SimSegment("rain", at(10), at(20)), SimSegment("wind", at(12), at(14)),
                    SimSegment("flood", at(11), at(15)), SimSegment("storm", at(13), at(14)))
    kinds = {}
    for h in (10, 11, 12, 13, 14, 16, 20):
        clock.t = at(h, 30)
        kinds[h] = src.scenario
    assert kinds == {10: "rain", 11: "flood", 12: "flood", 13: "storm", 14: "flood", 16: "rain", 20: "calm"}
    hours = src.forecast(at(9)).value.hours
    assert hours[at(12)].pcp_mm == 5.0 and hours[at(12)].wsd == 3.0  # flood > wind (약한 비, 바람 없음)
    assert hours[at(13)].wsd == 16.0  # storm


def test_sensors_and_warnings_return_to_normal_after_segment():
    clock = Clock()
    src = MockSources(clock=clock)
    src.timeline = (SimSegment("storm", at(15), at(17)),)
    assert {r.id: r.level for r in src.sensors().value}[4] == 3
    assert src.uv().value.index == 0.5 and src.breakwater().value[0].fall == 1
    clock.t = at(17, 5)
    assert all(r.level == 1 for r in src.sensors().value)
    assert src.warnings().value.active == [] and src.uv().value.index == 4.0
    assert src.breakwater().value[0].fall == 0


def test_short_storm_keeps_storm_weather_level():
    src = MockSources(clock=Clock(at(12)))
    src.timeline = (SimSegment("storm", at(15, 20), at(17)),)
    h = src.forecast(at(12)).value.hours[at(16)]
    assert (h.wsd, h.wav, h.pcp_mm, h.pty) == (16.0, 3.5, 8.0, 1)
    # 긴 폭풍은 시작 6~14시간째에 시간당 35mm (기존 storm 시나리오와 같다)
    src.timeline = (SimSegment("storm", at(12), at(18, day=11)),)
    hours = src.forecast(at(12)).value.hours
    assert [hours[at(h)].pcp_mm for h in (17, 18, 23)] == [8.0, 35.0, 35.0] and hours[at(2, day=11)].pcp_mm == 8.0


# ---------- PUT /safety/sim ----------

def test_get_sim_shows_timeline_and_kinds(use):
    client = use(MockSources(clock=Clock()))
    st = ok(client.get("/safety/sim"))
    assert st["timeline"] == [] and st["scenario"] == "calm" and st["segment"] is None
    assert set(st["kinds"]) == {"storm", "rain", "flood", "wind"}
    assert {"calm", "storm", "flood", "rain", "wind_tomorrow"} <= set(st["scenarios"])  # 앱 칩 호환


def test_timeline_replaces_and_storm_judgement(use):
    """대시보드 확인 장면: 15:20~17:00 폭풍 -> 16:00 방파제 위험, 17:30 안전 / 17:05 로 옮기면 센서·특보 평상시."""
    clock = Clock()
    client = use(MockSources(clock=clock))
    st = ok(client.put("/safety/sim", json={"timeline": [seg("storm", at(15, 20), at(17))]}))
    assert st["scenario"] == "storm" and st["segment"]["end"].startswith("2026-10-10T17:00")
    assert len(st["timeline"]) == 1 and st["mode"] == "sim"
    assert breakwater_risk(client, 16) == "danger"
    assert breakwater_risk(client, 17, 30) == "safe"
    during = ok(client.get("/safety/situation"))
    s = ok(client.get("/safety/status"))
    assert s["scenario"] == "storm" and s["segment"]["kind"] == "storm" and len(s["warnings"]) == 3
    assert during["scenario"] == "storm" and during["mode"] == "sim"

    ok(client.put("/safety/sim", json={"now": at(17, 5).isoformat()}))
    s = ok(client.get("/safety/status"))
    assert s["scenario"] == "calm" and s["segment"] is None and s["warnings"] == []
    assert all(x["level"] == 1 for x in s["sensors"]) and s["overall_risk"] == "safe"
    after = ok(client.get("/safety/situation"))
    assert after["fingerprint"] != during["fingerprint"]  # 구간이 끝나면 planner 가 다시 점검한다
    assert all(z == "safe" for z in after["zones"].values())


def test_segment_end_changes_fingerprint_even_without_other_change(use):
    """비(참고 신호만) 구간이 끝나는 순간에도 지금 구간이 바뀌므로 fingerprint 가 바뀐다."""
    clock = Clock(at(10))
    client = use(MockSources(clock=clock))
    ok(client.put("/safety/sim", json={"timeline": [seg("rain", at(9), at(11))]}))
    clock.t = at(10, 59)
    a = ok(client.get("/safety/situation"))["fingerprint"]
    clock.t = at(11, 0)
    assert ok(client.get("/safety/situation"))["fingerprint"] != a


def test_forecast_shows_future_segment_immediately(use):
    client = use(MockSources(clock=Clock()))
    ok(client.put("/safety/sim", json={"timeline": [seg("wind", at(13, day=11), at(15, day=11))]}))
    sit = ok(client.get("/safety/situation"))
    risky = sorted(h["time"][:16] for h in sit["hours"] if h["levels"]["breakwater"] == "danger")
    assert risky == ["2026-10-11T13:00", "2026-10-11T14:00"]
    assert sit["scenario"] == "calm" and all(z == "safe" for z in sit["zones"].values())  # 지금은 평상시


def test_preset_is_absolute_and_does_not_shift(use):
    clock = Clock()
    client = use(MockSources(clock=clock))
    st = ok(client.put("/safety/sim", json={"scenario": "storm", "now": NOW.isoformat()}))
    tl = st["timeline"]
    assert [(x["kind"], x["start"][:16], x["end"][:16]) for x in tl] == [
        ("storm", "2026-10-10T15:20", "2026-10-11T21:20")]
    st = ok(client.put("/safety/sim", json={"now": at(18).isoformat()}))  # 시각만 옮김
    assert st["timeline"] == tl and st["scenario"] == "storm"
    clock.t += 10 * H  # 실제 시간이 흘러도
    assert ok(client.get("/safety/sim"))["timeline"] == tl
    st = ok(client.put("/safety/sim", json={"now": at(22, day=11).isoformat()}))  # 구간이 지나면 평상시
    assert st["scenario"] == "calm" and st["timeline"] == tl


@pytest.mark.parametrize("scenario,expected", [
    ("calm", []),
    ("flood", [("flood", "10T15:20", "11T00:20")]),
    ("rain", [("rain", "10T17:20", "11T00:20")]),
    ("wind_tomorrow", [("wind", "11T12:00", "11T18:00")]),
])
def test_presets(use, scenario, expected):
    client = use(MockSources("storm", clock=Clock()))
    st = ok(client.put("/safety/sim", json={"scenario": scenario}))
    assert [(x["kind"], x["start"][8:16], x["end"][8:16]) for x in st["timeline"]] == expected


def test_preset_until(use):
    client = use(MockSources(clock=Clock()))
    st = ok(client.put("/safety/sim", json={"scenario": "storm", "until": "2026-10-10T17:00:00+09:00"}))
    assert [(x["start"][11:16], x["end"][11:16]) for x in st["timeline"]] == [("15:20", "17:00")]
    assert breakwater_risk(client, 16) == "danger" and breakwater_risk(client, 17, 30) == "safe"
    # until 은 KST 로 본다 (시간대가 없으면)
    st = ok(client.put("/safety/sim", json={"scenario": "wind_tomorrow", "until": "2026-10-11T15:00:00"}))
    assert [(x["start"][8:16], x["end"]) for x in st["timeline"]] == [("11T12:00", "2026-10-11T15:00:00+09:00")]


@pytest.mark.parametrize("body,code", [
    ({"timeline": [{"kind": "tsunami", "start": "2026-10-10T15:00:00+09:00", "end": "2026-10-10T16:00:00+09:00"}]},
     "unknown_kind"),
    ({"timeline": [{"kind": "storm", "start": "2026-10-10T16:00:00+09:00", "end": "2026-10-10T16:00:00+09:00"}]},
     "invalid_timeline"),
    ({"timeline": [{"kind": "rain", "start": f"2026-10-10T{h:02d}:00:00+09:00", "end": f"2026-10-10T{h:02d}:30:00+09:00"}
                   for h in range(21)]}, "invalid_timeline"),
    ({"until": "2026-10-10T17:00:00+09:00"}, "invalid_request"),
    ({"scenario": "storm", "timeline": []}, "invalid_request"),
    ({"scenario": "rain", "until": "2026-10-10T16:00:00+09:00"}, "invalid_until"),  # 비는 2시간 뒤 시작
])
def test_bad_timeline_requests(use, body, code):
    src = MockSources("storm", clock=Clock())
    before = src.timeline
    r = use(src).put("/safety/sim", json=body)
    assert r.status_code == 422 and r.json()["detail"]["code"] == code
    assert src.timeline == before  # 바뀌지 않는다


def test_twenty_segments_and_empty_timeline_are_ok(use):
    client = use(MockSources("storm", clock=Clock()))
    many = [seg("rain", at(h), at(h, 30)) for h in range(20)]
    assert len(ok(client.put("/safety/sim", json={"timeline": many}))["timeline"]) == 20
    st = ok(client.put("/safety/sim", json={"timeline": []}))
    assert st["timeline"] == [] and st["scenario"] == "calm"


def test_live_mode_ignores_timeline_but_remembers_it(use):
    live = MockSources("calm", clock=Clock())  # 실제 데이터 자리 (평상시)
    src = SimSources(live, MockSources(clock=Clock()), mode="live")
    client = use(src)
    st = ok(client.put("/safety/sim", json={"timeline": [seg("storm", at(15), at(17))]}))
    assert st["mode"] == "live" and st["scenario"] == "storm" and len(st["timeline"]) == 1  # 모드는 그대로
    s = ok(client.get("/safety/status"))
    assert s["mode"] == "live" and s["scenario"] is None and s["segment"] is None and s["warnings"] == []
    assert breakwater_risk(client, 16) == "safe"
    st = ok(client.put("/safety/sim", json={"source": "mock"}))  # 가상 상황으로 바꾸면 기억한 시간표를 쓴다
    assert st["mode"] == "sim" and len(st["timeline"]) == 1
    assert ok(client.get("/safety/status"))["scenario"] == "storm" and breakwater_risk(client, 16) == "danger"


# ---------- 시간표 정리 (저장 전 normalize_timeline) ----------

def spans(timeline) -> list[tuple[str, str, str]]:
    """(종류, 시작 HH:MM, 끝 HH:MM) 목록. SimSegment 또는 응답 dict 둘 다."""
    def f(x, k):
        v = x[k] if isinstance(x, dict) else getattr(x, k)
        return v if isinstance(v, str) else v.isoformat()
    return [(f(x, "kind"), f(x, "start")[11:16], f(x, "end")[11:16]) for x in timeline]


def S(kind, a, b):
    return SimSegment(kind, at(*a) if isinstance(a, tuple) else at(a), at(*b) if isinstance(b, tuple) else at(b))


def test_partial_overlap_stronger_kind_takes_the_overlap():
    assert spans(normalize_timeline([S("rain", 13, 16), S("wind", 15, 18)])) == [
        ("rain", "13:00", "15:00"), ("wind", "15:00", "18:00")]
    # 약한 쪽이 뒤에 와도 센 쪽이 차지한다
    assert spans(normalize_timeline([S("storm", 13, 16), S("flood", 15, 18)])) == [
        ("storm", "13:00", "16:00"), ("flood", "16:00", "18:00")]


def test_full_containment_splits_the_weaker_segment_in_two():
    assert spans(normalize_timeline([S("rain", 13, 17), S("storm", 14, 15)])) == [
        ("rain", "13:00", "14:00"), ("storm", "14:00", "15:00"), ("rain", "15:00", "17:00")]
    # 약한 구간이 센 구간 안에 다 들어가면 없어진다
    assert spans(normalize_timeline([S("storm", 13, 17), S("rain", 14, 15)])) == [("storm", "13:00", "17:00")]


def test_same_kind_overlapping_or_touching_are_merged():
    assert spans(normalize_timeline([S("rain", 13, 15), S("rain", 14, 17)])) == [("rain", "13:00", "17:00")]
    assert spans(normalize_timeline([S("rain", 15, 17), S("rain", 13, 15)])) == [("rain", "13:00", "17:00")]
    # 다른 종류가 맞닿은 것은 그대로 둘
    assert spans(normalize_timeline([S("rain", 13, 15), S("wind", 15, 17)])) == [
        ("rain", "13:00", "15:00"), ("wind", "15:00", "17:00")]


def test_chain_of_many_segments():
    tl = [S("rain", 10, 20), S("wind", 12, 14), S("flood", 11, 15), S("storm", 13, 14), S("rain", 20, 21),
          S("wind", (16, 30), 17), S("wind", 17, 18)]
    assert spans(normalize_timeline(tl)) == [
        ("rain", "10:00", "11:00"), ("flood", "11:00", "13:00"), ("storm", "13:00", "14:00"),
        ("flood", "14:00", "15:00"), ("rain", "15:00", "16:30"), ("wind", "16:30", "18:00"), ("rain", "18:00", "21:00")]


def test_normalize_is_idempotent_and_drops_zero_length():
    out = normalize_timeline([S("rain", 13, 17), S("storm", 14, 15), S("storm", 15, 16)])
    assert spans(out) == [("rain", "13:00", "14:00"), ("storm", "14:00", "16:00"), ("rain", "16:00", "17:00")]
    assert normalize_timeline(list(out)) == out
    assert all(x.start < x.end for x in out)
    assert normalize_timeline([]) == ()


@pytest.mark.parametrize("tl", [
    [S("rain", 13, 17), S("storm", 14, 15)],
    [S("rain", 10, 20), S("wind", 12, 14), S("flood", 11, 15), S("storm", 13, 14)],
    [S("wind", (9, 5), (11, 40)), S("rain", (10, 15), (13, 20)), S("flood", (11, 35), (12, 10)), S("rain", (13, 20), 14),
     S("wind", (13, 55), (15, 5)), S("flood", (14, 50), (14, 55))],
])
def test_weather_by_time_is_unchanged_by_normalize(tl):
    """정리 전후로 5분마다의 날씨 종류·지금 관측값(특보·센서)·시간별 예보가 같다 (이미 센 것 우선이므로)."""
    clock = Clock(at(8))
    raw, norm = MockSources(clock=clock), MockSources(clock=clock)
    raw.timeline, norm.timeline = tuple(tl), normalize_timeline(tl)
    t = at(8)
    while t < at(22):
        clock.t = t
        assert raw.scenario == norm.scenario, t
        assert [r.level for r in raw.sensors().value] == [r.level for r in norm.sensors().value]
        t += timedelta(minutes=5)
    rh, nh = raw.forecast(at(8)).value.hours, norm.forecast(at(8)).value.hours
    assert all((rh[k].wsd, rh[k].pcp_mm, rh[k].wav) == (nh[k].wsd, nh[k].pcp_mm, nh[k].wav) for k in rh)


def test_put_timeline_is_saved_normalized_and_get_returns_it(use):
    src = MockSources(clock=Clock(at(12)))
    client = use(src)
    # 대시보드 확인 장면: 비 13~17 위에 폭풍 14~15 -> 세 칸
    st = ok(client.put("/safety/sim", json={"timeline": [seg("rain", at(13), at(17)), seg("storm", at(14), at(15))]}))
    assert spans(st["timeline"]) == [("rain", "13:00", "14:00"), ("storm", "14:00", "15:00"), ("rain", "15:00", "17:00")]
    assert spans(ok(client.get("/safety/sim"))["timeline"]) == spans(st["timeline"])
    # 폭풍을 지우면 덮였던 비 14~15 는 돌아오지 않는다
    rest = [x for x in st["timeline"] if x["kind"] != "storm"]
    st = ok(client.put("/safety/sim", json={"timeline": rest}))
    assert spans(st["timeline"]) == [("rain", "13:00", "14:00"), ("rain", "15:00", "17:00")]
    # 15~17 을 왼쪽으로 늘려 13~14 와 맞닿게 하면 하나로
    st = ok(client.put("/safety/sim", json={"timeline": [seg("rain", at(13), at(14)), seg("rain", at(14), at(17))]}))
    assert spans(st["timeline"]) == [("rain", "13:00", "17:00")]


def test_limit_is_checked_after_normalize(use):
    client = use(MockSources(clock=Clock()))
    # 30개지만 서로 맞닿은 같은 종류라 정리하면 1개 -> 받아들인다
    touching = [seg("rain", at(10) + i * timedelta(minutes=10), at(10) + (i + 1) * timedelta(minutes=10))
                for i in range(30)]
    st = ok(client.put("/safety/sim", json={"timeline": touching}))
    assert spans(st["timeline"]) == [("rain", "10:00", "15:00")]
    # 정리해도 21개면 거절
    apart = [seg("rain", at(h), at(h, 30)) for h in range(21)]
    r = client.put("/safety/sim", json={"timeline": apart})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_timeline"


def test_preset_with_until_is_normalized_too(use):
    st = ok(use(MockSources(clock=Clock())).put("/safety/sim", json={"scenario": "storm", "until": "2026-10-10T17:00:00+09:00"}))
    assert spans(st["timeline"]) == [("storm", "15:20", "17:00")]
