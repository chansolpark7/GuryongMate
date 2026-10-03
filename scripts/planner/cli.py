"""Planner CLI — API 가 제대로 동작하는지 터미널에서 확인하기 위한 클라이언트.

    python scripts/planner/cli.py --url http://127.0.0.1:8002 plans
    python scripts/planner/cli.py shell          # 대화형 (명령을 한 줄씩 입력)

환경변수 GURYONG_API_URL, GURYONG_USER 로 기본값을 바꿀 수 있다. 명령 목록은 -h 참고.
"""
import argparse
import json
import os
import shlex
import sys
import unicodedata

from client import PlannerApiError, PlannerClient

MODE = {"walk": "도보", "car": "차량"}


# ---------- 출력 ----------

def width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def pad(s: str, n: int) -> str:
    return s + " " * max(0, n - width(s))


def table(headers: list[str], rows: list[list[str]], indent: str = "  ") -> None:
    cols = [max(width(str(x)) for x in col) for col in zip(headers, *rows)]
    for row in [headers, *rows]:
        print(indent + "  ".join(pad(str(x), w) for x, w in zip(row, cols)).rstrip())


def print_plan(p: dict) -> None:
    print(f"[계획 #{p['id']}] {p['title']}  |  {p['start_date']} ~ {p['end_date']}  |  "
          f"{p['day_start']}~{p['day_end']}  |  {MODE.get(p['travel_mode'], p['travel_mode'])}  |  user={p['user_id']}")
    if p["memo"]:
        print(f"  메모: {p['memo']}")
    for d in p["days"]:
        print(f"\n {d['day']}일차  {d['date']} ({d['weekday']})" + (f"  종료 {d['end_at']}" if d["end_at"] else "  (일정 없음)"))
        if not d["items"]:
            continue
        rows = []
        for i in d["items"]:
            place = i["place"]["title"] if i["place"] else "(정보 없음)"
            move = "-" if i["travel_minutes"] is None else f"{i['travel_minutes']}분/{i['travel_distance_m']}m"
            span = "건너뜀" if i["status"] == "skipped" else f"{i['start_at']}~{i['end_at']}"
            notes = [f"{i['start_time']} 고정"] if i["start_time"] else []
            if i["status"] not in ("planned", "skipped"):
                notes.append(i["status"])
            if i["memo"]:
                notes.append(i["memo"])
            rows.append([str(i["order"]), str(i["id"]), i["arrive_at"] or "-", span, f"{i['stay_minutes']}분", move,
                         f"{place} [{i['place_id']}]", " · ".join(notes)])
        table(["순서", "id", "도착", "시간", "체류", "이동", "장소", "비고"], rows)
    if p["constraints"]:
        print("\n 규칙")
        for c in p["constraints"]:
            params = " ".join(f"{k}={v}" for k, v in c["params"].items())
            print(f"  #{c['id']} {c['type']} {params} ({'hard' if c['hard'] else 'soft'}, {c['source']})"
                  + (f" — {c['note']}" if c["note"] else ""))
    print_violations(p["violations"])


def print_violations(violations: list[dict]) -> None:
    if not violations:
        print("\n 위반 없음")
        return
    print("\n 위반")
    for v in violations:
        print(f"  [{'E' if v['severity'] == 'error' else 'W'}] {v['code']}: {v['message']}")


# ---------- 명령 ----------

def cmd_plans(c: PlannerClient, a) -> None:
    plans = c.list_plans()
    if not plans:
        print("계획이 없습니다. `new` 로 만드세요.")
        return
    table(["id", "제목", "기간", "시간", "이동", "항목"],
          [[str(p["id"]), p["title"], f"{p['start_date']}~{p['end_date']}", f"{p['day_start']}~{p['day_end']}",
            MODE.get(p["travel_mode"], p["travel_mode"]), str(p["item_count"])] for p in plans], indent="")


def cmd_new(c, a):
    print_plan(c.create_plan(a.title, a.start, a.end, a.day_from, a.day_to, "car" if a.car else "walk", a.memo))


def cmd_show(c, a):
    p = c.get_plan(a.plan)
    if a.json:
        print(json.dumps(p, ensure_ascii=False, indent=2))
    else:
        print_plan(p)


def cmd_set(c, a):
    changes = {k: v for k, v in {"title": a.title, "start_date": a.start, "end_date": a.end, "day_start": a.day_from,
                                 "day_end": a.day_to, "travel_mode": a.mode, "memo": a.memo}.items() if v is not None}
    print_plan(c.update_plan(a.plan, **changes))


def cmd_drop(c, a):
    c.delete_plan(a.plan)
    print(f"계획 #{a.plan} 삭제됨")


def cmd_search(c, a):
    lat = lng = None
    if a.near:
        lat, lng = map(float, a.near.split(","))
    r = c.search_places(a.q, a.tag, a.type, a.area, lat, lng, a.radius, a.limit)
    print(f"{r['total']}건 중 {len(r['items'])}건")
    table(["id", "이름", "유형", "지역"] + (["거리"] if lat is not None else []),
          [[p["id"], p["title"], p["content_type"], p["area"]]
           + ([f"{p['distance_m']:.0f}m"] if lat is not None else []) for p in r["items"]], indent="")


def cmd_place(c, a):
    p = c.get_place(a.place_id)
    print(f"{p['title']} [{p['id']}]  {p['content_type']} · {p['area']}")
    for label, key in [("주소", "addr"), ("운영", "open_hours"), ("휴무", "rest_date"), ("요금", "fee"),
                       ("메뉴", "menu"), ("전화", "tel"), ("태그", "tags")]:
        val = ", ".join(p[key]) if isinstance(p[key], list) else p[key]
        if val:
            print(f"  {label}: {val.strip()}")
    if p["overview"]:
        print(f"  개요: {p['overview'][:300]}{'…' if len(p['overview']) > 300 else ''}")


def cmd_add(c, a):
    print_plan(c.add_item(a.plan, a.place_id, a.day, a.order, a.stay, a.at, a.memo))


def cmd_edit(c, a):
    changes = {k: v for k, v in {"day": a.day, "order": a.order, "stay_minutes": a.stay, "memo": a.memo,
                                 "status": a.status}.items() if v is not None}
    if a.at is not None:
        changes["start_time"] = None if a.at.lower() == "none" else a.at
    if not changes:
        raise SystemExit("바꿀 값을 하나 이상 지정하세요 (--day/--order/--stay/--at/--memo/--status).")
    print_plan(c.update_item(a.plan, a.item, **changes))


def cmd_rm(c, a):
    print_plan(c.delete_item(a.plan, a.item))


def cmd_rules(c, a):
    rules = c.list_constraints(a.plan)
    if not rules:
        print("규칙이 없습니다.")
    for r in rules:
        print(f"#{r['id']} {r['type']} {json.dumps(r['params'], ensure_ascii=False)} "
              f"({'hard' if r['hard'] else 'soft'}, {r['source']}) {r['note']}")


def cmd_rule(c, a):
    params = {}
    for kv in a.params:
        k, _, v = kv.partition("=")
        params[k] = int(v) if v.isdigit() else v
    print_plan(c.add_constraint(a.plan, a.type, params, hard=not a.soft, note=a.note))


def cmd_unrule(c, a):
    print_plan(c.delete_constraint(a.plan, a.rule))


def cmd_check(c, a):
    r = c.validate(a.plan)
    print("통과 (error 없음)" if r["ok"] else "실패 (error 있음)")
    print_violations(r["violations"])


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="planner", description="GuryongMate Planner CLI")
    ap.add_argument("--url", default=os.getenv("GURYONG_API_URL", "http://127.0.0.1:8000"), help="API 주소")
    ap.add_argument("--user", default=os.getenv("GURYONG_USER", "demo"), help="사용자 ID (X-User-Id)")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="명령")

    def add(name, fn, help_):
        p = sub.add_parser(name, help=help_)
        p.set_defaults(fn=fn)
        return p

    add("plans", cmd_plans, "내 계획 목록")

    p = add("new", cmd_new, "계획 만들기")
    p.add_argument("title")
    p.add_argument("--start", required=True, help="YYYY-MM-DD")
    p.add_argument("--end", help="YYYY-MM-DD (생략 시 당일치기)")
    p.add_argument("--from", dest="day_from", default="10:00", help="매일 시작 시각 HH:MM")
    p.add_argument("--to", dest="day_to", default="18:00", help="매일 종료 시각 HH:MM")
    p.add_argument("--car", action="store_true", help="차량 이동 (기본 도보)")
    p.add_argument("--memo", default="")

    p = add("show", cmd_show, "계획 조회 (타임라인 + 위반)")
    p.add_argument("plan", type=int)
    p.add_argument("--json", action="store_true", help="API 응답 JSON 그대로 출력")

    p = add("set", cmd_set, "계획 정보 수정")
    p.add_argument("plan", type=int)
    p.add_argument("--title")
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--from", dest="day_from")
    p.add_argument("--to", dest="day_to")
    p.add_argument("--mode", choices=["walk", "car"])
    p.add_argument("--memo")

    p = add("drop", cmd_drop, "계획 삭제")
    p.add_argument("plan", type=int)

    p = add("search", cmd_search, "장소 검색")
    p.add_argument("q", nargs="?")
    p.add_argument("--tag", action="append", help="반복 지정 가능 (모두 포함)")
    p.add_argument("--type", help="관광지, 음식점, 문화시설 ...")
    p.add_argument("--area", help="구룡포읍, 호미곶면 ...")
    p.add_argument("--near", help="LAT,LNG")
    p.add_argument("--radius", type=float, help="--near 반경 (m)")
    p.add_argument("--limit", type=int, default=15)

    p = add("place", cmd_place, "장소 상세")
    p.add_argument("place_id")

    p = add("add", cmd_add, "일정 추가")
    p.add_argument("plan", type=int)
    p.add_argument("place_id")
    p.add_argument("--day", type=int, default=1)
    p.add_argument("--order", type=int, help="그날 n번째 (생략 시 맨 뒤)")
    p.add_argument("--stay", type=int, default=60, help="체류 시간(분)")
    p.add_argument("--at", help="시작 시각 고정 HH:MM")
    p.add_argument("--memo", default="")

    p = add("edit", cmd_edit, "일정 수정 (순서·날짜 이동·시간·상태)")
    p.add_argument("plan", type=int)
    p.add_argument("item", type=int, help="일정 항목 id")
    p.add_argument("--day", type=int, help="다른 날로 이동")
    p.add_argument("--order", type=int, help="순서 변경")
    p.add_argument("--stay", type=int)
    p.add_argument("--at", help="시작 시각 고정 HH:MM, 'none' 이면 해제")
    p.add_argument("--memo")
    p.add_argument("--status", choices=["planned", "visiting", "done", "skipped"])

    p = add("rm", cmd_rm, "일정 삭제")
    p.add_argument("plan", type=int)
    p.add_argument("item", type=int)

    p = add("rules", cmd_rules, "규칙 목록")
    p.add_argument("plan", type=int)

    p = add("rule", cmd_rule, "규칙 추가  예) rule 1 end_before time=18:00 / rule 1 must_include place_id=kto-123")
    p.add_argument("plan", type=int)
    p.add_argument("type", choices=["must_include", "exclude", "end_before", "max_travel_minutes"])
    p.add_argument("params", nargs="*", help="key=value ...")
    p.add_argument("--soft", action="store_true", help="위반 시 warning (기본 error)")
    p.add_argument("--note", default="")

    p = add("unrule", cmd_unrule, "규칙 삭제")
    p.add_argument("plan", type=int)
    p.add_argument("rule", type=int)

    p = add("check", cmd_check, "검증만 실행")
    p.add_argument("plan", type=int)

    add("shell", None, "대화형 모드")
    return ap


def run(ap: argparse.ArgumentParser, argv: list[str], client: PlannerClient | None = None) -> None:
    a = ap.parse_args(argv)
    c = client or PlannerClient(a.url, a.user)
    if a.cmd == "shell":
        return shell(ap, a, c)
    try:
        a.fn(c, a)
    except PlannerApiError as e:
        print(f"오류 {e.status}: {json.dumps(e.detail, ensure_ascii=False) if not isinstance(e.detail, str) else e.detail}")
    except Exception as e:  # 연결 실패 등
        print(f"오류: {e}")


def shell(ap, a, c) -> None:
    print(f"Planner shell — {a.url} (user={a.user}). 'help' 로 명령 목록, 'exit' 로 종료.")
    while True:
        try:
            line = input("planner> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if line in ("exit", "quit"):
            return
        if not line:
            continue
        if line == "help":
            line = "-h"
        try:
            run(ap, shlex.split(line), c)
        except SystemExit:  # argparse 오류·도움말 출력 후 계속
            pass


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    run(build_parser(), sys.argv[1:])
