"""Event Policy (순수 함수). 감지(Detection)를 이벤트로 남길지, 단계 3 큐에서 무엇을 보낼지 정한다 (docs/trip/README.md §4.1).

- 우선순위: 안전(near_hazard, safety_raised) > 일정 영향(delay) > 안내(arrived, departed …)
- 중복 억제: 같은 원인 키로는 다시 알리지 않는다. 원인이 여럿('|')이면 하나라도 새것일 때만 알린다
  -> 제안을 거절해도 같은 상황이면 조용하고, 더 나빠지면(새 위반·새 경고) 다시 알린다.
  알린 위반이 해소(TRIP_RESOLVE_MIN 이어서 사라짐)된 뒤 다시 생기면 또 알린다 (active_causes). 큐에서 빠진(suppressed) delay 는 알린 것이 아니다.
- 단계 3은 한 번에 하나 (큐). 같은 원인 묶음의 오래된 것은 suppressed. 쿨다운 안에는 보내지 않는다 (safety_raised 는 무시, 시각을 크게 되돌렸으면 쿨다운 아님).
- 앱 활동이 없으면 단계 3 은 보류(queued). 활동이 돌아오면 지금도 유효한 것 중 하나만 보낸다 (pick_resume).
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.trip.judge import Detection

PRIORITY = {"near_hazard": 0, "safety_raised": 0, "delay": 1, "safety_eased": 2, "arrived": 2, "departed": 2,
            "day_started": 3, "day_ended": 3}
COOLDOWN_EXEMPT = {"safety_raised"}


def cause_parts(cause_key: str) -> set[str]:
    return set(cause_key.split("|")) if cause_key else set()


def known_causes(cause_keys: list[str]) -> set[str]:
    out: set[str] = set()
    for k in cause_keys:
        out |= cause_parts(k)
    return out


def active_causes(rows: list[tuple[int, str]], released: dict[str, int] | None = None) -> set[str]:
    """(이벤트 id, 원인 키) 목록에서 지금 '알린 것'으로 세는 원인. released[키] = id 면 그 id 까지의 이벤트는 그 원인을 세지 않는다
    (해소된 위반, judge.resolve_violations) — 그 뒤 이벤트가 다시 담으면 다시 센다."""
    out: set[str] = set()
    for eid, key in rows:
        out |= {k for k in cause_parts(key) if eid > (released or {}).get(k, 0)}
    return out


def fresh(dets: list[Detection], known: set[str]) -> list[Detection]:
    """이미 알린 원인만 있는 감지는 뺀다. 남은 것은 우선순위 순 (같은 요청 안에서 id 가 우선순위 순서가 되게)."""
    out, seen = [], set(known)
    for d in sorted(dets, key=lambda d: PRIORITY[d.type]):
        parts = cause_parts(d.cause_key)
        if parts and parts <= seen:
            continue
        seen |= parts
        out.append(d)
    return out


def initial_status(det: Detection) -> str:
    """단계 3(Agent 선제 턴)은 큐에 넣고 따로 보낸다. 나머지는 앱이 폴링으로 가져가므로 바로 delivered."""
    return "queued" if det.level == 3 else "delivered"


@dataclass(frozen=True)
class Queued:
    id: int
    type: str
    item_ids: tuple[int, ...] = ()


def group(q: Queued) -> str:
    """같은 원인 묶음: 지연은 그날 일정 하나에 대한 것이라 하나로 (새것이 지금 상황 전체를 담는다), 안전은 같은 항목들끼리."""
    if q.type == "delay":
        return "delay"
    return f"{q.type}:{','.join(map(str, sorted(q.item_ids)))}"


def pick_next(queued: list[Queued], last_delivered_at: datetime | None, now: datetime,
              cooldown: timedelta) -> tuple[Queued | None, list[int]]:
    """(지금 보낼 것 또는 None, suppressed 로 바꿀 id). 같은 묶음은 가장 새것만 남기고, 우선순위·오래된 순으로 고른다."""
    newest: dict[str, Queued] = {}
    for q in sorted(queued, key=lambda q: q.id):
        newest[group(q)] = q
    keep = {q.id for q in newest.values()}
    suppress = [q.id for q in queued if q.id not in keep]
    order = sorted(newest.values(), key=lambda q: (PRIORITY[q.type], q.id))
    if not order:
        return None, suppress
    first = order[0]
    # 마지막 전송이 지금보다 쿨다운 이상 늦으면 같은 날 가상 시각을 되돌린 것 -> 쿨다운으로 보지 않는다
    # (조금 늦은 것은 시계 캐시 차이일 수 있어 쿨다운 안으로 본다)
    cooling = last_delivered_at is not None and abs(now - last_delivered_at) < cooldown
    if cooling and first.type not in COOLDOWN_EXEMPT:
        return None, suppress
    return first, suppress


def pick_resume(queued: list[Queued], valid: set[int]) -> tuple[int | None, list[int]]:
    """앱 활동이 돌아왔을 때(§4.1 보류): 보류된 단계 3 중 지금도 유효한 것에서 우선순위(안전 > 지연)가 가장 높고 가장 새것 하나만 남긴다.
    (남길 id 또는 None, suppressed 로 바꿀 id) — 더는 유효하지 않은 것과 밀린 옛것은 모두 suppressed."""
    cands = [q for q in queued if q.id in valid]
    keep = min(cands, key=lambda q: (PRIORITY[q.type], -q.id)) if cands else None
    return (keep.id if keep else None), [q.id for q in queued if keep is None or q.id != keep.id]
