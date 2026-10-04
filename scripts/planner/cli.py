"""Planner CLI — API 가 제대로 동작하는지 터미널에서 확인하기 위한 클라이언트.

    python scripts/planner/cli.py --url http://127.0.0.1:8002 plans
    python scripts/planner/cli.py shell          # 대화형 (명령을 한 줄씩 입력)
    python scripts/planner/cli.py chat 1         # Planner Agent 와 대화 (응답 실시간 스트리밍)
    python scripts/planner/cli.py login seoyun   # 로그인 (이후 요청은 토큰으로). 로그인 안 했으면 --user 가 X-User-Id

환경변수 GURYONG_API_URL, GURYONG_USER 로 기본값을 바꿀 수 있다. 명령 목록은 -h 참고.
"""
import argparse
import getpass
import json
import os
import shlex
import sys
import unicodedata

import httpx

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
          f"{p['day_start']}~{p['day_end']}  |  {MODE.get(p['travel_mode'], p['travel_mode'])}  |  v{p['version']}  |  "
          f"user={p['user_id']}")
    if p["memo"]:
        print(f"  메모: {p['memo']}")
    for d in p["days"]:
        print(f"\n {d['day']}일차  {d['date']} ({d['weekday']})" + (f"  종료 {d['end_at']}" if d["end_at"] else "  (일정 없음)"))
        if not d["items"]:
            continue
        rows = []
        for i in d["items"]:
            place = i["place"]["title"] if i["place"] else "(정보 없음)"
            move = "-"
            if i["travel_minutes"] is not None:
                mode = {"walk": "도보", "car": "차량"}.get(i.get("leg_mode"), "")
                approx = "약 " if i.get("travel_source") == "estimate" else ""
                move = f"{mode} {approx}{i['travel_minutes']}분/{i['travel_distance_m']}m"
                other = {"walk": "car", "car": "walk"}.get(i.get("leg_mode"))
                if other and (i.get("travel_options") or {}).get(other):
                    move += f" ({'차량' if other == 'car' else '도보'} {i['travel_options'][other]['minutes']}분)"
                if i.get("travel_mode"):
                    move += " *"  # 이 구간만 수단을 정함
            span = "건너뜀" if i["status"] == "skipped" else f"{i['start_at']}~{i['end_at']}"
            notes = [f"{i['start_time']} 고정"] if i["start_time"] else []
            if i["status"] not in ("planned", "skipped"):
                notes.append(i["status"])
            if i["memo"]:
                notes.append(i["memo"])
            rows.append([str(i["order"]), str(i["id"]), i["arrive_at"] or "-", span, f"{i['stay_minutes']}분", move,
                         f"{place} [{i['place_id']}]", " · ".join(notes)])
        table(["순서", "id", "도착", "시간", "체류", "이동 (* 구간 지정)", "장소", "비고"], rows)
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
    print_plan(c.add_item(a.plan, a.place_id, a.day, a.order, a.stay, a.at, a.memo, a.mode))


def cmd_edit(c, a):
    changes = {k: v for k, v in {"day": a.day, "order": a.order, "stay_minutes": a.stay, "memo": a.memo,
                                 "status": a.status}.items() if v is not None}
    if a.at is not None:
        changes["start_time"] = None if a.at.lower() == "none" else a.at
    if a.mode is not None:
        changes["travel_mode"] = None if a.mode == "default" else a.mode
    if not changes:
        raise SystemExit("바꿀 값을 하나 이상 지정하세요 (--day/--order/--stay/--at/--memo/--status/--mode).")
    print_plan(c.update_item(a.plan, a.item, **changes))


LEVEL = {"safe": "안전", "caution": "주의", "danger": "위험", None: "판단 안 함"}


def cmd_safety(c, a):
    r = c.check_safety(a.plan)
    print(f"안전 점검 ({r['checked_at'][:16]}): 전체 {LEVEL[r['overall_risk']]}  {r['counts']}")
    for i in r["items"]:
        what = f"이동({'도보' if i['mode'] == 'walk' else '차량'}) {i['title']}" if i["kind"] == "travel" else i["title"]
        level = LEVEL[i["risk"]] + (f"({i['unassessed_reason']})" if i["risk"] is None else "")
        print(f"  {i['day']}일차 {i['start_at']}~{i['end_at']} {what} — {level}")
        for h in i["hazards"][:3]:
            print(f"      [{h['level']}] {h['reason']}")
        if i["detour"]:
            print(f"      우회하면 {i['detour']['detour_minutes']}분 더: {', '.join(i['detour']['avoided'])}")
    print(f"\n{r['guidance']}")


def cmd_alerts(c, a):
    alerts = c.safety_alerts(a.plan, a.status)
    if not alerts:
        print("경고 없음")
    for x in alerts:
        print(f"#{x['id']} {x['created_at'][:16]} [{x['status']}] {LEVEL[x['level']]}"
              + (" (조치 필요)" if x["needs_action"] else ""))
        print("   " + x["message"].replace("\n", "\n   "))


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


# ---------- 제안 · 추천 · Agent ----------

DIM, BOLD, RESET, CLEAR = "\033[2m", "\033[1m", "\033[0m", "\r\033[2K"
STATE = {"in_plan": "일정에 있음", "not_in_plan": "", "day_out_of_range": "날짜 없음"}
BUTTON = {"in_plan": "[빼기]", "not_in_plan": "[추가]", "day_out_of_range": ""}


def print_error(e: PlannerApiError) -> None:
    print(f"오류 {e.status} {e.code}: {e.message}")


def print_proposal(p: dict, no: int | None = None) -> None:
    label = f"카드 {no} · " if no else ""
    state = p["status"] + (f"/{p['decision']}" if p.get("decision") else "")
    print(f"\n{BOLD}[{label}변경 제안 #{p['id']}] ({state}){RESET}")
    for ch in p.get("changes", []):
        print(f"  - {ch['message']}")
    after = p.get("after")
    if after:
        errors = [v for v in after["violations"] if v["severity"] == "error"]
        warns = [v for v in after["violations"] if v["severity"] == "warning"]
        print(f"  검증: error {len(errors)} · warning {len(warns)}")
        for v in errors + warns:
            print(f"    [{'E' if v['severity'] == 'error' else 'W'}] {v['message']}")
    if p["status"] == "pending" and p.get("base_changed"):
        print(f"  {DIM}! 이 제안 이후 일정이 바뀌었어요 (승인 시 확인){RESET}")


def print_recommendation(r: dict, no: int | None = None) -> None:
    label = f"카드 {no} · " if no else ""
    print(f"\n{BOLD}[{label}장소 추천 #{r['id']}] {r['title']}{RESET}")
    for n, c in enumerate(r["candidates"], start=1):
        title = (c["place"] or {}).get("title", c["place_id"])
        when = f"{c['day']}일차" + (f" {c['time_hint']}쯤" if c["time_hint"] else "")
        state = STATE[c["state"]]
        if c["item"]:
            state += f" · {c['item']['start_at'] or '건너뜀'}"
        other = f" (다른 날: {', '.join(map(str, c['other_days']))}일차)" if c["other_days"] else ""
        print(f"  {n}. {title}  {when}  {BUTTON[c['state']]} {state}{other}")
        if c["reason"]:
            print(f"     {DIM}{c['reason']}{RESET}")


def cmd_proposals(c, a):
    rows = c.list_proposals(a.plan, a.status)
    if not rows:
        print("제안이 없습니다.")
    for p in rows:
        print(f"#{p['id']} {p['status']:<10} v{p['base_version']:<3} {p['created_at'][:16]}  "
              f"변경 {len(p['changes'])}건  {p['summary'][:50]!r}")


def cmd_proposal(c, a):
    p = c.get_proposal(a.plan, a.proposal)
    print_proposal(p)
    if a.full:
        print("\n--- 제안 당시 일정 ---")
        print_plan(p["before"])
        print("\n--- 승인 시 일정 ---")
        print_plan(p["after"])


def cmd_accept(c, a):
    try:
        r = c.plan_accept_proposal(a.plan, a.proposal, "overwrite" if a.overwrite else "auto")
    except PlannerApiError as e:
        if e.code != "base_changed":
            raise
        print_base_changed(e)
        print("카드 일정 그대로 반영하려면 --overwrite 를 붙이세요.")
        return
    print(f"제안 #{a.proposal} 반영됨 ({r['proposal']['decision']})\n")
    print_plan(r["plan"])


def cmd_reject(c, a):
    c.plan_reject_proposal(a.plan, a.proposal)
    print(f"제안 #{a.proposal} 거절됨")


def cmd_recs(c, a):
    rows = c.list_recommendations(a.plan)
    if not rows:
        print("추천이 없습니다.")
    for r in rows:
        print_recommendation(r)


def cmd_rec(c, a):
    print_recommendation(c.get_recommendation(a.plan, a.rec))


def _rec_action(c, a, action):
    r = c.plan_candidate(a.plan, a.rec, a.candidate, action)
    print_recommendation(r["recommendation"])


def cmd_rec_add(c, a):
    _rec_action(c, a, "add")


def cmd_rec_remove(c, a):
    _rec_action(c, a, "remove")


def print_base_changed(e: PlannerApiError) -> None:
    d = e.detail
    print(f"\n{BOLD}이 제안 이후 일정이 바뀌었어요{RESET} (제안 v{d['base_version']} → 지금 v{d['current_version']})")
    if d["since_proposal"]:
        print("  그대로 반영하면 사라지는 변경:")
        for ch in d["since_proposal"]:
            print(f"    - {ch['message']}")
    print("  카드 일정으로 바꾸면:")
    for ch in d["overwrite_effect"]:
        print(f"    - {ch['message']}")


class ChatPrinter:
    """SSE 이벤트를 터미널에 실시간으로 그린다. 단계는 진행 중엔 [..], 끝나면 [v] 로 같은 줄을 갱신한다."""

    def __init__(self, show_thinking: bool):
        self.show_thinking = show_thinking
        self.line_start = True
        self.thinking = False
        self.step_open = False  # 진행 중 단계 줄이 열려 있음 (줄바꿈 전)
        self.cards: list[dict] = []

    def _out(self, s: str) -> None:
        sys.stdout.write(s)
        sys.stdout.flush()
        if s:
            self.line_start = s.endswith("\n")

    def _newline(self) -> None:
        if self.step_open:
            self._out("\n")
            self.step_open = False
        if not self.line_start:
            self._out("\n")

    def handle(self, event: str, d: dict) -> None:
        if event != "thinking" and self.thinking:
            self.thinking = False
            if self.show_thinking:
                self._out(RESET)
                self._newline()
        if event == "thinking":
            if self.show_thinking:
                if not self.thinking:
                    self._newline()
                    self._out(DIM)
                    self.thinking = True
                self._out(d["delta"])
            elif not self.thinking:
                self._newline()
                self._out(f"{DIM}(생각 중…){RESET}\n")
                self.thinking = True
        elif event == "status":
            self._newline()
            self._out(f"{BOLD}{d['text']}{RESET}\n")
        elif event == "step":
            mark = {"running": "[..]", "done": "[v]", "error": "[x]"}[d["status"]]
            line = f"  {mark} {d['title']}" + (f" · {d['detail']}" if d["detail"] else "")
            if d["status"] == "running":
                if not self.step_open:
                    self._newline()
                self._out(CLEAR + DIM + line + RESET)
                self.step_open = True
            else:
                self._out(CLEAR + line + "\n")
                self.step_open = False
        elif event == "text":
            if self.step_open:
                self._newline()
            self._out(d["delta"])
        elif event == "proposal":
            self._newline()
            self.cards.append({"type": "proposal", "proposal": d})
            print_proposal(d)
            print(f"{DIM}  /accept 반영 · /reject 거절 · /detail 전체 일정 · 말로 더 고칠 수도 있어요{RESET}")
            self.line_start = True
        elif event == "recommendation":
            self._newline()
            self.cards.append({"type": "recommendation", "recommendation": d})
            print_recommendation(d)
            print(f"{DIM}  /add <번호> 일정에 추가 · /remove <번호> 빼기{RESET}")
            self.line_start = True
        elif event == "steps_done":
            self._newline()
            self._out(f"{DIM}[v] {d['summary']}{RESET}\n")
        elif event == "error":
            self._newline()
            self._out(f"오류 ({d['code']}): {d['message']}\n")
        elif event == "done":
            self._newline()


def _session_cards(c, sid: str) -> list[dict]:
    return [card for m in c.get_session(sid)["messages"] for card in m["cards"]]


def _pick(cards: list[dict], kind: str, arg: str | None) -> dict | None:
    """카드번호(1부터, 세션 전체 기준)로 고르거나, 생략하면 그 종류의 가장 최근 카드."""
    if arg:
        n = int(arg)
        if not 1 <= n <= len(cards) or cards[n - 1]["type"] != kind:
            print(f"카드 {n} 은(는) {'변경 제안' if kind == 'proposal' else '장소 추천'} 카드가 아닙니다. /cards 로 확인하세요.")
            return None
        return cards[n - 1][kind]
    found = [card[kind] for card in cards if card["type"] == kind]
    if not found:
        print("해당 카드가 없습니다.")
        return None
    if kind == "proposal":  # 제안은 대기 중인 것 중 가장 최근을 우선
        found = [p for p in found if p["status"] == "pending"] or found
    return found[-1]


def cmd_chat(c, a):
    if a.session:
        sid = a.session
    else:
        sessions = [] if a.new else c.list_sessions(a.plan)
        sid = sessions[0]["id"] if sessions else c.create_session(a.plan)["id"]
    detail = c.get_session(sid)
    plan_id = detail["plan_id"]
    print(f"{BOLD}Planner Agent{RESET} — 계획 #{plan_id}, 세션 {sid} ({c.agent_info()['provider']})")
    print(f"{DIM}/plan · /cards · /detail [n] · /accept [n] [overwrite] · /reject [n] · /repropose [n] · "
          f"/add <i> · /remove <i> · /exit{RESET}")
    for m in detail["messages"][-6:]:
        who = "나" if m["role"] == "user" else "Agent"
        print(f"{DIM}{who}> {m['content']}{RESET}")
        if m.get("steps_summary"):
            print(f"{DIM}  [v] {m['steps_summary']}{RESET}")

    def stream(events) -> None:
        printer = ChatPrinter(a.thinking)
        print(f"{BOLD}Agent>{RESET} ", end="", flush=True)
        try:
            for event, data in events:
                printer.handle(event, data)
        except KeyboardInterrupt:
            print("\n(중단)")
        except httpx.HTTPError as e:  # 스트림이 끊겨도 대화는 계속
            print(f"\n연결 오류: {e!r}")

    def accept(arg: str | None, overwrite: bool) -> None:
        p = _pick(_session_cards(c, sid), "proposal", arg)
        if not p:
            return
        try:
            r = c.accept_proposal(sid, p["id"], "overwrite" if overwrite else "auto")
        except PlannerApiError as e:
            if e.code != "base_changed":
                raise
            print_base_changed(e)
            print("  1) 카드에서 본 일정 그대로 반영   2) 현재 일정에 맞춰 다시 제안   3) 취소")
            try:
                choice = input("선택> ").strip()
            except EOFError:
                choice = "3"
            if choice == "1":
                r = c.accept_proposal(sid, p["id"], "overwrite")
            elif choice == "2":
                stream(c.repropose_stream(sid, p["id"]))
                return
            else:
                print("취소했어요.")
                return
        print(f"제안 #{p['id']} 반영됨 ({r['proposal']['decision']})\n")
        print_plan(r["plan"])

    def candidate(arg: str | None, action: str) -> None:
        rec = _pick(_session_cards(c, sid), "recommendation", None)
        if not rec:
            return
        if not arg or not arg.isdigit() or not 1 <= int(arg) <= len(rec["candidates"]):
            print(f"후보 번호를 1~{len(rec['candidates'])} 중에서 주세요. 예) /{action} 1")
            return
        cand = rec["candidates"][int(arg) - 1]
        r = c.candidate(sid, rec["id"], cand["id"], action)
        print_recommendation(r["recommendation"])

    queue = list(a.message or [])
    while True:
        if queue:
            line = queue.pop(0)
            print(f"{BOLD}나>{RESET} {line}")
        elif a.message:  # -m 으로 준 메시지만 보내고 끝
            return
        else:
            try:
                line = input("나> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return
        if not line:
            continue
        cmd, *args = line.split()
        arg = args[0] if args and args[0].isdigit() else None
        try:
            if cmd in ("/exit", "/quit"):
                return
            elif cmd == "/plan":
                print_plan(c.get_plan(plan_id))
            elif cmd == "/cards":
                cards = _session_cards(c, sid)
                if not cards:
                    print("카드가 없습니다.")
                for n, card in enumerate(cards, start=1):
                    if card["type"] == "proposal":
                        print_proposal(card["proposal"], n)
                    else:
                        print_recommendation(card["recommendation"], n)
            elif cmd == "/detail":
                p = _pick(_session_cards(c, sid), "proposal", arg)
                if p:
                    print("\n--- 제안 당시 일정 ---")
                    print_plan(p["before"])
                    print("\n--- 승인 시 일정 ---")
                    print_plan(p["after"])
            elif cmd == "/accept":
                accept(arg, "overwrite" in args)
            elif cmd == "/reject":
                p = _pick(_session_cards(c, sid), "proposal", arg)
                if p:
                    c.reject_proposal(sid, p["id"])
                    print(f"제안 #{p['id']} 거절됨 (일정 그대로)")
            elif cmd == "/repropose":
                p = _pick(_session_cards(c, sid), "proposal", arg)
                if p:
                    stream(c.repropose_stream(sid, p["id"]))
            elif cmd in ("/add", "/remove"):
                candidate(args[0] if args else None, cmd[1:])
            elif cmd.startswith("/"):
                print("명령: /plan /cards /detail [n] /accept [n] [overwrite] /reject [n] /repropose [n] "
                      "/add <i> /remove <i> /exit")
            else:
                stream(c.chat_stream(sid, line))
        except PlannerApiError as e:
            print_error(e)


# ---------- 계정 ----------

def _password(a, prompt: str = "비밀번호: ") -> str:
    return a.password if getattr(a, "password", None) else getpass.getpass(prompt)


def _print_login(tokens: dict, verb: str) -> None:
    u = tokens["user"]
    print(f"{verb}: {u['display_name']} ({u['login_id'] or u['email']}, {u['provider']}) · user id {u['id']}")


def cmd_signup(c, a):
    pw = _password(a)
    if not a.password and getpass.getpass("비밀번호 확인: ") != pw:
        raise SystemExit("비밀번호가 서로 다릅니다.")
    _print_login(c.signup(a.login_id, pw, a.name), "가입하고 로그인했습니다")


def cmd_login(c, a):
    _print_login(c.login(a.login_id, _password(a)), "로그인했습니다")


def cmd_logout(c, a):
    c.logout()
    print("로그아웃했습니다. (이제 요청은 --user 의 X-User-Id 로 보냅니다)")


def cmd_whoami(c, a):
    if not c.logged_in:
        print(f"로그인하지 않았습니다. 개발 모드 사용자: {a.user} (X-User-Id)")
        return
    u = c.me()
    print(f"{u['display_name']} ({u['login_id'] or u['email']}, {u['provider']}) · user id {u['id']}")


def cmd_passwd(c, a):
    current = getpass.getpass("현재 비밀번호: ")
    new = getpass.getpass("새 비밀번호: ")
    if getpass.getpass("새 비밀번호 확인: ") != new:
        raise SystemExit("새 비밀번호가 서로 다릅니다.")
    c.change_password(current, new)
    print("비밀번호를 바꿨습니다. 다른 기기는 로그아웃됩니다.")

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="planner", description="GuryongMate Planner CLI")
    ap.add_argument("--url", default=os.getenv("GURYONG_API_URL", "http://127.0.0.1:8000"), help="API 주소")
    ap.add_argument("--user", default=os.getenv("GURYONG_USER", "demo"),
                    help="로그인하지 않았을 때 쓸 사용자 ID (개발 모드 X-User-Id)")
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
    p.add_argument("--mode", choices=["walk", "car"], help="직전 장소에서 여기까지 수단 (생략 시 계획 기본)")

    p = add("edit", cmd_edit, "일정 수정 (순서·날짜 이동·시간·상태)")
    p.add_argument("plan", type=int)
    p.add_argument("item", type=int, help="일정 항목 id")
    p.add_argument("--day", type=int, help="다른 날로 이동")
    p.add_argument("--order", type=int, help="순서 변경")
    p.add_argument("--stay", type=int)
    p.add_argument("--at", help="시작 시각 고정 HH:MM, 'none' 이면 해제")
    p.add_argument("--memo")
    p.add_argument("--status", choices=["planned", "visiting", "done", "skipped"])
    p.add_argument("--mode", choices=["walk", "car", "default"], help="여기까지 오는 수단 (default: 계획 기본으로)")

    p = add("safety", cmd_safety, "안전 점검 (방문·이동 구간별 위험 단계)")
    p.add_argument("plan", type=int)

    p = add("alerts", cmd_alerts, "안전 경고 목록")
    p.add_argument("plan", type=int)
    p.add_argument("--status", choices=["new", "seen"])

    p = add("rm", cmd_rm, "일정 삭제")
    p.add_argument("plan", type=int)
    p.add_argument("item", type=int)

    p = add("rules", cmd_rules, "규칙 목록")
    p.add_argument("plan", type=int)

    p = add("rule", cmd_rule, "규칙 추가  예) rule 1 end_before time=18:00 / rule 1 must_include place_id=kto-123")
    p.add_argument("plan", type=int)
    p.add_argument("type", choices=["must_include", "exclude", "end_before", "max_travel_minutes", "max_walk_minutes",
                                    "max_daily_walk_minutes"])
    p.add_argument("params", nargs="*", help="key=value ...")
    p.add_argument("--soft", action="store_true", help="위반 시 warning (기본 error)")
    p.add_argument("--note", default="")

    p = add("unrule", cmd_unrule, "규칙 삭제")
    p.add_argument("plan", type=int)
    p.add_argument("rule", type=int)

    p = add("check", cmd_check, "검증만 실행")
    p.add_argument("plan", type=int)

    p = add("proposals", cmd_proposals, "변경 제안 목록")
    p.add_argument("plan", type=int)
    p.add_argument("--status", choices=["pending", "accepted", "rejected", "superseded"])

    p = add("proposal", cmd_proposal, "제안 상세 (변경 요약·검증)")
    p.add_argument("plan", type=int)
    p.add_argument("proposal", type=int)
    p.add_argument("--full", action="store_true", help="제안 당시·승인 시 일정 전체 출력")

    p = add("accept", cmd_accept, "제안 승인 (planner 직접. 대화 기록에는 안 남음)")
    p.add_argument("plan", type=int)
    p.add_argument("proposal", type=int)
    p.add_argument("--overwrite", action="store_true", help="일정이 바뀌었어도 카드의 일정 그대로 반영")

    p = add("reject", cmd_reject, "제안 거절 (planner 직접)")
    p.add_argument("plan", type=int)
    p.add_argument("proposal", type=int)

    p = add("recs", cmd_recs, "장소 추천 카드 목록 (후보 상태 포함)")
    p.add_argument("plan", type=int)

    p = add("rec", cmd_rec, "장소 추천 카드")
    p.add_argument("plan", type=int)
    p.add_argument("rec", type=int)

    p = add("rec-add", cmd_rec_add, "추천 후보를 일정에 추가 (planner 직접)")
    p.add_argument("plan", type=int)
    p.add_argument("rec", type=int)
    p.add_argument("candidate", type=int, help="후보 id")

    p = add("rec-remove", cmd_rec_remove, "추천 후보를 일정에서 빼기 (planner 직접)")
    p.add_argument("plan", type=int)
    p.add_argument("rec", type=int)
    p.add_argument("candidate", type=int, help="후보 id")

    p = add("signup", cmd_signup, "회원가입 (ID/비밀번호) 후 로그인")
    p.add_argument("login_id")
    p.add_argument("--name", help="표시 이름 (생략 시 아이디)")
    p.add_argument("--password", help="스크립트용. 생략하면 입력받는다")

    p = add("login", cmd_login, "로그인 (토큰을 ~/.guryongmate/session.json 에 저장)")
    p.add_argument("login_id")
    p.add_argument("--password", help="스크립트용. 생략하면 입력받는다")

    add("logout", cmd_logout, "로그아웃")
    add("whoami", cmd_whoami, "지금 사용자")
    add("passwd", cmd_passwd, "비밀번호 변경")

    p = add("chat", cmd_chat, "Planner Agent 와 대화 (계획의 최근 세션을 이어서)")
    p.add_argument("plan", type=int)
    p.add_argument("--session", help="이어갈 세션 id")
    p.add_argument("--new", action="store_true", help="새 세션 시작")
    p.add_argument("--thinking", action="store_true", help="모델의 추론 과정도 표시")
    p.add_argument("-m", "--message", action="append",
                   help="이 메시지만 보내고 종료 (여러 번 지정 가능, /accept 같은 명령도 가능)")

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
        print_error(e)
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
    if os.name == "nt":
        os.system("")  # Windows 콘솔 ANSI 색 활성화
    run(build_parser(), sys.argv[1:])
