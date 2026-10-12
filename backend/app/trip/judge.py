"""이벤트 판정 (순수 함수). 입력: 오늘 일정·마지막 상태·위치·now / planner·safety 응답 → 출력: 새 상태·감지(Detection).
DB·HTTP 를 쓰지 않는다. 같은 입력이면 같은 결과라 pytest 와 재생 파일로 그대로 검증한다 (docs/trip/README.md §3, §8).

- 도착·출발: step()       반경(60m + accuracy, 최대 150m) 안 90초 머묾 / 머무는 곳 반경 밖 120초
- 지연:      judge_delay() planner projection 의 new_violations 중 일정에 영향이 있는 것, 또는 다음 일정이 30분 이상 밀림
- 위험 구역: judge_hazard() safety /avoid 구역 안이거나 50m 안
- 안전 경고: judge_alert() planner 경고의 오늘 남은 항목 변화
- 상황 해제: judge_situation() 이 여행일에 주의·위험이던 safety 상황(구역 단계·특보)이 모두 평상시로
- 앱 활동:   is_active()   마지막 위치 전송·이벤트 조회가 TRIP_ACTIVE_MIN 안 (아니면 단계 3 전송 보류)
- 자동 종료: auto_end_reason() 자정, 또는 계획 종료 + 120분이 지나고 최근 위치도 없음
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

    - 머무는 중(A): 반경 밖이 depart_dwell_sec 이상 이어지면 출발 (시각 = 처음 나간 시각). 한 점만 밖이면 다시 들어올 때 초기화.
      가까운 장소로 옮겨 갈 때(반경이 겹쳐 A 반경 밖 시간이 짧다): 남은 항목 B 의 반경 안이고 A 보다 B 에 더 가까운 점이
      arrive_dwell_sec 이어지면 A 출발 → B 도착 (암묵 전환). 출발 시각 = A 반경을 이미 벗어났으면 처음 나간 시각, 아니면 B 후보 시작
      (도착보다 늦지 않게), 도착 시각 = B 후보 시작. 그 전에 정상 출발(반경 밖 depart_dwell_sec)이 나면 B 후보 시작을 이어받는다.
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
        else:
            here = distance_m(point.lat, point.lng, spot.lat, spot.lng) if spot.lat is not None else None
            out = None if here is not None and here <= r else (track.outside_since or t)
            # 머무는 중에도 다른 남은 항목 B 를 본다: B 반경 안이고 A 보다 B 에 더 가까울 때만 후보 (깨지면 초기화)
            cand = _nearest(spots, point, r, skip=spot.item_id, closer_than=here)
            c_since = None
            if cand is not None:
                c_since = track.cand_since if track.cand_item_id == cand.item_id and track.cand_since else t
            if cand is not None and (t - c_since).total_seconds() >= cfg.arrive_dwell_sec:
                dep = min(out, c_since) if out is not None else c_since
                dets += [_departed(spot, dep, track.since), _arrived(cand, c_since, spots)]
                return Track(state="visiting", item_id=cand.item_id, since=c_since, last_at=t), dets
            if out is not None and (t - out).total_seconds() >= cfg.depart_dwell_sec:
                dets.append(_departed(spot, out, track.since))
                # 머무는 중에 이미 B 에 들어와 있었으면 그 시각부터 이어서 센다 (도착 시각 = 실제 진입 시각)
                track = Track(state="moving", item_id=spot.item_id, since=out,
                              cand_item_id=cand.item_id if cand else None, cand_since=c_since)
                left = spot.item_id
            else:
                track = replace(track, outside_since=out, cand_item_id=cand.item_id if cand else None,
                                cand_since=c_since)

    if track.state != "visiting":
        s = _nearest(spots, point, r, skip=left)
        if s is None:
            track = replace(track, cand_item_id=None, cand_since=None)
        else:
            since = track.cand_since if track.cand_item_id == s.item_id and track.cand_since else t
            if (t - since).total_seconds() >= cfg.arrive_dwell_sec:
                dets.append(_arrived(s, since, spots))
                track = Track(state="visiting", item_id=s.item_id, since=since)
            else:
                track = replace(track, cand_item_id=s.item_id, cand_since=since)

    return replace(track, last_at=t), dets


def _nearest(spots: list[Spot], point: Point, r: float, skip: int | None = None,
             closer_than: float | None = None) -> Spot | None:
    """반경 r 안의 남은(planned) 항목 중 가장 가까운 것. closer_than: 그 거리보다 가까울 때만 (머무는 곳보다 더 가까움)."""
    near = None
    for s in spots:
        if s.status != "planned" or s.lat is None or s.item_id == skip:
            continue
        d = distance_m(point.lat, point.lng, s.lat, s.lng)
        if d <= r and (closer_than is None or d < closer_than) and (near is None or d < near[0]):
            near = (d, s)
    return near[1] if near else None


def _departed(spot: Spot, at: datetime, since: datetime | None) -> Detection:
    stayed = int((at - since).total_seconds() // 60) if since else None
    return Detection("departed", at, [spot.item_id], cause_key=f"departed:{spot.item_id}",
                     payload={"item_id": spot.item_id, "title": spot.title, "stayed_minutes": stayed})


def _arrived(s: Spot, since: datetime, spots: list[Spot]) -> Detection:
    first = next((x for x in sorted(spots, key=lambda x: x.order) if x.status == "planned"), None)
    out_of_order = first is not None and first.item_id != s.item_id
    return Detection("arrived", since, [s.item_id], message=f"{s.title}에 도착했어요. 이야기 들어볼까요?",
                     cause_key=f"arrived:{s.item_id}",
                     payload={"item_id": s.item_id, "place_id": s.place_id, "title": s.title, "out_of_order": out_of_order},
                     card={"kind": "docent", "place_id": s.place_id, "item_id": s.item_id})


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


def duration_ko(minutes: int) -> str:
    """64 -> '1시간', 75 -> '1시간 15분', 35 -> '35분' (10분 미만 나머지는 말하지 않는다)."""
    if minutes < 60:
        return f"{minutes}분"
    h, m = divmod(minutes, 60)
    return f"{h}시간" if m < 10 else f"{h}시간 {m}분"


def _ceil5(minutes: float) -> int:
    return int(-(-minutes // 5) * 5)


BEHIND_MARGIN_MIN = 15  # 밀림 추천안: 지금 장소 체류를 '지금 + 이만큼'까지로 늘린다 (조금 더 머물 여유)


def behind_ref(delay: float, notice: tuple[int, float | None] | None, step_min: float) -> tuple[str, float]:
    """일정 밀림을 셀 기준 (기준 이름, 기준 지연). notice: 이 여행일에 마지막으로 알린 delay 이벤트 (id, 그때 delay_minutes).
    - 알린 적 없음: 기준 0 -> 30분 밀리면 묻는다.
    - 알린 적 있음: 그때 지연이 기준 -> 그보다 30분 더 밀릴 때만 다시 묻는다 (위반으로 알렸어도, 다음 항목이 바뀌어도).
    - 그 뒤 지연이 그때보다 30분 이상 줄었으면 (제안 승인으로 일정이 미뤄짐) 새로 센다: 기준 0.
    계획 version 은 쓰지 않는다 — 승인한 변경이 지연을 흡수하지 않았으면(장면 ③: 끝 시각만 맞춤) 같은 지연을 또 묻게 된다."""
    if not notice or notice[1] is None:
        return "0", 0
    nid, nd = notice
    if delay <= nd - step_min:
        return f"{nid}r", 0
    return str(nid), max(nd, 0)


def behind_key(day: int, ref: str, delay: float, ref_delay: float, step_min: float) -> str:
    """일정 밀림 원인 키: 같은 기준에서 30분 단계가 오를 때만 새것."""
    return f"delay:behind:{day}:{ref}:{int((delay - ref_delay) // step_min)}"


def _day_items(plan: dict | None, day: int) -> dict[int, dict]:
    return {i["id"]: i for d in (plan or {}).get("days") or [] if d.get("day") in (None, day) for i in d["items"]}


def behind_suggestion(plan: dict | None, day: int, track: Track | None, nxt: dict, at: datetime,
                      projected: dict | None = None) -> dict | None:
    """밀림 추천안 (Agent 가 그대로 도구로 만들 변경). 남은 일정을 미루되 완료 항목·지난 시각은 건드리지 않는다.
    - 머무는 중: 지금 장소 체류를 계획 시작부터 (지금 + 여유 15분)과 재계산의 그 장소 종료 중 늦은 때까지로 늘린다 (5분 올림)
      -> 뒤 일정이 자동으로 밀린다. 계획 시작 기준이라 도착이 늦었던 만큼도 함께 흡수한다 (audit-2026-10-12 B5).
      규칙 B(늦게 온 곳은 예정 종료에 떠난다, planner §8.2)라 승인 뒤 재계산 종료 = max(계획 시작 + 새 체류, 지금) 이다.
      일찍 왔으면 재계산은 실제 시작 + 체류에 끝나므로 계획 시작·재계산 시작 중 이른 쪽부터 센다.
    - 이동 중·첫 장소 전 (늘릴 체류가 없음): 다음 장소 시작 시각을 예상 도착(5분 올림)으로 정한다 -> 그 뒤 일정이 밀린다.
    projected: 재계산 일정 PlanDetail (projection.plan)."""
    items = _day_items(plan, day)
    now_min = at.hour * 60 + at.minute
    cur = items.get(track.item_id) if track is not None and track.state == "visiting" else None
    if cur and cur.get("status") != "done" and cur.get("start_at") and cur.get("stay_minutes") is not None:
        until = now_min + BEHIND_MARGIN_MIN
        start = minutes_of(cur["start_at"])
        here = _day_items(projected, day).get(cur["id"])
        if here and here.get("end_at"):
            until = max(until, minutes_of(here["end_at"]))
        if here and here.get("start_at"):
            # 일찍 왔으면 재계산은 실제 시작 + 체류에 끝난다 (늦게 왔으면 예정 종료, 규칙 B) -> 둘 중 이른 시작 기준
            start = min(start, minutes_of(here["start_at"]))
        stay = max(_ceil5(until - start), cur["stay_minutes"] + 5)
        return {"kind": "extend_stay", "item_id": cur["id"], "title": (cur.get("place") or {}).get("title"),
                "stay_minutes": stay, "from_stay_minutes": cur["stay_minutes"]}
    if nxt.get("eta"):
        start = _ceil5(max(minutes_of(nxt["eta"]), now_min))
        return {"kind": "shift_next", "item_id": nxt["item_id"], "title": nxt.get("title"),
                "start_time": f"{start // 60:02d}:{start % 60:02d}", "from_start": nxt.get("planned_start")}
    return None


def judge_delay(projection: dict, at: datetime, known: set[str] | frozenset = frozenset(), *,
                behind_min: float = 0, plan: dict | None = None, day: int = 1, track: Track | None = None,
                notice: tuple[int, float | None] | None = None) -> Detection | None:
    """planner projection(§6.2)으로 delay 감지. 둘 중 하나라도 새것이면 이벤트 하나:
    - 위반: new_violations 중 일정에 영향이 있는 것. 원인 키는 위반마다 하나 ('|' 로 묶음) — 하나라도 새것이면 다시 알린다.
    - 일정 밀림: 다음 항목이 계획 시작보다 behind_min(TRIP_BEHIND_MIN) 이상 늦음 (0 이면 보지 않음).
      다시 묻는 것은 마지막으로 알린 지연(notice)보다 behind_min 더 밀릴 때 (behind_ref). 원인 키는 behind_key.
    payload.reason: 새 위반이 있으면 violations (밀림 정보도 담는다), 밀림만 새것이면 behind_schedule.
    plan: 기준 일정 PlanDetail (version·계획 종료·지금 장소의 계획 시작). track: 지금 판정 상태 (추천안).
    가벼운 지연(새 위반 없음, 밀림 behind_min 미만)은 None."""
    vs = [v for v in projection.get("new_violations") or [] if is_delay_violation(v)]
    delay = projection.get("delay_minutes")
    nxt = projection.get("next")
    behind = False
    if behind_min and nxt is not None and delay is not None:
        ref, ref_delay = behind_ref(delay, notice, behind_min)
        behind = delay >= ref_delay + behind_min
    if not vs and not behind:
        return None
    keys = [violation_key(v) for v in vs]
    fresh = [v for v, k in zip(vs, keys) if k not in known]
    titles = {i["id"]: (i.get("place") or {}).get("title") or i["place_id"]
              for d in (projection.get("plan") or {}).get("days") or [] for i in d["items"]}
    payload: dict = {"reason": "violations", "new_violations": vs, "delay_minutes": delay,
                     "next": {**nxt, "title": titles.get(nxt["item_id"])} if nxt else nxt}
    item_ids = {v["item_id"] for v in vs if v.get("item_id") is not None}
    if behind:
        keys.append(behind_key(day, ref, delay, ref_delay, behind_min))
        payload["plan_version"] = (plan or {}).get("version")
        payload["end"] = {"planned": _day_end(plan, day), "expected": _day_end(projection.get("plan"), day)}
        payload["suggest"] = behind_suggestion(plan, day, track, payload["next"], at, projection.get("plan"))
        if not fresh:  # 새 위반이 없으면 밀림이 이 이벤트의 이유다 (밀림도 이미 알렸으면 policy.fresh 가 뺀다)
            payload["reason"] = "behind_schedule"
            item_ids = {nxt["item_id"]}
    if payload["reason"] == "behind_schedule":
        n = payload["next"]
        msg = (f"{n['title'] or '다음 장소'} 도착이 {n.get('planned_start')} 예정에서 {n.get('eta')}으로 "
               f"{duration_ko(int(delay))} 늦어질 것 같아요.")
    else:
        first = (fresh or vs)[0]
        msg = f"지금대로면 일정에 문제가 생겨요. {first['message']}"
        if len(vs) > 1:
            msg += f" (그 밖에 {len(vs) - 1}건)"
    return Detection("delay", at, sorted(item_ids), msg, payload=payload,
                     cause_key="|".join(sorted(set(keys))), severity="warning")


def _day_end(plan: dict | None, day: int) -> str | None:
    days = (plan or {}).get("days") or []
    d = next((x for x in days if x.get("day") == day), None)
    return (d or {}).get("end_at")


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


# ---------- 상황 해제 (safety /situation) ----------

SITUATION_CLEARED_MSG = "악천후가 그쳤어요. 지금 구룡포는 특이사항이 없어요."


def judge_situation(sit: dict, state: dict | None, at: datetime, day: int) -> tuple[dict, Detection | None]:
    """safety 상황(GET /safety/situation)을 보고 이 여행일의 상황 기록을 갱신한다 -> (새 기록, 상황 해제 감지 또는 None).
    - 주의·위험: 구역 단계가 caution·danger 이거나 발효 특보가 있음. 처음이면 상황 번호 n 을 하나 올린다 (그 뒤는 구역·특보를 모은다).
    - 해제: 주의·위험이던 상황에서 지금은 주의·위험 구역도 특보도 없고, 그때 주의·위험이던 구역이 지금 모두 safe (값이 없으면 모르는 것 -> 기다림).
      safety_eased (단계 1, payload.reason=situation_cleared). 원인 키 safety_eased:situation:{day}:{n} -> 같은 해제는 한 번만.
    planner 경고 완화는 남은 일정 항목 단계가 내려갈 때만 나와, 위험했던 일정이 이미 지난 뒤 상황이 풀리면 신호가 없다 (docs/console §7.6)."""
    st = dict(state or {"abnormal": False, "n": 0, "zones": [], "warnings": []})
    zones = sit.get("zones") or {}
    bad = sorted(z for z, v in zones.items() if v in ("caution", "danger"))
    warns = sorted(sit.get("warnings") or [])
    if bad or warns:
        if not st.get("abnormal"):
            return {"abnormal": True, "n": st.get("n", 0) + 1, "zones": bad, "warnings": warns,
                    "since": at.isoformat()}, None
        return st | {"zones": sorted(set(st.get("zones") or []) | set(bad)),
                     "warnings": sorted(set(st.get("warnings") or []) | set(warns))}, None
    if not st.get("abnormal") or any(zones.get(z) != "safe" for z in st.get("zones") or []):
        return st, None
    n = st.get("n", 1)
    det = Detection("safety_eased", at, [], SITUATION_CLEARED_MSG, cause_key=f"safety_eased:situation:{day}:{n}",
                    payload={"reason": "situation_cleared", "alert_id": None, "changes": [],
                             "zones": st.get("zones") or [], "warnings": st.get("warnings") or [],
                             "since": st.get("since")})
    return st | {"abnormal": False}, det


# ---------- 앱 활동 ----------

def is_active(active_at: datetime | None, now: datetime, active_min: float) -> bool:
    """마지막 활동(위치 전송·이벤트 조회)이 active_min 분 안이면 활동 중. 기록이 없거나(이 기능 전 여행일) 0 이면 활동 중으로 본다.
    활동 시각이 지금보다 늦어도(가상 시각을 되돌림, 시계 캐시) 활동 중이다."""
    if not active_min or active_at is None:
        return True
    return now - active_at <= timedelta(minutes=active_min)


def bump_active(old: datetime | None, new: datetime) -> datetime:
    """활동 시각 갱신: 늦은 쪽을 남긴다 (trip 시계 캐시가 위치 recorded_at 보다 몇 초 늦어도 줄지 않게).
    다만 가상 시각을 크게(6시간 넘게) 되돌린 것이면 새 값으로."""
    if old is None or new >= old or old - new > timedelta(hours=6):
        return new
    return old


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


def auto_end_reason(now: datetime, trip_date: date, end_at: str | None, auto_end_min: float,
                    located_at: datetime | None = None, active_min: float = 0) -> str | None:
    """자동 종료할 이유, 아니면 None.
    - midnight: 자정이 지남 (언제나)
    - plan_end: 그날 마지막 일정 종료 + auto_end_min 이 지났고, 최근 위치(active_min 분 안)가 없음.
      많이 늦은 채 아직 다니는 중이면(위치가 들어옴) 미루고, 위치가 끊긴 뒤 active_min 분이 지나면 끝낸다."""
    if now.date() > trip_date:
        return "midnight"
    if not end_at:
        return None
    end = datetime.combine(trip_date, time.fromisoformat(end_at), tzinfo=now.tzinfo)
    if now < end + timedelta(minutes=auto_end_min):
        return None
    if active_min and located_at is not None and now - located_at <= timedelta(minutes=active_min):
        return None
    return "plan_end"


def should_auto_end(now: datetime, trip_date: date, end_at: str | None, auto_end_min: float,
                    located_at: datetime | None = None, active_min: float = 0) -> bool:
    return auto_end_reason(now, trip_date, end_at, auto_end_min, located_at, active_min) is not None
