"""planner API 테스트. 실제 DB 를 쓰고, places 조회는 가짜 구현으로 바꾼다.

    docker compose run --rm api pytest tests/planner
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.db import SessionLocal, init_db
from app.main import app
from app.planner.models import PlannerPlan
from app.planner.places import PlacesUnavailable, get_place_lookup
from app.planner.schemas import PlaceInfo

USER = "test-planner-user"
H = {"X-User-Id": USER}
PLACES = {
    "p-house": PlaceInfo(id="p-house", title="가옥거리", lat=35.9880, lng=129.5570),
    "p-food": PlaceInfo(id="p-food", title="물회집", lat=35.9907, lng=129.5570),
    "p-museum": PlaceInfo(id="p-museum", title="과메기문화관", lat=35.9934, lng=129.5570),
}


class FakeLookup:
    down = False

    def get(self, place_id):
        if self.down:
            raise PlacesUnavailable("down")
        return PLACES.get(place_id)


fake = FakeLookup()


@pytest.fixture(scope="module")
def client():
    init_db()

    def cleanup():
        with SessionLocal.begin() as s:
            s.execute(delete(PlannerPlan).where(PlannerPlan.user_id.like("test-%")))

    cleanup()
    app.dependency_overrides[get_place_lookup] = lambda: fake
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_place_lookup)
    cleanup()


def ok(resp, status=200):
    assert resp.status_code == status, resp.text
    return resp.json()


def new_plan(client, **kw):
    body = {"title": "구룡포 당일치기", "start_date": "2026-10-10", "day_start": "12:30", "day_end": "18:00"} | kw
    return ok(client.post("/planner/plans", json=body, headers=H), 201)


def order(plan, day=1):
    return [i["place_id"] for i in plan["days"][day - 1]["items"]]


def test_create_list_and_owner_isolation(client):
    plan = new_plan(client, end_date="2026-10-11")
    assert plan["day_start"] == "12:30"
    assert [d["weekday"] for d in plan["days"]] == ["토", "일"]
    assert plan["id"] in [p["id"] for p in ok(client.get("/planner/plans", headers=H))]
    assert client.get(f"/planner/plans/{plan['id']}", headers={"X-User-Id": "test-other"}).status_code == 404
    assert client.get(f"/planner/plans/{plan['id']}").status_code == 422  # 헤더 필수


def test_add_reorder_move_delete(client):
    pid = new_plan(client, end_date="2026-10-11")["id"]
    url = f"/planner/plans/{pid}/items"
    for place in ("p-house", "p-food", "p-museum"):
        plan = ok(client.post(url, json={"place_id": place}, headers=H), 201)
    assert order(plan) == ["p-house", "p-food", "p-museum"]
    ids = {i["place_id"]: i["id"] for i in plan["days"][0]["items"]}

    plan = ok(client.post(url, json={"place_id": "p-food", "order": 1, "stay_minutes": 30}, headers=H), 201)
    assert order(plan) == ["p-food", "p-house", "p-food", "p-museum"]
    extra = plan["days"][0]["items"][0]["id"]

    plan = ok(client.patch(f"{url}/{ids['p-museum']}", json={"order": 1}, headers=H))
    assert order(plan) == ["p-museum", "p-food", "p-house", "p-food"]

    plan = ok(client.patch(f"{url}/{ids['p-house']}", json={"day": 2}, headers=H))
    assert order(plan) == ["p-museum", "p-food", "p-food"]
    assert order(plan, 2) == ["p-house"]
    assert [i["order"] for i in plan["days"][0]["items"]] == [1, 2, 3]

    plan = ok(client.delete(f"{url}/{extra}", headers=H))
    assert order(plan) == ["p-museum", "p-food"]
    assert [i["order"] for i in plan["days"][0]["items"]] == [1, 2]


def test_times_and_fixed_start(client):
    pid = new_plan(client)["id"]
    url = f"/planner/plans/{pid}/items"
    ok(client.post(url, json={"place_id": "p-house", "stay_minutes": 90}, headers=H), 201)
    plan = ok(client.post(url, json={"place_id": "p-food", "start_time": "13:30"}, headers=H), 201)
    food = plan["days"][0]["items"][1]
    assert food["start_time"] == "13:30"
    assert [v["code"] for v in plan["violations"]] == ["fixed_time_missed"]

    plan = ok(client.patch(f"{url}/{food['id']}", json={"start_time": None}, headers=H))
    food = plan["days"][0]["items"][1]
    assert food["start_time"] is None and food["start_at"] == food["arrive_at"]
    assert plan["violations"] == []

    plan = ok(client.patch(f"{url}/{food['id']}", json={"stay_minutes": 300}, headers=H))
    assert [v["code"] for v in plan["violations"]] == ["day_overflow"]
    result = ok(client.get(f"/planner/plans/{pid}/validate", headers=H))
    assert result["ok"] is False

    plan = ok(client.patch(f"{url}/{food['id']}", json={"status": "skipped"}, headers=H))
    assert plan["violations"] == []


def test_input_errors(client):
    pid = new_plan(client)["id"]
    url = f"/planner/plans/{pid}/items"
    assert client.post(url, json={"place_id": "nope"}, headers=H).status_code == 422
    assert client.post(url, json={"place_id": "p-house", "day": 2}, headers=H).status_code == 422
    assert client.post("/planner/plans", json={"title": "x", "start_date": "2026-10-10", "end_date": "2026-10-09"},
                       headers=H).status_code == 422
    fake.down = True
    try:
        assert client.post(url, json={"place_id": "p-house"}, headers=H).status_code == 503
    finally:
        fake.down = False


def test_update_plan_range(client):
    pid = new_plan(client, end_date="2026-10-11")["id"]
    ok(client.post(f"/planner/plans/{pid}/items", json={"place_id": "p-house", "day": 2}, headers=H), 201)
    assert client.patch(f"/planner/plans/{pid}", json={"end_date": "2026-10-10"}, headers=H).status_code == 409
    assert client.patch(f"/planner/plans/{pid}", json={"day_end": "10:00"}, headers=H).status_code == 422
    plan = ok(client.patch(f"/planner/plans/{pid}", json={"title": "1박2일", "travel_mode": "car"}, headers=H))
    assert plan["title"] == "1박2일" and plan["travel_mode"] == "car"


def test_constraints(client):
    pid = new_plan(client)["id"]
    url = f"/planner/plans/{pid}/constraints"
    plan = ok(client.post(url, json={"type": "must_include", "params": {"place_id": "p-museum"}}, headers=H), 201)
    assert [v["code"] for v in plan["violations"]] == ["must_include"]
    assert client.post(url, json={"type": "end_before", "params": {}}, headers=H).status_code == 422
    assert client.post(url, json={"type": "must_include", "params": {"place_id": "nope"}}, headers=H).status_code == 422

    plan = ok(client.post(f"/planner/plans/{pid}/items", json={"place_id": "p-museum"}, headers=H), 201)
    assert plan["violations"] == []
    cid = plan["constraints"][0]["id"]
    assert ok(client.get(url, headers=H))[0]["params"] == {"place_id": "p-museum"}
    plan = ok(client.delete(f"{url}/{cid}", headers=H))
    assert plan["constraints"] == []


def test_delete_plan(client):
    pid = new_plan(client)["id"]
    assert client.delete(f"/planner/plans/{pid}", headers=H).status_code == 204
    assert client.get(f"/planner/plans/{pid}", headers=H).status_code == 404


def test_preview_does_not_save_and_supports_temp_ids(client):
    pid = new_plan(client)["id"]
    ok(client.post(f"/planner/plans/{pid}/items", json={"place_id": "p-house"}, headers=H), 201)
    ops = [
        {"op": "add_item", "place_id": "p-food"},
        {"op": "add_item", "place_id": "p-museum"},
        {"op": "update_item", "item_id": -2, "order": 1, "start_time": "13:00"},  # 방금 추가한 항목을 임시 id 로
    ]
    r = ok(client.post(f"/planner/plans/{pid}/preview", json={"ops": ops}, headers=H))
    assert order(r["plan"]) == ["p-museum", "p-house", "p-food"]
    assert [c["kind"] for c in r["changes"]] == ["added", "moved", "added", "day_end"]
    assert order(ok(client.get(f"/planner/plans/{pid}", headers=H))) == ["p-house"]  # 저장 안 됨

    bad = client.post(f"/planner/plans/{pid}/preview", json={"ops": [{"op": "remove_item", "item_id": 999}]}, headers=H)
    assert bad.status_code == 404


def test_proposal_accept_reject_supersede(client):
    pid = new_plan(client)["id"]
    plan = ok(client.post(f"/planner/plans/{pid}/items", json={"place_id": "p-house"}, headers=H), 201)
    house = plan["days"][0]["items"][0]["id"]
    url = f"/planner/plans/{pid}/proposals"

    p1 = ok(client.post(url, json={"ops": [{"op": "add_item", "place_id": "p-food"}], "summary": "점심"}, headers=H), 201)
    assert p1["status"] == "pending" and p1["applicable"]
    assert order(p1["before"]) == ["p-house"] and order(p1["after"]) == ["p-house", "p-food"]

    p2 = ok(client.post(url, json={"ops": [{"op": "add_item", "place_id": "p-museum"},
                                           {"op": "update_item", "item_id": house, "stay_minutes": 30}],
                                   "supersedes": p1["id"]}, headers=H), 201)
    assert ok(client.get(f"{url}/{p1['id']}", headers=H))["status"] == "superseded"
    assert [p["id"] for p in ok(client.get(url, params={"status": "pending"}, headers=H))] == [p2["id"]]

    result = ok(client.post(f"{url}/{p2['id']}/accept", headers=H))
    assert result["proposal"]["status"] == "accepted"
    assert order(result["plan"]) == ["p-house", "p-museum"]
    assert result["plan"]["days"][0]["items"][0]["stay_minutes"] == 30
    assert client.post(f"{url}/{p2['id']}/accept", headers=H).status_code == 409

    p3 = ok(client.post(url, json={"ops": [{"op": "remove_item", "item_id": house}]}, headers=H), 201)
    assert ok(client.post(f"{url}/{p3['id']}/reject", headers=H))["status"] == "rejected"
    assert order(ok(client.get(f"/planner/plans/{pid}", headers=H))) == ["p-house", "p-museum"]


def test_stale_proposal_cannot_be_accepted(client):
    pid = new_plan(client)["id"]
    plan = ok(client.post(f"/planner/plans/{pid}/items", json={"place_id": "p-house"}, headers=H), 201)
    house = plan["days"][0]["items"][0]["id"]
    url = f"/planner/plans/{pid}/proposals"
    p = ok(client.post(url, json={"ops": [{"op": "update_item", "item_id": house, "stay_minutes": 30}]}, headers=H), 201)
    ok(client.delete(f"/planner/plans/{pid}/items/{house}", headers=H))  # 사용자가 그사이 직접 삭제
    detail = ok(client.get(f"{url}/{p['id']}", headers=H))
    assert detail["applicable"] is False and detail["error"]
    assert client.post(f"{url}/{p['id']}/accept", headers=H).status_code == 409


def test_batch_ops_are_atomic(client):
    pid = new_plan(client)["id"]
    url = f"/planner/plans/{pid}/ops"
    r = client.post(url, json={"ops": [{"op": "add_item", "place_id": "p-house"}, {"op": "add_item", "place_id": "nope"}]},
                    headers=H)
    assert r.status_code == 422 and "ops[1]" in r.json()["detail"]
    assert order(ok(client.get(f"/planner/plans/{pid}", headers=H))) == []
    plan = ok(client.post(url, json={"ops": [{"op": "add_item", "place_id": "p-house"},
                                             {"op": "add_constraint", "type": "must_include", "params": {"place_id": "p-food"}},
                                             {"op": "update_plan", "day_end": "20:00"}]}, headers=H))
    assert order(plan) == ["p-house"] and plan["day_end"] == "20:00"
    assert [v["code"] for v in plan["violations"]] == ["must_include"]
