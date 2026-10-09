"""docent·가이드·safety·routing·places CLI 명령 (scripts/planner/cli.py 에 등록된다). 명세: docs/planner/README.md §11

    memory [--plan 3] / remember --scope plan --plan 3 --cat companion "어머니 동행" / forget 12
    notes 3 / note 3 kto-1909659 [--long] / guide-prepare 3 [--places kto-1,kto-2] [--wait]
    outlook 2026-10-10 [--from 12:00 --to 18:00] / status / zones / situation / avoid [--min danger]
    route kto-1909659 kto-2610501 [--car] [--avoid] / legs … / matrix … [--to …] / rstatus
    passages "일제강점기 어업" [--place kto-…] [--near LAT,LNG --radius 300] / stories kto-… / stories-near LAT,LNG
모든 명령에 --json (API 응답 그대로).
"""
import time as _time

from fmt import LEVEL, dump, hhmm, latlng, short, table, when

CATEGORY = {"interest": "관심사", "style": "해설 스타일", "companion": "동행자·제약", "food": "음식",
            "avoid": "피하고 싶은 것", "known": "이미 아는 것", "feedback": "반응"}
NOTE_STATUS = {"pending": "대기", "preparing": "준비 중", "ready": "준비됨", "failed": "실패", "stale": "다시 만들기 필요"}
TIER = {"official": "공식", "public": "공공", "media": "언론", "community": "커뮤니티", "unknown": "모름"}
MODE = {"walk": "도보", "car": "차량"}


def _title(c, place_id: str, cache: dict[str, str] = {}) -> str:  # noqa: B006 — 프로세스 동안 쓰는 캐시
    if place_id not in cache:
        try:
            cache[place_id] = c.get_place(place_id)["title"]
        except Exception:
            cache[place_id] = place_id
    return cache[place_id]


# ---------- docent: 메모리 ----------

def cmd_memory(c, a):
    r = c.memory(a.plan)
    if a.json:
        return dump(r)
    style = {"short": "짧게", "long": "자세히", None: "-"}[r.get("style_length")]
    print(f"메모리 v{r['memory_version']} · 해설 길이 선호: {style} · {len(r['items'])}개")
    if not r["items"]:
        print("  (비어 있음)  remember 로 추가하세요.")
        return
    table(["id", "범위", "분류", "내용", "출처", "수정"],
          [[str(m["id"]), "user" if m["scope"] == "user" else f"plan #{m['plan_id']}",
            CATEGORY.get(m["category"], m["category"]), short(m["text"], 50), short(m["source"], 16),
            when(m["updated_at"])] for m in r["items"]])


def cmd_remember(c, a):
    if a.scope == "plan" and a.plan is None:
        raise SystemExit("--scope plan 이면 --plan 이 필요합니다.")
    if a.scope == "user" and a.plan is not None:
        raise SystemExit("--scope user 에는 --plan 을 주지 않습니다.")
    m = c.remember(a.scope, a.cat, a.text, a.plan, a.source)
    if a.json:
        return dump(m)
    where = "user" if m["scope"] == "user" else f"plan #{m['plan_id']}"
    print(f"기억함 #{m['id']} [{where} · {CATEGORY.get(m['category'], m['category'])}] {m['text']}")


def cmd_forget(c, a):
    c.forget(a.memory_id)
    print(f"메모리 #{a.memory_id} 삭제됨")


# ---------- docent: 가이드 노트 ----------

def cmd_notes(c, a):
    rows = c.notes(a.plan)
    if a.json:
        return dump(rows)
    if not rows:
        print("가이드 노트가 없습니다. guide-prepare 로 준비하세요.")
        return
    out = []
    for n in rows:
        try:
            full = c.note(a.plan, n["place_id"])
            sources = len(full["sources"])
        except Exception:
            sources = "-"
        out.append([n["place_id"], _title(c, n["place_id"]), NOTE_STATUS.get(n["status"], n["status"]), str(sources),
                    str(n["memory_version"] if n["memory_version"] is not None else "-"),
                    when(n["prepared_at"])])
    table(["장소", "이름", "상태", "출처", "메모리 v", "준비 시각"], out, indent="")


def cmd_note(c, a):
    n = c.note(a.plan, a.place_id)
    if a.json:
        return dump(n)
    print(f"[{_title(c, a.place_id)}] {NOTE_STATUS.get(n['status'], n['status'])}"
          f" · 메모리 v{n['memory_version']} · {n['model'] or '-'} · {when(n['prepared_at'])}")
    if n.get("error"):
        print(f"  실패 이유: {n['error']}")
    body = n["long"] if a.long else n["short"]
    print(f"\n {'자세히' if a.long else '짧게'} ({len(body)}자)\n  {body or '(없음)'}")
    for label, key in [("당신에게", "for_you"), ("볼거리", "highlights"), ("동행 팁", "companion_tips"),
                       ("주의", "cautions")]:
        if n[key]:
            print(f"\n {label}")
            for x in n[key]:
                print(f"  - {x}")
    if n["qa"]:
        print("\n 나올 만한 질문")
        for q in n["qa"]:
            print(f"  Q. {q['q']}\n     {short(q['a'], 120)}")
    print(f"\n 출처 {len(n['sources'])}개")
    for s in n["sources"]:
        print(f"  [{TIER.get(s['tier'], s['tier'])}] {s['title']}  {s['url']}")


def _usage(u: dict | None) -> str:
    if not u:
        return "-"
    return f"in {u.get('input', 0):,} (캐시 {u.get('cached', 0):,}) · out {u.get('output', 0):,} · 호출 {u.get('calls', 0)}"


def print_job(job: dict) -> None:
    state = "끝" if job["status"] == "done" else f"진행 중 (지금 {job['current'] or '-'}, 남은 약 {job['eta_sec'] or 0}초)"
    print(f"가이드 준비 #{job['job_id'][:8]} 계획 #{job['plan_id']}: {state} — "
          f"{job['finished']}/{job['total']}곳 (준비됨 {job['ready']}, 실패 {job['failed']})")
    if job["places"]:
        table(["장소", "이름", "상태", "출처", "웹 검색", "초", "토큰"],
              [[p["place_id"], short(p["title"], 16), NOTE_STATUS.get(p["status"], p["status"]), str(p["sources"]),
                str(p["web_searches"]), f"{p['seconds']:.0f}" if p["seconds"] is not None else "-", _usage(p["usage"])]
               for p in job["places"]])
        for p in job["places"]:
            if p["error"]:
                print(f"  {p['place_id']} 실패: {p['error']}")
    print(f"  합계: {_usage(job['usage'])}")


def cmd_guide_prepare(c, a):
    places = [x.strip() for x in a.places.split(",") if x.strip()] if a.places else None
    job = c.guide_prepare(a.plan, places, a.force)
    if not a.wait:
        return dump(job) if a.json else print_job(job)
    last = None
    try:
        while job["status"] != "done":
            key = (job["finished"], job["current"])
            if key != last and not a.json:
                print(f"  … {job['finished']}/{job['total']}곳, 지금 {job['current'] or '-'} (남은 약 {job['eta_sec'] or 0}초)")
                last = key
            _time.sleep(a.interval)
            job = c.guide_job(a.plan, job["job_id"])
    except KeyboardInterrupt:
        print("\n(기다리기 중단 — 작업은 서버에서 계속된다)")
    dump(job) if a.json else print_job(job)


# ---------- safety ----------

def _hazards(hs: list[dict], indent: str = "  ") -> None:
    for h in hs:
        print(f"{indent}[{LEVEL.get(h['level'], h['level'])}] {h['reason']} ({h['source']})")


def cmd_outlook(c, a):
    r = c.outlook(a.date, a.start, a.end)
    if a.json:
        return dump(r)
    print(f"{r['date']} 전망 ({a.start or '09:00'}~{a.end or '18:00'}) · 기준 {r['now'][:16]} · {r['horizon']} · "
          f"신뢰 {r['confidence']}" + ("" if r["forecast_available"] else " · 예보 없음 (판단하지 않음)"))
    s = r.get("summary")
    if s:
        print(f"  하늘 {s['sky'] or '-'} · 강수확률 최대 {s['max_pop']}% · 강수 {s['total_pcp_mm']}mm · "
              f"풍속 최대 {s['max_wind_ms']}m/s · 파고 최대 {s['max_wave_m']}m · 기온 {s['temp_min']}~{s['temp_max']}℃")
    if r["warnings"]:
        print("  특보: " + " / ".join(r["warnings"]))
    if r["hazards"]:
        print(" 위험 신호")
        _hazards(r["hazards"])
    if r["safe_windows"]:
        print(" 신호 없는 시간대")
        kind = {"breakwater": "방파제", "flood": "해안 저지대", "outdoor": "그 밖의 야외"}
        table(["장소 종류", "시간", "참고 신호"],
              [[kind.get(w["place_kind"], w["place_kind"]), f"{hhmm(w['start'])}~{hhmm(w['end'])}",
                "있음" if w["has_info"] else ""] for w in r["safe_windows"]])
    if r["tides"]:
        print("  물때: " + ", ".join(f"{'만조' if t['kind'] == 'high' else '간조'} {t['time'][5:16].replace('T', ' ')}"
                                     for t in r["tides"]))
    for n in r["seasonal_notes"]:
        print(f"  (경향) {n}")
    if r.get("recheck_from"):
        print(f"  {r['recheck_from']} 부터 다시 확인")


def cmd_status(c, a):
    r = c.safety_status()
    if a.json:
        return dump(r)
    print(f"지금 안전 상황 ({r['now'][:16]}): 전체 {LEVEL.get(r['overall_risk'], r['overall_risk'])}")
    if r["warnings"]:
        print("  특보: " + " / ".join(w if isinstance(w, str) else str(w) for w in r["warnings"]))
    print("\n 구역")
    table(["id", "이름", "위험", "근거"],
          [[z["id"], z["name"], LEVEL.get(z["risk"], z["risk"]), short("; ".join(z["reasons"]), 60) or "-"]
           for z in r["zones"]])
    print("\n 센서 (실측)")
    table(["id", "이름", "종류", "값", "등급", "위험", "참고"],
          [[str(s["id"]), s["name"], s["type"], "-" if s["value"] is None else f"{s['value']:g}",
            "-" if s["level"] is None else str(s["level"]), LEVEL.get(s["risk"], s["risk"]), s["note"] or ""]
           for s in r["sensors"]])
    if r["breakwater_activity"]:
        print("\n 방파제 CCTV (최근 1시간)")
        table(["방파제", "시각", "실족", "침입", "접근", "이상"],
              [[b["name"], hhmm(b["hour"]), str(b["fall"]), str(b["intrusion"]), str(b["approach"]), str(b["abnormal"])]
               for b in r["breakwater_activity"]])
    if r.get("uv"):
        print(f"\n 자외선 {r['uv']['index']} ({hhmm(r['uv']['at'])})")
    if r["next_hours"]:
        print(" 앞으로 12시간 예보 신호")
        _hazards(r["next_hours"])


def cmd_zones(c, a):
    zones = c.safety_zones()
    if a.json:
        return dump(zones)
    kind = {"breakwater": "방파제", "flood": "침수 감시"}
    table(["id", "이름", "종류", "반경", "중심"],
          [[z["id"], z["name"], kind.get(z["kind"], z["kind"]), f"{z['radius_m']:.0f}m",
            f"{sum(p[0] for p in z['path']) / len(z['path']):.6f},{sum(p[1] for p in z['path']) / len(z['path']):.6f}"]
           for z in zones], indent="")


def cmd_situation(c, a):
    r = c.safety_situation()
    if a.json:
        return dump(r)
    until = (r["forecast_until"] or "-")[5:16].replace("T", " ")
    print(f"상황 {r['now'][:16]} · fingerprint {r['fingerprint']} · 예보 {until}까지")
    bad = {k: v for k, v in r["zones"].items() if v != "safe"}
    print("  구역: " + (", ".join(f"{k} {LEVEL.get(v, v)}" for k, v in bad.items()) if bad else "모두 안전"))
    if r["warnings"]:
        print("  특보: " + " / ".join(map(str, r["warnings"])))
    failed = [k for k, ok in r["data_ok"].items() if not ok]
    if failed:
        print("  데이터 못 받음: " + ", ".join(failed))
    hours = [h for h in r["hours"] if any(v != "safe" for v in h["levels"].values()) or h["info"]]
    if hours:
        kinds = ["breakwater", "flood", "outdoor", "indoor"]
        print(f" 신호가 있는 시각 ({len(hours)}개, 처음 12개)")
        table(["시각", "방파제", "침수 구역", "야외", "실내", "참고"],
              [[h["time"][5:16].replace("T", " ")] + [LEVEL.get(h["levels"].get(k), "-") for k in kinds]
               + [", ".join(h["info"])] for h in hours[:12]])
    else:
        print("  예보상 신호 없음")


def cmd_avoid(c, a):
    r = c.safety_avoid(a.min)
    if a.json:
        return dump(r)
    print(f"길찾기에서 피할 구역 ({LEVEL[a.min]} 이상, {r['now'][:16]}): {len(r['zones'])}곳")
    if r["zones"]:
        table(["id", "이름", "위험", "근거", "다각형"],
              [[z["id"], z["name"], LEVEL.get(z["risk"], z["risk"]), short(z["reason"], 50), f"{len(z['polygon'])}점"]
               for z in r["zones"]])
    print(f"  {r['guidance']}")


# ---------- routing ----------

def _stops(c, specs: list[str]) -> list[tuple[str, tuple[float, float]]]:
    """place_id 또는 LAT,LNG -> (이름, 좌표)."""
    out = []
    for s in specs:
        if "," in s:
            out.append((s, latlng(s)))
        else:
            p = c.get_place(s)
            out.append((p["title"], (p["lat"], p["lng"])))
    return out


def _approx(x: dict) -> str:
    return "약 " if x.get("source") == "estimate" else ""


def cmd_route(c, a):
    stops = _stops(c, a.stops)
    r = c.route([p for _, p in stops], "car" if a.car else "walk", "avoid" if a.avoid else "report", steps=True)
    if a.json:
        return dump(r)
    print(f"{' → '.join(n for n, _ in stops)}")
    print(f"  {MODE[r['mode']]} {_approx(r)}{r['minutes']}분 · {r['distance_m']:,}m"
          + (" (길을 못 찾아 직선 근사)" if r["source"] == "estimate" else "")
          + ("" if r["engine"]["ok"] else f" · 엔진 오류: {r['engine']['error']}"))
    h = r["hazards"]
    if h["checked"]:
        if h["on_route"]:
            print("  지나는 위험 구역 (막힘): " + ", ".join(f"{z['name']}({LEVEL.get(z['risk'], z['risk'])})" for z in h["on_route"]))
        if h["avoided"]:
            print("  우회한 구역: " + ", ".join(f"{z['name']}({LEVEL.get(z['risk'], z['risk'])})" for z in h["avoided"]))
        if h["baseline"]:
            print(f"  우회 전 {h['baseline']['minutes']}분 · {h['baseline']['distance_m']:,}m → 우회하면 {h['detour_minutes']}분 더")
        if not h["on_route"] and not h["avoided"]:
            print("  지나는 위험 구역 없음")
        if h["note"]:
            print(f"  ({h['note']})")
    else:
        print("  (위험 구역을 확인하지 못함)")
    for n, leg in enumerate(r["legs"], start=1):
        if len(r["legs"]) > 1:
            print(f"\n 구간 {n}: {leg['minutes']}분 · {leg['distance_m']:,}m")
        if leg["steps"]:
            table(["#", "안내", "거리", "초"],
                  [[str(i), s["instruction"], f"{s['distance_m']}m", str(s["seconds"])] for i, s in enumerate(leg["steps"], 1)])


def cmd_legs(c, a):
    stops = _stops(c, a.stops)
    if len(stops) < 2:
        raise SystemExit("장소를 두 곳 이상 주세요.")
    r = c.legs([(x[1], y[1]) for x, y in zip(stops, stops[1:])], "car" if a.car else "walk")
    if a.json:
        return dump(r)
    table(["출발", "도착", "시간", "거리"],
          [[x[0], y[0], f"{_approx(leg)}{leg['minutes']}분", f"{leg['distance_m']:,}m"]
           for x, y, leg in zip(stops, stops[1:], r["legs"])], indent="")
    if not r["engine"]["ok"]:
        print(f"  (엔진 오류, 직선 근사: {r['engine']['error']})")


def cmd_matrix(c, a):
    src = _stops(c, a.stops)
    dst = _stops(c, a.to) if a.to else src
    r = c.matrix([p for _, p in src], [p for _, p in dst], "car" if a.car else "walk")
    if a.json:
        return dump(r)
    print(f"{MODE[r['mode']]} 이동시간 (분, 행 = 출발)")
    table([""] + [short(n, 12) for n, _ in dst],
          [[short(n, 12)] + [f"{_approx(x)}{x['minutes']}" for x in row] for (n, _), row in zip(src, r["matrix"])],
          indent="")


def cmd_rstatus(c, a):
    r = c.routing_status()
    if a.json:
        return dump(r)
    e = r["engine"]
    print(f"길찾기 엔진: {'정상' if e.get('ok') else '오류'}" + "".join(f" · {k} {v}" for k, v in e.items() if k != "ok"))


# ---------- places ----------

def cmd_passages(c, a):
    lat = lng = None
    if a.near:
        lat, lng = latlng(a.near)
    rows = c.passages(a.q, a.place, a.kind, lat, lng, a.radius, a.limit)
    if a.json:
        return dump(rows)
    if not rows:
        print("근거 조각이 없습니다.")
    for p in rows:
        dist = f" · {p['distance_m']:.0f}m" if p.get("distance_m") is not None else ""
        kind = "소개" if p["kind"] == "place" else "이야기"
        print(f"{p['score']:.3f} [{kind}] {p['title']} ({p['place_id'] or '-'}){dist}")
        print(f"   {short(p['text'], 160)}")


def _stories(rows: list[dict], near: bool) -> None:
    if not rows:
        print("이야기가 없습니다.")
        return
    table(["id", "장소"] + (["거리"] if near else []) + ["제목", "길이", "대본"],
          [[s["id"], s["place_id"] or "-"] + ([f"{s['distance_m']:.0f}m"] if near else [])
           + [short(s["title"], 24), f"{s['play_time_sec'] // 60}분 {s['play_time_sec'] % 60}초", short(s["script"], 40)]
           for s in rows], indent="")


def cmd_stories(c, a):
    rows = c.stories(a.place_id)
    dump(rows) if a.json else _stories(rows, near=False)


def cmd_stories_near(c, a):
    lat, lng = latlng(a.at)
    rows = c.stories_near(lat, lng, a.radius, a.limit)
    dump(rows) if a.json else _stories(rows, near=True)


# ---------- 등록 ----------

def register(add) -> None:
    """cli.py 의 add(name, fn, help) 로 명령을 붙인다. 모든 명령에 --json."""

    def cmd(name, fn, help_):
        p = add(name, fn, help_)
        p.add_argument("--json", action="store_true", help="API 응답 JSON 그대로 출력")
        return p

    # docent·가이드
    p = cmd("memory", cmd_memory, "사용자 메모리 (user 범위 + --plan 의 plan 범위)")
    p.add_argument("--plan", type=int, help="이 계획의 plan 범위 항목도 본다")
    p = cmd("remember", cmd_remember, '메모리 추가  예) remember --scope user --cat style "해설은 핵심만 짧게"')
    p.add_argument("--scope", choices=["user", "plan"], required=True)
    p.add_argument("--plan", type=int, help="--scope plan 일 때 계획 id")
    p.add_argument("--cat", choices=list(CATEGORY), required=True, help="분류")
    p.add_argument("text")
    p.add_argument("--source", default="user_edit", help="근거 (기본 user_edit)")
    p = cmd("forget", cmd_forget, "메모리 삭제")
    p.add_argument("memory_id", type=int)
    p = cmd("notes", cmd_notes, "가이드 노트 목록 (상태·출처 수)")
    p.add_argument("plan", type=int)
    p = cmd("note", cmd_note, "가이드 노트 (짧게/자세히, 맞춤 포인트, 동행 팁, 출처 등급)")
    p.add_argument("plan", type=int)
    p.add_argument("place_id")
    p.add_argument("--long", action="store_true", help="자세히(long) 해설")
    p = cmd("guide-prepare", cmd_guide_prepare, "가이드 노트 준비 작업 시작 (agent, LLM 사용)")
    p.add_argument("plan", type=int)
    p.add_argument("--places", help="이 장소만 (쉼표 구분 place_id)")
    p.add_argument("--force", action="store_true", help="준비된 노트도 다시 만든다")
    p.add_argument("--wait", action="store_true", help="끝날 때까지 진행 상황을 보여준다")
    p.add_argument("--interval", type=float, default=3, help="--wait 확인 간격(초)")

    # safety
    p = cmd("outlook", cmd_outlook, "그날 안전 전망 (요약·위험 신호·신호 없는 시간대·물때)")
    p.add_argument("date", help="YYYY-MM-DD")
    p.add_argument("--from", dest="start", help="HH:MM (기본 09:00)")
    p.add_argument("--to", dest="end", help="HH:MM (기본 18:00)")
    cmd("status", cmd_status, "지금 안전 상황 (구역·센서 실측·방파제 CCTV·특보)")
    cmd("zones", cmd_zones, "방파제·침수 감시 구역 위치")
    cmd("situation", cmd_situation, "안전 상황 요약과 fingerprint (planner 주기 점검이 보는 것)")
    p = cmd("avoid", cmd_avoid, "길찾기에서 피할 구역")
    p.add_argument("--min", choices=["caution", "danger"], default="caution", help="이 단계 이상 (기본 caution)")

    # routing
    p = cmd("route", cmd_route, "길찾기: 시간·거리·한국어 안내·지나는 위험 구역 (--avoid 우회)")
    p.add_argument("stops", nargs="+", help="place_id 또는 LAT,LNG (2~20개, 순서대로)")
    p.add_argument("--car", action="store_true", help="차량 (기본 도보)")
    p.add_argument("--avoid", action="store_true", help="위험 구역 우회 (기본: 지나는 구역만 알림)")
    p = cmd("legs", cmd_legs, "연속 구간 이동시간 (A→B→C …)")
    p.add_argument("stops", nargs="+", help="place_id 또는 LAT,LNG")
    p.add_argument("--car", action="store_true")
    p = cmd("matrix", cmd_matrix, "이동시간 행렬 (--to 생략 시 주어진 곳끼리)")
    p.add_argument("stops", nargs="+", help="출발지 place_id 또는 LAT,LNG")
    p.add_argument("--to", nargs="+", help="도착지 (생략 시 출발지와 같음)")
    p.add_argument("--car", action="store_true")
    cmd("rstatus", cmd_rstatus, "길찾기 엔진 상태")

    # places
    p = cmd("passages", cmd_passages, "근거 조각 검색 (소개문·오디오 가이드 대본, 의미 유사도 순)")
    p.add_argument("q")
    p.add_argument("--place", help="이 장소의 조각만")
    p.add_argument("--kind", choices=["place", "story"])
    p.add_argument("--near", help="LAT,LNG")
    p.add_argument("--radius", type=float, help="--near 반경 (m)")
    p.add_argument("--limit", type=int, default=5)
    p = cmd("stories", cmd_stories, "장소의 오디오 가이드 이야기")
    p.add_argument("place_id")
    p = cmd("stories-near", cmd_stories_near, "위치 주변 이야기")
    p.add_argument("at", metavar="LAT,LNG")
    p.add_argument("--radius", type=float, help="반경 (m)")
    p.add_argument("--limit", type=int)
