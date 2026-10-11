"""가이드 자동 준비 (명세: docs/docent/README.md §3.2, docs/agent/README.md §12).

제안 승인·추천 추가 뒤 노트가 없는 장소를 백그라운드로 준비하고, 작업이 돌고 있으면 끝난 뒤 이어서 준비한다.
trip 은 여행 시작·도착 때 POST …/guide/prepare {"auto": true} 로 부른다. 가짜 LLM(대본) + test_docent_tools 의 가짜 places.

    docker compose run --rm agent pytest tests/agent/test_guide_auto.py
"""
import json
import time

import pytest

from app.agent import guide
from app.agent import router as agent_router
from app.agent.config import settings
from tests.agent import test_harness as th
from tests.agent.test_docent_tools import (  # noqa: F401  (픽스처)
    NOTE, PASSAGES, chat, new_session, note, prepare, up, user, wait_job,
)
from tests.agent.test_harness import _reset, client, last, ok  # noqa: F401  (픽스처)

FOOD = [{"id": 11, "kind": "place", "place_id": "p-food", "story_id": None, "title": "물회집",
         "text": "구룡포 앞바다에서 잡은 생선으로 물회를 만든다.", "score": 0.8}]
CAFE = [{"id": 12, "kind": "place", "place_id": "p-cafe", "story_id": None, "title": "카페",
         "text": "바다가 보이는 카페.", "score": 0.8}]


def note_json(src: str = "P1") -> list[str]:
    return [json.dumps(NOTE | {"sources": [src]}, ensure_ascii=False)]


@pytest.fixture(autouse=True)
def auto(monkeypatch):
    monkeypatch.setattr(settings, "guide_auto", True)
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


def test_auto_off_does_nothing(client, up, user, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "guide_auto", False)
    sid, pid = new_session(client, user, ("p-house",))
    p = propose_add(client, user, sid, "p-food")
    accept(client, user, sid, p)
    assert prepare(client, user, pid, {"auto": True})["total"] == 0
    wait_idle(pid)
    assert status(client, user, pid, "p-food") is None and status(client, user, pid, "p-house") is None
