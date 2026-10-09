"""이벤트 판정 (순수 함수). 입력: 오늘 일정·마지막 상태·위치·now / planner·safety 응답 → 출력: 새 상태·감지(Detection).
DB·HTTP 를 쓰지 않는다. 같은 입력이면 같은 결과라 pytest 와 재생 파일로 그대로 검증한다 (docs/trip/README.md §3, §8).

- 도착·출발: step()       반경(60m + accuracy, 최대 150m) 안 90초 머묾 / 머무는 곳 반경 밖 120초
- 지연:      judge_delay() planner projection 의 new_violations 중 일정에 영향이 있는 것
- 위험 구역: judge_hazard() safety /avoid 구역 안이거나 50m 안
- 안전 경고: judge_alert() planner 경고의 오늘 남은 항목 변화
감지 결과의 중복 억제·전송 순서는 policy.py 가 정한다.
"""
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta

from app.trip.geo import distance_m, polygon_distance_m

# 개입 단계 (§3.1): 0 상태만 / 1 고정 문구 알림 / 2 카드 / 3 Agent 선제 턴
LEVELS = {"day_started": 0, "day_ended": 0, "arrived": 2, "departed": 1, "delay": 3,
          "safety_raised": 3, "safety_eased": 1, "near_hazard": 1}
RISK_KO = {"safe": "안전", "caution": "주의", "danger": "위험"}
RISK_RANK = {"safe": 0, "caution": 1, "danger": 2}
MODE_KO = {"walk": "도보", "car": "차량"}


@dataclass(frozen=True)
class Spot:
    """오늘 일정 항목 하나 (planner PlanDetail 에서 뽑은 것)."""
    item_id: int
    order: int
    title: str
    place_id: str
    lat: float | None
    lng: float | None
    status: str  # planned | visiting | done | skipped
    planned_start: str | None = None
    leg_mode: str | None = None


@dataclass(frozen=True)
class Point:
    lat: float
    lng: float
    accuracy: float
    recorded_at: datetime
    source: str = "app"


@dataclass(frozen=True)
class Track:
    """판정 상태. state: idle (시작 전·끝) | visiting (item_id 에 머묾) | moving (item_id 를 떠나 이동 중)."""
    state: str = "idle"
    item_id: int | None = None
    since: datetime | None = None
    cand_item_id: int | None = None  # 반경 안에 들어와 머무름을 재는 항목
    cand_since: datetime | None = None
    outside_since: datetime | None = None  # 머무는 곳 반경 밖으로 처음 나간 시각
    last_at: datetime | None = None  # 마지막으로 받은 점의 시각 (순서 뒤바뀜 판정)


@dataclass(frozen=True)
class TrackConfig:
    arrive_radius_m: float = 60
    max_radius_m: float = 150
    arrive_dwell_sec: float = 90
    depart_dwell_sec: float = 120
    max_accuracy_m: float = 100


@dataclass
class Detection:
    type: str
    at: datetime
    item_ids: list[int] = field(default_factory=list)
    message: str = ""
    payload: dict = field(default_factory=dict)
    cause_key: str = ""  # 중복 억제용. 여러 원인이면 '|' 로 잇는다 (하나라도 새것이면 알린다)
    severity: str = "info"  # info | warning | danger
    card: dict | None = None

    @property
    def level(self) -> int:
        return LEVELS[self.type]


def hhmm(t: datetime) -> str:
    return f"{t:%H:%M}"


def time_of(s: str) -> time:
    """'HH:MM' 또는 'HH:MM:SS' -> time"""
    return time.fromisoformat(s)


def minutes_of(s: str) -> int:
    h, m = s.split(":")[:2]
    return int(h) * 60 + int(m)


# ---------- 오늘 일정 ----------

def spots_from_plan(plan: dict, day: int) -> list[Spot]:
    """PlanDetail(JSON) 의 그날 항목. 날짜 범위 밖이면 빈 목록."""
    days = plan.get("days") or []
    if not 1 <= day <= len(days):
        return []
    out = []
    for it in days[day - 1]["items"]:
        place = it.get("place") or {}
        out.append(Spot(item_id=it["id"], order=it["order"], title=place.get("title") or it["place_id"],
                        place_id=it["place_id"], lat=place.get("lat"), lng=place.get("lng"), status=it["status"],
                        planned_start=it.get("start_at"), leg_mode=it.get("leg_mode")))
    return out


def remaining_ids(spots: list[Spot]) -> set[int]:
    """오늘 남은 항목 (다녀오지도, 건너뛰지도 않은 것. 지금 머무는 곳 포함)."""
    return {s.item_id for s in spots if s.status in ("planned", "visiting")}


def apply_progress(spots: list[Spot], dets: list[Detection]) -> list[Spot]:
    """감지한 도착·출발을 일정 상태에 반영 (planner 에 보내는 것과 같은 변화)."""
    status = {s.item_id: s.status for s in spots}
    for d in dets:
        if d.type == "arrived":
            status[d.item_ids[0]] = "visiting"
        elif d.type == "departed":
            status[d.item_ids[0]] = "done"
    return [replace(s, status=status[s.item_id]) for s in spots]


def track_from_spots(spots: list[Spot], arrived_at: dict[int, datetime]) -> Track:
    """여행일을 (다시) 시작할 때 planner 에 기록된 진행 상태에서 판정 상태를 만든다. 머무는 항목이 여럿이면 일정상 마지막."""
    visiting = [s for s in spots if s.status == "visiting"]
    if visiting:
        s = max(visiting, key=lambda x: x.order)
        return Track(state="visiting", item_id=s.item_id, since=arrived_at.get(s.item_id))
    return Track()


# ---------- 도착·출발 ----------

def radius_m(cfg: TrackConfig, accuracy: float) -> float:
    return min(cfg.arrive_radius_m + accuracy, cfg.max_radius_m)


def ignore_reason(track: Track, point: Point, cfg: TrackConfig) -> str | None:
    """버릴 점이면 이유 (accuracy: 부정확 / out_of_order: 마지막 점보다 이름), 아니면 None."""
    if point.accuracy > cfg.max_accuracy_m:
        return "accuracy"
    if track.last_at is not None and point.recorded_at < track.last_at:
        return "out_of_order"
    return None


def step(track: Track, spots: list[Spot], point: Point, cfg: TrackConfig) -> tuple[Track, list[Detection]]:
    """점 하나를 받아 새 상태와 도착·출발 감지를 돌려준다 (ignore_reason 을 통과한 점만 넣는다).

    - 머무는 중: 반경 밖이 depart_dwell_sec 이상 이어지면 출발 (시각 = 처음 나간 시각). 한 점만 밖이면 다시 들어올 때 초기화.
    - 그 밖: 오늘 남은(planned) 항목 중 가장 가까운 것의 반경 안에 arrive_dwell_sec 이상 머물면 도착 (시각 = 처음 들어온 시각).
      예정 순서가 아니어도 도착으로 본다 (out_of_order).
    """
    t, r = point.recorded_at, radius_m(cfg, point.accuracy)
    by_id = {s.item_id: s for s in spots}
    dets: list[Detection] = []
    left: int | None = None

    if track.state == "visiting":
        spot = by_id.get(track.item_id)
        if spot is None or spot.status in ("done", "skipped"):
            track = Track(last_at=track.last_at)  # 일정에서 빠졌거나 다른 곳에서 완료 처리됨 -> 조용히 끝
        elif spot.lat is not None and distance_m(point.lat, point.lng, spot.lat, spot.lng) <= r:
            track = replace(track, outside_since=None)
        else:
            out = track.outside_since or t
            if (t - out).total_seconds() >= cfg.depart_dwell_sec:
                stayed = int((out - track.since).total_seconds() // 60) if track.since else None
                dets.append(Detection("departed", out, [spot.item_id], cause_key=f"departed:{spot.item_id}",
                                      payload={"item_id": spot.item_id, "title": spot.title, "stayed_minutes": stayed}))
                track = Track(state="moving", item_id=spot.item_id, since=out)
                left = spot.item_id
            else:
                track = replace(track, outside_since=out)

    if track.state != "visiting":
        near = None
        for s in spots:
            if s.status != "planned" or s.lat is None or s.item_id == left:
                continue
            d = distance_m(point.lat, point.lng, s.lat, s.lng)
            if d <= r and (near is None or d < near[0]):
                near = (d, s)
        if near is None:
            track = replace(track, cand_item_id=None, cand_since=None)
        else:
            s = near[1]
            since = track.cand_since if track.cand_item_id == s.item_id and track.cand_since else t
            if (t - since).total_seconds() >= cfg.arrive_dwell_sec:
                first = next((x for x in sorted(spots, key=lambda x: x.order) if x.status == "planned"), None)
                out_of_order = first is not None and first.item_id != s.item_id
                dets.append(Detection(
                    "arrived", since, [s.item_id], message=f"{s.title}에 도착했어요. 이야기 들어볼까요?",
                    cause_key=f"arrived:{s.item_id}",
                    payload={"item_id": s.item_id, "place_id": s.place_id, "title": s.title, "out_of_order": out_of_order},
                    card={"kind": "docent", "place_id": s.place_id, "item_id": s.item_id}))
                track = Track(state="visiting", item_id=s.item_id, since=since)
            else:
                track = replace(track, cand_item_id=s.item_id, cand_since=since)

    return replace(track, last_at=t), dets


def departed_message(title: str, nxt: dict | None, next_title: str | None, mode: str | None, at: datetime) -> str:
    """'가옥거리를 떠났어요. 다음은 과메기문화관, 도보 약 8분 (15:12 도착 예정)'"""
    msg = f"{title}을(를) 떠났어요."
    if nxt and next_title:
        msg += f" 다음은 {next_title}"
        if nxt.get("eta"):
            mins = minutes_of(nxt["eta"]) - (at.hour * 60 + at.minute)
            if mins >= 0:
                msg += f", {MODE_KO.get(mode or 'walk', '이동')} 약 {mins}분"
            msg += f" ({nxt['eta']} 도착 예정)"
        msg += "."
    return msg


# ---------- 지연 ----------

DELAY_CODES = {"fixed_time_missed", "day_overflow", "outside_open_hours", "closed_day"}


def is_delay_violation(v: dict) -> bool:
    """일정에 실제 문제가 되는 위반: 고정 시각 놓침·하루 초과·운영시간·휴무·hard 규칙 (이동 근사 경고 등은 뺀다)."""
    return v["code"] in DELAY_CODES or (v.get("constraint_id") is not None and v.get("severity") == "error")


def violation_key(v: dict) -> str:
    return f"delay:{v['code']}:{v.get('day')}:{v.get('item_id')}:{v.get('constraint_id')}"


def judge_delay(projection: dict, at: datetime, known: set[str] | frozenset = frozenset()) -> Detection | None:
    """planner projection(§6.2)의 new_violations 중 일정에 영향이 있는 것이 있으면 delay 감지.
    가벼운 지연(새 위반 없음)은 None. 원인 키는 위반마다 하나 ('|' 로 묶음) — 하나라도 새것이면 다시 알린다."""
    vs = [v for v in projection.get("new_violations") or [] if is_delay_violation(v)]
    if not vs:
        return None
    keys = [violation_key(v) for v in vs]
    fresh = [v for v, k in zip(vs, keys) if k not in known]
    first = (fresh or vs)[0]
    msg = f"지금대로면 일정에 문제가 생겨요. {first['message']}"
    if len(vs) > 1:
        msg += f" (그 밖에 {len(vs) - 1}건)"
    return Detection("delay", at, sorted({v["item_id"] for v in vs if v.get("item_id") is not None}), msg,
                     payload={"new_violations": vs, "delay_minutes": projection.get("delay_minutes"),
                              "next": projection.get("next")},
                     cause_key="|".join(sorted(set(keys))), severity="warning")


# ---------- 위험 구역 ----------

def judge_hazard(lat: float, lng: float, at: datetime, zones: list[dict], near_m: float) -> Detection | None:
    """지금 위치가 safety /avoid 구역 안이거나 near_m 안이면 near_hazard (LLM 없이 고정 경고).
    걸린 구역을 하나로 묶고, 가까운 구역(같으면 더 위험한 구역)부터 알린다."""
    hits = []
    for z in zones:
        d = polygon_distance_m(lat, lng, z["polygon"])
        if d <= near_m:
            hits.append((d, z))
    if not hits:
        return None
    hits.sort(key=lambda x: (x[0], -RISK_RANK[x[1]['risk']]))
    d, z = hits[0]
    if z["kind"] == "flood":
        msg = f"침수 감시 구역({z['name']}) 근처예요. 물이 고인 길은 피하세요."
    else:
        msg = f"{z['name']} 위험 구역 근처예요. 방파제에는 들어가지 마세요."
    worst = max(RISK_RANK[x["risk"]] for _, x in hits)
    return Detection(
        "near_hazard", at, [], msg,
        payload={"zones": [{"id": x["id"], "name": x["name"], "kind": x["kind"], "risk": x["risk"],
                            "reason": x.get("reason", ""), "distance_m": round(dd)} for dd, x in hits]},
        cause_key="|".join(sorted(f"near_hazard:{x['id']}:{x['risk']}" for _, x in hits)),
        severity="danger" if worst == 2 else "warning")


# ---------- 안전 경고 (planner) ----------

def _change_line(c: dict) -> str:
    it = c["item"]
    reason = next((h["reason"] for h in it.get("hazards") or [] if h.get("level") != "info"), "")
    line = f"{it['start_at']} {it['title']}: {RISK_KO[c['from_risk']]} → {RISK_KO[c['to_risk']]}"
    return line + (f" ({reason})" if reason else "")


def judge_alert(alert: dict, trip_date: date, remaining: set[int], at: datetime) -> list[Detection]:
    """planner 안전 경고(SafetyAlertView) 중 오늘 남은 항목의 변화만 본다.
    raised(needs_action) -> safety_raised (단계 3), lowered·cleared -> safety_eased (단계 1). 원인 키는 경고 id."""
    day = trip_date.isoformat()
    changes = [c for c in alert.get("changes") or []
               if c["item"]["date"] == day and c["item"]["item_id"] in remaining]
    raised = [c for c in changes if c["kind"] == "raised"]
    eased = [c for c in changes if c["kind"] in ("lowered", "cleared")]
    out = []
    if raised and alert.get("needs_action"):
        worst = max(RISK_RANK[c["to_risk"]] for c in raised)
        msg = "남은 일정에 위험 신호가 생겼어요. " + _change_line(raised[0])
        if len(raised) > 1:
            msg += f" (그 밖에 {len(raised) - 1}건)"
        out.append(Detection("safety_raised", at, sorted({c["item"]["item_id"] for c in raised}), msg,
                             payload={"alert_id": alert["id"], "situation": alert.get("situation"), "changes": raised},
                             cause_key=f"safety_raised:{alert['id']}", severity="danger" if worst == 2 else "warning"))
    if eased:
        it = eased[0]["item"]
        if eased[0]["kind"] == "cleared":
            msg = f"{it['title']}의 위험 신호가 해제됐어요."
        else:
            msg = f"{it['title']}의 위험 단계가 {RISK_KO[eased[0]['to_risk']]}(으)로 낮아졌어요."
        if len(eased) > 1:
            msg += f" (그 밖에 {len(eased) - 1}건)"
        out.append(Detection("safety_eased", at, sorted({c["item"]["item_id"] for c in eased}), msg,
                             payload={"alert_id": alert["id"], "changes": eased},
                             cause_key=f"safety_eased:{alert['id']}", severity="info"))
    return out


# ---------- 자동 종료 ----------

def day_end_at(plan: dict, day: int) -> str | None:
    """자동 종료 기준: 그날 기준 일정의 마지막 종료와 실제 완료 시각 중 늦은 것 (HH:MM). 일정이 없으면 None.
    재계산(projection) 종료는 쓰지 않는다 — 아무 데도 가지 않으면 지금을 따라 계속 밀려서 끝나지 않는다."""
    days = plan.get("days") or []
    if not 1 <= day <= len(days):
        return None
    ends = [days[day - 1].get("end_at")] + [i.get("done_at") for i in days[day - 1]["items"]]
    ends = [e[:5] for e in ends if e]
    return max(ends) if ends else None


def should_auto_end(now: datetime, trip_date: date, end_at: str | None, auto_end_min: float) -> bool:
    """자정이 지났거나, 그날 마지막 일정 종료 + auto_end_min 이 지나면 자동 종료."""
    if now.date() > trip_date:
        return True
    if not end_at:
        return False
    end = datetime.combine(trip_date, time.fromisoformat(end_at), tzinfo=now.tzinfo)
    return now >= end + timedelta(minutes=auto_end_min)
