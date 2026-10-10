"""가이드 자동 준비의 trip 쪽 (docs/docent/README.md §3.2, docs/trip/README.md §1.2·§3.2).

여행 시작 뒤 일정 전체, 도착 뒤 그 장소로 agent 에 POST /agent/plans/{id}/guide/prepare (auto) 를 응답 뒤에 부른다.
노트가 있는지는 agent 가 거른다. 실패해도 여행은 그대로 (로그만).

    docker compose run --rm trip pytest tests/trip/test_guide_auto.py
"""
from app.core.auth import decode_token
from app.trip.clients import UpstreamError

from .conftest import H, USER, err, ok

LUNCH, PORT = "kto-2834330", "kto-2610501"


def user_of(auth: dict) -> str:
    return decode_token(auth["Authorization"].removeprefix("Bearer "))


def test_start_requests_guide_for_whole_plan(env):
    s = env.start("12:20")
    assert s["active"] is True
    [(plan_id, place_ids, auth)] = env.agent.guide_calls
    assert (plan_id, place_ids) == (env.plan_id, None)  # 일정 전체 (노트가 없는 곳만은 agent 가 거른다)
    assert user_of(auth) == USER  # 선제 턴과 같은 그 사용자의 짧은 토큰


def test_arrival_requests_guide_for_that_place(env):
    env.start("12:20")
    env.agent.guide_calls.clear()
    events = env.stay(LUNCH, "12:20", 2)
    assert [e["type"] for e in events] == ["arrived"]
    assert [(p, ids) for p, ids, _ in env.agent.guide_calls] == [(env.plan_id, [LUNCH])]  # 도착 한 번에 한 번
    env.stay(PORT, "13:40", 2)  # 떠나서 다음 장소 도착
    assert [ids for _, ids, _ in env.agent.guide_calls] == [[LUNCH], [PORT]]


def test_agent_failure_does_not_affect_trip(env):
    env.agent.guide_error = UpstreamError("agent", None, "connection refused")
    s = env.start("12:20")
    assert s["active"] is True and env.types() == ["day_started"]
    events = env.stay(LUNCH, "12:20", 2)
    assert [e["type"] for e in events] == ["arrived"] and env.state()["current"]["state"] == "visiting"
    env.agent.guide_error = UpstreamError("agent", 429, {"code": "rate_limited", "message": "한도"})
    assert env.start("12:30")["active"] is True  # 멱등 시작도 그대로 (요청은 다시 하지만 실패는 무시)
    assert len(env.agent.guide_calls) == 3


def test_failed_start_does_not_request_guide(env):
    env.clock.when = env.clock.when.replace(day=9)
    err(env.c.post(env.url("/start"), headers=H), 409, "not_trip_day")
    assert env.agent.guide_calls == []
    ok(env.c.get(env.url("/state"), headers=H))
