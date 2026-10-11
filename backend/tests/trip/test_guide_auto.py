"""가이드 자동 준비의 trip 쪽 (docs/docent/README.md §3.2, docs/trip/README.md §1.2·§3.2·§4.3).

여행 시작 뒤 일정 전체, 도착 뒤 그 장소, 틱에서 오늘 남은 장소 중 노트가 stale 인 곳(앱 활동 중일 때만)으로 agent 에 POST /agent/plans/{id}/guide/prepare (auto) 를 응답 뒤에 부른다.
노트가 있는지는 agent 가 거른다. 실패해도 여행은 그대로 (로그만).

    docker compose run --rm trip pytest tests/trip/test_guide_auto.py
"""
from app.core.auth import decode_token
from app.trip import service
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


# ---------- 틱: 관심사가 바뀐 뒤 남은 장소 노트를 미리 다시 준비 (§4.3, docs/docent/README.md §3.2) ----------

HOUSE, GWAMEGI = "kto-1909659", "kto-2660046"


def _at_port(env) -> None:
    """짬홍 다녀옴(done) -> 구룡포항 머무는 중(visiting). 가옥거리·과메기문화관·역사관은 planned."""
    env.start("12:20")
    env.stay(LUNCH, "12:20", 2)
    env.stay(PORT, "13:40", 2)
    assert env.state()["current"]["state"] == "visiting"
    env.agent.guide_calls.clear()


def test_tick_prepares_stale_notes_of_remaining_places(env):
    _at_port(env)
    env.docent.notes[env.plan_id] = {LUNCH: "stale", PORT: "stale", HOUSE: "stale", GWAMEGI: "ready"}
    env.tick("13:45")
    [(plan_id, place_ids, auth)] = env.agent.guide_calls
    # 지난(done) 짬홍은 빼고, 머무는 곳·남은 곳 중 stale 만 일정 순서대로. ready·노트 없음(역사관)은 빼고
    assert (plan_id, place_ids) == (env.plan_id, [PORT, HOUSE]) and user_of(auth) == USER
    env.docent.notes[env.plan_id] |= {PORT: "pending", HOUSE: "pending"}  # agent 가 바로 pending 으로 표시
    env.tick("13:46")
    assert len(env.agent.guide_calls) == 1  # 반복 호출 없음


def test_tick_without_stale_notes_does_not_prepare(env):
    _at_port(env)
    env.docent.notes[env.plan_id] = {LUNCH: "stale", PORT: "ready", HOUSE: "failed"}  # 지난 장소만 stale
    env.tick("13:45")
    assert env.agent.guide_calls == []


def test_tick_with_app_off_does_not_prepare(env):
    _at_port(env)
    env.docent.notes[env.plan_id] = {HOUSE: "stale"}
    env.docent.calls = 0
    env.tick("14:30", app=False)  # 마지막 활동(13:42)에서 15분 넘게 지남 -> 앱 꺼짐
    assert env.agent.guide_calls == [] and env.docent.calls == 0  # 노트도 보지 않는다
    env.tick("14:31")  # 앱이 돌아오면
    assert [ids for _, ids, _ in env.agent.guide_calls] == [[HOUSE]]


def test_tick_prepare_failure_is_logged_and_waits(env):
    _at_port(env)
    env.docent.notes[env.plan_id] = {HOUSE: "stale"}
    env.agent.guide_error = UpstreamError("agent", 429, {"code": "rate_limited", "message": "한도"})
    tick = env.tick("13:45")
    assert tick["state"]["active"] is True  # 틱은 그대로
    env.tick("13:46")
    assert len(env.agent.guide_calls) == 1  # 실패 뒤 잠시(REFRESH_RETRY_SEC) 다시 묻지 않는다
    env.docent.down = True
    env.agent.guide_error = None
    service._refresh_wait.clear()
    env.tick("13:47")  # docent 에 접근할 수 없어도 틱은 그대로, 준비 요청 없음
    assert len(env.agent.guide_calls) == 1


def test_tick_all_prepares_stale_notes(env):
    _at_port(env)
    env.docent.notes[env.plan_id] = {HOUSE: "stale"}
    env.set_active("13:45")
    env.clock.when = env.clock.when.replace(hour=13, minute=45)
    assert service.tick_all(env.deps, env.clock, users=[USER]) == 1
    assert [ids for _, ids, _ in env.agent.guide_calls] == [[HOUSE]]
