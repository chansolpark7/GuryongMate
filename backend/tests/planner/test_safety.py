"""일정 안전 점검·주기 점검·경고. safety 서비스는 같은 앱(ASGI)으로 부르고, 데이터는 MockSources(시각 고정)를 쓴다.

    docker compose run --rm planner pytest tests/planner/test_safety.py
"""
from datetime import date, datetime, timedelta

import pytest

from app.core.config import settings as core_settings
from app.main import app
from app.planner import safety
from app.planner.safety_client import SafetyUnavailable, get_safety_client
from app.planner.safety_watch import SafetyWatcher, get_watcher
from app.planner.schemas import PlaceInfo, PlanDetail, SafetyCheck, SafetyItem
from app.safety.places import PlacePoint, get_place_lookup as safety_places
from app.safety.router import get_sources
from app.safety.sources import KST, Fetched, MockSources
from .conftest import H, PLACES, USER, err, fake, get_plan, item_id, new_plan, ok

NOW = datetime(2026, 10, 4, 10, 0, tzinfo=KST)  # 여행 전날 오전 (내일 일정은 단기예보 범위)
SPOTS = {  # safety 테스트와 같은 좌표: 방파제 선 위, 내륙 거리, 내륙 실내, 침수 센서 옆
    "s-breakwater": ("구룡포 북방파제", 35.9871, 129.5583, "관광지"),
    "s-street": ("일본인가옥거리", 35.9820, 129.5420, "관광지"),
    "s-museum": ("근대문화역사관", 35.9821, 129.5421, "문화시설"),
    "s-police": ("파출소 앞", 35.9887, 129.5531, "관광지"),
}
PLACES.update({k: PlaceInfo(id=k, title=t, lat=a, lng=b, content_type=c) for k, (t, a, b, c) in SPOTS.items()})


class SpotLookup:
    def get(self, place_id):
        s = SPOTS.get(place_id)
        return PlacePoint(place_id, s[0], s[1], s[2], s[3]) if s else None


class AsgiSafety:
    """planner 의 SafetyClient 를 같은 앱의 safety API 로 연결한다 (실제 HTTP 대신)."""

    def __init__(self, client):
        self.http = client
        self.down = False
        self.on_assess = None  # 점검 도중에 끼어들 동작 (테스트용)

    def assess(self, visits):
        if self.down:
            raise SafetyUnavailable("down")
        if self.on_assess:
            self.on_assess()
        return ok(self.http.post("/safety/assess", json={"visits": visits}))

    def situation(self):
        if self.down:
            raise SafetyUnavailable("down")
        return ok(self.http.get("/safety/situation"))


class ForecastDown(MockSources):
    def forecast(self, now):
        return Fetched(None, NOW, "SourceError: down")


@pytest.fixture
def src():
    return MockSources("calm", clock=lambda: NOW)


@pytest.fixture
def sc(client, src):
    """safety 연결. sc.src 로 시나리오를 바꾼다."""
    s = AsgiSafety(client)
    s.src = src
    app.dependency_overrides[get_sources] = lambda: s.src
    app.dependency_overrides[safety_places] = lambda: SpotLookup()
    app.dependency_overrides[get_safety_client] = lambda: s
    yield s
    for dep in (get_sources, safety_places, get_safety_client):
        app.dependency_overrides.pop(dep, None)


def trip(client, start="2026-10-05", **kw) -> dict:
    """내일 13시 시작: 방파제(14:00 고정) -> 역사관 -> 가옥거리."""
    p = new_plan(client, start_date=start, day_start="13:00", day_end="20:00", **kw)
    for pid, extra in (("s-breakwater", {"start_time": "14:00"}), ("s-museum", {}), ("s-street", {})):
        ok(client.post(f"/planner/plans/{p['id']}/items", json={"place_id": pid} | extra, headers=H), 201)
    return get_plan(client, p["id"])


def check(client, pid) -> dict:
    return ok(client.get(f"/planner/plans/{pid}/safety", headers=H))


def by_place(result) -> dict:
    return {i["place_id"]: i for i in result["items"]}


# ---------- 일정 점검 ----------

def test_calm_plan_is_safe(client, sc):
    p = trip(client)
    r = check(client, p["id"])
    assert r["plan_id"] == p["id"] and r["plan_version"] == p["version"]
    assert r["overall_risk"] == "safe" and r["checked_at"].startswith("2026-10-04T10:00")
    assert [i["place_id"] for i in r["items"]] == ["s-breakwater", "s-museum", "s-street"]
    assert all(i["risk"] == "safe" and i["unassessed_reason"] is None for i in r["items"])
    assert r["counts"] == {"safe": 3, "caution": 0, "danger": 0, "unassessed": 0, "info": 0}
    bw = by_place(r)["s-breakwater"]
    assert (bw["start_at"], bw["end_at"], bw["day"], bw["date"]) == ("14:00", "15:00", 1, "2026-10-05")
    assert "방파제" in bw["sensitivity"] and "바뀔 수 있으니" in r["guidance"]


def test_wind_tomorrow_three_levels_by_place(client, sc):
    sc.src.scenario = "wind_tomorrow"  # 내일 12~18시 강풍·높은 파도
    p = trip(client)
    r = check(client, p["id"])
    items = by_place(r)
    bw, museum, street = items["s-breakwater"], items["s-museum"], items["s-street"]
    assert bw["risk"] == "danger" and {"wind", "wave"} <= {h["type"] for h in bw["hazards"]}
    assert bw["better_times"] and "피하는 것이 좋다" in bw["advice"]
    assert street["risk"] == "caution"  # 그 밖의 야외는 한 단계 낮게
    # 실내는 안전 단계지만 바람 소식은 참고(info)로 남는다
    assert museum["risk"] == "safe" and museum["hazards"] and {h["level"] for h in museum["hazards"]} == {"info"}
    assert r["overall_risk"] == "danger"
    assert r["counts"] == {"safe": 1, "caution": 1, "danger": 1, "unassessed": 0, "info": 1}
    assert "주의·위험 2곳" in r["guidance"] and "참고 신호" in r["guidance"]


def test_light_rain_is_info_not_a_level(client, sc):
    sc.src.scenario = "rain"
    p = new_plan(client, start_date="2026-10-04", day_start="13:00", day_end="18:00")
    ok(client.post(f"/planner/plans/{p['id']}/items", json={"place_id": "s-street"}, headers=H), 201)
    r = check(client, p["id"])
    it = r["items"][0]
    assert it["risk"] == "safe" and [h["level"] for h in it["hazards"]] == ["info"] and "mm/h" in it["hazards"][0]["reason"]
    assert r["overall_risk"] == "safe" and r["counts"]["info"] == 1


def test_far_future_is_not_assessed(client, sc):
    sc.src.scenario = "storm"  # 지금 폭풍이어도 먼 일정은 판단하지 않는다
    p = trip(client, start="2027-01-15")
    r = check(client, p["id"])
    assert all(i["risk"] is None and i["unassessed_reason"] == "beyond_forecast" for i in r["items"])
    assert r["overall_risk"] is None and r["counts"]["unassessed"] == 3
    assert "바꾸지 마라" in r["guidance"] and "2027-01-12" in r["guidance"]


def test_data_failure_is_not_safe(client, sc):
    sc.src = ForecastDown("calm", clock=lambda: NOW)
    r = check(client, trip(client)["id"])
    assert all(i["risk"] is None and i["unassessed_reason"] == "data_missing" for i in r["items"])
    assert r["overall_risk"] is None and r["data_ok"]["forecast"] is False and "안전하다고 말하지 마라" in r["guidance"]


def test_skipped_done_and_empty(client, sc):
    p = trip(client)
    bw = item_id(p, "s-breakwater")
    ok(client.patch(f"/planner/plans/{p['id']}/items/{bw}", json={"status": "skipped"}, headers=H))
    ok(client.patch(f"/planner/plans/{p['id']}/items/{item_id(p, 's-museum')}", json={"status": "done"}, headers=H))
    assert [i["place_id"] for i in check(client, p["id"])["items"]] == ["s-street"]
    empty = check(client, new_plan(client, start_date="2026-10-05")["id"])
    assert empty["items"] == [] and empty["overall_risk"] == "safe" and "점검할 앞으로의 일정이 없다" in empty["guidance"]


def test_preview_checks_draft(client, sc):
    sc.src.scenario = "wind_tomorrow"
    p = new_plan(client, start_date="2026-10-05", day_start="13:00", day_end="20:00")
    body = {"ops": [{"op": "add_item", "place_id": "s-breakwater", "start_time": "15:00"},
                    {"op": "add_item", "place_id": "s-museum"}]}
    r = ok(client.post(f"/planner/plans/{p['id']}/safety/preview", json=body, headers=H))
    assert [(i["item_id"] < 0, i["place_id"], i["risk"]) for i in r["items"]] == [
        (True, "s-breakwater", "danger"), (True, "s-museum", "safe")]
    assert get_plan(client, p["id"])["days"][0]["items"] == []  # 저장 안 함
    # 바꿀 수 없는 ops 는 preview 와 같은 오류
    err(client.post(f"/planner/plans/{p['id']}/safety/preview", json={"ops": [{"op": "remove_item", "item_id": 999999}]},
                    headers=H), 404, "item_not_found")


def test_safety_errors(client, sc):
    p = trip(client)
    sc.down = True
    d = err(client.get(f"/planner/plans/{p['id']}/safety", headers=H), 503, "safety_unavailable")
    assert d["reason"]
    sc.down = False
    err(client.get("/planner/plans/999999/safety", headers=H), 404, "plan_not_found")
    err(client.get(f"/planner/plans/{p['id']}/safety", headers={"X-User-Id": "test-planner-other"}), 404,
        "plan_not_found")
    err(client.post(f"/planner/plans/{p['id']}/safety/preview", json={"ops": "x"}, headers=H), 422, "invalid_request")


# ---------- 순수 함수 ----------

def test_visits_handles_midnight_and_window(client, sc):
    p = new_plan(client, start_date="2026-10-05", end_date="2026-10-06", day_start="22:00", day_end="23:59")
    ok(client.post(f"/planner/plans/{p['id']}/items", json={"place_id": "s-street", "stay_minutes": 90}, headers=H), 201)
    ok(client.post(f"/planner/plans/{p['id']}/items", json={"place_id": "s-street"}, headers=H), 201)  # 23:30 시작
    ok(client.post(f"/planner/plans/{p['id']}/items", json={"place_id": "s-street", "day": 2}, headers=H), 201)
    detail = PlanDetail.model_validate(get_plan(client, p["id"]))
    vs = safety.visits(detail)
    assert [(v["place_id"], v["date"], v["start"], v.get("end")) for v in vs] == [
        ("s-street", "2026-10-05", "22:00", "23:30"), ("s-street", "2026-10-05", "23:30", None),  # 종료가 자정 넘음
        ("s-street", "2026-10-06", "22:00", "23:00")]
    assert [v["date"] for v in safety.visits(detail, date(2026, 10, 6), date(2026, 10, 6))] == ["2026-10-06"]


def _item(i, risk, reason=None, start="14:00"):
    return SafetyItem(item_id=i, day=1, date=date(2026, 10, 5), place_id="p", title=f"장소{i}", start_at=start,
                      end_at="15:00", risk=risk, unassessed_reason=reason, horizon="short_range", hazards=[],
                      advice="", better_times=[], sensitivity=[], notes=[])


def _result(*items):
    return SafetyCheck(plan_id=1, plan_version=1, checked_at=NOW, overall_risk=None, counts={}, guidance="",
                       items=list(items), data_ok={})


def test_diff_rules():
    k = lambda i, start="14:00": f"{i}@2026-10-05 {start}"  # noqa: E731
    changes, state = safety.diff({}, _result(_item(1, "danger"), _item(2, "safe"), _item(3, None, "beyond_forecast")))
    assert [(c.kind, c.from_risk, c.to_risk, c.item.item_id) for c in changes] == [("raised", "safe", "danger", 1)]
    assert state == {k(1): "danger", k(2): "safe"}  # 판단하지 않은 처음 항목은 상태에 넣지 않는다
    prev = {k(1): "danger", k(2): "caution", k(4): "danger"}
    changes, state = safety.diff(prev, _result(_item(1, None, "data_missing"), _item(2, "safe"), _item(5, "caution")))
    assert [(c.kind, c.item.item_id) for c in changes] == [("cleared", 2), ("raised", 5)]
    assert state == {k(1): "danger", k(2): "safe", k(5): "caution"}  # 장애 중엔 지난 단계 유지, 없어진 4 는 뺀다
    changes, _ = safety.diff({k(1): "danger"}, _result(_item(1, "caution")))
    assert [(c.kind, c.to_risk) for c in changes] == [("lowered", "caution")]
    assert safety.alert_level(changes) == "caution"
    # 시각이 바뀌면 다른 방문으로 본다
    changes, _ = safety.diff({k(1): "danger"}, _result(_item(1, "danger", start="16:00")))
    assert [c.kind for c in changes] == ["raised"]


# ---------- 주기 점검 + 경고 ----------

@pytest.fixture
def watcher(sc):
    return SafetyWatcher(sc, fake, recheck=timedelta(hours=1), users=[USER])


def alerts(client, pid, **params) -> list[dict]:
    return ok(client.get(f"/planner/plans/{pid}/safety/alerts", params=params, headers=H))


def test_watch_alerts_only_when_level_changes(client, sc, watcher):
    p = trip(client)
    far = trip(client, start="2027-01-15")  # 예보 범위 밖 계획은 점검하지 않는다
    first = watcher.tick()
    assert first.ran and first.reason == "첫 점검" and first.alerts == [] and first.plans >= 1
    again = watcher.tick()
    assert not again.ran and again.reason == "상황이 같고 재점검 주기 전"

    sc.src.scenario = "wind_tomorrow"
    r = watcher.tick()
    assert r.ran and r.reason == "안전 상황이 바뀜"
    mine = [a for a in r.alerts if a.plan_id == p["id"]]
    assert len(mine) == 1 and not any(a.plan_id == far["id"] for a in r.alerts)
    a = mine[0]
    assert a.level == "danger" and a.needs_action and a.status == "new" and a.plan_version == p["version"]
    assert [(c.kind, c.from_risk, c.to_risk, c.item.place_id) for c in a.changes] == [
        ("raised", "safe", "danger", "s-breakwater"), ("raised", "safe", "caution", "s-street")]
    assert a.message.startswith("일정 2곳의 위험 단계가 올라갔습니다.") and "안전 → 위험" in a.message

    # 상황이 같으면 다시 경고하지 않는다 (강제로 돌려도 바뀐 게 없으면 경고 없음)
    assert not watcher.tick().ran
    forced = watcher.tick(force=True)
    assert forced.ran and forced.reason == "요청에 의한 점검" and not [x for x in forced.alerts if x.plan_id == p["id"]]

    # 데이터 장애는 '해제'가 아니다
    sc.src = ForecastDown("wind_tomorrow", clock=lambda: NOW)
    assert not [x for x in watcher.tick().alerts if x.plan_id == p["id"]]
    sc.src = MockSources("wind_tomorrow", clock=lambda: NOW)
    assert not [x for x in watcher.tick().alerts if x.plan_id == p["id"]]

    # 잠잠해지면 해제 경고 (조치 필요 없음)
    sc.src.scenario = "calm"
    cleared = [x for x in watcher.tick().alerts if x.plan_id == p["id"]]
    assert len(cleared) == 1 and cleared[0].level == "safe" and not cleared[0].needs_action
    assert {c.kind for c in cleared[0].changes} == {"cleared"} and "낮아졌습니다" in cleared[0].message

    listed = alerts(client, p["id"])
    assert [x["id"] for x in listed] == [cleared[0].id, a.id]  # 최신순
    assert alerts(client, far["id"]) == []


def test_watch_rechecks_when_plan_changes(client, sc, watcher):
    sc.src.scenario = "wind_tomorrow"
    p = trip(client)
    watcher.tick()
    assert len(alerts(client, p["id"])) == 1
    # 위험한 시간대(14시)에서 안전한 오전으로 옮기면 상황이 같아도 재점검 주기에 반영된다
    p2 = new_plan(client, start_date="2026-10-05", day_start="13:00", day_end="20:00")
    ok(client.post(f"/planner/plans/{p2['id']}/items", json={"place_id": "s-breakwater", "start_time": "16:00"},
                   headers=H), 201)
    watcher.recheck = timedelta(0)
    r = watcher.tick()
    assert r.ran and r.reason == "재점검 주기"
    assert [c.to_risk for x in r.alerts if x.plan_id == p2["id"] for c in x.changes] == ["danger"]


def test_watch_skips_plan_changed_during_check(client, sc, watcher):
    sc.src.scenario = "wind_tomorrow"
    hdr = {"X-User-Id": "test-planner-race"}
    p = ok(client.post("/planner/plans", json={"title": "t", "start_date": "2026-10-05", "day_start": "13:00"},
                       headers=hdr), 201)
    ok(client.post(f"/planner/plans/{p['id']}/items", json={"place_id": "s-breakwater", "start_time": "14:00"},
                   headers=hdr), 201)

    def edit():  # safety 를 부르는 사이 사용자가 일정을 바꿈
        sc.on_assess = None
        ok(client.patch(f"/planner/plans/{p['id']}", json={"memo": "수정"}, headers=hdr))

    sc.on_assess = edit
    watcher.users = ["test-planner-race"]
    r = watcher.tick(force=True)
    assert not [x for x in r.alerts if x.plan_id == p["id"]]  # 이번엔 건너뛰고
    assert [x for x in watcher.tick(force=True).alerts if x.plan_id == p["id"]]  # 다음 점검에서 본다


def test_alert_endpoints(client, sc, watcher):
    sc.src.scenario = "wind_tomorrow"
    p = trip(client)
    watcher.tick()
    [a] = alerts(client, p["id"], status="new")
    assert a["changes"][0]["item"]["hazards"] and a["changes"][0]["item"]["better_times"]
    seen = ok(client.post(f"/planner/plans/{p['id']}/safety/alerts/{a['id']}/seen", headers=H))
    assert seen["status"] == "seen" and alerts(client, p["id"], status="new") == []
    assert [x["id"] for x in alerts(client, p["id"], status="seen")] == [a["id"]]
    err(client.get(f"/planner/plans/{p['id']}/safety/alerts", params={"status": "old"}, headers=H), 422,
        "invalid_request")
    other = new_plan(client)
    err(client.post(f"/planner/plans/{other['id']}/safety/alerts/{a['id']}/seen", headers=H), 404, "alert_not_found")
    err(client.get(f"/planner/plans/{p['id']}/safety/alerts", headers={"X-User-Id": "test-planner-other"}), 404,
        "plan_not_found")


def test_scan_endpoint(client, sc, watcher, monkeypatch):
    app.dependency_overrides[get_watcher] = lambda: watcher
    try:
        sc.src.scenario = "wind_tomorrow"
        p = trip(client)
        r = ok(client.post("/planner/safety/scan"))
        assert r["ran"] and r["situation"] and any(a["plan_id"] == p["id"] for a in r["alerts"])
        assert ok(client.post("/planner/safety/scan"))["ran"] is False
        assert ok(client.post("/planner/safety/scan", params={"force": True}))["ran"] is True
        sc.down = True
        err(client.post("/planner/safety/scan"), 503, "safety_unavailable")
        sc.down = False
        monkeypatch.setattr(core_settings, "auth_required", True)
        err(client.post("/planner/safety/scan"), 404, "not_available")
    finally:
        app.dependency_overrides.pop(get_watcher, None)


def test_delete_me_removes_alerts(client, sc, watcher):
    sc.src.scenario = "wind_tomorrow"
    user = "test-planner-leaver"
    hdr = {"X-User-Id": user}
    p = ok(client.post("/planner/plans", json={"title": "t", "start_date": "2026-10-05", "day_start": "13:00"},
                       headers=hdr), 201)
    ok(client.post(f"/planner/plans/{p['id']}/items", json={"place_id": "s-breakwater", "start_time": "14:00"},
                   headers=hdr), 201)
    watcher.users = [user]
    assert watcher.tick().alerts
    assert client.delete("/planner/me", headers=hdr).status_code == 204
    from app.db import SessionLocal
    from app.planner.models import PlannerSafetyAlert, PlannerSafetyState
    with SessionLocal() as db:
        assert db.query(PlannerSafetyAlert).filter_by(plan_id=p["id"]).count() == 0
        assert db.query(PlannerSafetyState).filter_by(plan_id=p["id"]).count() == 0
