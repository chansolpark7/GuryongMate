"""변경 제안 (명세 §5): 스냅샷 기록, auto/overwrite 승인, base_changed, 대체·거절."""
from .conftest import H, add, err, get_plan, item_id, new_plan, ok, order


def propose(client, pid, ops, **kw) -> dict:
    return ok(client.post(f"/planner/plans/{pid}/proposals", json={"ops": ops} | kw, headers=H), 201)


def accept(client, pid, prop_id, mode=None):
    body = {"mode": mode} if mode else None
    return client.post(f"/planner/plans/{pid}/proposals/{prop_id}/accept", json=body, headers=H)


def setup_plan(client) -> tuple[int, int]:
    pid = new_plan(client)["id"]
    plan = add(client, pid, "p-house")
    return pid, item_id(plan, "p-house")


def test_create_records_snapshots(client):
    pid, house = setup_plan(client)
    p = propose(client, pid, [{"op": "add_item", "place_id": "p-food", "stay_minutes": 50},
                              {"op": "update_item", "item_id": house, "start_time": "13:00"}],
                summary="점심 추가", request="점심 넣어줘", origin="sess-1")
    assert p["status"] == "pending" and p["decision"] is None and p["decided_at"] is None
    assert (p["summary"], p["request"], p["source"], p["origin"]) == ("점심 추가", "점심 넣어줘", "llm", "sess-1")
    assert p["base_version"] == p["current_version"] == 2 and p["base_changed"] is False
    assert order(p["before"]) == ["p-house"] and order(p["after"]) == ["p-house", "p-food"]
    assert p["after"]["days"][0]["items"][1]["id"] < 0  # 새 항목은 음수 id
    assert [c["kind"] for c in p["changes"]] == ["updated", "added", "day_end"]  # 일정 순서대로
    # 보낸 필드만 기록·반환 (생략한 필드를 null 로 채우지 않는다)
    assert p["ops"] == [{"op": "add_item", "place_id": "p-food", "stay_minutes": 50},
                        {"op": "update_item", "item_id": house, "start_time": "13:00"}]
    # 일정은 그대로, version 도 그대로
    plan = get_plan(client, pid)
    assert order(plan) == ["p-house"] and plan["version"] == 2


def test_create_rejects_inapplicable_ops(client):
    pid, house = setup_plan(client)
    d = err(client.post(f"/planner/plans/{pid}/proposals", json={"ops": [
        {"op": "add_item", "place_id": "p-food"}, {"op": "remove_item", "item_id": 999999}]}, headers=H),
        404, "item_not_found")
    assert d["op_index"] == 1
    assert ok(client.get(f"/planner/plans/{pid}/proposals", headers=H)) == []


def test_expected_version(client):
    pid, house = setup_plan(client)
    ops = [{"op": "add_item", "place_id": "p-food"}]
    d = err(client.post(f"/planner/plans/{pid}/proposals", json={"ops": ops, "expected_version": 1}, headers=H),
            409, "version_conflict")
    assert (d["expected_version"], d["current_version"]) == (1, 2)
    propose(client, pid, ops, expected_version=2)


def test_accept_auto_when_unchanged(client):
    pid, house = setup_plan(client)
    p = propose(client, pid, [{"op": "add_item", "place_id": "p-food"},
                              {"op": "update_item", "item_id": house, "stay_minutes": 30}])
    r = ok(accept(client, pid, p["id"]))  # 본문 생략 = auto
    assert r["proposal"]["status"] == "accepted" and r["proposal"]["decision"] == "applied"
    assert r["proposal"]["decided_at"] and r["proposal"]["base_changed"] is True  # 반영으로 version 이 올랐으므로
    assert order(r["plan"]) == ["p-house", "p-food"] and r["plan"]["version"] == 3
    assert r["plan"]["days"][0]["items"][0]["stay_minutes"] == 30
    err(accept(client, pid, p["id"]), 409, "proposal_not_pending")
    err(client.post(f"/planner/plans/{pid}/proposals/{p['id']}/reject", headers=H), 409, "proposal_not_pending")


def test_accept_with_empty_object_body_is_auto(client):
    pid, house = setup_plan(client)
    p = propose(client, pid, [{"op": "add_item", "place_id": "p-food"}])
    r = ok(client.post(f"/planner/plans/{pid}/proposals/{p['id']}/accept", json={}, headers=H))
    assert r["proposal"]["decision"] == "applied"


def test_base_changed_then_overwrite(client):
    pid, house = setup_plan(client)
    p = propose(client, pid, [{"op": "add_item", "place_id": "p-cafe", "stay_minutes": 30},
                              {"op": "update_item", "item_id": house, "stay_minutes": 40}])
    # 제안 이후 사용자가 직접 점심을 넣음
    add(client, pid, "p-food")
    assert ok(client.get(f"/planner/plans/{pid}/proposals/{p['id']}", headers=H))["base_changed"] is True

    d = err(accept(client, pid, p["id"]), 409, "base_changed")
    assert (d["base_version"], d["current_version"]) == (2, 3)
    assert any(c["kind"] == "added" and "물회집" in c["message"] for c in d["since_proposal"])   # 사라질 변경
    assert any(c["kind"] == "removed" and "물회집" in c["message"] for c in d["overwrite_effect"])
    assert any(c["kind"] == "added" and "카페" in c["message"] for c in d["overwrite_effect"])
    assert order(get_plan(client, pid)) == ["p-house", "p-food"]  # 아무것도 반영 안 됨
    assert ok(client.get(f"/planner/plans/{pid}/proposals/{p['id']}", headers=H))["status"] == "pending"

    r = ok(accept(client, pid, p["id"], "overwrite"))
    assert r["proposal"]["decision"] == "overwritten"
    plan = r["plan"]
    assert order(plan) == order(p["after"]) == ["p-house", "p-cafe"]       # 카드에서 본 일정 그대로
    assert item_id(plan, "p-house") == house                               # 남아 있던 항목은 id 유지
    assert plan["days"][0]["items"][0]["stay_minutes"] == 40
    assert plan["version"] == 4


def test_overwrite_restores_plan_settings_rules_and_deleted_items(client):
    pid, house = setup_plan(client)
    ok(client.post(f"/planner/plans/{pid}/constraints", json={"type": "exclude", "params": {"place_id": "p-far"}},
                   headers=H), 201)
    p = propose(client, pid, [{"op": "update_plan", "title": "제안 제목", "day_end": "19:00"},
                              {"op": "add_constraint", "type": "end_before", "params": {"time": "18:30"}}])
    # 그사이: 제목 변경, 규칙 삭제, 항목 삭제
    rule_id = get_plan(client, pid)["constraints"][0]["id"]
    ok(client.patch(f"/planner/plans/{pid}", json={"title": "내 제목"}, headers=H))
    ok(client.delete(f"/planner/plans/{pid}/constraints/{rule_id}", headers=H))
    ok(client.delete(f"/planner/plans/{pid}/items/{house}", headers=H))

    plan = ok(accept(client, pid, p["id"], "overwrite"))["plan"]
    assert (plan["title"], plan["day_end"]) == ("제안 제목", "19:00")
    assert order(plan) == ["p-house"] and item_id(plan, "p-house") != house   # 삭제됐던 항목은 새 id 로 다시 생김
    assert sorted(c["type"] for c in plan["constraints"]) == ["end_before", "exclude"]


def test_overwrite_when_unchanged_equals_applied_result(client):
    pid, house = setup_plan(client)
    p = propose(client, pid, [{"op": "add_item", "place_id": "p-food"}])
    r = ok(accept(client, pid, p["id"], "overwrite"))
    assert r["proposal"]["decision"] == "overwritten" and order(r["plan"]) == ["p-house", "p-food"]
    assert all(i["id"] > 0 for i in r["plan"]["days"][0]["items"])


def test_reject_and_list(client):
    pid, house = setup_plan(client)
    p1 = propose(client, pid, [{"op": "remove_item", "item_id": house}])
    p2 = propose(client, pid, [{"op": "add_item", "place_id": "p-food"}])
    r = ok(client.post(f"/planner/plans/{pid}/proposals/{p1['id']}/reject", headers=H))
    assert r["status"] == "rejected" and r["decided_at"] and r["decision"] is None
    assert order(get_plan(client, pid)) == ["p-house"]
    listed = ok(client.get(f"/planner/plans/{pid}/proposals", headers=H))
    assert [x["id"] for x in listed] == [p2["id"], p1["id"]]                 # 최신순
    assert "before" not in listed[0] and listed[0]["changes"]                # 목록은 요약만
    pending = ok(client.get(f"/planner/plans/{pid}/proposals", params={"status": "pending"}, headers=H))
    assert [x["id"] for x in pending] == [p2["id"]]


def test_supersede(client):
    pid, house = setup_plan(client)
    p1 = propose(client, pid, [{"op": "add_item", "place_id": "p-food"}], origin="s")
    p2 = propose(client, pid, [{"op": "add_item", "place_id": "p-museum"}], origin="s", supersedes=p1["id"])
    assert ok(client.get(f"/planner/plans/{pid}/proposals/{p1['id']}", headers=H))["status"] == "superseded"
    err(accept(client, pid, p1["id"]), 409, "proposal_not_pending")
    d = err(client.post(f"/planner/plans/{pid}/proposals",
                        json={"ops": [{"op": "add_item", "place_id": "p-cafe"}], "supersedes": p1["id"]}, headers=H),
            409, "supersede_conflict")
    assert d["status"] == "superseded"
    err(client.post(f"/planner/plans/{pid}/proposals",
                    json={"ops": [{"op": "add_item", "place_id": "p-cafe"}], "supersedes": 999999}, headers=H),
        404, "proposal_not_found")
    ok(accept(client, pid, p2["id"]))


def test_history_snapshot_never_changes(client):
    pid, house = setup_plan(client)
    p = propose(client, pid, [{"op": "add_item", "place_id": "p-food"}])
    add(client, pid, "p-cafe")
    ok(client.delete(f"/planner/plans/{pid}/items/{house}", headers=H))
    again = ok(client.get(f"/planner/plans/{pid}/proposals/{p['id']}", headers=H))
    assert again["before"] == p["before"] and again["after"] == p["after"] and again["changes"] == p["changes"]


def test_proposal_scoped_to_plan_and_user(client):
    pid, house = setup_plan(client)
    other_pid, _ = setup_plan(client)
    p = propose(client, pid, [{"op": "add_item", "place_id": "p-food"}])
    err(client.get(f"/planner/plans/{other_pid}/proposals/{p['id']}", headers=H), 404, "proposal_not_found")
    err(accept(client, other_pid, p["id"]), 404, "proposal_not_found")
    err(client.post(f"/planner/plans/{pid}/proposals/{p['id']}/accept", headers={"X-User-Id": "test-planner-x"}),
        404, "plan_not_found")
    err(client.post(f"/planner/plans/{pid}/proposals/{p['id']}/accept", json={"mode": "force"}, headers=H),
        422, "invalid_request")
