"""가이드 노트 저장·조회·stale (docs/docent/README.md §3, §5)."""
import httpx

from app.docent.plans import get_http
from app.main import app
from tests.docent.conftest import H, H2, READY_NOTE, err, new_plan, ok, remember


def test_put_get_list(client):
    pid = new_plan(client)
    url = f"/docent/plans/{pid}/notes/kto-2610501"
    err(client.get(url, headers=H), 404, "note_not_found")

    pending = ok(client.put(url, json={"status": "pending"}, headers=H), 201)
    assert pending["status"] == "pending" and pending["short"] == "" and pending["prepared_at"] is None

    note = ok(client.put(url, json=READY_NOTE, headers=H))  # 이미 있으면 200 으로 교체
    assert note["status"] == "ready" and note["prepared_at"]
    assert note["short"] == READY_NOTE["short"] and note["qa"] == READY_NOTE["qa"]
    assert note["sources"] == READY_NOTE["sources"] and note["memory_version"] == 1 and note["model"] == "gpt-5.4-mini"
    assert ok(client.get(url, headers=H)) == note

    ok(client.put(f"/docent/plans/{pid}/notes/kto-other", json={"status": "preparing"}, headers=H), 201)
    listed = ok(client.get(f"/docent/plans/{pid}/notes", headers=H))
    assert [(n["place_id"], n["status"]) for n in listed] == [("kto-2610501", "ready"), ("kto-other", "preparing")]


def test_ready_needs_body_and_sources(client):
    pid = new_plan(client)
    url = f"/docent/plans/{pid}/notes/kto-1"
    err(client.put(url, json=READY_NOTE | {"sources": []}, headers=H), 422, "invalid_request")
    err(client.put(url, json=READY_NOTE | {"long": ""}, headers=H), 422, "invalid_request")
    err(client.put(url, json=READY_NOTE | {"status": "done"}, headers=H), 422, "invalid_request")
    failed = ok(client.put(url, json={"status": "failed", "error": "출처를 찾지 못함"}, headers=H), 201)
    assert failed["status"] == "failed" and failed["error"] == "출처를 찾지 못함"


def test_regenerating_keeps_previous_body(client):
    """다시 만드는 동안(preparing)·실패(failed) 에도 이전 본문을 현장에서 쓸 수 있다."""
    pid = new_plan(client)
    url = f"/docent/plans/{pid}/notes/kto-1"
    ok(client.put(url, json=READY_NOTE, headers=H), 201)
    prep = ok(client.put(url, json={"status": "preparing"}, headers=H))
    assert prep["status"] == "preparing" and prep["short"] == READY_NOTE["short"] and prep["memory_version"] == 1
    failed = ok(client.put(url, json={"status": "failed", "error": "시간 초과"}, headers=H))
    assert failed["short"] == READY_NOTE["short"] and failed["error"] == "시간 초과"
    again = ok(client.put(url, json=READY_NOTE | {"short": "새 해설", "memory_version": 5}, headers=H))
    assert again["short"] == "새 해설" and again["memory_version"] == 5 and again["error"] == ""


def test_stale_endpoint(client):
    pid = new_plan(client)
    url = f"/docent/plans/{pid}/notes/kto-1"
    err(client.post(f"{url}/stale", headers=H), 404, "note_not_found")
    ok(client.put(url, json={"status": "pending"}, headers=H), 201)
    assert ok(client.post(f"{url}/stale", headers=H))["status"] == "pending"  # 아직 안 만든 노트는 그대로
    ok(client.put(url, json=READY_NOTE, headers=H))
    stale = ok(client.post(f"{url}/stale", headers=H))
    assert stale["status"] == "stale" and stale["short"] == READY_NOTE["short"]  # 현장에서는 그대로 쓴다


def test_memory_change_marks_notes_stale(client):
    headers = {"X-User-Id": "test-docent-stale"}
    p1, p2 = new_plan(client, headers=headers), new_plan(client, headers=headers)

    def put_ready(pid, place):
        resp = client.put(f"/docent/plans/{pid}/notes/{place}", json=READY_NOTE, headers=headers)
        assert resp.status_code in (200, 201), resp.text

    def statuses():
        return {(pid, n["place_id"]): n["status"]
                for pid in (p1, p2) for n in ok(client.get(f"/docent/plans/{pid}/notes", headers=headers))}

    for pid in (p1, p2):
        put_ready(pid, "a")
    ok(client.put(f"/docent/plans/{p1}/notes/b", json={"status": "preparing"}, headers=headers), 201)

    # food 는 노트 내용에 영향 없음 -> 그대로
    remember(client, "food", "회는 못 먹음", headers=headers)
    assert set(statuses().values()) == {"ready", "preparing"}

    # plan 범위 companion -> 그 계획의 ready 노트만
    remember(client, "companion", "아이(7살) 동행", scope="plan", plan_id=p1, headers=headers)
    assert statuses() == {(p1, "a"): "stale", (p1, "b"): "preparing", (p2, "a"): "ready"}

    # user 범위 style -> 모든 계획의 ready 노트
    put_ready(p1, "a")
    item = remember(client, "style", "핵심만 짧게", headers=headers)
    assert statuses() == {(p1, "a"): "stale", (p1, "b"): "preparing", (p2, "a"): "stale"}

    # 수정·삭제도 마찬가지
    put_ready(p2, "a")
    ok(client.patch(f"/docent/memory/{item['id']}", json={"text": "자세히"}, headers=headers))
    assert statuses()[(p2, "a")] == "stale"
    put_ready(p2, "a")
    ok(client.delete(f"/docent/memory/{item['id']}", headers=headers), 204)
    assert statuses()[(p2, "a")] == "stale"

    # food 항목을 interest 로 옮기면 바뀐 분류 기준으로 stale
    put_ready(p2, "a")
    food = next(m for m in ok(client.get("/docent/memory", headers=headers))["items"] if m["category"] == "food")
    ok(client.patch(f"/docent/memory/{food['id']}", json={"category": "interest"}, headers=headers))
    assert statuses()[(p2, "a")] == "stale"


def test_other_users_plan(client):
    theirs = new_plan(client, headers=H2)
    ok(client.put(f"/docent/plans/{theirs}/notes/kto-1", json=READY_NOTE, headers=H2), 201)
    err(client.get(f"/docent/plans/{theirs}/notes", headers=H), 404, "plan_not_found")
    err(client.get(f"/docent/plans/{theirs}/notes/kto-1", headers=H), 404, "plan_not_found")
    err(client.put(f"/docent/plans/{theirs}/notes/kto-1", json={"status": "pending"}, headers=H), 404, "plan_not_found")
    err(client.post(f"/docent/plans/{theirs}/notes/kto-1/stale", headers=H), 404, "plan_not_found")
    err(client.get("/docent/plans/999999999/notes", headers=H), 404, "plan_not_found")
    assert ok(client.get(f"/docent/plans/{theirs}/notes/kto-1", headers=H2))["status"] == "ready"


def test_planner_unreachable(client):
    dead = httpx.AsyncClient(base_url="http://127.0.0.1:9", timeout=1)
    saved = app.dependency_overrides[get_http]
    app.dependency_overrides[get_http] = lambda: dead
    try:
        err(client.get("/docent/plans/1/notes", headers=H), 503, "planner_unavailable")
        # 계획과 무관한 요청은 planner 없이 동작
        ok(client.get("/docent/memory", headers=H))
    finally:
        app.dependency_overrides[get_http] = saved
