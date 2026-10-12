"""판정 순수 함수 (judge.py) 단위 테스트. DB·HTTP 없음."""
from datetime import date, datetime, timedelta

from app.trip import judge
from app.trip.clock import KST
from app.trip.geo import distance_m, polygon_distance_m
from app.trip.judge import Point, Spot, Track, TrackConfig

from .fakes import circle

CFG = TrackConfig()  # 반경 60 + accuracy (최대 150), 도착 90초, 출발 120초
T0 = datetime(2026, 10, 10, 14, 0, tzinfo=KST)
# 약 300m 간격 (북쪽으로)
A = Spot(1, 1, "가옥거리", "p-a", 35.9880, 129.5570, "planned")
B = Spot(2, 2, "물회집", "p-b", 35.9907, 129.5570, "planned")
C = Spot(3, 3, "문화관", "p-c", 35.9934, 129.5570, "planned")
SPOTS = [A, B, C]


def north(spot: Spot, m: float) -> tuple[float, float]:
    """spot 에서 북쪽으로 m 미터."""
    return spot.lat + m / 111_320, spot.lng


def pt(latlng, sec: float, acc: float = 10) -> Point:
    return Point(latlng[0], latlng[1], acc, T0 + timedelta(seconds=sec))


def run(points: list[Point], track: Track = Track(), spots=SPOTS) -> tuple[Track, list[judge.Detection]]:
    dets = []
    for p in points:
        if judge.ignore_reason(track, p, CFG):
            continue
        track, found = judge.step(track, spots, p, CFG)
        spots = judge.apply_progress(spots, found)
        dets += found
    return track, dets


def test_arrive_after_dwell():
    inside = north(A, 20)
    track, dets = run([pt(inside, s) for s in (0, 30, 60)])
    assert dets == [] and track.cand_item_id == 1
    track, dets = run([pt(inside, 90)], track)
    assert [(d.type, d.item_ids, d.at) for d in dets] == [("arrived", [1], T0)]  # 시각은 처음 들어온 때
    assert track.state == "visiting" and track.item_id == 1 and track.since == T0
    assert dets[0].card == {"kind": "docent", "place_id": "p-a", "item_id": 1}
    assert dets[0].payload["out_of_order"] is False and dets[0].level == 2


def test_boundary_bounce_does_not_arrive():
    """반경(60+10=70m) 경계에서 한 점만 안/밖으로 튀면 도착이 생기지 않는다 (머무름이 다시 시작된다)."""
    pts = [pt(north(A, 65), 0), pt(north(A, 80), 30), pt(north(A, 65), 60), pt(north(A, 80), 90),
           pt(north(A, 65), 120), pt(north(A, 65), 180)]
    _, dets = run(pts)
    assert dets == []
    _, dets = run(pts + [pt(north(A, 65), 210)])  # 120초부터 90초 이어서 안 -> 도착
    assert [d.type for d in dets] == ["arrived"] and dets[0].at == T0 + timedelta(seconds=120)


def test_boundary_bounce_does_not_depart():
    track = Track(state="visiting", item_id=1, since=T0)
    pts = [pt(north(A, 90), 0), pt(north(A, 20), 30), pt(north(A, 90), 60), pt(north(A, 90), 150),
           pt(north(A, 20), 170)]  # 밖으로 나간 지 110초에 다시 안 -> 출발 아님
    track, dets = run(pts, track)
    assert dets == [] and track.state == "visiting" and track.outside_since is None


def test_depart_after_dwell():
    track = Track(state="visiting", item_id=1, since=T0 - timedelta(minutes=50))
    away = north(A, 150)  # A 와 B 사이 (B 반경 밖)
    track, dets = run([pt(away, 0), pt(away, 60), pt(away, 119)], track)
    assert dets == []
    track, dets = run([pt(away, 120)], track)
    assert [(d.type, d.item_ids, d.at) for d in dets] == [("departed", [1], T0)]
    assert dets[0].payload["stayed_minutes"] == 50
    assert track.state == "moving" and track.item_id == 1


def test_depart_then_arrive_next():
    track = Track(state="visiting", item_id=1, since=T0)
    pts = [pt(north(A, 150), s) for s in (0, 60, 120)] + [pt(north(B, 0), s) for s in (300, 330, 360, 390)]
    track, dets = run(pts, track)
    assert [(d.type, d.item_ids) for d in dets] == [("departed", [1]), ("arrived", [2])]
    assert track.state == "visiting" and track.item_id == 2


def test_out_of_order_arrival():
    _, dets = run([pt(north(C, 0), s) for s in (0, 90)])
    assert dets[0].type == "arrived" and dets[0].item_ids == [3] and dets[0].payload["out_of_order"] is True


def test_done_and_skipped_are_not_candidates():
    spots = [judge.replace(A, status="done"), judge.replace(B, status="skipped"), C]
    _, dets = run([pt(north(A, 0), s) for s in (0, 90)] + [pt(north(B, 0), s) for s in (200, 290)], spots=spots)
    assert dets == []


def test_accuracy_widens_radius_up_to_max():
    assert judge.radius_m(CFG, 10) == 70 and judge.radius_m(CFG, 200) == 150
    far = north(A, 120)
    _, dets = run([pt(far, s, acc=80) for s in (0, 90)])  # 반경 140m
    assert [d.type for d in dets] == ["arrived"]
    _, dets = run([pt(far, s, acc=10) for s in (0, 90)])
    assert dets == []


def test_ignore_inaccurate_and_out_of_order():
    track = Track(last_at=T0)
    assert judge.ignore_reason(track, pt(north(A, 0), 10, acc=101), CFG) == "accuracy"
    assert judge.ignore_reason(track, pt(north(A, 0), -1), CFG) == "out_of_order"
    assert judge.ignore_reason(track, pt(north(A, 0), 0), CFG) is None  # 같은 시각은 받는다
    # 부정확한 점·순서가 뒤바뀐 점은 머무름을 끊지도 잇지도 않는다
    pts = [pt(north(A, 10), 0), pt(north(A, 500), 30, acc=150), pt(north(A, 10), 90), pt(north(A, 500), 60)]
    _, dets = run(pts)
    assert [d.type for d in dets] == ["arrived"]


def test_visiting_item_removed_resets_quietly():
    track = Track(state="visiting", item_id=99, since=T0)
    track, dets = run([pt(north(A, 500), 0)], track)
    assert dets == [] and track.state == "idle"


def test_track_from_planner_progress():
    spots = [judge.replace(A, status="done"), judge.replace(B, status="visiting"), C]
    t = judge.track_from_spots(spots, {2: T0})
    assert (t.state, t.item_id, t.since) == ("visiting", 2, T0)
    assert judge.track_from_spots(SPOTS, {}) == Track()


# ---------- 지연 ----------

def violation(code: str, item_id: int | None, **kw) -> dict:
    return {"code": code, "severity": "error", "day": 1, "item_id": item_id, "constraint_id": None,
            "message": f"{code} {item_id}"} | kw


def test_light_delay_has_no_event():
    proj = {"new_violations": [], "delay_minutes": 25, "next": {"item_id": 3}}
    assert judge.judge_delay(proj, T0) is None
    # 일정에 영향이 없는 경고(이동 근사, 긴 도보)만 새로 생겨도 알리지 않는다
    proj["new_violations"] = [violation("travel_estimated", 3, severity="warning"), violation("long_walk", 3)]
    assert judge.judge_delay(proj, T0) is None


def test_delay_on_new_violation():
    proj = {"new_violations": [violation("outside_open_hours", 5, severity="warning"), violation("day_overflow", 5)],
            "delay_minutes": 35, "next": {"item_id": 4}}
    d = judge.judge_delay(proj, T0)
    assert d.type == "delay" and d.level == 3 and d.item_ids == [5] and d.severity == "warning"
    assert d.cause_key == "delay:day_overflow:1:5:None|delay:outside_open_hours:1:5:None"
    assert d.payload["delay_minutes"] == 35 and len(d.payload["new_violations"]) == 2
    # hard 규칙 위반도 지연이다 (soft 는 아님)
    proj["new_violations"] = [violation("rule_end_before", None, constraint_id=7)]
    assert judge.judge_delay(proj, T0).cause_key == "delay:rule_end_before:1:None:7"
    proj["new_violations"] = [violation("rule_end_before", None, constraint_id=7, severity="warning")]
    assert judge.judge_delay(proj, T0) is None


def test_delay_message_explains_fresh_violation_first():
    proj = {"new_violations": [violation("outside_open_hours", 5), violation("fixed_time_missed", 4)]}
    d = judge.judge_delay(proj, T0, known={"delay:outside_open_hours:1:5:None"})
    assert "fixed_time_missed 4" in d.message and "그 밖에 1건" in d.message


# 여유 있는 계획: 가옥거리(12:30~13:30, 머무는 중) -> 구룡포항 13:39 -> … 15:42 종료
BASE = {"version": 3, "days": [{"day": 1, "end_at": "15:42", "items": [
    {"id": 1, "place_id": "p-a", "place": {"title": "가옥거리"}, "status": "visiting", "start_at": "12:30", "stay_minutes": 60},
    {"id": 2, "place_id": "p-b", "place": {"title": "구룡포항"}, "status": "planned", "start_at": "13:39", "stay_minutes": 50}]}]}
VISITING = Track(state="visiting", item_id=1, since=T0.replace(hour=12, minute=30))


def behind(delay: int, eta: str, violations=(), plan_end: str = "15:42") -> dict:
    return {"new_violations": list(violations), "delay_minutes": delay,
            "next": {"item_id": 2, "planned_start": "13:39", "eta": eta, "start_at": eta},
            "plan": {"days": [{"day": 1, "end_at": plan_end, "items": [
                {"id": 2, "place_id": "p-b", "place": {"title": "구룡포항"}}]}]}}


def judge_behind(proj, at, known=frozenset(), notice=None, track=VISITING):
    return judge.judge_delay(proj, at, known, behind_min=15, plan=BASE, day=1, track=track, notice=notice)


def test_behind_schedule_asks_at_15_minutes():
    t = T0.replace(hour=13, minute=54)
    assert judge_behind(behind(14, "13:53"), t) is None  # 15분 미만: 가벼운 지연
    assert judge.judge_delay(behind(64, "14:43"), t) is None  # behind_min 0(기본): 보지 않음 (기존 동작)
    assert judge_behind(behind(15, "13:54"), t).cause_key == "delay:behind:1:0:1"
    d = judge_behind(behind(64, "14:43", plan_end="16:46"), t.replace(hour=14, minute=34))
    assert (d.type, d.level, d.item_ids, d.payload["reason"]) == ("delay", 3, [2], "behind_schedule")
    assert d.message == "구룡포항 도착이 13:39 예정에서 14:43으로 1시간 늦어질 것 같아요."
    assert d.cause_key == "delay:behind:1:0:4"  # 15분 단계 4
    assert d.payload["end"] == {"planned": "15:42", "expected": "16:46"} and d.payload["plan_version"] == 3
    assert d.payload["next"]["title"] == "구룡포항"
    # 추천안: 가옥거리 체류를 계획 시작(12:30)부터 지금(14:34) + 여유 15분 = 140분(5분 올림)으로
    assert d.payload["suggest"] == {"kind": "extend_stay", "item_id": 1, "title": "가옥거리", "stay_minutes": 140,
                                    "from_stay_minutes": 60}
    assert judge.duration_ko(35) == "35분" and judge.duration_ko(75) == "1시간 15분"


def test_behind_schedule_asks_again_15_minutes_after_last_notice():
    t = T0.replace(hour=14, minute=10)
    first = judge_behind(behind(20, "13:59"), t)
    assert first.cause_key == "delay:behind:1:0:1"
    # 알린 뒤(이벤트 9, 그때 20분)에는 그보다 15분 더 밀릴 때마다
    for d in (20, 30, 34):
        assert judge_behind(behind(d, "x"), t, notice=(9, 20)) is None
    again = judge_behind(behind(35, "14:14"), t, notice=(9, 20))
    assert again.cause_key == "delay:behind:1:9:1" and again.payload["reason"] == "behind_schedule"
    assert judge_behind(behind(50, "x"), t, notice=(9, 20)).cause_key == "delay:behind:1:9:2"
    # 위반으로 알린 지연(23분)도 기준이다 -> 다음 항목이 바뀌어도 38분 전에는 다시 묻지 않는다
    assert judge_behind(behind(30, "x"), t, notice=(5, 23)) is None
    assert judge_behind(behind(38, "x"), t, notice=(5, 23)) is not None
    # 따라잡아(직접 고쳐) 지연이 15분 이상 줄면(50 -> 0) 새로 센다: 그 뒤 15분이 되면 다시 묻는다
    assert judge_behind(behind(0, "x"), t, notice=(12, 50)) is None
    assert judge_behind(behind(14, "x"), t, notice=(12, 50)) is None
    reset = judge_behind(behind(15, "x"), t, notice=(12, 50))
    assert reset.cause_key == "delay:behind:1:12r:1"


def test_behind_schedule_counts_fresh_after_accepted_proposal():
    """알린 뒤 제안을 승인했으면(notice 의 세 번째 값) 그때 지연과 상관없이 승인한 일정 기준 0 부터 센다 (§3.2)."""
    t = T0.replace(hour=16, minute=40)
    # 20분으로 알림 -> 승인. 지연이 줄지 않았어도(승인한 변경이 다 흡수하지 못함) 새 일정 기준 15분이면 묻는다
    assert judge_behind(behind(14, "x"), t, notice=(12, 20, True)) is None
    d = judge_behind(behind(15, "x"), t, notice=(12, 20, True))
    assert d.cause_key == "delay:behind:1:12a:1" and d.payload["reason"] == "behind_schedule"
    assert judge_behind(behind(32, "x"), t, notice=(12, 20, True)).cause_key == "delay:behind:1:12a:2"
    # 승인하지 않았으면 그때 지연(20) + 15 = 35분까지 조용
    assert judge_behind(behind(32, "x"), t, notice=(12, 20, False)) is None
    assert judge.behind_ref(32, (12, 20, False), 15) == ("12", 20)


DAY_OVERFLOW = "delay:day_overflow:1:5:None"


def test_resolve_violations_after_grace():
    """알린 위반이 재계산에서 10분 이어서 사라지면 해소 (released), 그 전에 돌아오면 그대로 (§4.1 다시 알리기)."""
    t = T0.replace(hour=16, minute=20)
    known = {DAY_OVERFLOW, "delay:behind:1:0:1", "arrived:3"}
    st = judge.resolve_violations(None, set(), known, t, 10, last_id=40)
    assert st == {"gone": {DAY_OVERFLOW: t.isoformat()}, "released": {}}  # 밀림·도착 키는 보지 않는다
    # 5분 뒤 다시 나타남 -> 사라진 시각을 지운다 (깜빡임은 해소가 아니다)
    st = judge.resolve_violations(st, {DAY_OVERFLOW}, known, t + timedelta(minutes=5), 10, last_id=40)
    assert st["gone"] == {} and st["released"] == {}
    # 다시 사라져 10분 -> 해소: 그때까지의 이벤트(id 41 까지)는 이 원인을 알린 것으로 세지 않는다
    st = judge.resolve_violations(st, set(), known, t + timedelta(minutes=6), 10, last_id=41)
    st = judge.resolve_violations(st, set(), known, t + timedelta(minutes=15), 10, last_id=41)
    assert st["gone"] == {DAY_OVERFLOW: (t + timedelta(minutes=6)).isoformat()}
    st = judge.resolve_violations(st, set(), known, t + timedelta(minutes=16), 10, last_id=41)
    assert st["gone"] == {} and st["released"] == {DAY_OVERFLOW: 41}
    # 해소된 원인은 known 에서 빠져 있으므로 더 보지 않는다 / 시각이 되돌아가면 사라진 시각을 지금으로
    st2 = judge.resolve_violations({"gone": {DAY_OVERFLOW: t.isoformat()}}, set(), known, t - timedelta(hours=1), 10, 9)
    assert st2["gone"] == {DAY_OVERFLOW: (t - timedelta(hours=1)).isoformat()} and st2["released"] == {}
    assert judge.resolve_violations({"accepted": {"event": 1}}, set(), set(), t, 10, 9)["accepted"] == {"event": 1}


def test_behind_and_violation_together_make_one_event():
    t = T0.replace(hour=15, minute=45)
    v = violation("outside_open_hours", 5)
    d = judge_behind(behind(31, "x", [v]), t)
    assert d.payload["reason"] == "violations" and d.item_ids == [5] and "outside_open_hours 5" in d.message
    assert d.cause_key == "delay:behind:1:0:2|delay:outside_open_hours:1:5:None"  # 31분: 15분 단계 2
    assert d.payload["suggest"]["kind"] == "extend_stay"  # 밀림 정보도 담는다
    # 위반은 이미 알렸고 밀림만 새것이면 밀림이 이유
    d = judge_behind(behind(31, "16:16", [v]), t, known={"delay:outside_open_hours:1:5:None"})
    assert d.payload["reason"] == "behind_schedule" and d.item_ids == [2] and d.message.startswith("구룡포항 도착이")


def test_behind_suggestion_while_moving_shifts_next_start():
    moving = Track(state="moving", item_id=1, since=T0)
    d = judge_behind(behind(33, "14:12"), T0.replace(hour=14, minute=5), track=moving)
    assert d.payload["suggest"] == {"kind": "shift_next", "item_id": 2, "title": "구룡포항", "start_time": "14:15",
                                    "from_start": "13:39"}


# ---------- 위험 구역 ----------

ZONE = {"id": "flood_4", "name": "구룡포수협_스마트맨홀 주변", "kind": "flood", "risk": "danger", "reason": "등급 3",
        "polygon": circle(35.991235, 129.557327, 80)}


def test_polygon_distance():
    assert polygon_distance_m(35.991235, 129.557327, ZONE["polygon"]) == 0
    d = polygon_distance_m(35.991235 + 120 / 111_320, 129.557327, ZONE["polygon"])
    assert 38 < d < 42  # 반경 80m 원(16각형) 밖 약 40m


def test_near_hazard():
    inside = judge.judge_hazard(35.991235, 129.557327, T0, [ZONE], 50)
    assert inside.type == "near_hazard" and inside.level == 1 and inside.severity == "danger"
    assert inside.message.startswith("침수 감시 구역(구룡포수협_스마트맨홀 주변) 근처예요")
    assert inside.cause_key == "near_hazard:flood_4:danger"
    near = judge.judge_hazard(35.991235 + 120 / 111_320, 129.557327, T0, [ZONE], 50)  # 구역 밖 40m
    assert near is not None and near.payload["zones"][0]["distance_m"] in (39, 40, 41)
    assert judge.judge_hazard(35.991235 + 150 / 111_320, 129.557327, T0, [ZONE], 50) is None  # 70m


def test_near_hazard_merges_zones_nearest_first():
    other = ZONE | {"id": "flood_11", "name": "구룡포수협_지표면 수위계 주변", "risk": "caution",
                    "polygon": circle(35.991109, 129.557354, 80)}
    d = judge.judge_hazard(35.991109, 129.557354, T0, [other, ZONE], 50)  # 두 구역 안 -> 더 위험한 쪽부터
    assert [z["id"] for z in d.payload["zones"]] == ["flood_4", "flood_11"]
    d = judge.judge_hazard(35.991109 - 100 / 111_320, 129.557354, T0, [ZONE, other], 50)  # 남쪽: flood_11 이 더 가깝다
    assert [z["id"] for z in d.payload["zones"]] == ["flood_11", "flood_4"]
    assert d.cause_key == "near_hazard:flood_11:caution|near_hazard:flood_4:danger" and d.severity == "danger"


# ---------- 안전 경고 ----------

def change(kind: str, item_id: int, frm: str, to: str, day: str = "2026-10-10") -> dict:
    return {"kind": kind, "from_risk": frm, "to_risk": to,
            "item": {"item_id": item_id, "date": day, "title": f"장소{item_id}", "start_at": "15:00",
                     "hazards": [{"level": to, "reason": "풍랑 특보"}]}}


def test_alert_today_remaining_only():
    alert = {"id": 7, "needs_action": True, "situation": "fp",
             "changes": [change("raised", 2, "safe", "caution"), change("raised", 1, "safe", "danger"),
                         change("raised", 3, "safe", "danger", day="2026-10-11"), change("cleared", 4, "caution", "safe")]}
    dets = judge.judge_alert(alert, date(2026, 10, 10), remaining={2, 4}, at=T0)
    raised, eased = dets
    assert (raised.type, raised.level, raised.item_ids, raised.severity) == ("safety_raised", 3, [2], "warning")
    assert raised.cause_key == "safety_raised:7" and "15:00 장소2: 안전 → 주의 (풍랑 특보)" in raised.message
    assert (eased.type, eased.level, eased.item_ids) == ("safety_eased", 1, [4])
    assert eased.message == "장소4의 위험 신호가 해제됐어요."


def test_alert_without_today_changes_is_ignored():
    alert = {"id": 8, "needs_action": True, "changes": [change("raised", 3, "safe", "danger", day="2026-10-11")]}
    assert judge.judge_alert(alert, date(2026, 10, 10), {3}, T0) == []


# ---------- 기타 ----------

def test_auto_end():
    d = date(2026, 10, 10)
    assert not judge.should_auto_end(T0, d, "17:00", 120)
    assert not judge.should_auto_end(T0.replace(hour=18, minute=59), d, "17:00", 120)
    assert judge.should_auto_end(T0.replace(hour=19), d, "17:00", 120)
    assert not judge.should_auto_end(T0.replace(hour=23), d, None, 120)  # 일정이 없으면 자정까지
    assert judge.should_auto_end(datetime(2026, 10, 11, 0, 1, tzinfo=KST), d, None, 120)


def test_departed_message():
    msg = judge.departed_message("가옥거리", {"item_id": 3, "eta": "15:12"}, "과메기문화관", "walk", T0.replace(hour=15, minute=4))
    assert msg == "가옥거리을(를) 떠났어요. 다음은 과메기문화관, 도보 약 8분 (15:12 도착 예정)."
    assert judge.departed_message("가옥거리", None, None, None, T0) == "가옥거리을(를) 떠났어요."


def test_distance():
    assert 299 < distance_m(A.lat, A.lng, B.lat, B.lng) < 302


def test_day_end_at_uses_plan_or_actual_done():
    plan = {"days": [{"end_at": "17:06", "items": [{"done_at": None}, {"done_at": "17:40:00"}]}]}
    assert judge.day_end_at(plan, 1) == "17:40"
    plan["days"][0]["items"][1]["done_at"] = "16:00"
    assert judge.day_end_at(plan, 1) == "17:06"
    assert judge.day_end_at(plan, 2) is None
    assert judge.day_end_at({"days": [{"end_at": None, "items": []}]}, 1) is None


# ---------- 가까운 두 장소 사이 이동 (반경이 겹침, 암묵 전환) ----------

# 가옥거리 -> 근대문화역사관 약 104m (fakes.PLACES 좌표). 반경 70m 가 겹친다
HOUSE = Spot(11, 1, "가옥거리", "kto-1909659", 35.9906209551, 129.5604550366, "visiting")
HIST = Spot(12, 2, "근대문화역사관", "kto-2603493", 35.9905, 129.5616, "planned")
NEAR = [HOUSE, HIST]
WALK_SEC = 25 / 70 * 60  # 25m 간격, 70m/분


def between(f: float) -> tuple[float, float]:
    """가옥거리(0) -> 역사관(1) 사이 f 지점."""
    return HOUSE.lat + (HIST.lat - HOUSE.lat) * f, HOUSE.lng + (HIST.lng - HOUSE.lng) * f


def staying_house() -> Track:
    return Track(state="visiting", item_id=11, since=T0 - timedelta(minutes=30), last_at=T0 - timedelta(seconds=30))


def near_walk() -> list[Point]:
    """가옥거리에서 역사관까지 25m 간격으로 걷고, 역사관에 30초 간격으로 3분 머문다."""
    total = distance_m(*between(0), *between(1))
    n = int(total // 25)
    pts = [pt(between(min(i * 25 / total, 1)), i * WALK_SEC) for i in range(n + 1)] + [pt(between(1), n * WALK_SEC + 1)]
    end = pts[-1].recorded_at
    return pts + [Point(HIST.lat, HIST.lng, 10, end + timedelta(seconds=s)) for s in range(30, 181, 30)]


def test_near_places_switch_after_arrive_dwell():
    pts = near_walk()
    # 첫 'B 가 더 가깝고 B 반경 안' 점
    first = next(p for p in pts if distance_m(p.lat, p.lng, HIST.lat, HIST.lng) < distance_m(p.lat, p.lng, HOUSE.lat, HOUSE.lng)
                 and distance_m(p.lat, p.lng, HIST.lat, HIST.lng) <= 70)
    before = [p for p in pts if p.recorded_at < first.recorded_at + timedelta(seconds=90)]
    track, dets = run(before, staying_house(), NEAR)
    assert dets == [] and track.state == "visiting" and track.item_id == 11 and track.cand_item_id == 12
    track, dets = run(pts, staying_house(), NEAR)
    assert [(d.type, d.item_ids) for d in dets] == [("departed", [11]), ("arrived", [12])]
    assert dets[1].at == first.recorded_at and dets[0].at <= dets[1].at
    assert dets[0].payload["stayed_minutes"] == 31 and dets[1].card["place_id"] == "kto-2603493"
    assert track.state == "visiting" and track.item_id == 12 and track.since == first.recorded_at
    assert (track.cand_item_id, track.outside_since) == (None, None)


def test_overlap_but_house_closer_stays():
    p = between(0.4)  # 가옥거리 약 42m, 역사관 약 62m: 둘 다 반경 안이지만 가옥거리가 더 가깝다
    track, dets = run([pt(p, s) for s in range(0, 601, 30)], staying_house(), NEAR)
    assert dets == [] and (track.state, track.item_id, track.cand_item_id) == ("visiting", 11, None)


def test_brief_closer_to_next_then_back_resets_candidate():
    pts = [pt(between(0.7), s) for s in (0, 30, 60)] + [pt(between(0.2), s) for s in (90, 120, 300)]
    track, dets = run(pts, staying_house(), NEAR)
    assert dets == [] and (track.state, track.item_id, track.cand_item_id, track.cand_since) == ("visiting", 11, None, None)


def test_no_implicit_switch_to_done_or_skipped():
    for status in ("done", "skipped"):
        spots = [HOUSE, judge.replace(HIST, status=status)]
        pts = [pt(between(1), s) for s in range(0, 181, 30)]  # 가옥거리 반경(70m) 밖, 역사관 위
        track, dets = run(pts[:4], staying_house(), spots)  # 90초: 암묵 전환 없음
        assert dets == []
        track, dets = run(pts, staying_house(), spots)  # 120초 정상 출발만, 도착 없음
        assert [(d.type, d.at) for d in dets] == [("departed", T0)]
        assert track.state == "moving"


def test_normal_departure_keeps_earlier_entry_to_next():
    """정상 출발(반경 밖 120초)이 먼저 나도, 그 전에 들어온 B 반경 진입 시각을 이어받아 도착 시각이 된다."""
    track = Track(state="visiting", item_id=1, since=T0 - timedelta(minutes=30))
    pts = [pt(north(A, 150), 0), pt(north(B, 0), 60), pt(north(B, 0), 90), pt(north(B, 0), 120)]
    track, dets = run(pts, track)
    assert [(d.type, d.at) for d in dets] == [("departed", T0)]
    assert (track.state, track.cand_item_id, track.cand_since) == ("moving", 2, T0 + timedelta(seconds=60))
    track, dets = run([pt(north(B, 0), 150)], track)
    assert [(d.type, d.at) for d in dets] == [("arrived", T0 + timedelta(seconds=60))]  # 출발 점부터 다시 세지 않음
