"""장소 추천 (명세 §6): 후보 상태는 지금 일정(같은 날·같은 장소)에서 계산, [추가] 위치, [빼기] 대상."""
from datetime import datetime

import pytest

from app.planner import clock
from app.planner.clock import KST, FixedClock
from .conftest import H, add, err, get_plan, item_id, new_plan, ok, order


def recommend(client, pid, candidates, title="추천") -> dict:
    return ok(client.post(f"/planner/plans/{pid}/recommendations",
                          json={"title": title, "candidates": candidates}, headers=H), 201)


def act(client, pid, rec, cand_id, action):
    return client.post(f"/planner/plans/{pid}/recommendations/{rec['id']}/candidates/{cand_id}/{action}", headers=H)


def cand(rec: dict, place_id: str, day: int = 1) -> dict:
    return next(c for c in rec["candidates"] if c["place_id"] == place_id and c["day"] == day)


def test_create_and_initial_state(client):
    pid = new_plan(client, end_date="2026-10-11")["id"]
    add(client, pid, "p-house")
    rec = recommend(client, pid, [
        {"place_id": "p-house", "day": 1, "reason": "이미 있음"},
        {"place_id": "p-cafe", "day": 1, "time_hint": "14:00", "stay_minutes": 30, "reason": "쉬어가기"},
        {"place_id": "p-house", "day": 2},
    ], title="카페 추천")
    assert rec["title"] == "카페 추천" and rec["source"] == "llm" and len(rec["candidates"]) == 3
    house1, cafe, house2 = rec["candidates"]
    assert house1["state"] == "in_plan" and house1["item"]["item_id"] == item_id(get_plan(client, pid), "p-house")
    assert cafe["state"] == "not_in_plan" and cafe["item"] is None and cafe["time_hint"] == "14:00"
    assert cafe["place"]["title"] == "카페" and cafe["reason"] == "쉬어가기"
    assert house2["state"] == "not_in_plan" and house2["other_days"] == [1]   # 다른 날에 있어도 상태는 별개
    # 추천을 만들어도 일정·version 은 그대로
    assert get_plan(client, pid)["version"] == 2


def test_create_validation(client):
    pid = new_plan(client)["id"]
    url = f"/planner/plans/{pid}/recommendations"
    err(client.post(url, json={"title": "t", "candidates": [{"place_id": "nope", "day": 1}]}, headers=H),
        422, "place_not_found")
    err(client.post(url, json={"title": "t", "candidates": [{"place_id": "p-cafe", "day": 2}]}, headers=H),
        422, "day_out_of_range")
    err(client.post(url, json={"title": "t", "candidates": [{"place_id": "p-cafe", "day": 1},
                                                           {"place_id": "p-cafe", "day": 1}]}, headers=H),
        422, "duplicate_candidate")
    assert ok(client.get(url, headers=H)) == []


def test_add_and_remove(client):
    pid = new_plan(client)["id"]
    add(client, pid, "p-house")
    add(client, pid, "p-museum")
    rec = recommend(client, pid, [{"place_id": "p-food", "day": 1, "stay_minutes": 45, "reason": "점심"}])
    cid = rec["candidates"][0]["id"]

    r = ok(act(client, pid, rec, cid, "add"))
    c = r["recommendation"]["candidates"][0]
    assert c["state"] == "in_plan" and c["item"]["item_id"] == item_id(r["plan"], "p-food")
    added = next(i for i in r["plan"]["days"][0]["items"] if i["place_id"] == "p-food")
    assert added["stay_minutes"] == 45 and added["memo"] == "점심"
    assert r["plan"]["version"] == 4
    d = err(act(client, pid, rec, cid, "add"), 409, "already_in_plan")
    assert d["item_id"] == added["id"]

    r = ok(act(client, pid, rec, cid, "remove"))
    assert r["recommendation"]["candidates"][0]["state"] == "not_in_plan"
    assert order(r["plan"]) == ["p-house", "p-museum"] and r["plan"]["version"] == 5
    err(act(client, pid, rec, cid, "remove"), 409, "not_in_plan")


def test_state_follows_direct_edits(client):
    """버튼 대신 직접 추가·삭제해도 카드 상태는 지금 일정을 따른다."""
    pid = new_plan(client)["id"]
    rec = recommend(client, pid, [{"place_id": "p-cafe", "day": 1}])
    cid = rec["candidates"][0]["id"]
    plan = add(client, pid, "p-cafe")                       # 사용자가 직접 추가
    view = ok(client.get(f"/planner/plans/{pid}/recommendations/{rec['id']}", headers=H))
    assert view["candidates"][0]["state"] == "in_plan"
    err(act(client, pid, rec, cid, "add"), 409, "already_in_plan")
    ok(client.delete(f"/planner/plans/{pid}/items/{item_id(plan, 'p-cafe')}", headers=H))   # 직접 삭제
    view = ok(client.get(f"/planner/plans/{pid}/recommendations/{rec['id']}", headers=H))
    assert view["candidates"][0]["state"] == "not_in_plan"


def test_time_drift_does_not_change_state(client):
    """앞 일정이 늘어 시각이 밀려도 같은 날이면 in_plan 그대로 (중복 추가 방지)."""
    pid = new_plan(client)["id"]
    plan = add(client, pid, "p-house", stay_minutes=30)
    rec = recommend(client, pid, [{"place_id": "p-cafe", "day": 1, "time_hint": "13:00"}])
    ok(act(client, pid, rec, rec["candidates"][0]["id"], "add"))
    ok(client.patch(f"/planner/plans/{pid}/items/{item_id(plan, 'p-house')}", json={"stay_minutes": 240}, headers=H))
    view = ok(client.get(f"/planner/plans/{pid}/recommendations/{rec['id']}", headers=H))
    assert view["candidates"][0]["state"] == "in_plan"


def test_same_place_twice_same_day_remove_nearest_to_hint(client):
    pid = new_plan(client, day_start="09:00", day_end="22:00")["id"]
    add(client, pid, "p-house", stay_minutes=60)        # 09:00~10:00
    add(client, pid, "p-food", stay_minutes=300)        # ~15:00 대
    plan = add(client, pid, "p-house", stay_minutes=60)  # 오후에 다시 가옥거리
    morning, evening = [i["id"] for i in plan["days"][0]["items"] if i["place_id"] == "p-house"]
    rec = recommend(client, pid, [{"place_id": "p-house", "day": 1, "time_hint": "16:00"}])
    assert rec["candidates"][0]["item"]["item_id"] == evening
    plan = ok(act(client, pid, rec, rec["candidates"][0]["id"], "remove"))["plan"]
    assert [i["id"] for i in plan["days"][0]["items"] if i["place_id"] == "p-house"] == [morning]
    # 힌트가 없으면 그날 가장 앞 항목
    rec2 = recommend(client, pid, [{"place_id": "p-house", "day": 1}])
    assert rec2["candidates"][0]["item"]["item_id"] == morning


def test_add_same_place_on_another_day(client):
    pid = new_plan(client, end_date="2026-10-11")["id"]
    add(client, pid, "p-house", day=1)
    rec = recommend(client, pid, [{"place_id": "p-house", "day": 2}])
    r = ok(act(client, pid, rec, rec["candidates"][0]["id"], "add"))
    assert order(r["plan"], 1) == ["p-house"] and order(r["plan"], 2) == ["p-house"]


def test_add_position_by_time_hint(client):
    pid = new_plan(client, day_start="10:00", day_end="20:00")["id"]
    add(client, pid, "p-house", stay_minutes=60)    # 10:00~11:00
    add(client, pid, "p-food", stay_minutes=60)     # ~11:05~12:05
    add(client, pid, "p-museum", stay_minutes=60)   # ~12:10~13:10
    rec = recommend(client, pid, [{"place_id": "p-cafe", "day": 1, "time_hint": "12:00", "stay_minutes": 30}])
    plan = ok(act(client, pid, rec, rec["candidates"][0]["id"], "add"))["plan"]
    assert order(plan) == ["p-house", "p-food", "p-cafe", "p-museum"]


def test_add_position_minimizes_travel_without_hint(client):
    pid = new_plan(client, day_start="10:00", day_end="22:00")["id"]
    add(client, pid, "p-house")    # 35.9880
    add(client, pid, "p-museum")   # 35.9934
    # p-food(35.9907) 는 둘 사이 -> 가운데에 넣어야 이동이 가장 적게 는다
    rec = recommend(client, pid, [{"place_id": "p-food", "day": 1}])
    plan = ok(act(client, pid, rec, rec["candidates"][0]["id"], "add"))["plan"]
    assert order(plan) == ["p-house", "p-food", "p-museum"]


def test_add_to_empty_day(client):
    pid = new_plan(client)["id"]
    rec = recommend(client, pid, [{"place_id": "p-cafe", "day": 1, "time_hint": "15:00"}])
    plan = ok(act(client, pid, rec, rec["candidates"][0]["id"], "add"))["plan"]
    assert order(plan) == ["p-cafe"]


def test_day_out_of_range_after_plan_shrinks(client):
    pid = new_plan(client, end_date="2026-10-11")["id"]
    rec = recommend(client, pid, [{"place_id": "p-cafe", "day": 2}])
    ok(client.patch(f"/planner/plans/{pid}", json={"end_date": "2026-10-10"}, headers=H))
    view = ok(client.get(f"/planner/plans/{pid}/recommendations/{rec['id']}", headers=H))
    assert view["candidates"][0]["state"] == "day_out_of_range" and view["candidates"][0]["item"] is None
    err(act(client, pid, rec, rec["candidates"][0]["id"], "add"), 422, "day_out_of_range")
    err(act(client, pid, rec, rec["candidates"][0]["id"], "remove"), 409, "not_in_plan")


def test_list_and_scope(client):
    pid = new_plan(client)["id"]
    other = new_plan(client)["id"]
    r1 = recommend(client, pid, [{"place_id": "p-cafe", "day": 1}])
    r2 = recommend(client, pid, [{"place_id": "p-food", "day": 1}])
    assert [r["id"] for r in ok(client.get(f"/planner/plans/{pid}/recommendations", headers=H))] == [r2["id"], r1["id"]]
    err(client.get(f"/planner/plans/{other}/recommendations/{r1['id']}", headers=H), 404, "recommendation_not_found")
    err(client.post(f"/planner/plans/{other}/recommendations/{r1['id']}/candidates/{r1['candidates'][0]['id']}/add",
                    headers=H), 404, "recommendation_not_found")
    err(act(client, pid, r1, r2["candidates"][0]["id"], "add"), 404, "candidate_not_found")  # 다른 카드의 후보
    err(client.get(f"/planner/plans/{pid}/recommendations/{r1['id']}", headers={"X-User-Id": "test-planner-x"}),
        404, "plan_not_found")


def test_old_card_still_works_after_plan_changes(client):
    pid = new_plan(client)["id"]
    rec = recommend(client, pid, [{"place_id": "p-cafe", "day": 1, "time_hint": "13:00"}])
    for place in ("p-house", "p-food", "p-museum"):
        add(client, pid, place)
    plan = ok(act(client, pid, rec, rec["candidates"][0]["id"], "add"))["plan"]
    assert "p-cafe" in order(plan)


# ---------- 여행 중 [추가] 위치 (2026-10-12) ----------

@pytest.fixture
def at_1030():
    saved = clock._clock
    clock._clock = FixedClock(datetime(2026, 10, 10, 10, 30, tzinfo=KST))
    yield
    clock._clock = saved


def progress(client, pid, iid, status, at):
    return ok(client.post(f"/planner/plans/{pid}/items/{iid}/progress", json={"status": status, "at": at}, headers=H))


def three_stops(client, **museum) -> tuple[int, dict]:
    """10:00 시작: 가옥거리(30분) -> 물회집 -> 과메기문화관. 카페는 가옥거리·물회집 사이에 있다."""
    pid = new_plan(client, day_start="10:00", day_end="22:00")["id"]
    add(client, pid, "p-house", stay_minutes=30)
    add(client, pid, "p-food", stay_minutes=30)
    return pid, add(client, pid, "p-museum", **museum)


def test_add_before_trip_keeps_old_rule(client):
    """여행 전(진행 기록 없음): 이동이 가장 적게 느는 곳 (가옥거리 바로 뒤)."""
    pid, _ = three_stops(client)
    rec = recommend(client, pid, [{"place_id": "p-cafe", "day": 1}])
    plan = ok(act(client, pid, rec, rec["candidates"][0]["id"], "add"))["plan"]
    assert order(plan) == ["p-house", "p-cafe", "p-food", "p-museum"]


def test_add_during_trip_goes_after_done_items(client, at_1030):
    """여행 중인 날은 다녀온·머무는 항목 앞에 넣지 않는다 (마지막 done·visiting 뒤만)."""
    pid, plan = three_stops(client)
    progress(client, pid, item_id(plan, "p-house"), "visiting", "10:00")
    progress(client, pid, item_id(plan, "p-house"), "done", "10:20")
    progress(client, pid, item_id(plan, "p-food"), "visiting", "10:25")
    rec = recommend(client, pid, [{"place_id": "p-cafe", "day": 1}])
    plan = ok(act(client, pid, rec, rec["candidates"][0]["id"], "add"))["plan"]
    assert order(plan)[:2] == ["p-house", "p-food"] and "p-cafe" in order(plan)[2:]


def test_add_during_trip_avoids_new_violation(client, at_1030):
    """다녀온 뒤 자리 중 새 위반이 없는 곳을 먼저 고른다. 문화관(12:00 고정) 앞에 넣으면 놓치므로 맨 뒤로
    (위반을 안 보면 이동이 가장 적게 느는 가옥거리 바로 뒤가 된다)."""
    pid, plan = three_stops(client, start_time="12:00")
    assert plan["violations"] == []
    progress(client, pid, item_id(plan, "p-house"), "visiting", "10:00")
    progress(client, pid, item_id(plan, "p-house"), "done", "10:25")
    rec = recommend(client, pid, [{"place_id": "p-cafe", "day": 1, "stay_minutes": 90}])
    plan = ok(act(client, pid, rec, rec["candidates"][0]["id"], "add"))["plan"]
    assert order(plan) == ["p-house", "p-food", "p-museum", "p-cafe"]
    assert plan["violations"] == []
