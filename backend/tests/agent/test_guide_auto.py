"""가이드 자동 준비 (명세: docs/docent/README.md §3.2, docs/agent/README.md §12).

제안 승인·추천 추가 뒤 노트가 없는 장소를 백그라운드로 준비하고, 작업이 돌고 있으면 끝난 뒤 이어서 준비한다.
trip 은 여행 시작·도착 때 POST …/guide/prepare {"auto": true} 로 부른다. 가짜 LLM(대본) + test_docent_tools 의 가짜 places.

    docker compose run --rm agent pytest tests/agent/test_guide_auto.py
"""
import json
import time
from datetime import datetime

import pytest

from app.agent import guide
from app.agent import router as agent_router
from app.agent.config import settings
from tests.agent import test_harness as th
from tests.agent.test_docent_tools import (  # noqa: F401  (픽스처)
    NOTE, PASSAGES, chat, new_session, note, prepare, up, user, wait_job,
)
from tests.agent.test_harness import _reset, client, last, ok  # noqa: F401  (픽스처)

READY_NOTE = {"status": "ready", "short": "짧은 해설", "long": "긴 해설", "sources": [{"title": "포항시", "url": "https://www.pohang.go.kr/"}]}

FOOD = [{"id": 11, "kind": "place", "place_id": "p-food", "story_id": None, "title": "물회집",
         "text": "구룡포 앞바다에서 잡은 생선으로 물회를 만든다.", "score": 0.8}]
CAFE = [{"id": 12, "kind": "place", "place_id": "p-cafe", "story_id": None, "title": "카페",
         "text": "바다가 보이는 카페.", "score": 0.8}]


def note_json(src: str = "P1") -> list[str]:
    return [json.dumps(NOTE | {"sources": [src]}, ensure_ascii=False)]


def set_today(monkeypatch, y, m, d) -> None:
    """agent 의 '지금' (자동 준비가 지난 날을 판단하는 기준, 가상 시각)."""
    from app.agent import clock

    async def now(api):
        return datetime(y, m, d, 12, 0, tzinfo=clock.KST), True

    monkeypatch.setattr(clock, "now", now)


@pytest.fixture(autouse=True)
def auto(monkeypatch):
    monkeypatch.setattr(settings, "guide_auto", True)
    set_today(monkeypatch, 2026, 10, 10)  # 테스트 계획 날짜 (여행 당일)
    PASSAGES["p-food"], PASSAGES["p-cafe"] = FOOD, CAFE
    yield
    PASSAGES.pop("p-food", None)
    PASSAGES.pop("p-cafe", None)
    wait_idle()


def wait_until(cond, timeout=10):
    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end, "시간 초과"
        time.sleep(0.03)


def wait_idle(pid: int | None = None) -> None:
    """자동 준비 요청과 (pid 면) 그 계획의 작업·이어서 할 작업이 모두 끝날 때까지."""
    wait_until(lambda: not agent_router._auto_tasks
               and (pid is None or (guide.running_job(pid) is None and not guide.queued_places(pid))))


def status(client, h, pid, place) -> str | None:  # noqa: F811
    r = client.get(f"/docent/plans/{pid}/notes/{place}", headers=h)
    return r.json()["status"] if r.status_code == 200 else None


def jobs_of(pid: int) -> list[guide.GuideJob]:
    return sorted((j for j in guide._jobs.values() if j.plan_id == pid and j.places), key=lambda j: j.started_at)


def propose_add(client, h, sid, place) -> dict:  # noqa: F811
    return last(chat(client, h, sid, "추가", [[("add_item", {"place_id": place})], ["제안"]]), "proposal")


def accept(client, h, sid, p) -> dict:  # noqa: F811
    return ok(client.post(f"/agent/sessions/{sid}/proposals/{p['id']}/accept", headers=h))


def test_accept_prepares_only_new_places_in_background(client, up, user):  # noqa: F811
    sid, pid = new_session(client, user, ("p-house",))
    th.llm.script = [note_json()]
    wait_job(client, user, pid, prepare(client, user, pid)["job_id"])  # 가옥거리는 이미 ready
    assert status(client, user, pid, "p-house") == "ready"

    p = propose_add(client, user, sid, "p-food")
    gate: list = []

    async def hold():  # 노트를 쓰는 동안 멈춘다 (승인 응답이 준비를 기다리지 않는지 보려고)
        import anyio
        while not gate:
            await anyio.sleep(0.01)

    th.llm.script = [[hold, *note_json()]]
    calls = len(th.llm.calls)
    r = accept(client, user, sid, p)
    assert r["proposal"]["decision"] == "applied"
    wait_until(lambda: len(th.llm.calls) == calls + 1)  # 백그라운드 작업이 LLM 을 부르는 중
    assert status(client, user, pid, "p-food") == "preparing"  # 승인 응답은 이미 왔다
    gate.append(1)
    wait_idle(pid)
    assert status(client, user, pid, "p-food") == "ready" and len(th.llm.calls) == calls + 1
    [_, auto_job] = jobs_of(pid)
    assert [x["place_id"] for x in auto_job.places] == ["p-food"]  # 이미 ready 인 가옥거리는 건너뜀


def test_failed_note_is_not_retried_automatically(client, up, user):  # noqa: F811
    sid, pid = new_session(client, user, ("p-house",))
    th.llm.script = [["노트 대신 말"], ["또 말"]]  # JSON 을 못 받아 failed
    wait_job(client, user, pid, prepare(client, user, pid)["job_id"])
    assert status(client, user, pid, "p-house") == "failed"
    p = propose_add(client, user, sid, "p-cafe")
    th.llm.script = [note_json()]
    accept(client, user, sid, p)
    wait_idle(pid)
    assert status(client, user, pid, "p-cafe") == "ready" and status(client, user, pid, "p-house") == "failed"
    assert [x["place_id"] for x in jobs_of(pid)[-1].places] == ["p-cafe"]  # 실패는 수동 버튼으로만


def test_accept_while_job_running_follows_up_after_it(client, up, user):  # noqa: F811
    sid, pid = new_session(client, user, ("p-house",))
    gate: list = []

    async def hold():
        import anyio
        while not gate:
            await anyio.sleep(0.01)

    th.llm.script = [[hold, *note_json()]]
    first = prepare(client, user, pid)
    wait_until(lambda: len(th.llm.calls) == 1)
    p = propose_add(client, user, sid, "p-food")  # 대화는 다음 대본 (준비 작업은 이미 첫 대본을 꺼냈다)
    th.llm.script = [note_json()]
    accept(client, user, sid, p)
    wait_until(lambda: not agent_router._auto_tasks)
    assert status(client, user, pid, "p-food") == "pending"  # 앱에는 바로 '대기'
    view = ok(client.get(f"/agent/plans/{pid}/guide/jobs/{first['job_id']}", headers=user))
    assert view["status"] == "running" and view["queued"] == ["p-food"] and view["total"] == 1
    # 같은 요청이 또 와도(수동 버튼·trip) 이미 대기 중이라 겹치지 않는다
    again = prepare(client, user, pid)
    assert again["job_id"] == first["job_id"] and again["queued"] == ["p-food"]

    gate.append(1)
    wait_idle(pid)
    assert status(client, user, pid, "p-house") == "ready" and status(client, user, pid, "p-food") == "ready"
    [one, two] = jobs_of(pid)
    assert (one.id, [x["place_id"] for x in two.places]) == (first["job_id"], ["p-food"])
    assert wait_job(client, user, pid, first["job_id"])["queued"] == []


def test_accept_then_button_at_once_makes_one_job(client, up, user, monkeypatch):  # noqa: F811
    """승인 직후 [가이드 준비]가 바로 와도 (run_demo s1_accept) 작업은 하나. 노트 목록 조회 중에 둘이 겹치게 늦춘다."""
    from app.agent.api import ServiceApi
    slow = ServiceApi.list_notes

    async def list_notes(self, plan_id):
        import anyio
        await anyio.sleep(0.3)
        return await slow(self, plan_id)

    sid, pid = new_session(client, user, ("p-house",))
    p = propose_add(client, user, sid, "p-food")
    monkeypatch.setattr(ServiceApi, "list_notes", list_notes)
    th.llm.script = [note_json(), note_json(), note_json(), note_json()]
    accept(client, user, sid, p)  # 자동 준비가 노트 목록을 기다리는 중에
    manual = prepare(client, user, pid)  # 버튼
    wait_idle(pid)
    jobs = jobs_of(pid)
    assert len(jobs) == 1 and sorted(x["place_id"] for x in jobs[0].places) == ["p-food", "p-house"]
    assert manual["job_id"] == jobs[0].id or manual["total"] == 0
    assert len(th.llm.calls) == 2 + 2  # 대화 2번 + 장소 2곳 (같은 장소를 두 번 만들지 않음)


def test_recommendation_add_prepares_that_place(client, up, user):  # noqa: F811
    sid, pid = new_session(client, user, ("p-house",))
    th.llm.script = [note_json()]
    wait_job(client, user, pid, prepare(client, user, pid)["job_id"])
    rec = last(chat(client, user, sid, "카페", [[("recommend_places", {"title": "카페", "candidates": [
        {"place_id": "p-cafe", "reason": "바다"}]})], ["골라봤어요"]]), "recommendation")
    th.llm.script = [note_json()]
    base = f"/agent/sessions/{sid}/recommendations/{rec['id']}/candidates/{rec['candidates'][0]['id']}"
    ok(client.post(f"{base}/add", headers=user))
    wait_idle(pid)
    assert status(client, user, pid, "p-cafe") == "ready"
    n = len(th.llm.calls)
    ok(client.post(f"{base}/remove", headers=user))  # 빼기는 준비하지 않는다
    wait_idle(pid)
    assert len(th.llm.calls) == n and len(jobs_of(pid)) == 2


def test_limit_exceeded_skips_quietly_and_accept_succeeds(client, up, user, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "limit_guide_hour", 1)
    sid, pid = new_session(client, user, ("p-house",))
    th.llm.script = [note_json()]
    wait_job(client, user, pid, prepare(client, user, pid)["job_id"])  # 한도 1회를 씀
    p = propose_add(client, user, sid, "p-food")
    n = len(th.llm.calls)
    r = accept(client, user, sid, p)
    assert r["proposal"]["decision"] == "applied"
    wait_idle(pid)
    assert status(client, user, pid, "p-food") is None and len(th.llm.calls) == n  # 조용히 건너뜀 (pending 도 남기지 않음)
    # trip 의 자동 요청은 429 를 받고 로그만 남긴다
    d = ok(client.post(f"/agent/plans/{pid}/guide/prepare", json={"auto": True}, headers=user), 429)["detail"]
    assert d["code"] == "rate_limited" and d["kind"] == "guide"


def test_auto_endpoint_for_trip(client, up, user):  # noqa: F811
    """trip 의 여행 시작(일정 전체)·도착(그 장소): 노트가 없는 곳만, 일정에 없는 장소는 무시 (422 아님)."""
    sid, pid = new_session(client, user, ("p-house", "p-food", "p-cafe"))
    th.llm.script = [["말만"], ["또 말만"]]  # 가옥거리 failed
    wait_job(client, user, pid, prepare(client, user, pid, {"place_ids": ["p-house"]})["job_id"])
    th.llm.script = [note_json()]
    wait_job(client, user, pid, prepare(client, user, pid, {"place_ids": ["p-cafe"]})["job_id"])  # 카페 ready

    th.llm.script = [note_json()]
    started = prepare(client, user, pid, {"auto": True})  # 여행 시작
    assert [x["place_id"] for x in started["places"]] == ["p-food"]
    wait_idle(pid)
    assert status(client, user, pid, "p-food") == "ready"

    n = len(th.llm.calls)
    arrived = prepare(client, user, pid, {"auto": True, "place_ids": ["p-cafe", "p-nowhere"]})  # 도착 (이미 ready)
    assert arrived["status"] == "done" and arrived["total"] == 0
    assert prepare(client, user, pid, {"auto": True, "place_ids": ["p-house"]})["total"] == 0  # failed 는 다시 안 함
    assert len(th.llm.calls) == n


def hold_gate() -> tuple[list, object]:
    """대본에 넣으면 gate 에 값이 들어올 때까지 LLM 응답을 멈춘다."""
    gate: list = []

    async def hold():
        import anyio
        while not gate:
            await anyio.sleep(0.01)

    return gate, hold


def test_leftover_pending_after_restart_is_prepared_again(client, up, user):  # noqa: F811
    """agent 가 다시 시작되면 작업 상태(프로세스 메모리)는 사라지고 노트는 pending·preparing 으로 남는다.
    자동 준비는 지금 준비 중인 작업이 없는 pending·preparing 노트를 다시 대상으로 삼는다 (영구 고착 방지)."""
    sid, pid = new_session(client, user, ("p-house", "p-food"))
    ok(client.put(f"/docent/plans/{pid}/notes/p-house", json={"status": "preparing"}, headers=user), 201)
    ok(client.put(f"/docent/plans/{pid}/notes/p-food", json={"status": "pending"}, headers=user), 201)
    assert guide.running_job(pid) is None  # 재시작 뒤: 이 프로세스에는 작업이 없다
    th.llm.script = [note_json(), note_json()]
    started = prepare(client, user, pid, {"auto": True})
    assert [x["place_id"] for x in started["places"]] == ["p-house", "p-food"]
    wait_idle(pid)
    assert status(client, user, pid, "p-house") == "ready" and status(client, user, pid, "p-food") == "ready"


def test_auto_does_not_duplicate_place_being_prepared(client, up, user):  # noqa: F811
    """지금 준비 중인(이 프로세스의 작업에 있는) pending·preparing 은 자동 준비가 다시 넣지 않는다."""
    sid, pid = new_session(client, user, ("p-house",))
    gate, hold = hold_gate()
    th.llm.script = [[hold, *note_json()]]
    first = prepare(client, user, pid)
    wait_until(lambda: len(th.llm.calls) == 1)
    assert status(client, user, pid, "p-house") == "preparing"
    again = prepare(client, user, pid, {"auto": True})
    assert again["job_id"] == first["job_id"] and again["queued"] == []
    gate.append(1)
    wait_idle(pid)
    assert len(jobs_of(pid)) == 1 and len(th.llm.calls) == 1


def two_day_plan(client, h) -> tuple[int, dict]:  # noqa: F811
    """10/10(1일차 가옥거리) ~ 10/11(2일차 물회집·카페). {place_id: item_id}"""
    plan = ok(client.post("/planner/plans", json={"title": "이틀", "start_date": "2026-10-10", "end_date": "2026-10-11",
                                                  "day_start": "10:00", "day_end": "18:00"}, headers=h), 201)
    for place, day in (("p-house", 1), ("p-food", 2), ("p-cafe", 2)):
        detail = ok(client.post(f"/planner/plans/{plan['id']}/items", json={"place_id": place, "day": day}, headers=h), 201)
    return plan["id"], {i["place_id"]: i["id"] for d in detail["days"] for i in d["items"]}


def test_auto_skips_done_items_and_past_days(client, up, user, monkeypatch):  # noqa: F811
    """승인·추천 추가·여행 시작 뒤 자동 준비는 다녀온(done) 항목과, 여행 기간 중이면 지난 날의 장소(노트가 stale 이어도)는
    다시 만들지 않는다. 수동 [가이드 준비]는 그대로 모두 대상."""
    pid, items = two_day_plan(client, user)
    set_today(monkeypatch, 2026, 10, 11)  # 2일차
    for place in ("p-house", "p-cafe"):  # 관심사가 바뀌어 stale 이 된 노트
        url = f"/docent/plans/{pid}/notes/{place}"
        ok(client.put(url, json=READY_NOTE | {"memory_version": None}, headers=user), 201)
        assert ok(client.post(f"{url}/stale", headers=user))["status"] == "stale"
    ok(client.post(f"/planner/plans/{pid}/items/{items['p-cafe']}/progress", json={"status": "done", "at": "10:30"},
                   headers=user))
    th.llm.script = [note_json()]
    started = prepare(client, user, pid, {"auto": True})
    assert [x["place_id"] for x in started["places"]] == ["p-food"]  # 1일차(지난 날)·카페(done) 제외
    wait_idle(pid)
    assert status(client, user, pid, "p-house") == "stale" and status(client, user, pid, "p-cafe") == "stale"

    th.llm.script = [note_json(), note_json()]  # 수동 버튼은 그대로 (ready 가 아닌 곳 모두)
    manual = prepare(client, user, pid)
    assert sorted(x["place_id"] for x in manual["places"]) == ["p-cafe", "p-house"]
    wait_idle(pid)


def test_plan_places_auto_filter():
    days = [{"date": "2026-10-10", "items": [{"place_id": "a", "status": "done"}, {"place_id": "b", "status": "planned"}]},
            {"date": "2026-10-11", "items": [{"place_id": "a", "status": "planned"}, {"place_id": "c", "status": "done"},
                                             {"place_id": "d", "status": "skipped"}]}]
    plan = {"days": days}
    ids = lambda **kw: [p["place_id"] for p in guide.plan_places(plan, **kw)]  # noqa: E731
    assert ids() == ["a", "b", "c"]  # 수동: 건너뜀만 제외
    assert ids(auto=True, today="2026-10-10") == ["b", "a"]  # done 제외 (a 는 2일차에 다시 감)
    assert ids(auto=True, today="2026-10-11") == ["a"]  # 지난 날(1일차) 제외
    assert ids(auto=True, today="2026-10-09") == ids(auto=True, today="2026-10-20") == ["b", "a"]  # 기간 밖: 날짜로 빼지 않음


def test_memory_change_while_preparing(client, up, user):  # noqa: F811
    """장면 ② «다음부터는 더 짧게 해줘요» 가 가이드 준비 도중에 오면: 그때 쓰던 장소는 stale 로 저장되고(다시 준비 대상),
    남은 장소는 새 메모리로 만든다 (메모리 요약을 장소마다 다시 받는다)."""
    sid, pid = new_session(client, user, ("p-house", "p-food"))
    gate, hold = hold_gate()
    th.llm.script = [[hold, *note_json()], note_json()]
    prepare(client, user, pid)
    wait_until(lambda: len(th.llm.calls) == 1)  # 가옥거리를 쓰는 중
    ok(client.post("/docent/memory", json={"scope": "user", "category": "style", "text": "다음부터는 더 짧게",
                                           "source": "user_edit"}, headers=user), 201)
    gate.append(1)
    wait_idle(pid)
    assert status(client, user, pid, "p-house") == "stale"  # 옛 메모리로 만든 노트 -> 다시 준비 대상
    assert status(client, user, pid, "p-food") == "ready"
    assert "다음부터는 더 짧게" not in th.llm.calls[0][1]["content"]
    assert "다음부터는 더 짧게" in th.llm.calls[1][1]["content"]  # 남은 장소는 새 메모리로
    version = ok(client.get("/docent/memory", headers=user))["memory_version"]
    assert ok(client.get(f"/docent/plans/{pid}/notes/p-food", headers=user))["memory_version"] == version


def test_delete_plan_removes_chat_notes_plan_memory_and_stops_job(client, up, user):  # noqa: F811
    """계획 삭제(앱 확인창: "일정과 AI 대화가 모두 사라져요"): planner 가 지운 뒤 agent 대화·가이드 준비 작업,
    docent 노트·이번 여행 메모리를 지운다. 다른 계획·user 범위 메모리는 그대로."""
    from app.planner.cleanup import PlanCleaner, get_plan_cleaner
    from app.main import app

    sid, pid = new_session(client, user, ("p-house", "p-food"))
    other_sid, other = new_session(client, user, ("p-house",))
    ok(client.post("/docent/memory", json={"scope": "plan", "plan_id": pid, "category": "companion", "text": "어머니 동행",
                                           "source": "user_edit"}, headers=user), 201)
    ok(client.post("/docent/memory", json={"scope": "user", "category": "interest", "text": "역사",
                                           "source": "user_edit"}, headers=user), 201)
    ok(client.put(f"/docent/plans/{other}/notes/p-house", json={"status": "pending"}, headers=user), 201)
    gate, hold = hold_gate()
    th.llm.script = [[hold, *note_json()], note_json()]
    job = prepare(client, user, pid)
    wait_until(lambda: len(th.llm.calls) == 1)

    app.dependency_overrides[get_plan_cleaner] = lambda: PlanCleaner("http://app", client=client)
    try:
        assert client.delete(f"/planner/plans/{pid}", headers=user).status_code == 204
    finally:
        app.dependency_overrides.pop(get_plan_cleaner, None)
    wait_until(lambda: guide.get_job(job["job_id"]).status == "done")  # 진행 중인 작업을 멈춤
    gate.append(1)
    assert len(th.llm.calls) == 1  # 남은 장소(물회집)는 만들지 않는다
    assert [s["id"] for s in ok(client.get("/agent/sessions", headers=user))] == [other_sid]
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.docent.models import DocentGuideNote, DocentMemory
    with SessionLocal() as s:
        uid = user["X-User-Id"]
        assert s.scalars(select(DocentGuideNote.plan_id).where(DocentGuideNote.user_id == uid)).all() == [other]
        assert s.scalars(select(DocentMemory.text).where(DocentMemory.user_id == uid)).all() == ["역사"]


def test_delete_plan_cleanup_failure_keeps_deletion(client, up, user):  # noqa: F811
    """정리 호출이 실패해도(서비스 접근 불가) 계획 삭제는 그대로 204 (로그만)."""
    sid, pid = new_session(client, user, ("p-house",))
    assert client.delete(f"/planner/plans/{pid}", headers=user).status_code == 204  # 테스트 기본: 닫힌 포트로 정리 -> 실패
    assert client.get(f"/planner/plans/{pid}", headers=user).status_code == 404


def test_auto_off_does_nothing(client, up, user, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "guide_auto", False)
    sid, pid = new_session(client, user, ("p-house",))
    p = propose_add(client, user, sid, "p-food")
    accept(client, user, sid, p)
    assert prepare(client, user, pid, {"auto": True})["total"] == 0
    wait_idle(pid)
    assert status(client, user, pid, "p-food") is None and status(client, user, pid, "p-house") is None
