"""trip·sim CLI 명령 (scripts/planner/cli.py 에 등록된다). 명세: docs/trip/README.md §8

    trip start 3 [--session SID]          여행일 시작 / trip end 3 / trip state 3
    trip loc 3 35.987 129.558 [--acc 10] [--at 14:20]     위치 한 점
    trip goto 3 kto-2660046 [--wait 120]  그 장소 좌표로 보내고 머무름 시간만큼 시계를 진행 (도착 판정 확인용)
    trip replay 3 scene3_delay.json [--speed 10]          경로 재생 (plan 자리에 new: 재생 파일의 계획을 새로 만듦)
                                                          (accept 단계: 선제 턴의 제안을 Agent 로 승인)
    trip events 3 [--follow] [--seen 41]  이벤트 보기 (--follow: 폴링, --seen: 확인 표시)
    trip tick 3                           틱(지연·안전 경고·자동 종료)을 지금 한 번
    sim / sim scenario storm / sim now 2026-10-10T14:00 / sim reset    safety 가상 상황·시각
"""
import json
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scene import expand, matches

KST = timezone(timedelta(hours=9))
SCENES = Path(__file__).resolve().parent / "scenes"
BOLD, DIM, RED, GREEN, YELLOW, RESET = "\033[1m", "\033[2m", "\033[31m", "\033[32m", "\033[33m", "\033[0m"
SEVERITY = {"danger": RED, "warning": YELLOW, "info": ""}


# ---------- 출력 ----------

def print_state(s: dict) -> None:
    if not s["active"]:
        tail = f" (종료: {s['ended_reason']})" if s.get("ended_at") else ""
        print(f"[계획 #{s['plan_id']}] 여행 중 아님{tail} · 지금 {s['now'][:16]} ({s['clock']})")
        return
    print(f"{BOLD}[계획 #{s['plan_id']}] {s['day']}일차 {s['date']} 여행 중{RESET} · 지금 {s['now'][11:16]} ({s['clock']})"
          + (f" · 세션 {s['session_id'][:8]}" if s.get("session_id") else ""))
    cur, loc, nxt = s.get("current") or {}, s.get("location"), s.get("next")
    label = {"visiting": "머무는 중", "moving": "이동 중", "idle": "대기"}.get(cur.get("state"), "-")
    print(f"  상태: {label}" + (f" (항목 {cur['item_id']}, {cur['since']}부터)" if cur.get("item_id") else ""))
    if loc:
        print(f"  위치: {loc['lat']:.6f}, {loc['lng']:.6f} ±{loc['accuracy']:.0f}m ({loc['recorded_at'][11:19]}, {loc['source']})")
    if nxt:
        delay = s.get("delay_minutes")
        late = "" if delay is None else (f", {delay}분 늦음" if delay > 0 else f", {-delay}분 여유" if delay < 0 else ", 예정대로")
        print(f"  다음: {nxt.get('title') or nxt['item_id']} {nxt.get('planned_start') or '-'} 예정 → 예상 도착 {nxt.get('eta') or '-'}{late}")


def print_event(e: dict) -> None:
    color = SEVERITY.get(e["severity"], "")
    extra = ""
    if e.get("agent"):
        extra = f" → Agent 메시지 {e['agent'].get('message_id')}, 제안 {e['agent'].get('proposal_id')}"
    elif e["status"] == "queued" and e.get("last_error"):
        extra = f" (대기, 시도 {e['attempts']}번: {e['last_error'][:60]})"
    elif e["status"] == "failed":
        extra = f" (전송 실패 {e['attempts']}번: {(e.get('last_error') or '')[:60]})"
    if e.get("card"):
        extra += f" [카드 {e['card']['kind']}]"
    print(f"  {color}#{e['id']} {e['now'][11:16]} {e['type']} (단계 {e['level']}, {e['status']}){RESET} {e['message']}{DIM}{extra}{RESET}")


def _print_result(r: dict, quiet: bool = False) -> None:
    for e in r.get("events", []):
        print_event(e)
    if not quiet:
        print_state(r["state"])


# ---------- 시각 ----------

def _sim_now(c) -> datetime | None:
    """safety 가상 시각. 가상 상황이 꺼져 있으면(404) None."""
    try:
        return datetime.fromisoformat(c.sim()["now"]).astimezone(KST)
    except Exception:
        return None


def _now(c) -> datetime:
    return _sim_now(c) or datetime.now(KST)


def _at(c, a, plan: int) -> datetime:
    """--at HH:MM -> 여행일(없으면 지금) 날짜의 그 시각."""
    if not a.at:
        return _now(c)
    state = c.trip_state(plan)
    day = datetime.fromisoformat(state["date"]).date() if state.get("date") else _now(c).date()
    h, m = a.at.split(":")[:2]
    return datetime(day.year, day.month, day.day, int(h), int(m), tzinfo=KST)


def _point(lat: float, lng: float, at: datetime, acc: float | None, source: str) -> dict:
    p = {"lat": lat, "lng": lng, "recorded_at": at.isoformat(), "source": source}
    if acc is not None:
        p["accuracy"] = acc
    return p


# ---------- trip ----------

def cmd_start(c, a):
    print_state(c.trip_start(a.plan, a.session))


def cmd_end(c, a):
    print_state(c.trip_end(a.plan))


def cmd_state(c, a):
    s = c.trip_state(a.plan)
    if a.json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
    else:
        print_state(s)


def cmd_loc(c, a):
    _print_result(c.trip_location(a.plan, [_point(a.lat, a.lng, _at(c, a, a.plan), a.acc, "cli")]))


def cmd_goto(c, a):
    """장소 좌표로 30초마다 wait 초 동안 점을 보내고, 가상 시각도 그만큼 옮긴다 (도착 판정 확인용)."""
    place = c.get_place(a.place_id)
    t0 = _now(c)
    pts = [_point(place["lat"], place["lng"], t0 + timedelta(seconds=s), a.acc, "cli")
           for s in range(0, max(a.wait, 0) + 1, 30)]
    if a.wait % 30:
        pts.append(_point(place["lat"], place["lng"], t0 + timedelta(seconds=a.wait), a.acc, "cli"))
    print(f"{place['title']} ({place['lat']:.6f}, {place['lng']:.6f}) {t0:%H:%M:%S}부터 {a.wait}초 머묾")
    r = c.trip_location(a.plan, pts)
    if a.wait > 0 and _sim_now(c) is not None:
        c.set_sim(now=(t0 + timedelta(seconds=a.wait)).isoformat())
    _print_result(r)


def cmd_events(c, a):
    if a.seen is not None:
        print_event(c.trip_event_seen(a.plan, a.seen))
        return
    after = a.after
    while True:
        for e in c.trip_events(a.plan, after, 200):
            print_event(e)
            after = e["id"]
        if not a.follow:
            return
        try:
            _time.sleep(a.interval)
        except KeyboardInterrupt:
            return


def cmd_tick(c, a):
    _print_result(c.trip_tick(a.plan, _at(c, a, a.plan).isoformat() if a.at else None))


def _scene_path(name: str) -> Path:
    p = Path(name)
    if p.exists():
        return p
    for cand in (SCENES / name, SCENES / f"{name}.json"):
        if cand.exists():
            return cand
    raise SystemExit(f"재생 파일을 찾을 수 없습니다: {name} (scripts/trip/scenes/ 에 {', '.join(x.name for x in SCENES.glob('*.json'))})")


def _create_plan(c, spec: dict) -> int:
    p = c.create_plan(spec["title"], spec["start_date"], spec.get("end_date"), spec["day_start"], spec["day_end"],
                      spec.get("travel_mode", "walk"))
    for it in spec["items"]:
        c.add_item(p["id"], it["place_id"], it.get("day", 1), stay_minutes=it.get("stay_minutes", 60),
                   start_time=it.get("start_time"), travel_mode=it.get("travel_mode"))
    for r in spec.get("constraints", []):
        c.add_constraint(p["id"], r["type"], r["params"], hard=r.get("hard", True), note=r.get("note", ""))
    print(f"계획 #{p['id']} 를 만들었습니다 ({spec['title']}, {len(spec['items'])}곳)")
    return p["id"]


def cmd_replay(c, a):
    """재생 파일을 시간순으로 실제 API 에 보낸다. --speed: 시뮬레이션 시각이 흐르는 배속 (0 이면 기다리지 않음).
    가상 시각(safety sim)을 재생 시각에 맞춰 옮긴다 (SAFETY_SOURCE=mock 이어야 planner·trip 의 '지금'이 맞는다)."""
    data = json.loads(_scene_path(a.scene).read_text(encoding="utf-8"))
    print(f"{BOLD}{data.get('name', a.scene)}{RESET}  {DIM}{data.get('description', '')}{RESET}")
    plan = _create_plan(c, data["plan"]) if a.plan == "new" else int(a.plan)
    places: dict[str, tuple[float, float]] = {}

    def coords(pid: str) -> tuple[float, float]:
        if pid not in places:
            p = c.get_place(pid)
            places[pid] = (p["lat"], p["lng"])
        return places[pid]

    def path(src, dst, mode="walk"):
        try:
            return [(lat, lng) for lng, lat in c.route([src, dst], mode)["geometry"]["coordinates"]]
        except Exception as e:
            print(f"{DIM}  (경로선을 못 받아 직선으로 갑니다: {e}){RESET}")
            return None

    actions = expand(data, coords, path)
    sim_ok, started, prev, passed, failed = True, False, None, 0, 0
    for act in actions:
        if prev is not None and a.speed > 0:
            _time.sleep(max(0.0, (act.at - prev).total_seconds() / a.speed))
        prev = act.at
        if sim_ok:  # 가상 시각을 재생 시각에 맞춘다 (--speed 0 이어도 모든 동작마다)
            try:
                c.set_sim(scenario=act.data.get("scenario") if act.kind == "sim" else None, now=act.at.isoformat())
            except Exception as e:
                sim_ok = False
                print(f"{YELLOW}가상 상황을 쓸 수 없습니다 (SAFETY_SOURCE=mock 필요): {e}. 위치의 recorded_at 만으로 재생합니다.{RESET}")
        if act.kind == "sim":
            print(f"{DIM}{act.at:%H:%M:%S}{RESET} 가상 상황: {act.data.get('scenario', '-')}")
            if not started:
                if sim_ok:
                    _wait_clock(c, plan, act.at)
                print_state(c.trip_start(plan))
                started = True
            else:
                _tick(c, plan, act.at, wait=sim_ok and a.speed == 0)
        elif act.kind == "loc":
            r = c.trip_location(plan, [_point(act.lat, act.lng, act.at, 10, "replay")])
            if a.verbose:
                print(f"{DIM}{act.at:%H:%M:%S} {act.lat:.6f}, {act.lng:.6f}{RESET}")
            for e in r["events"]:
                print_event(e)
        elif act.kind == "skip":
            _wait_proactive(c, plan)
            item = next((i for d in c.get_plan(plan)["days"] for i in d["items"]
                         if i["place_id"] == act.data["place_id"] and i["status"] == "planned"), None)
            if item is None:
                print(f"{YELLOW}건너뛸 항목이 없습니다: {act.data['place_id']}{RESET}")
            else:
                c.update_item(plan, item["id"], status="skipped")
                print(f"{DIM}{act.at:%H:%M:%S}{RESET} 일정에서 뺌: {item['place']['title'] if item['place'] else item['place_id']}")
        elif act.kind == "accept":
            _wait_proactive(c, plan)
            _accept_proactive(c, plan, act.at, act.data)
        elif act.kind == "tick":
            _tick(c, plan, act.at, wait=sim_ok and a.speed == 0)
        elif act.kind == "expect":
            if a.speed > 0:
                _time.sleep(0.5)  # 응답 뒤 선제 턴 전송(백그라운드)을 조금 기다린다
            ok = matches(act.data, c.trip_events(plan, 0, 200))
            passed, failed = passed + ok, failed + (not ok)
            print(f"{act.at:%H:%M:%S} 기대 {act.data}: {GREEN + '통과' if ok else RED + '실패'}{RESET}")
    if sim_ok and prev is not None and a.speed == 0:
        _wait_clock(c, plan, prev)  # 끝난 뒤 상태와 이어서 하는 `trip tick` 이 재생 마지막 시각을 보게
    print()
    print_state(c.trip_state(plan))
    print(f"\n기대 {passed + failed}개 중 {passed}개 통과" + (f", {RED}{failed}개 실패{RESET}" if failed else ""))


def _tick(c, plan: int, at: datetime, wait: bool = False) -> None:
    """planner 안전 점검(개발 모드)을 바로 돌리고 재생 시각으로 trip 틱. 운영 모드면 점검은 planner 주기(60초)에 맡긴다.
    wait: 틱이 보내는 선제 턴은 trip·planner 의 지금을 쓰므로, 기다리지 않는 재생(--speed 0)이면 시계가 따라올 때까지 기다린다."""
    if wait:
        _wait_clock(c, plan, at)
    try:
        c.safety_scan()
    except Exception:
        pass
    _print_result(c.trip_tick(plan, at.isoformat()), quiet=True)


def _accept_proactive(c, plan: int, at: datetime, expect: dict) -> None:
    """가장 최근 선제 턴(단계 3)의 제안을 승인한다 (앱의 [전체 적용]). expect {"remove", "add"}: 기대하는 변경 — 확인만 한다."""
    ev = next((e for e in reversed(c.trip_events(plan, 0, 200))
               if e["level"] == 3 and (e.get("agent") or {}).get("proposal_id")), None)
    if ev is None:
        print(f"{YELLOW}{at:%H:%M:%S} 승인할 선제 제안이 없습니다 (선제 턴이 제안을 만들지 않음).{RESET}")
        return
    sid, pid = ev["agent"]["session_id"], ev["agent"]["proposal_id"]
    try:
        r = c.accept_proposal(sid, pid)
    except Exception as e:  # PlannerApiError (409 progress_changed·base_changed 등)
        print(f"{YELLOW}{at:%H:%M:%S} 제안 #{pid} 승인 실패: {e}{RESET}")
        return
    places = {i["place_id"] for d in r["plan"]["days"] for i in d["items"] if i["status"] != "skipped"}
    print(f"{DIM}{at:%H:%M:%S}{RESET} 선제 제안 #{pid} 승인 ({r['proposal']['status']})")
    if expect.get("remove") in places or (expect.get("add") and expect["add"] not in places):
        print(f"{YELLOW}  기대한 변경(빼기 {expect.get('remove')}, 넣기 {expect.get('add')})과 다릅니다. 재생은 계속합니다.{RESET}")


def _wait_proactive(c, plan: int, limit_sec: float = 120) -> None:
    """보내는 중인 선제 턴(단계 3 queued)이 끝날 때까지 기다린다. 사용자는 Agent 의 말을 보고 일정을 바꾸므로,
    기다리지 않는 재생(--speed 0)에서 일정 변경(skip)이 응답 도중에 끼어들어 제안이 버려지지 않게 한다."""
    end = _time.monotonic() + limit_sec
    while _time.monotonic() < end:
        if not any(e["status"] == "queued" and e["level"] == 3 for e in c.trip_events(plan, 0, 200)):
            return
        _time.sleep(2)
    print(f"{YELLOW}선제 턴이 {limit_sec:.0f}초 안에 끝나지 않았습니다. 그대로 진행합니다.{RESET}")


def _wait_clock(c, plan: int, target: datetime, limit_sec: float = 10) -> None:
    """trip·planner 는 가상 시각을 몇 초씩 기억한다(TRIP_CLOCK_CACHE_SEC). 가상 시각을 크게 옮긴 직후 시작하면
    '여행일이 아님'이 될 수 있어, trip 의 지금이 재생 시각에 가까워질 때까지 기다린다.
    (planner 도 같은 길이로 캐시하므로 trip 이 따라오면 planner 도 곧 따라온다)"""
    end = _time.monotonic() + limit_sec
    while _time.monotonic() < end:
        now = datetime.fromisoformat(c.trip_state(plan)["now"])
        if abs((now - target).total_seconds()) < 60:
            return
        _time.sleep(1)
    print(f"{YELLOW}trip 의 지금이 재생 시각({target:%H:%M})을 따라오지 않았습니다 (TRIP_CLOCK=safety 인지 확인).{RESET}")


# ---------- sim ----------

def print_sim(s: dict) -> None:
    print(f"가상 상황: {s['scenario']} · 지금 {s['now'][:19]} (실제와 {s['offset_minutes']}분 차이)")
    print(f"{DIM}  시나리오: {', '.join(s['scenarios'])}{RESET}")


def cmd_sim(c, a):
    if a.sim_cmd is None:
        print_sim(c.sim())
    elif a.sim_cmd == "scenario":
        print_sim(c.set_sim(scenario=a.name))
    elif a.sim_cmd == "now":
        when = a.when
        if len(when) <= 5:  # HH:MM -> 가상 시각의 날짜
            when = f"{_now(c):%Y-%m-%d}T{when}"
        t = datetime.fromisoformat(when)
        print_sim(c.set_sim(now=(t if t.tzinfo else t.replace(tzinfo=KST)).isoformat()))
    elif a.sim_cmd == "reset":
        print_sim(c.set_sim(reset_clock=True))


# ---------- 등록 ----------

def register(sub) -> None:
    """cli.py 의 서브파서에 trip·sim 명령을 붙인다."""
    tp = sub.add_parser("trip", help="여행 중 (동행 모드): start·end·state·loc·goto·replay·events·tick")
    ts = tp.add_subparsers(dest="trip_cmd", required=True, metavar="명령")

    def add(name, fn, help_):
        p = ts.add_parser(name, help=help_)
        p.set_defaults(fn=fn)
        p.add_argument("plan", **({} if name == "replay" else {"type": int}),
                       help="계획 id" + (" (new: 재생 파일의 계획을 새로 만듦)" if name == "replay" else ""))
        return p

    p = add("start", cmd_start, "여행일 시작 (오늘이 계획 기간이어야 함)")
    p.add_argument("--session", help="선제 턴을 보낼 Agent 세션 (생략: 최근 세션)")
    add("end", cmd_end, "여행일 종료")
    p = add("state", cmd_state, "지금 상태")
    p.add_argument("--json", action="store_true")
    p = add("loc", cmd_loc, "위치 한 점")
    p.add_argument("lat", type=float)
    p.add_argument("lng", type=float)
    p.add_argument("--acc", type=float, default=10, help="정확도 m (기본 10)")
    p.add_argument("--at", help="시각 HH:MM (생략: 지금)")
    p = add("goto", cmd_goto, "장소로 가서 머무름 (30초마다 점, 가상 시각도 진행)")
    p.add_argument("place_id")
    p.add_argument("--wait", type=int, default=120, help="머무는 초 (기본 120: 도착 판정 90초 이상)")
    p.add_argument("--acc", type=float, default=10)
    p = add("replay", cmd_replay, "재생 파일로 경로 재생 (scripts/trip/scenes/)")
    p.add_argument("scene", help="파일 경로 또는 scenes/ 안의 이름 (예: scene3_delay)")
    p.add_argument("--speed", type=float, default=10, help="배속 (기본 10, 0 이면 기다리지 않음)")
    p.add_argument("-v", "--verbose", action="store_true", help="보내는 점도 출력")
    p = add("events", cmd_events, "이벤트 보기 (failed: 선제 턴 전송을 여러 번 실패해 더 보내지 않음)")
    p.add_argument("--after", type=int, default=0)
    p.add_argument("--seen", type=int, metavar="ID", help="이 이벤트를 확인 표시 (delivered -> handled)")
    p.add_argument("--follow", "-f", action="store_true", help="계속 폴링 (Ctrl+C 로 끝)")
    p.add_argument("--interval", type=float, default=3)
    p = add("tick", cmd_tick, "틱을 지금 한 번 (지연 재판정·안전 경고 수집·자동 종료·선제 턴 전송)")
    p.add_argument("--at", help="판정 시각 HH:MM (생략: trip 의 지금)")

    sp = sub.add_parser("sim", help="safety 가상 상황·시각 (SAFETY_SOURCE=mock): sim / sim scenario storm / sim now 14:00")
    sp.set_defaults(fn=cmd_sim)
    ss = sp.add_subparsers(dest="sim_cmd", metavar="명령")
    p = ss.add_parser("scenario", help="calm | rain | wind_tomorrow | flood | storm")
    p.add_argument("name")
    p = ss.add_parser("now", help="가상 시각 (2026-10-10T14:00 또는 HH:MM)")
    p.add_argument("when")
    ss.add_parser("reset", help="실제 시각으로")
