"""재생 파일(scripts/trip/scenes/*.json)을 서버 없이 재생한다 — CLI `trip replay` 와 같은 펼침(scripts/trip/scene.py)을 쓰고,
planner·safety·agent 는 가짜로 바꾼다 (docs/trip/README.md §8). 컨테이너에서는 compose 가 scripts/ 를 /scripts 로 마운트한다.
"""
import importlib.util
import json
from datetime import timedelta
from pathlib import Path

import pytest

from app.trip import judge

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
        elif a.kind == "skip":
            env.planner.skip(env.plan_id, a.data["place_id"])
        elif a.kind == "accept":  # 가짜 agent 는 제안을 만들지 않으므로 기대하는 변경을 가짜 planner 에 그대로
            env.planner.replace(env.plan_id, a.data["remove"], a.data["add"])
        elif a.kind == "tick":
            ok(env.c.post(env.url("/tick"), headers=H))
        elif a.kind == "expect":
            events = env.events()
            assert scene.matches(a.data, events), (
                f"{a.at:%H:%M:%S} 기대 {a.data}, 실제 {[(e['type'], e['now'][11:16]) for e in events]}")
    return data, env.events()


def test_scene2_arrival_card(env):
    _, events = replay(env, "scene2_arrival.json")
    [e] = [e for e in events if e["type"] == "arrived"]
    assert (e["level"], e["status"], e["now"][11:16]) == (2, "delivered", "12:30")  # 반경에 처음 들어온 시각
    assert e["card"]["kind"] == "docent" and e["card"]["place_id"] == "kto-1909659"
    assert env.agent.received == []  # LLM 없이


def play(env, start: str, steps: list[dict]) -> list[dict]:
    """재생 파일 형식의 단계를 그 시각부터 이어서 보낸다 (위치만). 마지막 이벤트 목록."""
    for a in scene.expand({"start": f"2026-10-10T{start}:00+09:00", "steps": steps}, coords):
        if a.kind == "loc":
            env.clock.when = a.at
            ok(env.c.post(env.url("/location"), headers=H, json={
                "lat": a.lat, "lng": a.lng, "accuracy": 10, "recorded_at": a.at.isoformat(), "source": "replay"}))
    return env.events()


def test_scene3_delay(env):
    """데모 장면 ③ (docs/demo/README.md): 가옥거리에 오래 머묾 -> 13:45 일정 밀림 선제 턴 한 번 -> «과메기문화관은 유지해줘» 로
    고친 제안 승인(가옥거리를 추천안만큼 늘리고 구룡포항 30분) -> 이동. 승인한 일정 기준으로 새로 세므로 그 뒤 선제 턴은 없다."""
    _, events = replay(env, "scene3_delay.json")
    [d] = [e for e in events if e["type"] == "delay"]
    assert d["status"] == "delivered" and d["agent"]["proposal_id"]
    assert d["payload"]["reason"] == "behind_schedule" and d["payload"]["new_violations"] == []
    assert d["now"][11:16] == "13:45" and d["payload"]["delay_minutes"] == 15  # 18:00 초과(13:53)보다 먼저
    sg = d["payload"]["suggest"]
    assert sg["kind"] == "extend_stay" and sg["title"] == "구룡포 일본인 가옥거리" and sg["stay_minutes"] == 90
    assert [r[1]["type"] for r in env.agent.received] == ["delay"]
    # 대화로 고친 제안(id 가 선제 턴 제안보다 큼)을 승인
    env.planner.accept(env.plan_id, d["agent"]["proposal_id"] + 1, {"kto-1909659": sg["stay_minutes"], "kto-2610501": 30})
    events = play(env, "13:48", [{"goto": "kto-1909659"}, {"at": "14:00"}, {"walk_to": "kto-2610501", "stay": 30},
                                 {"walk_to": "kto-2660046", "stay": 5}, {"at": "15:18"}])
    assert [e["type"] for e in events if e["type"] == "delay"] == ["delay"]  # 두 번째 지연 알림 없음
    assert [r[1]["type"] for r in env.agent.received] == ["delay"]
    arrived = [e["payload"]["place_id"] for e in events if e["type"] == "arrived"]
    assert arrived == ["kto-1909659", "kto-2610501", "kto-2660046"]
    assert env.state()["delay_minutes"] <= 0  # 승인한 일정보다 늦지 않음


def test_scene4_safety(env):
    _, events = replay(env, "scene4_safety.json")
    raised = [e for e in events if e["type"] == "safety_raised"]
    assert len(raised) == 1 and raised[0]["status"] == "delivered" and raised[0]["now"][11:16] == "15:20"
    change = raised[0]["payload"]["changes"][0]
    assert change["item"]["place_id"] == "poi-gr-breakwater-north" and change["to_risk"] == "danger"  # 북방파제 위험
    hazard = next(e for e in events if e["type"] == "near_hazard")
    assert hazard["payload"]["zones"][0]["id"] == "flood_4" and hazard["level"] == 1
    assert hazard["now"] > raised[0]["now"]  # 카페로 가는 길
    # 선제 턴은 안전 경고 하나뿐 (near_hazard 는 LLM 없이 고정 문구)
    assert [r[1]["type"] for r in env.agent.received] == ["safety_raised"]
    assert env.agent.received[0][2]["current"]["state"] == "visiting"  # 과메기문화관에 있을 때
    # 제안 승인(북방파제 -> 근대문화역사관) 뒤 역사관에 순서대로 도착하고 카페로
    museum = next(e for e in events if e["type"] == "arrived" and e["payload"]["place_id"] == "kto-2603493")
    cafe = next(e for e in events if e["type"] == "arrived" and e["payload"]["place_id"] == "kto-2834344")
    assert museum["payload"]["out_of_order"] is False and cafe["payload"]["out_of_order"] is False
    assert museum["now"] < hazard["now"] < cafe["now"]
    assert "delay" not in [e["type"] for e in events]


HOUSE, PORT, MUSEUM, BREAKWATER, CAFE, HISTORY = ("kto-1909659", "kto-2610501", "kto-2660046", "poi-gr-breakwater-north",
                                                   "kto-2834344", "kto-2603493")


def walk_times(order: list[str], parts: list[list[dict]], swap: tuple[int, str] | None = None) -> list[tuple]:
    """진행 스크립트(scripts/demo/run_demo.py)가 보내는 위치 열을 판정에 그대로 넣는다 (경로선 대신 직선, 가짜 서비스 없이).
    시작: 12:25, 구룡포항 -> 가옥거리 길에서 가옥거리 420m 앞. parts: run_demo 단계마다의 재생 단계. swap: (그 단계 뒤, 바꿀 장소)."""
    spots = [judge.Spot(i, i, p, p, *coords(p), "planned") for i, p in enumerate(order, start=1)]
    track, cfg, out = judge.Track(), judge.TrackConfig(), []
    house, port = coords(HOUSE), coords(PORT)
    f = 420 / scene._dist(port, house)
    pos, t = [house[0] + (port[0] - house[0]) * f, house[1] + (port[1] - house[1]) * f], "2026-10-10T12:25:00+09:00"
    for n, steps in enumerate([[]] + parts):
        for a in scene.expand({"start": t, "steps": [{"goto": pos}] + steps}, coords)[1:]:
            if a.kind != "loc":
                continue
            p = judge.Point(a.lat, a.lng, 10, a.at)
            if judge.ignore_reason(track, p, cfg) is None:
                track, dets = judge.step(track, spots, p, cfg)
                spots = judge.apply_progress(spots, dets)
                out += [(d.type, d.payload["title"], f"{d.at:%H:%M:%S}") for d in dets]
            pos, t = [a.lat, a.lng], a.at.isoformat()
        if swap and swap[0] == n:  # 제안 승인: 북방파제 -> 근대문화역사관 (같은 순서)
            spots = [judge.replace(s, item_id=99, title=swap[1], place_id=swap[1], lat=coords(swap[1])[0],
                                   lng=coords(swap[1])[1]) if s.title == BREAKWATER else s for s in spots]
    return out


def test_demo_walk_arrival_departure_times():
    """T1(멈춤 판정) 전후로 데모 진행 스크립트의 도착·출발 시각이 같다 (logic-bugs-2026-10-12 T1, 수정 전 값으로 고정).
    장면 ②~④: 가옥거리 3분 → 13:47 까지 → 14:00 구룡포항 30분 → 과메기문화관 → 15:25 승인 → 15:47 역사관 40분 → 차로 수협 앞 → 카페."""
    parts = [[{"walk_to": HOUSE, "stay": 3}], [{"at": "13:45"}, {"at": "13:47"}],
             [{"at": "14:00"}, {"walk_to": PORT, "stay": 30}, {"walk_to": MUSEUM, "stay": 5}, {"at": "15:18"}],
             [{"at": "15:25"}], [{"at": "15:47", "walk_to": HISTORY, "stay": 40}], [{"drive_to": [35.9912, 129.5572]}],
             [{"drive_to": CAFE, "stay": 5}], [{"at": "17:05"}]]
    got = walk_times([HOUSE, PORT, MUSEUM, BREAKWATER, CAFE], parts, swap=(4, HISTORY))
    assert got == [("arrived", HOUSE, "12:29:56"), ("departed", HOUSE, "14:01:27"), ("arrived", PORT, "14:05:53"),
                   ("departed", PORT, "14:37:25"), ("arrived", MUSEUM, "14:45:17"), ("departed", MUSEUM, "15:48:27"),
                   ("arrived", HISTORY, "15:49:57"), ("departed", HISTORY, "16:30:51"), ("arrived", CAFE, "16:35:25")]
    # 가까운 두 장소 (가옥거리 -> 근대문화역사관 104m, 반경이 겹침): 암묵 전환 시각도 그대로
    got = walk_times([HOUSE, HISTORY, PORT], [[{"walk_to": HOUSE, "stay": 10}, {"walk_to": HISTORY, "stay": 10},
                                              {"walk_to": PORT, "stay": 5}]])
    assert got == [("arrived", HOUSE, "12:29:56"), ("departed", HOUSE, "12:41:53"), ("arrived", HISTORY, "12:41:53"),
                   ("departed", HISTORY, "12:53:49"), ("arrived", PORT, "12:59:14")]


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


def test_expand_drive_and_coordinates():
    data = {"start": "2026-10-10T12:00:00+09:00",
            "steps": [{"goto": [35.99, 129.56]}, {"drive_to": "kto-2834344"}, {"walk_to": [35.977, 129.5515]}]}
    calls = []

    def path(a, b, mode):
        calls.append(mode)
        return None  # 경로선이 없으면 직선

    locs = [a for a in scene.expand(data, coords, path) if a.kind == "loc"]
    assert calls == ["car", "walk"]
    assert (locs[0].lat, locs[0].lng) == (35.99, 129.56)
    drive = [a for a in locs if a.at <= next(a.at for a in locs if (a.lat, a.lng) == coords("kto-2834344"))][1:]
    gaps = [(b.at - a.at).total_seconds() for a, b in zip(drive, drive[1:])]
    assert max(gaps) <= 10  # 차는 10초 간격 (400m/분)
    assert (drive[-1].at - locs[0].at).total_seconds() == pytest.approx(scene._dist((35.99, 129.56), coords("kto-2834344")) / 400 * 60, abs=20)  # 점마다 초 미만을 버린다
    assert (locs[-1].lat, locs[-1].lng) == (35.977, 129.5515)


def test_matches():
    events = [{"type": "arrived", "level": 2, "payload": {"place_id": "kto-1"},
               "card": {"kind": "docent", "place_id": "kto-1", "item_id": 3}}]
    assert scene.matches({"type": "arrived", "place_id": "kto-1"}, events)
    assert scene.matches({"card": {"kind": "docent"}}, events)  # dict 는 준 키만 비교
    assert not scene.matches({"card": {"kind": "other"}}, events)
    assert not scene.matches({"type": "arrived", "place_id": "kto-2"}, events)
    assert scene.matches({"type": "delay", "not": True}, events)
    assert not scene.matches({"type": "arrived", "not": True}, events)
