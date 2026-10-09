"""사용자당 LLM 호출 수 제한 (app/agent/limits.py, docs/deploy/README.md §3.3). 각본 LLM 으로 돌린다."""
from sqlalchemy import delete

from app.agent.config import settings
from app.agent.models import AgentUsage
from app.db import SessionLocal
from tests.agent.test_guide import delay_event, trip_plan, trip_state, up  # noqa: F401  (픽스처)
from tests.agent.test_harness import USER, H, _reset, client, ok, session  # noqa: F401  (픽스처)
from tests.agent.test_scripted import chat, scripted  # noqa: F401  (픽스처)


def _clear():
    with SessionLocal.begin() as db:
        db.execute(delete(AgentUsage).where(AgentUsage.user_id.like("test-agent-%")))


def test_no_limit_by_default(client, session, scripted):  # noqa: F811
    _clear()
    sid, _ = session
    for _ in range(3):
        assert chat(client, sid, "점심 넣어줘")[-1][0] == "done"
    assert ok(client.get("/agent/limits", headers=H))["chat"]["hour"] == {"used": 3, "limit": None}


def test_chat_limit(client, session, scripted, monkeypatch):  # noqa: F811
    _clear()
    sid, _ = session
    monkeypatch.setattr(settings, "limit_chat_hour", 2)
    monkeypatch.setattr(settings, "limit_chat_day", 5)
    for _ in range(2):
        assert chat(client, sid, "점심 넣어줘")[-1][0] == "done"
    r = client.post(f"/agent/sessions/{sid}/chat", json={"message": "점심 넣어줘"}, headers=H)
    assert r.status_code == 429
    d = r.json()["detail"]
    assert d["code"] == "rate_limited" and d["kind"] == "chat" and d["limit"] == 2 and d["window"] == "1시간"
    assert 3500 < d["retry_after"] <= 3601 and "1시간 대화 한도(2회)" in d["message"]
    lim = ok(client.get("/agent/limits", headers=H))
    assert lim["chat"] == {"hour": {"used": 2, "limit": 2}, "day": {"used": 2, "limit": 5}}
    # 다른 사용자는 따로 센다
    other = {"X-User-Id": f"{USER}-other"}
    plan = ok(client.post("/planner/plans", json={"title": "t", "start_date": "2026-10-10"}, headers=other), 201)
    s2 = ok(client.post("/agent/sessions", json={"plan_id": plan["id"]}, headers=other), 201)
    assert client.post(f"/agent/sessions/{s2['id']}/chat", json={"message": "x"}, headers=other).status_code == 200
    # 하루 한도도 따로 본다
    monkeypatch.setattr(settings, "limit_chat_hour", 0)
    monkeypatch.setattr(settings, "limit_chat_day", 2)
    d = client.post(f"/agent/sessions/{sid}/chat", json={"message": "x"}, headers=H).json()["detail"]
    assert d["code"] == "rate_limited" and d["window"] == "하루"


def test_event_limit(client, trip_plan, up, scripted, monkeypatch):  # noqa: F811
    _clear()
    sid, pid, items = trip_plan
    up.trip = trip_state(pid, items)
    monkeypatch.setattr(settings, "limit_event_hour", 1)
    body = {"event": delay_event(pid, items), "trip": trip_state(pid, items)}
    assert ok(client.post(f"/agent/sessions/{sid}/events", json=body, headers=H))["error"] is None
    # 같은 이벤트 재시도는 이전 결과를 돌려주므로 세지 않는다
    assert ok(client.post(f"/agent/sessions/{sid}/events", json=body, headers=H))["error"] is None
    body["event"] = {**body["event"], "id": body["event"]["id"] + 1}
    r = client.post(f"/agent/sessions/{sid}/events", json=body, headers=H)
    assert r.status_code == 429 and r.json()["detail"]["kind"] == "event"
