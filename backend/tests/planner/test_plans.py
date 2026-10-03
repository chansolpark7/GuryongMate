"""계획·직접 편집·ops·preview·version (명세 §3, §4, §2.2)."""
from .conftest import H, add, err, get_plan, item_id, new_plan, ok, order


def test_create_defaults_and_list(client):
    plan = new_plan(client, end_date="2026-10-11")
    assert plan["version"] == 1 and plan["item_count"] == 0
    assert plan["day_start"] == "12:30" and plan["travel_mode"] == "walk"
    assert [d["weekday"] for d in plan["days"]] == ["토", "일"]
    one_day = ok(client.post("/planner/plans", json={"title": "x", "start_date": "2026-10-12"}, headers=H), 201)
    assert one_day["end_date"] == "2026-10-12" and one_day["day_start"] == "10:00" and one_day["day_end"] == "18:00"
    listed = ok(client.get("/planner/plans", headers=H))
    assert {plan["id"], one_day["id"]} <= {p["id"] for p in listed}


def test_owner_isolation(client):
    pid = new_plan(client)["id"]
    other = {"X-User-Id": "test-planner-other"}
    err(client.get(f"/planner/plans/{pid}", headers=other), 404, "plan_not_found")
    err(client.patch(f"/planner/plans/{pid}", json={"title": "x"}, headers=other), 404, "plan_not_found")
    err(client.delete(f"/planner/plans/{pid}", headers=other), 404, "plan_not_found")
    err(client.post(f"/planner/plans/{pid}/items", json={"place_id": "p-house"}, headers=other), 404, "plan_not_found")
    assert pid not in [p["id"] for p in ok(client.get("/planner/plans", headers=other))]


def test_add_reorder_move_delete(client):
    pid = new_plan(client, end_date="2026-10-11")["id"]
    for place in ("p-house", "p-food", "p-museum"):
        plan = add(client, pid, place)
    assert order(plan) == ["p-house", "p-food", "p-museum"]
    url = f"/planner/plans/{pid}/items"

    plan = add(client, pid, "p-cafe", order=1, stay_minutes=30)
    assert order(plan) == ["p-cafe", "p-house", "p-food", "p-museum"]
    plan = add(client, pid, "p-far", order=99)  # 항목 수보다 크면 맨 뒤
    assert order(plan)[-1] == "p-far"

    plan = ok(client.patch(f"{url}/{item_id(plan, 'p-museum')}", json={"order": 1}, headers=H))
    assert order(plan) == ["p-museum", "p-cafe", "p-house", "p-food", "p-far"]
    plan = ok(client.patch(f"{url}/{item_id(plan, 'p-house')}", json={"day": 2}, headers=H))
    assert order(plan) == ["p-museum", "p-cafe", "p-food", "p-far"] and order(plan, 2) == ["p-house"]
    assert [i["order"] for i in plan["days"][0]["items"]] == [1, 2, 3, 4]
    plan = ok(client.patch(f"{url}/{item_id(plan, 'p-food')}", json={"day": 2, "order": 1}, headers=H))
    assert order(plan, 2) == ["p-food", "p-house"]

    plan = ok(client.delete(f"{url}/{item_id(plan, 'p-cafe')}", headers=H))
    assert order(plan) == ["p-museum", "p-far"] and [i["order"] for i in plan["days"][0]["items"]] == [1, 2]
    err(client.delete(f"{url}/999999", headers=H), 404, "item_not_found")
    err(client.patch(f"{url}/999999", json={"stay_minutes": 30}, headers=H), 404, "item_not_found")


def test_times_fixed_start_and_status(client):
    pid = new_plan(client)["id"]
    add(client, pid, "p-house", stay_minutes=90)
    plan = add(client, pid, "p-food", start_time="13:30")
    food = plan["days"][0]["items"][1]
    assert food["start_time"] == "13:30"
    assert [v["code"] for v in plan["violations"]] == ["fixed_time_missed"]

    url = f"/planner/plans/{pid}/items/{food['id']}"
    plan = ok(client.patch(url, json={"start_time": None}, headers=H))  # null = 고정 해제
    food = plan["days"][0]["items"][1]
    assert food["start_time"] is None and food["start_at"] == food["arrive_at"] and plan["violations"] == []

    plan = ok(client.patch(url, json={"stay_minutes": 300}, headers=H))
    assert [v["code"] for v in plan["violations"]] == ["day_overflow"]
    assert ok(client.get(f"/planner/plans/{pid}/validate", headers=H))["ok"] is False

    plan = ok(client.patch(url, json={"status": "skipped"}, headers=H))
    assert plan["violations"] == [] and plan["days"][0]["items"][1]["start_at"] is None
    assert ok(client.get(f"/planner/plans/{pid}/validate", headers=H)) == {"ok": True, "violations": []}


def test_update_plan(client):
    pid = new_plan(client, end_date="2026-10-11")["id"]
    add(client, pid, "p-house", day=2)
    url = f"/planner/plans/{pid}"
    err(client.patch(url, json={"end_date": "2026-10-10"}, headers=H), 409, "range_conflict")
    err(client.patch(url, json={"day_end": "10:00"}, headers=H), 422, "invalid_range")
    err(client.patch(url, json={"end_date": "2026-10-09"}, headers=H), 422, "invalid_range")
    err(client.patch(url, json={"end_date": "2026-10-30"}, headers=H), 422, "invalid_range")  # 14일 초과
    plan = ok(client.patch(url, json={"title": "1박2일", "travel_mode": "car", "memo": "메모"}, headers=H))
    assert (plan["title"], plan["travel_mode"], plan["memo"]) == ("1박2일", "car", "메모")
    assert get_plan(client, pid)["title"] == "1박2일"


def test_constraints(client):
    pid = new_plan(client)["id"]
    url = f"/planner/plans/{pid}/constraints"
    plan = ok(client.post(url, json={"type": "must_include", "params": {"place_id": "p-museum"}}, headers=H), 201)
    assert [v["code"] for v in plan["violations"]] == ["must_include"]
    err(client.post(url, json={"type": "must_include", "params": {"place_id": "nope"}}, headers=H), 422, "place_not_found")
    err(client.post(url, json={"type": "end_before", "params": {"time": "17:00", "day": 3}}, headers=H),
        422, "day_out_of_range")

    plan = add(client, pid, "p-museum")
    assert plan["violations"] == []
    cid = plan["constraints"][0]["id"]
    assert ok(client.get(url, headers=H))[0]["params"] == {"place_id": "p-museum"}
    plan = ok(client.post(url, json={"type": "end_before", "params": {"time": "17:00"}, "hard": False}, headers=H), 201)
    assert plan["constraints"][1]["params"] == {"time": "17:00"}
    assert ok(client.delete(f"{url}/{cid}", headers=H))["constraints"][0]["type"] == "end_before"
    err(client.delete(f"{url}/{cid}", headers=H), 404, "constraint_not_found")


def test_delete_plan_cascades(client):
    pid = new_plan(client)["id"]
    add(client, pid, "p-house")
    ok(client.post(f"/planner/plans/{pid}/proposals", json={"ops": [{"op": "add_item", "place_id": "p-food"}]},
                   headers=H), 201)
    ok(client.post(f"/planner/plans/{pid}/recommendations",
                   json={"title": "t", "candidates": [{"place_id": "p-cafe", "day": 1}]}, headers=H), 201)
    assert client.delete(f"/planner/plans/{pid}", headers=H).status_code == 204
    err(client.get(f"/planner/plans/{pid}", headers=H), 404, "plan_not_found")


def test_version_increments_only_on_changes(client):
    plan = new_plan(client)
    pid = plan["id"]
    assert plan["version"] == 1
    v = lambda: get_plan(client, pid)["version"]  # noqa: E731

    add(client, pid, "p-house")
    assert v() == 2
    hid = item_id(get_plan(client, pid), "p-house")
    ok(client.patch(f"/planner/plans/{pid}/items/{hid}", json={"stay_minutes": 30}, headers=H))
    ok(client.patch(f"/planner/plans/{pid}", json={"memo": "m"}, headers=H))
    ok(client.post(f"/planner/plans/{pid}/constraints", json={"type": "exclude", "params": {"place_id": "p-far"}},
                   headers=H), 201)
    ok(client.post(f"/planner/plans/{pid}/ops", json={"ops": [{"op": "add_item", "place_id": "p-food"},
                                                              {"op": "add_item", "place_id": "p-cafe"}]}, headers=H))
    assert v() == 6  # ops 한 번 = 1

    # 바꾸지 않는 요청은 version 그대로
    ok(client.post(f"/planner/plans/{pid}/preview", json={"ops": [{"op": "remove_item", "item_id": hid}]}, headers=H))
    ok(client.get(f"/planner/plans/{pid}/validate", headers=H))
    ok(client.post(f"/planner/plans/{pid}/proposals", json={"ops": [{"op": "remove_item", "item_id": hid}]},
                   headers=H), 201)
    assert v() == 6

    # 실패한 요청도 version 그대로
    err(client.post(f"/planner/plans/{pid}/ops", json={"ops": [{"op": "add_item", "place_id": "p-food"},
                                                               {"op": "remove_item", "item_id": 999999}]},
                    headers=H), 404, "item_not_found")
    assert v() == 6


def test_batch_ops_atomic_with_op_index(client):
    pid = new_plan(client)["id"]
    url = f"/planner/plans/{pid}/ops"
    d = err(client.post(url, json={"ops": [{"op": "add_item", "place_id": "p-house"},
                                           {"op": "add_item", "place_id": "nope"}]}, headers=H), 422, "place_not_found")
    assert d["op_index"] == 1 and d["place_id"] == "nope"
    assert order(get_plan(client, pid)) == []

    plan = ok(client.post(url, json={"ops": [
        {"op": "add_item", "place_id": "p-house"},
        {"op": "add_item", "place_id": "p-food"},
        {"op": "update_item", "item_id": -2, "order": 1, "memo": "점심"},   # 방금 추가한 항목을 임시 id 로
        {"op": "add_constraint", "type": "must_include", "params": {"place_id": "p-museum"}},
        {"op": "remove_constraint", "constraint_id": -1},
        {"op": "update_plan", "day_end": "20:00"},
    ]}, headers=H))
    assert order(plan) == ["p-food", "p-house"] and plan["days"][0]["items"][0]["memo"] == "점심"
    assert plan["constraints"] == [] and plan["day_end"] == "20:00"
    err(client.post(url, json={"ops": [{"op": "remove_item", "item_id": -1}]}, headers=H), 404, "item_not_found")


def test_preview_does_not_save(client):
    pid = new_plan(client)["id"]
    add(client, pid, "p-house")
    r = ok(client.post(f"/planner/plans/{pid}/preview", json={"ops": [
        {"op": "add_item", "place_id": "p-food"},
        {"op": "add_item", "place_id": "p-museum"},
        {"op": "update_item", "item_id": -2, "order": 1},
    ]}, headers=H))
    assert order(r["plan"]) == ["p-museum", "p-house", "p-food"]
    assert sorted(i["id"] for i in r["plan"]["days"][0]["items"] if i["id"] < 0) == [-2, -1]
    assert [c["kind"] for c in r["changes"]] == ["added", "added", "day_end"]  # 가옥거리는 밀렸을 뿐 이동 아님
    assert order(get_plan(client, pid)) == ["p-house"]
    d = err(client.post(f"/planner/plans/{pid}/preview", json={"ops": [{"op": "add_item", "place_id": "p-food", "day": 2}]},
                        headers=H), 422, "day_out_of_range")
    assert (d["day"], d["num_days"], d["op_index"]) == (2, 1, 0)


def test_places_unavailable(client):
    from .conftest import fake
    pid = new_plan(client)["id"]
    add(client, pid, "p-house")
    fake.down = True
    err(client.post(f"/planner/plans/{pid}/items", json={"place_id": "p-food"}, headers=H), 503, "places_unavailable")
    plan = get_plan(client, pid)  # 조회는 되고, 장소 정보만 비고 경고가 붙는다
    assert plan["days"][0]["items"][0]["place"] is None
    assert [v["code"] for v in plan["violations"]] == ["place_unavailable"]


def test_concurrent_edits_are_serialized(client):
    from concurrent.futures import ThreadPoolExecutor
    pid = new_plan(client)["id"]
    places = ["p-house", "p-food", "p-museum", "p-cafe"]
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda pl: client.post(f"/planner/plans/{pid}/items", json={"place_id": pl}, headers=H),
                                places))
    assert all(r.status_code == 201 for r in results)
    plan = get_plan(client, pid)
    assert plan["version"] == 5 and sorted(order(plan)) == sorted(places)
    assert [i["order"] for i in plan["days"][0]["items"]] == [1, 2, 3, 4]
