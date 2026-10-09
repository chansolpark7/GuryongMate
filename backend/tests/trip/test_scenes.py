"""재생 파일(scripts/trip/scenes/*.json)을 서버 없이 재생한다 — CLI `trip replay` 와 같은 펼침(scripts/trip/scene.py)을 쓰고,
planner·safety·agent 는 가짜로 바꾼다 (docs/trip/README.md §8). 컨테이너에서는 compose 가 scripts/ 를 /scripts 로 마운트한다.
"""
import importlib.util
import json
from datetime import timedelta
from pathlib import Path

import pytest

from .conftest import H, ok
from .fakes import PLACES

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts" / "trip"
assert (SCRIPTS / "scene.py").exists(), f"{SCRIPTS} 가 없습니다 (compose 의 ./scripts:/scripts 마운트 확인)"
_spec = importlib.util.spec_from_file_location("trip_scene", SCRIPTS / "scene.py")
scene = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scene)


def coords(place_id: str) -> tuple[float, float]:
    return PLACES[place_id]["lat"], PLACES[place_id]["lng"]


def replay(env, name: str) -> tuple[dict, list[dict]]:
    """재생하고 (재생 파일, 마지막 이벤트 목록). expect 가 어긋나면 그 자리에서 실패."""
    data = json.loads((SCRIPTS / "scenes" / name).read_text(encoding="utf-8"))
    env.plan_id = env.planner.create("test-trip-user", data["plan"])
    started = False
    for a in scene.expand(data, coords):
        env.clock.when = a.at
        if a.kind == "sim":
            changed = a.data.get("scenario", env.safety.scenario) != env.safety.scenario
            env.safety.scenario = a.data.get("scenario", env.safety.scenario)
            if changed:
                env.planner.on_scenario(env.safety.scenario)  # planner 주기 점검이 남길 경고
            if not started:
                ok(env.c.post(env.url("/start"), headers=H))
                started = True
        elif a.kind == "loc":
            ok(env.c.post(env.url("/location"), headers=H, json={
                "lat": a.lat, "lng": a.lng, "accuracy": 10, "recorded_at": a.at.isoformat(), "source": "replay"}))
        elif a.kind == "tick":
            ok(env.c.post(env.url("/tick"), headers=H))
        elif a.kind == "expect":
            events = env.events()
            assert scene.matches(a.data, events), (
                f"{a.at:%H:%M:%S} 기대 {a.data}, 실제 {[(e['type'], e['now'][11:16]) for e in events]}")
    return data, env.events()


def test_scene3_delay(env):
    _, events = replay(env, "scene3_delay.json")
    delays = [e for e in events if e["type"] == "delay"]
    assert len(delays) == 1  # 같은 지연으로 두 번 나오지 않는다
    d = delays[0]
    assert d["status"] == "delivered" and d["agent"]["proposal_id"]
    assert {v["code"] for v in d["payload"]["new_violations"]} == {"outside_open_hours"}
    assert "15:40" < d["now"][11:16] < "15:50"  # 가옥거리에 15:40 넘게 머물면 역사관 17:30 마감을 넘는다
    assert [r[1]["type"] for r in env.agent.received] == ["delay"]
    kinds = [e["type"] for e in events]
    assert kinds.count("arrived") == 4 and kinds.count("departed") == 3


def test_scene4_safety(env):
    _, events = replay(env, "scene4_safety.json")
    raised = [e for e in events if e["type"] == "safety_raised"]
    assert len(raised) == 1 and raised[0]["status"] == "delivered" and raised[0]["severity"] == "danger"
    hazard = next(e for e in events if e["type"] == "near_hazard")
    assert hazard["payload"]["zones"][0]["id"] == "flood_4" and hazard["level"] == 1
    # 선제 턴은 안전 경고 하나뿐 (near_hazard 는 LLM 없이 고정 문구)
    assert [r[1]["type"] for r in env.agent.received] == ["safety_raised"]
    assert env.agent.received[0][2]["current"]["state"] == "visiting"  # 짬홍에 있을 때


@pytest.mark.parametrize("name", sorted(p.name for p in (SCRIPTS / "scenes").glob("*.json")))
def test_scene_files_are_valid(name):
    data = json.loads((SCRIPTS / "scenes" / name).read_text(encoding="utf-8"))
    assert {"name", "plan", "start", "steps"} <= data.keys()
    actions = scene.expand(data, coords)
    times = [a.at for a in actions]
    assert times == sorted(times)
    assert any(a.kind == "expect" for a in actions)


def test_expand_walk_points():
    data = {"start": "2026-10-10T12:00:00+09:00",
            "steps": [{"goto": "kto-2834330"}, {"walk_to": "kto-2610501"}, {"stay": 10}, {"at": "+30"}, {"tick": True}]}
    acts = scene.expand(data, coords)
    locs = [a for a in acts if a.kind == "loc"]
    walk = locs[1:]
    # 367m 를 70m/분, 35m(30초) 간격으로
    first_stay = next(i for i, a in enumerate(walk) if (a.lat, a.lng) == coords("kto-2610501"))
    assert 10 <= first_stay <= 11
    assert (walk[first_stay].at - locs[0].at).total_seconds() == pytest.approx(367 / 70 * 60, abs=10)
    assert acts[-1].kind == "tick" and acts[-1].at.strftime("%H:%M") == "12:30"
    # 머무름: 처음 3분은 30초마다, 그 뒤 5분마다
    stay = [a.at for a in walk[first_stay:]]
    gaps = [(b - a).total_seconds() for a, b in zip(stay, stay[1:])]
    assert gaps[:6] == [30] * 6 and max(gaps) == 300  # 단계 끝에서는 남은 만큼만
    assert stay[-1] == locs[0].at + timedelta(minutes=30)  # at +30 까지 같은 곳


def test_matches():
    events = [{"type": "arrived", "level": 2, "payload": {"place_id": "kto-1"}}]
    assert scene.matches({"type": "arrived", "place_id": "kto-1"}, events)
    assert not scene.matches({"type": "arrived", "place_id": "kto-2"}, events)
    assert scene.matches({"type": "delay", "not": True}, events)
    assert not scene.matches({"type": "arrived", "not": True}, events)
