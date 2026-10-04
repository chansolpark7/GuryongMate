"""구간별 이동 수단과 이동 검증 (긴 도보·하루 도보 합계·근사 구간·도보 규칙). 이동시간은 직선거리 근사(conftest)."""
import pytest

from app.planner import travel

from .conftest import H, add, err, get_plan, item_id, new_plan, ok


def leg(plan: dict, place_id: str) -> dict:
    return next(i for i in plan["days"][0]["items"] if i["place_id"] == place_id)


def codes(plan: dict) -> list[str]:
    return sorted(v["code"] for v in plan["violations"])


def far_trip(client, **kw) -> dict:
    """가옥거리 -> 호미곶 (도보 약 160분, 차량 약 30분)."""
    p = new_plan(client, day_end="23:59", **kw)
    add(client, p["id"], "p-house")
    return add(client, p["id"], "p-far")


def test_options_and_default_mode(client):
    p = far_trip(client)
    far = leg(p, "p-far")
    assert far["travel_mode"] is None and far["leg_mode"] == "walk"
    opts = far["travel_options"]
    assert set(opts) == {"walk", "car"} and opts["car"]["minutes"] < 40 < opts["walk"]["minutes"]
    assert far["travel_minutes"] == opts["walk"]["minutes"]
    first = leg(p, "p-house")
    assert first["leg_mode"] is None and first["travel_options"] is None and first["travel_minutes"] is None


def test_per_leg_mode_overrides_plan_default(client):
    p = far_trip(client)
    far_id = item_id(p, "p-far")
    walk_end = leg(p, "p-far")["end_at"]
    p = ok(client.patch(f"/planner/plans/{p['id']}/items/{far_id}", json={"travel_mode": "car"}, headers=H))
    far = leg(p, "p-far")
    assert (far["travel_mode"], far["leg_mode"]) == ("car", "car")
    assert far["travel_minutes"] == far["travel_options"]["car"]["minutes"] and far["end_at"] < walk_end
    # 계획 기본을 차량으로 바꿔도, 구간에 정한 수단이 우선
    p = ok(client.patch(f"/planner/plans/{p['id']}/items/{far_id}", json={"travel_mode": "walk"}, headers=H))
    p = ok(client.patch(f"/planner/plans/{p['id']}", json={"travel_mode": "car"}, headers=H))
    assert leg(p, "p-far")["leg_mode"] == "walk"
    # null 이면 계획 기본으로
    p = ok(client.patch(f"/planner/plans/{p['id']}/items/{far_id}", json={"travel_mode": None}, headers=H))
    assert (leg(p, "p-far")["travel_mode"], leg(p, "p-far")["leg_mode"]) == (None, "car")


def test_mode_on_add_ops_preview_and_proposal(client):
    p = new_plan(client, day_end="23:59")
    add(client, p["id"], "p-house")
    p = add(client, p["id"], "p-far", travel_mode="car")
    assert leg(p, "p-far")["leg_mode"] == "car"
    far_id = item_id(p, "p-far")
    r = ok(client.post(f"/planner/plans/{p['id']}/preview", headers=H,
                       json={"ops": [{"op": "update_item", "item_id": far_id, "travel_mode": None}]}))
    assert leg(r["plan"], "p-far")["leg_mode"] == "walk"
    assert any("오는 수단 차량 → 계획 기본" in c["message"] for c in r["changes"])
    # 제안을 만들고 덮어쓰기로 승인해도 구간 수단이 스냅샷대로 남는다
    prop = ok(client.post(f"/planner/plans/{p['id']}/proposals", headers=H, json={
        "ops": [{"op": "update_item", "item_id": far_id, "stay_minutes": 30}], "summary": "짧게"}), 201)
    ok(client.patch(f"/planner/plans/{p['id']}/items/{item_id(p, 'p-house')}", json={"stay_minutes": 90}, headers=H))
    res = ok(client.post(f"/planner/plans/{p['id']}/proposals/{prop['id']}/accept", json={"mode": "overwrite"},
                         headers=H))
    assert leg(res["plan"], "p-far")["travel_mode"] == "car"


@pytest.mark.parametrize("body", [{"travel_mode": "bike"}, {"travel_mode": 1}])
def test_mode_validation(client, body):
    p = far_trip(client)
    err(client.patch(f"/planner/plans/{p['id']}/items/{item_id(p, 'p-far')}", json=body, headers=H), 422,
        "invalid_request")
    err(client.post(f"/planner/plans/{p['id']}/items", json={"place_id": "p-cafe"} | body, headers=H), 422,
        "invalid_request")


def test_long_walk_and_daily_total(client):
    p = far_trip(client)
    assert codes(p) == ["daily_walk_total", "long_walk"]
    lw = next(v for v in p["violations"] if v["code"] == "long_walk")
    assert lw["severity"] == "warning" and lw["item_id"] == item_id(p, "p-far") and "차량이면 약" in lw["message"]
    p = ok(client.patch(f"/planner/plans/{p['id']}/items/{item_id(p, 'p-far')}", json={"travel_mode": "car"},
                        headers=H))
    assert codes(p) == []


def test_walk_rules_replace_defaults(client):
    p = new_plan(client)
    for pid in ("p-house", "p-food", "p-museum"):  # 약 300m 간격, 도보 약 6분씩
        p = add(client, p["id"], pid)
    assert codes(p) == []
    p = ok(client.post(f"/planner/plans/{p['id']}/constraints", headers=H,
                       json={"type": "max_walk_minutes", "params": {"minutes": 5}, "hard": False}), 201)
    assert codes(p) == ["max_walk_minutes", "max_walk_minutes"]
    assert all(v["severity"] == "warning" and "차량이면" in v["message"] for v in p["violations"])
    p = ok(client.post(f"/planner/plans/{p['id']}/constraints", headers=H,
                       json={"type": "max_daily_walk_minutes", "params": {"minutes": 10}}), 201)
    daily = [v for v in p["violations"] if v["code"] == "max_daily_walk_minutes"]
    assert len(daily) == 1 and daily[0]["severity"] == "error" and daily[0]["day"] == 1
    # 규칙이 있으면 기본 검사(long_walk 30분)는 하지 않는다: 호미곶을 넣어도 규칙 위반만
    p = add(client, p["id"], "p-far")
    assert "long_walk" not in codes(p) and "daily_walk_total" not in codes(p)
    err(client.post(f"/planner/plans/{p['id']}/constraints", headers=H,
                    json={"type": "max_walk_minutes", "params": {"minutes": 0}}), 422, "invalid_request")


def test_travel_estimated_when_routing_unavailable(client):
    saved, travel._lookup = travel._lookup, travel.EstimateTravel()  # routing 장애: 모든 구간이 근사
    try:
        p = new_plan(client)
        for pid in ("p-house", "p-food", "p-museum"):
            p = add(client, p["id"], pid)
        est = [v for v in p["violations"] if v["code"] == "travel_estimated"]
        assert len(est) == 1 and est[0]["day"] == 1 and "2구간" in est[0]["message"]
        assert {i["travel_source"] for i in p["days"][0]["items"][1:]} == {"estimate"}
    finally:
        travel._lookup = saved
    assert "travel_estimated" not in codes(get_plan(client, p["id"]))
