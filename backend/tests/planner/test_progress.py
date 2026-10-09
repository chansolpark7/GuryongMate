"""여행 중 API (명세 §8): 진행 상태(progress)는 version 을 올리지 않는다, 현재 기준 재계산(projection)."""
from datetime import datetime

import pytest

from app.planner import clock
from app.planner.clock import KST, FixedClock
from .conftest import H, PLACES, add, err, get_plan, item_id, new_plan, ok


def progress(client, pid, iid, status, at=None):
    body = {"status": status} | ({"at": at} if at else {})
    return client.post(f"/planner/plans/{pid}/items/{iid}/progress", json=body, headers=H)


def project(client, pid, **body):
    return client.post(f"/planner/plans/{pid}/projection", json=body, headers=H)


def view(plan, iid) -> dict:
    return next(i for d in plan["days"] for i in d["items"] if i["id"] == iid)


def trip(client, **food) -> tuple[int, dict]:
    """12:30 시작: 가옥거리 -> 물회집 -> 과메기문화관 (약 300m 간격, 도보 6분)."""
    pid = new_plan(client)["id"]
    add(client, pid, "p-house")
    add(client, pid, "p-food", **food)
    plan = add(client, pid, "p-museum")
    return pid, {k: item_id(plan, k) for k in ("p-house", "p-food", "p-museum")}


@pytest.fixture
def fixed_now():
    saved = clock._clock
    clock._clock = FixedClock(datetime(2026, 10, 10, 14, 7, 30, tzinfo=KST))
    yield
    clock._clock = saved


# ---------- 진행 상태 ----------

def test_progress_does_not_bump_version(client):
    pid, ids = trip(client)
    before = get_plan(client, pid)
    assert before["progress_version"] == 0

    plan = ok(progress(client, pid, ids["p-house"], "visiting", "12:35"))
    assert plan["version"] == before["version"] and plan["progress_version"] == 1
    house = view(plan, ids["p-house"])
    assert (house["status"], house["arrived_at"], house["done_at"]) == ("visiting", "12:35", None)

    plan = ok(progress(client, pid, ids["p-house"], "done", "13:40"))
    house = view(plan, ids["p-house"])
    assert (house["status"], house["arrived_at"], house["done_at"]) == ("done", "12:35", "13:40")
    assert plan["version"] == before["version"] and plan["progress_version"] == 2
    # 일정 시각은 계획 기준 그대로 (재계산은 projection)
    assert house["start_at"] == "12:30"

    # 완료가 도착보다 이르면 거부
    ok(progress(client, pid, ids["p-food"], "visiting", "14:00"))
    err(progress(client, pid, ids["p-food"], "done", "13:00"), 422, "invalid_range")

    # 기록 취소
    plan = ok(progress(client, pid, ids["p-house"], "planned"))
    house = view(plan, ids["p-house"])
    assert (house["status"], house["arrived_at"], house["done_at"]) == ("planned", None, None)
    assert plan["version"] == before["version"]
    assert get_plan(client, pid)["progress_version"] == 4


def test_progress_default_time_is_planner_now(client, fixed_now):
    pid, ids = trip(client)
    plan = ok(progress(client, pid, ids["p-house"], "visiting"))
    assert view(plan, ids["p-house"])["arrived_at"] == "14:07"


def test_progress_errors(client):
    pid, ids = trip(client)
    err(progress(client, pid, 999999, "visiting"), 404, "item_not_found")
    err(client.post(f"/planner/plans/{pid}/items/{ids['p-house']}/progress", json={"status": "skipped"}, headers=H),
        422, "invalid_request")
    # 건너뜀은 일정 변경: PATCH 로 하고 version 이 오른다
    v = get_plan(client, pid)["version"]
    plan = ok(client.patch(f"/planner/plans/{pid}/items/{ids['p-food']}", json={"status": "skipped"}, headers=H))
    assert plan["version"] == v + 1
    err(progress(client, pid, ids["p-food"], "visiting"), 409, "item_skipped")


def test_patch_status_clears_progress_times(client):
    pid, ids = trip(client)
    ok(progress(client, pid, ids["p-house"], "visiting", "12:40"))
    plan = ok(client.patch(f"/planner/plans/{pid}/items/{ids['p-house']}", json={"status": "planned"}, headers=H))
    assert view(plan, ids["p-house"])["arrived_at"] is None


# ---------- 제안 승인과 진행 상태 ----------

def test_pending_proposal_auto_accepts_during_trip(client):
    pid, ids = trip(client)
    prop = ok(client.post(f"/planner/plans/{pid}/proposals", headers=H, json={
        "ops": [{"op": "update_item", "item_id": ids["p-museum"], "stay_minutes": 30}]}), 201)
    ok(progress(client, pid, ids["p-house"], "done", "13:30"))
    ok(progress(client, pid, ids["p-food"], "visiting", "13:40"))

    detail = ok(client.get(f"/planner/plans/{pid}/proposals/{prop['id']}", headers=H))
    assert detail["base_changed"] is False
    r = ok(client.post(f"/planner/plans/{pid}/proposals/{prop['id']}/accept", headers=H))
    assert r["proposal"]["decision"] == "applied"
    plan = r["plan"]
    assert view(plan, ids["p-museum"])["stay_minutes"] == 30
    # 진행 상태는 그대로
    assert (view(plan, ids["p-house"])["status"], view(plan, ids["p-house"])["done_at"]) == ("done", "13:30")
    assert (view(plan, ids["p-food"])["status"], view(plan, ids["p-food"])["arrived_at"]) == ("visiting", "13:40")
    assert plan["progress_version"] == 2


def test_overwrite_keeps_current_progress(client):
    pid, ids = trip(client)
    prop = ok(client.post(f"/planner/plans/{pid}/proposals", headers=H, json={
        "ops": [{"op": "update_item", "item_id": ids["p-museum"], "stay_minutes": 30}]}), 201)
    ok(client.patch(f"/planner/plans/{pid}/items/{ids['p-food']}", json={"memo": "맵게"}, headers=H))  # 일정 변경
    ok(progress(client, pid, ids["p-house"], "done", "13:30"))

    d = err(client.post(f"/planner/plans/{pid}/proposals/{prop['id']}/accept", headers=H), 409, "base_changed")
    # 진행 상태 변화는 일정 변경 요약에 나오지 않는다 (덮어써도 사라지지 않으므로)
    assert all("상태" not in c["message"] for c in d["since_proposal"] + d["overwrite_effect"])
    r = ok(client.post(f"/planner/plans/{pid}/proposals/{prop['id']}/accept", json={"mode": "overwrite"}, headers=H))
    plan = r["plan"]
    assert r["proposal"]["decision"] == "overwritten"
    assert view(plan, ids["p-museum"])["stay_minutes"] == 30 and view(plan, ids["p-food"])["memo"] == ""
    house = view(plan, ids["p-house"])
    assert (house["status"], house["done_at"]) == ("done", "13:30")


# ---------- 현재 기준 재계산 ----------

def test_projection_from_stored_progress_light_delay(client):
    pid, ids = trip(client)
    ok(progress(client, pid, ids["p-house"], "done", "12:30"))
    ok(progress(client, pid, ids["p-food"], "visiting", "13:50"))
    before = get_plan(client, pid)
    r = ok(project(client, pid, day=1, now="14:00"))
    assert (r["day"], r["now"]) == (1, "14:00")
    assert view(r["plan"], ids["p-food"])["end_at"] == "14:50"
    assert r["new_violations"] == [] and r["delay_minutes"] == 14
    assert r["next"] == {"item_id": ids["p-museum"], "planned_start": "14:42", "eta": "14:56", "start_at": "14:56"}
    after = get_plan(client, pid)  # 저장하지 않는다
    assert (after["version"], after["progress_version"]) == (before["version"], before["progress_version"])
    assert view(after, ids["p-food"])["end_at"] == "14:36"


def test_projection_day_overflow(client):
    pid, ids = trip(client)
    ok(progress(client, pid, ids["p-house"], "done", "12:30"))
    r = ok(project(client, pid, day=1, now="17:10",
                   position={"state": "visiting", "item_id": ids["p-food"], "arrived_at": "15:00"}))
    assert [(v["code"], v["item_id"]) for v in r["new_violations"]] == [("day_overflow", ids["p-museum"])]
    assert r["baseline_violations"] == [] and r["delay_minutes"] == 154


def test_projection_moving_misses_fixed_time(client):
    """호미곶 근처에서 이동 중: 물회집(14:00 고정)에 한참 늦는다."""
    pid, ids = trip(client, start_time="14:00")
    ok(progress(client, pid, ids["p-house"], "done", "13:30"))
    far = PLACES["p-far"]
    r = ok(project(client, pid, day=1, now="13:40", position={"state": "moving", "lat": far.lat, "lng": far.lng}))
    codes = {(v["code"], v["item_id"]) for v in r["new_violations"]}
    assert ("fixed_time_missed", ids["p-food"]) in codes and ("day_overflow", ids["p-museum"]) in codes
    assert r["next"]["item_id"] == ids["p-food"] and r["next"]["planned_start"] == "14:00"
    assert r["delay_minutes"] > 60


def test_projection_defaults_and_errors(client, fixed_now):
    pid, ids = trip(client)
    r = ok(client.post(f"/planner/plans/{pid}/projection", headers=H))  # 본문 생략: planner 의 오늘·지금
    assert (r["day"], r["now"]) == (1, "14:07")
    assert view(r["plan"], ids["p-house"])["start_at"] == "14:07"  # 아직 시작 안 함 -> 지금 출발

    other = new_plan(client, start_date="2026-12-20")["id"]
    err(client.post(f"/planner/plans/{other}/projection", headers=H), 422, "day_out_of_range")
    ok(client.patch(f"/planner/plans/{pid}/items/{ids['p-food']}", json={"status": "skipped"}, headers=H))
    err(project(client, pid, position={"state": "visiting", "item_id": ids["p-food"]}), 422, "invalid_position")
    err(project(client, pid, position={"state": "visiting", "item_id": 999999}), 404, "item_not_found")
    err(project(client, pid, position={"state": "flying"}), 422, "invalid_request")
