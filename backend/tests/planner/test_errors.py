"""잘못된 요청을 확실하게 거부하는지 (명세 §1). 모든 오류는 {"detail": {"code", "message"}} 형식."""
import pytest

from .conftest import H, add, err, get_plan, item_id, new_plan


@pytest.fixture(scope="module")
def pid(client):
    plan = new_plan(client, end_date="2026-10-11")
    add(client, plan["id"], "p-house")
    return plan["id"]


def invalid(resp) -> dict:
    d = err(resp, 422, "invalid_request")
    assert d["errors"] and all({"loc", "msg"} <= set(e) for e in d["errors"])
    return d


def test_user_header_required(client, pid):
    invalid(client.get(f"/planner/plans/{pid}"))
    invalid(client.get("/planner/plans", headers={"X-User-Id": ""}))
    invalid(client.get("/planner/plans", headers={"X-User-Id": "x" * 65}))


def test_path_ids_must_be_integers(client, pid):
    invalid(client.get("/planner/plans/abc", headers=H))
    invalid(client.delete(f"/planner/plans/{pid}/items/abc", headers=H))
    err(client.get("/planner/plans/-5", headers=H), 404, "plan_not_found")


@pytest.mark.parametrize("body", [
    {},                                                           # 필수값 누락
    {"title": "x"},
    {"title": "", "start_date": "2026-10-10"},                    # 빈 제목
    {"title": "x" * 201, "start_date": "2026-10-10"},
    {"title": "x", "start_date": "10/10/2026"},                   # 날짜 형식
    {"title": "x", "start_date": "2026-10-10", "day_start": "25:00"},
    {"title": "x", "start_date": "2026-10-10", "travel_mode": "bike"},
    {"title": "x", "start_date": "2026-10-10", "memo": "m" * 501},
    {"title": "x", "start_date": "2026-10-10", "unknown": 1},     # 모르는 필드
    {"title": "x", "start_date": "2026-10-10", "end_date": None, "day_start": None},  # null 불가 (day_start)
])
def test_create_plan_rejects(client, body):
    invalid(client.post("/planner/plans", json=body, headers=H))


def test_create_plan_range(client):
    err(client.post("/planner/plans", json={"title": "x", "start_date": "2026-10-10", "end_date": "2026-10-09"},
                    headers=H), 422, "invalid_range")
    err(client.post("/planner/plans", json={"title": "x", "start_date": "2026-10-01", "end_date": "2026-10-15"},
                    headers=H), 422, "invalid_range")
    err(client.post("/planner/plans", json={"title": "x", "start_date": "2026-10-10", "day_start": "18:00",
                                            "day_end": "18:00"}, headers=H), 422, "invalid_range")


def test_body_must_be_json_object(client, pid):
    invalid(client.post("/planner/plans", content="not json", headers=H | {"Content-Type": "application/json"}))
    invalid(client.post("/planner/plans", json=[1, 2], headers=H))


@pytest.mark.parametrize("body", [
    {},                               # 바꿀 값 없음
    {"title": None},                  # null 불가
    {"title": ""},
    {"travel_mode": "bike"},
    {"version": 3},                   # 모르는(읽기 전용) 필드
])
def test_patch_plan_rejects(client, pid, body):
    invalid(client.patch(f"/planner/plans/{pid}", json=body, headers=H))


@pytest.mark.parametrize("body", [
    {},
    {"place_id": ""},
    {"place_id": "p-food", "day": 0},
    {"place_id": "p-food", "day": 15},
    {"place_id": "p-food", "order": 0},
    {"place_id": "p-food", "stay_minutes": 4},
    {"place_id": "p-food", "stay_minutes": 721},
    {"place_id": "p-food", "start_time": "9시"},
    {"place_id": "p-food", "status": "done"},          # 추가 시에는 status 를 받지 않는다
])
def test_add_item_rejects(client, pid, body):
    invalid(client.post(f"/planner/plans/{pid}/items", json=body, headers=H))


def test_add_item_semantic_errors(client, pid):
    d = err(client.post(f"/planner/plans/{pid}/items", json={"place_id": "nope"}, headers=H), 422, "place_not_found")
    assert d["place_id"] == "nope"
    d = err(client.post(f"/planner/plans/{pid}/items", json={"place_id": "p-food", "day": 3}, headers=H),
            422, "day_out_of_range")
    assert d == d | {"day": 3, "num_days": 2}


@pytest.mark.parametrize("body", [
    {},
    {"stay_minutes": None},           # start_time 외에는 null 불가
    {"day": None},
    {"memo": None},
    {"status": "gone"},
    {"place_id": "p-food"},           # 장소는 바꿀 수 없다 (삭제 후 추가)
])
def test_update_item_rejects(client, pid, body):
    hid = item_id(get_plan(client, pid), "p-house")
    invalid(client.patch(f"/planner/plans/{pid}/items/{hid}", json=body, headers=H))


def test_update_item_day_out_of_range(client, pid):
    hid = item_id(get_plan(client, pid), "p-house")
    err(client.patch(f"/planner/plans/{pid}/items/{hid}", json={"day": 3}, headers=H), 422, "day_out_of_range")


@pytest.mark.parametrize("body", [
    {"type": "nope", "params": {}},
    {"type": "end_before", "params": {}},                             # 필수 params 누락
    {"type": "end_before", "params": {"time": "17:00", "extra": 1}},  # params 의 모르는 키
    {"type": "max_travel_minutes", "params": {"minutes": 0}},
    {"type": "must_include", "params": {"place_id": "p-food"}, "source": "event"},
    {"type": "must_include", "params": {"place_id": "p-food"}, "note": "n" * 301},
])
def test_constraint_rejects(client, pid, body):
    invalid(client.post(f"/planner/plans/{pid}/constraints", json=body, headers=H))


@pytest.mark.parametrize("ops", [
    [],                                                   # 빈 목록
    [{"op": "teleport"}],                                 # 모르는 op
    [{"op": "add_item"}],                                 # 필수값 누락
    [{"op": "update_item", "item_id": 1}],                # 바꿀 값 없음
    [{"op": "update_item", "item_id": 1, "stay_minutes": None}],
    [{"op": "update_plan"}],
    [{"op": "remove_item"}],
    [{"op": "add_item", "place_id": "p-food", "foo": 1}],
    [{"op": "add_item", "place_id": "p-food"}] * 101,     # 최대 100개
])
def test_ops_rejects(client, pid, ops):
    for path in ("ops", "preview", "proposals"):
        invalid(client.post(f"/planner/plans/{pid}/{path}", json={"ops": ops}, headers=H))


def test_proposal_and_recommendation_body_rejects(client, pid):
    url = f"/planner/plans/{pid}"
    ops = [{"op": "add_item", "place_id": "p-food"}]
    invalid(client.post(f"{url}/proposals", json={"ops": ops, "source": "robot"}, headers=H))
    invalid(client.post(f"{url}/proposals", json={"ops": ops, "summary": "s" * 2001}, headers=H))
    invalid(client.post(f"{url}/proposals", json={"ops": ops, "origin": "o" * 65}, headers=H))
    invalid(client.get(f"{url}/proposals", params={"status": "done"}, headers=H))
    cand = {"place_id": "p-food", "day": 1}
    invalid(client.post(f"{url}/recommendations", json={"title": "t", "candidates": []}, headers=H))
    invalid(client.post(f"{url}/recommendations", json={"title": "", "candidates": [cand]}, headers=H))
    invalid(client.post(f"{url}/recommendations", json={"title": "t", "candidates": [cand | {"x": 1}]}, headers=H))
    invalid(client.post(f"{url}/recommendations", json={"title": "t", "candidates": [cand | {"time_hint": "noon"}]},
                        headers=H))
    invalid(client.post(f"{url}/recommendations",
                        json={"title": "t", "candidates": [{"place_id": f"p{i}", "day": 1} for i in range(11)]},
                        headers=H))


@pytest.mark.parametrize("place_id", ["../plans", "a/b", "p-food?x=1", "한글"])
def test_place_id_format(client, pid, place_id):
    invalid(client.post(f"/planner/plans/{pid}/items", json={"place_id": place_id}, headers=H))
