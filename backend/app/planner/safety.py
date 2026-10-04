"""일정 안전 점검. validation.py 처럼 결정론적이지만 별개의 검사다 (위반이 아니라 '위험 단계').

- 일정의 계산된 방문 시각(build_schedule)과 장소를 safety 서비스(/safety/assess)에 보내 항목별 위험 단계를 받는다.
- 위험 단계는 safe / caution / danger 3단계. 판단하지 않은 항목(먼 날짜·지난 일정·데이터 장애)은 risk=None.
- 참고(info) 신호(약한 비 등)는 단계를 올리지 않지만 hazards 에 그대로 남겨 사람·LLM 이 일정 조정에 참고한다.
- 지금은 방문지만 본다. 이동 경로 평가는 나중에 추가한다.

DB·HTTP 에 의존하지 않는 순수 함수만 둔다 (HTTP 는 safety_client.py, 주기 점검은 safety_watch.py).
"""
from datetime import date, datetime

from app.planner.schemas import (
    PlanDetail, SafetyChange, SafetyCheck, SafetyCounts, SafetyHazard, SafetyItem, SafetyWindow,
)

RANK = {"safe": 0, "caution": 1, "danger": 2}
LABEL = {"safe": "안전", "caution": "주의", "danger": "위험"}
SKIP_STATUS = {"skipped", "done"}  # 건너뛴 항목과 이미 다녀온 항목은 점검하지 않는다
DAY_MIN = 24 * 60


def _minutes(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def visits(plan: PlanDetail, start: date | None = None, end: date | None = None) -> list[dict]:
    """/safety/assess 요청의 visits. key = 항목 id. start~end 날짜 밖 항목은 뺀다.
    계산된 시작 시각이 자정을 넘는 항목은 날짜가 애매해 빼고, 종료만 넘으면 60분으로 본다(end 생략)."""
    out = []
    for d in plan.days:
        if (start and d.date < start) or (end and d.date > end):
            continue
        for it in d.items:
            if it.status in SKIP_STATUS or it.start_at is None or it.end_at is None:
                continue
            s, e = _minutes(it.start_at), _minutes(it.end_at)
            if s >= DAY_MIN:
                continue
            v = {"key": str(it.id), "place_id": it.place_id, "date": d.date.isoformat(), "start": it.start_at}
            if e < DAY_MIN:
                v["end"] = it.end_at
            if it.place is not None:
                v |= {"label": it.place.title, "lat": it.place.lat, "lng": it.place.lng}
                if it.place.content_type:
                    v["content_type"] = it.place.content_type
            out.append(v)
    return out


def _item(plan: PlanDetail, v: dict) -> SafetyItem | None:
    for d in plan.days:
        for it in d.items:
            if str(it.id) == v["key"]:
                return SafetyItem(
                    item_id=it.id, day=d.day, date=d.date, place_id=it.place_id,
                    title=it.place.title if it.place else (v.get("label") or it.place_id),
                    start_at=it.start_at, end_at=it.end_at, risk=v["risk"],
                    unassessed_reason=v.get("unassessed_reason"), data_missing=v.get("data_missing", False),
                    horizon=v["horizon"],
                    hazards=[SafetyHazard(type=h["type"], level=h["level"], reason=h["reason"], source=h["source"])
                             for h in v["hazards"]],
                    advice=v["advice"],
                    better_times=[SafetyWindow(**w) for w in v.get("better_times", [])],
                    sensitivity=v.get("sensitivity", []), notes=v.get("notes", []),
                    recheck_from=v.get("recheck_from"))
    return None  # 보낸 사이 항목이 사라짐


def _guidance(items: list[SafetyItem], data_ok: dict[str, bool]) -> str:
    risky = [i for i in items if i.risk in ("caution", "danger")]
    info = [i for i in items if i.risk == "safe" and i.hazards]
    far = [i for i in items if i.unassessed_reason == "beyond_forecast"]
    missing = [i for i in items if i.unassessed_reason == "data_missing"]
    parts = []
    if risky:
        names = ", ".join(f"{i.day}일차 {i.start_at} {i.title}({LABEL[i.risk]})" for i in risky[:4])
        parts.append(f"주의·위험 {len(risky)}곳: {names}. 항목별 advice 를 따르고, 바꾸기 전에 근거(hazards)를 사용자에게 "
                     "설명하라. 시간을 옮길 때는 better_times 안에서, 없으면 실내 장소로 대안을 찾는다.")
    if info:
        parts.append(f"안전 단계지만 참고 신호가 있는 곳 {len(info)}곳 (약한 비 등). 일정을 바꿀 이유는 아니지만 "
                     "순서·시간을 정할 때 참고할 수 있다.")
    if far:
        recheck = min((i.recheck_from for i in far if i.recheck_from), default=None)
        parts.append(f"{len(far)}곳은 예보 범위 밖이라 판단하지 않았다(risk=null, 안전하다는 뜻 아님). "
                     "안전을 이유로 바꾸지 마라." + (f" {recheck:%Y-%m-%d}부터 다시 점검된다." if recheck else ""))
    if missing:
        bad = [k for k, ok in data_ok.items() if not ok]
        parts.append(f"{len(missing)}곳은 데이터({', '.join(bad) or '일부'})를 받지 못해 판단하지 못했다. "
                     "안전하다고 말하지 마라.")
    if not parts:
        future = [i for i in items if i.unassessed_reason != "past"]
        parts.append("예보·실측상 주의·위험 신호가 없다. 예보는 바뀔 수 있으니 '안전이 보장된다'고 말하지 마라."
                     if future else "점검할 앞으로의 일정이 없다.")
    return " ".join(parts)


def check(plan: PlanDetail, assessed: dict) -> SafetyCheck:
    """/safety/assess 결과(JSON) -> 일정 기준 점검 결과. 결과에 없는 항목은 빠진다."""
    by_key = {v["key"]: v for v in assessed.get("visits", [])}
    items = []
    for d in plan.days:
        for it in d.items:
            v = by_key.get(str(it.id))
            if v is not None and (si := _item(plan, v)) is not None:
                items.append(si)
    counts = SafetyCounts()
    for i in items:
        if i.risk is None:
            counts.unassessed += 1
        else:
            setattr(counts, i.risk, getattr(counts, i.risk) + 1)
            counts.info += i.risk == "safe" and bool(i.hazards)
    future = [i for i in items if i.unassessed_reason != "past"]
    risky = [i.risk for i in future if i.risk in ("caution", "danger")]
    if risky:
        overall = max(risky, key=RANK.__getitem__)
    elif any(i.risk is None for i in future):
        overall = None
    else:
        overall = "safe"
    data_ok = {k: bool(s.get("ok", True)) for k, s in assessed.get("data_status", {}).items()}
    checked = assessed.get("now")
    return SafetyCheck(
        plan_id=plan.id, plan_version=plan.version,
        checked_at=datetime.fromisoformat(checked) if isinstance(checked, str) else checked,
        overall_risk=overall, counts=counts, guidance=_guidance(items, data_ok), items=items, data_ok=data_ok)


# ---------- 주기 점검: 지난번과 비교 ----------

def state_key(item: SafetyItem) -> str:
    """항목·날짜로 구분한다. 앞 항목을 고쳐 시작 시각이 밀려도 같은 방문이다 (단계가 같으면 다시 경고하지 않음).
    다른 날로 옮기면 새 방문으로 본다."""
    return f"{item.item_id}@{item.date.isoformat()}"


def diff(prev: dict[str, str], result: SafetyCheck) -> tuple[list[SafetyChange], dict[str, str]]:
    """지난 단계(prev: key -> risk)와 비교해 바뀐 항목과 새 상태를 돌려준다.
    - 처음 보는 항목은 지난 단계를 safe 로 본다 (주의·위험이면 바로 경고).
    - 판단하지 않은 항목(risk=None)은 지난 단계를 그대로 둔다 (데이터 장애로 '해제'되지 않게).
    - 근거 일부가 빠진 결과(data_missing)로는 단계를 내리지 않는다 (올리는 것만 반영).
    - 이번 결과에 없는 항목(삭제·점검 범위 밖)은 상태에서 뺀다."""
    changes: list[SafetyChange] = []
    state: dict[str, str] = {}
    for it in result.items:
        k = state_key(it)
        before = prev.get(k)
        if it.risk is None:
            if before is not None:
                state[k] = before
            continue
        old = before or "safe"
        if it.data_missing and RANK[it.risk] < RANK[old]:
            state[k] = old
            continue
        state[k] = it.risk
        if RANK[it.risk] > RANK[old]:
            kind = "raised"
        elif RANK[it.risk] < RANK[old]:
            kind = "cleared" if it.risk == "safe" else "lowered"
        else:
            continue
        changes.append(SafetyChange(kind=kind, from_risk=old, to_risk=it.risk, item=it))
    return changes, state


def alert_level(changes: list[SafetyChange]) -> str:
    levels = [c.to_risk for c in changes if c.kind != "cleared"]
    return max(levels, key=RANK.__getitem__) if levels else "safe"


def alert_message(changes: list[SafetyChange]) -> str:
    raised = sum(c.kind == "raised" for c in changes)
    head = (f"일정 {raised}곳의 위험 단계가 올라갔습니다." if raised
            else f"일정 {len(changes)}곳의 위험 단계가 낮아졌습니다.")
    lines = []
    for c in changes:
        it = c.item
        why = next((h.reason for h in it.hazards if h.level != "info"), "")
        lines.append(f"- {it.day}일차 {it.start_at} {it.title}: {LABEL[c.from_risk]} → {LABEL[c.to_risk]}"
                     + (f" ({why})" if why else ""))
    return "\n".join([head, *lines])
