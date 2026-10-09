"""메모리 CRUD·범위·버전·요약·탈퇴 (docs/docent/README.md §1, §5)."""
from tests.docent.conftest import H, H2, err, new_plan, ok, remember


def _version(client, headers=H) -> int:
    return ok(client.get("/docent/memory", headers=headers))["memory_version"]


def test_crud_and_version(client):
    v0 = _version(client)
    item = remember(client, "style", "해설은 핵심만 짧게")
    assert item["scope"] == "user" and item["plan_id"] is None and item["source"] == "msg-1"
    assert _version(client) == v0 + 1

    got = ok(client.get("/docent/memory", headers=H))
    assert item["id"] in [m["id"] for m in got["items"]]

    patched = ok(client.patch(f"/docent/memory/{item['id']}", json={"text": "해설은 자세히", "source": "msg-9"}, headers=H))
    assert patched["text"] == "해설은 자세히" and patched["source"] == "msg-9"
    assert _version(client) == v0 + 2

    # 근거(source)만 바꾸거나 같은 값이면 내용 변경이 아니다 -> 버전 그대로
    ok(client.patch(f"/docent/memory/{item['id']}", json={"source": "user_edit"}, headers=H))
    ok(client.patch(f"/docent/memory/{item['id']}", json={"text": "해설은 자세히"}, headers=H))
    assert _version(client) == v0 + 2

    ok(client.delete(f"/docent/memory/{item['id']}", headers=H), 204)
    assert item["id"] not in [m["id"] for m in ok(client.get("/docent/memory", headers=H))["items"]]
    assert _version(client) == v0 + 3
    err(client.delete(f"/docent/memory/{item['id']}", headers=H), 404, "memory_not_found")


def test_same_text_is_refreshed_not_duplicated(client):
    first = remember(client, "food", "과메기는 꼭 먹고 싶어함")
    v = _version(client)
    again = remember(client, "food", "과메기는 꼭 먹고 싶어함", status=200)
    assert again["id"] == first["id"]
    assert _version(client) == v
    texts = [m["text"] for m in ok(client.get("/docent/memory", headers=H))["items"]]
    assert texts.count("과메기는 꼭 먹고 싶어함") == 1


def test_plan_scope_visibility(client):
    p1, p2 = new_plan(client), new_plan(client)
    u = remember(client, "interest", "사진 찍기를 좋아함")
    a = remember(client, "companion", "어머니(60대) 동행, 오래 걷기 부담", scope="plan", plan_id=p1)
    b = remember(client, "companion", "친구와 둘이 여행", scope="plan", plan_id=p2)
    assert a["scope"] == "plan" and a["plan_id"] == p1

    def ids(**params):
        return {m["id"] for m in ok(client.get("/docent/memory", params=params, headers=H))["items"]}

    assert u["id"] in ids() and a["id"] not in ids() and b["id"] not in ids()  # plan_id 없으면 user 범위만
    assert {u["id"], a["id"]} <= ids(plan_id=p1) and b["id"] not in ids(plan_id=p1)
    assert {u["id"], b["id"]} <= ids(plan_id=p2) and a["id"] not in ids(plan_id=p2)


def test_scope_validation(client):
    pid = new_plan(client)
    base = {"category": "interest", "text": "x", "source": "msg-1"}
    err(client.post("/docent/memory", json=base | {"scope": "plan"}, headers=H), 422, "invalid_request")
    err(client.post("/docent/memory", json=base | {"scope": "user", "plan_id": pid}, headers=H), 422, "invalid_request")
    err(client.post("/docent/memory", json=base | {"scope": "user", "category": "age"}, headers=H), 422, "invalid_request")
    err(client.post("/docent/memory", json=base | {"scope": "user", "text": "  "}, headers=H), 422, "invalid_request")
    err(client.post("/docent/memory", json={"scope": "user", "category": "interest", "text": "x"}, headers=H),
        422, "invalid_request")  # source 필수


def test_other_users_plan_and_memory(client):
    mine = new_plan(client)
    theirs = new_plan(client, headers=H2)
    # 다른 사용자의 계획 범위로 저장·조회 불가 (planner 가 404)
    err(client.post("/docent/memory", json={"scope": "plan", "plan_id": theirs, "category": "interest",
                                            "text": "x", "source": "m"}, headers=H), 404, "plan_not_found")
    err(client.get("/docent/memory", params={"plan_id": theirs}, headers=H), 404, "plan_not_found")
    err(client.get("/docent/memory/summary", params={"plan_id": 999999999}, headers=H), 404, "plan_not_found")

    # 다른 사용자의 메모리는 보이지도, 고치지도, 지우지도 못한다
    item = remember(client, "avoid", "해산물 비린내는 싫어함", scope="plan", plan_id=mine)
    assert item["id"] not in [m["id"] for m in ok(client.get("/docent/memory", headers=H2))["items"]]
    err(client.patch(f"/docent/memory/{item['id']}", json={"text": "y"}, headers=H2), 404, "memory_not_found")
    err(client.delete(f"/docent/memory/{item['id']}", headers=H2), 404, "memory_not_found")


def test_summary(client):
    remember(client, "style", "해설은 핵심만 짧게", headers=H2)
    remember(client, "interest", "근대 건축", headers=H2)
    remember(client, "interest", "사진", headers=H2)
    remember(client, "companion", "어머니 동행", scope="plan", plan_id=new_plan(client, headers=H2), headers=H2)
    s = ok(client.get("/docent/memory/summary", headers=H2))
    lines = s["text"].splitlines()
    assert lines[0] == "- 해설 스타일: 해설은 핵심만 짧게"
    assert lines[1] == "- 관심사: 사진; 근대 건축"  # 최신이 앞
    assert "어머니" not in s["text"]  # plan_id 없으면 user 범위만
    assert s["count"] == 3 and s["memory_version"] == _version(client, H2)


def test_summary_with_plan_marks_trip_items(client):
    headers = {"X-User-Id": "test-docent-summary"}
    pid = new_plan(client, headers=headers)
    remember(client, "companion", "어머니(60대) 동행", scope="plan", plan_id=pid, headers=headers)
    remember(client, "companion", "계단은 피하고 싶음", headers=headers)
    s = ok(client.get("/docent/memory/summary", params={"plan_id": pid}, headers=headers))
    assert s["text"] == "- 동행자·제약: 계단은 피하고 싶음; (이번 여행) 어머니(60대) 동행"
    empty = ok(client.get("/docent/memory/summary", headers={"X-User-Id": "test-docent-nobody"}))
    assert empty == {"text": "", "count": 0, "memory_version": 0}


def test_delete_me(client):
    headers = {"X-User-Id": "test-docent-leaving"}
    pid = new_plan(client, headers=headers)
    remember(client, "interest", "등대", headers=headers)
    ok(client.put(f"/docent/plans/{pid}/notes/kto-1", json={"status": "pending"}, headers=headers), 201)
    keep = remember(client, "interest", "남의 메모리는 남는다", headers=H)

    ok(client.delete("/docent/me", headers=headers), 204)
    assert ok(client.get("/docent/memory", headers=headers)) == {"items": [], "memory_version": 0}
    assert ok(client.get(f"/docent/plans/{pid}/notes", headers=headers)) == []
    assert keep["id"] in [m["id"] for m in ok(client.get("/docent/memory", headers=H))["items"]]
