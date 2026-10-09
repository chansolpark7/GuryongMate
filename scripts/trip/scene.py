"""재생 파일(scripts/trip/scenes/*.json)을 시간순 동작 목록으로 펼친다 (docs/trip/README.md §8).
CLI(`trip replay`)와 pytest(backend/tests/trip/test_scenes.py)가 같은 파일·같은 펼침을 쓴다. 표준 라이브러리만 쓴다.

    {"plan_fixture": "seoyun_mom_1010", "start": "2026-10-10T12:20:00+09:00", "scenario": "calm",
     "steps": [{"goto": "kto-2834330", "stay": 65},     # 그 장소로 순간이동해 65분 머묾
               {"walk_to": "kto-2610501"},               # 경로선(없으면 직선)을 따라 걸음 (점 간격 30초)
               {"at": "+95", "sim": {"scenario": "storm"}},  # 시작 +95분까지 기다린 뒤 safety 가상 상황 변경
               {"tick": true},                           # trip 틱 (planner 안전 점검 포함)
               {"expect": {"type": "delay"}},            # 지금까지 이 이벤트가 있어야 함 ("not": true 면 없어야 함)
     ]}

한 단계 안에서는 at → sim → goto → walk_to → stay → tick → expect 순서로 처리한다.
"""
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

WALK_M_PER_MIN = 70  # routing 기본 걷는 속도 (4.2km/h)
POINT_SEC = 30  # 걷는 동안 점 간격
DWELL_POINTS_SEC = 180  # 머무를 때 처음 이 시간 동안은 30초마다 (도착 판정), 그 뒤로는 5분마다
STAY_POINT_SEC = 300

LatLng = tuple[float, float]


@dataclass
class Action:
    kind: str  # loc | sim | tick | expect
    at: datetime
    lat: float | None = None
    lng: float | None = None
    data: dict = field(default_factory=dict)


def _dist(a: LatLng, b: LatLng) -> float:
    k = 111_320 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot((a[0] - b[0]) * 111_320, (a[1] - b[1]) * k)


def resample(path: list[LatLng], step_m: float) -> list[LatLng]:
    """경로선 위에서 step_m 간격 점 (첫 점 제외, 마지막 점 포함)."""
    out: list[LatLng] = []
    walked, want = 0.0, step_m  # 지금까지 걸은 거리, 다음 점을 찍을 거리
    for a, b in zip(path, path[1:]):
        seg = _dist(a, b)
        while seg > 0 and want <= walked + seg:
            f = (want - walked) / seg
            out.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
            want += step_m
        walked += seg
    if path and (not out or _dist(out[-1], path[-1]) > 1):
        out.append(path[-1])
    return out


def _target(start: datetime, at: str) -> datetime:
    """'+95' (시작 + 분) 또는 'HH:MM' (시작한 날의 시각)."""
    if at.startswith("+"):
        return start + timedelta(minutes=float(at[1:]))
    h, m = at.split(":")
    return start.replace(hour=int(h), minute=int(m), second=0, microsecond=0)


def expand(scene: dict, coords: Callable[[str], LatLng],
           path: Callable[[LatLng, LatLng], list[LatLng] | None] | None = None) -> list[Action]:
    """coords: place_id -> (lat, lng). path: (출발, 도착) -> 경로선 [(lat, lng), …] (None 이면 직선)."""
    start = datetime.fromisoformat(scene["start"])
    t, pos = start, None
    out = [Action("sim", t, data={"scenario": scene.get("scenario", "calm"), "now": start.isoformat()})]

    def loc():
        out.append(Action("loc", t, pos[0], pos[1]))

    def wait_until(until: datetime, first_dwell: bool):
        nonlocal t
        begin = t
        while t < until:
            gap = POINT_SEC if first_dwell and (t - begin).total_seconds() < DWELL_POINTS_SEC else STAY_POINT_SEC
            t = min(t + timedelta(seconds=gap), until)
            if pos is not None:
                loc()

    for step in scene["steps"]:
        if "at" in step:
            wait_until(_target(start, str(step["at"])), first_dwell=False)
        if "sim" in step:
            out.append(Action("sim", t, data={**step["sim"], "now": t.isoformat()}))
        if "goto" in step:
            pos = coords(step["goto"])
            loc()
        if "walk_to" in step:
            dest = coords(step["walk_to"])
            if pos is None:
                pos = dest
                loc()
            else:
                line = (path(pos, dest) if path else None) or [pos, dest]
                step_m = WALK_M_PER_MIN * POINT_SEC / 60
                prev = pos
                for p in resample(line, step_m):
                    t += timedelta(seconds=POINT_SEC * _dist(prev, p) / step_m)
                    t = t.replace(microsecond=0)
                    pos, prev = p, p
                    loc()
        if "stay" in step:
            wait_until(t + timedelta(minutes=float(step["stay"])), first_dwell=True)
        if step.get("tick"):
            out.append(Action("tick", t))
        if "expect" in step:
            out.append(Action("expect", t, data=dict(step["expect"])))
    return out


def matches(expect: dict, events: list[dict]) -> bool:
    """expect 의 키가 모두 같은 이벤트가 있는지. 키는 이벤트 필드(type, level, severity, status …), 없으면 payload 에서 찾는다
    (예: place_id). "not": true 면 그런 이벤트가 없어야 참."""
    want = {k: v for k, v in expect.items() if k != "not"}

    def field_of(e: dict, k: str):
        return e[k] if k in e else (e.get("payload") or {}).get(k)

    found = any(all(field_of(e, k) == v for k, v in want.items()) for e in events)
    return not found if expect.get("not") else found
