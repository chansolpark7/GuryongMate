"""발표 데모 진행 — 장면 ①~④(PROJECT.md §3)를 한 단계씩 실제 서버에 보내고, 단계마다 앱 화면에 보여야 할 것을 안내한다.
운영 문서: docs/demo/README.md · 데모 데이터: docs/app/README.md §6.1 (엄마랑 구룡포, 북방파제 계획)

    python scripts/demo/run_demo.py --url http://<서버>:8000                # 엔터로 한 단계씩 (발표)
    python scripts/demo/run_demo.py --url ... --plan prebuilt               # 장면 ① 을 미리 만든 계획으로
    python scripts/demo/run_demo.py --url ... --chat app                    # 대화·승인을 발표자가 앱에서 (스크립트는 결과를 기다림)
    python scripts/demo/run_demo.py --url ... --auto --speed 0              # 리허설: 멈추지 않고 끝까지, 기대 이벤트 확인
    python scripts/demo/run_demo.py --url ... --from s3_delay               # 그 단계부터 (계획·위치·시각은 저장해 둔 진행 상태에서)
    python scripts/demo/run_demo.py --url ... --fast-to s4_storm            # 준비부터 그 단계 앞까지 빨리 감고 거기서부터 한 단계씩
    python scripts/demo/run_demo.py --list                                  # 단계 목록

키 (단계마다): Enter 실행 · s 건너뛰기 · b 이전 단계 · l 목록 · q 끝내기. 실행한 뒤: Enter 다음 · r 다시
- 기존 코드를 그대로 쓴다: API 클라이언트(scripts/planner/client.py), 대화 출력(cli.ChatPrinter), 재생기(trip_cli.Player, scene.expand).
- LLM 은 서버 설정(AGENT_PROVIDER=claude | scripted)을 그대로 쓴다. 시작할 때 어느 쪽인지 보여준다.
- safety 는 SAFETY_SIM_ENABLED=true 여야 장면 ④ 의 '실제 데이터 -> 가상 악천후' 전환을 보여줄 수 있다 (없으면 그 단계만 건너뜀).
- 공개 서버(docker-compose.prod.yml)는 가상 상황·즉시 안전 점검·trip 틱·가입을 관리 토큰으로만 연다: --admin-token 또는
  환경변수 GURYONG_ADMIN_TOKEN (deploy/admin_token.sh 로 확인, docs/deploy/README.md §3). 토큰이 있으면 planner 점검 주기를 기다리지 않는다.
"""
import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts" / "planner"), str(ROOT / "scripts" / "trip")]
import cli  # noqa: E402  대화 출력 (ChatPrinter, print_proposal, print_plan)
import trip_cli  # noqa: E402  재생기 (Player), 이벤트·상태 출력
from client import PlannerApiError, PlannerClient, Session  # noqa: E402
from scene import expand  # noqa: E402

try:
    import msvcrt  # Windows: 키 하나로 진행
except ImportError:
    msvcrt = None

KST = timezone(timedelta(hours=9))
BOLD, DIM, RED, GREEN, YELLOW, CYAN, RESET = "\033[1m", "\033[2m", "\033[31m", "\033[32m", "\033[33m", "\033[36m", "\033[0m"
STATE_DIR = Path.home() / ".guryongmate"

# 데모 데이터 (docs/app/README.md §6.1). 계획은 재생 파일과 같은 것을 쓴다 (한 곳에서 관리)
DEMO_PLAN = json.loads((ROOT / "scripts" / "trip" / "scenes" / "scene4_safety.json").read_text(encoding="utf-8"))["plan"]
HOUSE, PORT, MUSEUM = "kto-1909659", "kto-2610501", "kto-2660046"
BREAKWATER, CAFE, HISTORY = "poi-gr-breakwater-north", "kto-2834344", "kto-2603493"
HAZARD_VIA = [35.9912, 129.5572]  # 카페 가는 길에 지나는 구룡포수협 앞 (침수 감시 구역)
PLAN_NOW = "2026-10-09T10:00:00+09:00"  # 장면 ①: 여행 전날
# 장면 ① 에서 Agent 가 조건을 되물을 때의 답 (페르소나: 역사·사진 / 음식·바다, 오래 걷기 부담. PROJECT.md §3)
ANSWER = ("엄마가 오래 걷는 건 힘들어하셔. 역사랑 사진, 바다랑 과메기 같은 음식을 좋아해. 일본인 가옥거리 보고 구룡포항에서 점심 먹고, "
          "과메기문화관이랑 북방파제 들렀다가 마지막엔 바다 보이는 카페파도에서 쉬고 싶어. 시간은 지금대로 12시 반부터 6시 전까지.")
DAY = DEMO_PLAN["start_date"]


def at(hhmm: str) -> datetime:
    h, m = hhmm.split(":")
    return datetime.fromisoformat(f"{DAY}T{int(h):02d}:{int(m):02d}:00+09:00")


@dataclass
class Step:
    id: str
    scene: str
    title: str
    screen: list[str]  # 이 단계 뒤 앱에 보여야 할 것 (발표자용)
    run: Callable[[], None]
    when: Callable[[], bool] = lambda: True  # False 면 목록에 없음 (예: 계획을 미리 만들면 대화 단계 없음)
    pace: float = 1  # 배속에 곱함 (보여줄 것이 적은 이동 구간은 빨리)


@dataclass
class Progress:
    """단계 사이에 이어지는 것. 파일에 저장해 --from 으로 이어서 할 수 있다."""
    plan_id: int | None = None
    session_id: str | None = None
    t: str | None = None  # 재생 시각 (가상)
    pos: list[float] | None = None  # 마지막 위치
    guide_job: str | None = None
    done: list[str] = field(default_factory=list)


class Demo:
    def __init__(self, a):
        self.a = a
        self.url = a.url.rstrip("/")
        self.c = PlannerClient(self.url, timeout=60, session=Session(self.url, STATE_DIR / "demo_session.json"),
                               admin_token=a.admin_token)
        self.state_file = Path(a.state) if a.state else STATE_DIR / "demo_state.json"
        self.p = Progress()
        self.places: dict[str, tuple[float, float]] = {}
        self.results: list[dict] = []  # 단계별 소요 시간·기대 결과
        self.usage = {"input": 0, "cached": 0, "output": 0, "turns": 0}
        self.provider = "?"
        self.switchable = False
        self.steps = self._steps()

    # ---------- 저장 ----------
    def load(self) -> None:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if data.get("url") == self.url and data.get("login") == self.a.login:
            self.p = Progress(**{k: v for k, v in data.items() if k in Progress.__dataclass_fields__})

    def save(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps({"url": self.url, "login": self.a.login, **self.p.__dict__},
                                              ensure_ascii=False, indent=1), encoding="utf-8")

    # ---------- 공통 ----------
    @property
    def plan(self) -> int:
        if self.p.plan_id is None:
            raise SystemExit("계획이 없습니다. 준비 단계(prep)부터 하세요.")
        return self.p.plan_id

    @property
    def t(self) -> datetime:
        return datetime.fromisoformat(self.p.t) if self.p.t else at("12:25")

    def coords(self, place_id: str) -> tuple[float, float]:
        if place_id not in self.places:
            pl = self.c.get_place(place_id)
            self.places[place_id] = (pl["lat"], pl["lng"])
        return self.places[place_id]

    def path(self, src, dst, mode="walk"):
        try:
            return [(lat, lng) for lng, lat in self.c.route([src, dst], mode)["geometry"]["coordinates"]]
        except Exception as e:
            print(f"{DIM}  (경로선을 못 받아 직선으로 갑니다: {e}){RESET}")
            return None

    def sim(self, **kw) -> dict:
        return self.c.set_sim(**{k: v for k, v in kw.items() if v is not None})

    def session(self) -> str:
        """대화할 세션: 이 계획의 가장 최근 세션 (앱이 만든 세션도). 없으면 만든다."""
        sessions = self.c.list_sessions(self.plan)
        if sessions:
            self.p.session_id = max(sessions, key=lambda s: s["updated_at"])["id"]
        elif not self.p.session_id:
            self.p.session_id = self.c.create_session(self.plan)["id"]
        return self.p.session_id

    def expect(self, want: dict) -> bool:
        ok = trip_cli.matches(want, self.c.trip_events(self.plan, 0, 200))
        self.results[-1]["expect"].append({"want": want, "ok": ok})
        print(f"  기대 {want}: {GREEN + '통과' if ok else RED + '실패'}{RESET}")
        return ok

    def check(self, ok: bool, what: str) -> None:
        self.results[-1]["expect"].append({"want": what, "ok": ok})
        print(f"  확인 {what}: {GREEN + '통과' if ok else RED + '실패'}{RESET}")

    # ---------- 재생 ----------
    def play(self, steps: list[dict]) -> trip_cli.Player:
        """재생 단계(scripts/trip/scenes 형식)를 지금 위치·시각에서 이어서 보낸다. 기대(expect)는 이 데모의 결과에 모은다."""
        scene = {"start": self.t.isoformat(), "steps": ([{"goto": self.p.pos}] if self.p.pos else []) + steps}
        actions = [x for x in expand(scene, self.coords, self.path)[1:] if x.kind != "expect"]  # 맨 앞은 시작 sim 동작
        player = trip_cli.Player(self.c, self.plan, self.a.speed, self.a.stay_speed, on_event=self.on_event)
        player.started = True
        player.play(actions)
        player.finish()
        last = [x for x in actions if x.kind == "loc"]
        if last:
            self.p.pos = [last[-1].lat, last[-1].lng]
        if actions:
            self.p.t = actions[-1].at.isoformat()
        return player

    def stay_until(self, hhmm: str) -> None:
        """지금 자리에서 그 시각까지 머문다 (5분마다 점)."""
        self.play([{"at": hhmm}])

    def on_event(self, e: dict) -> None:
        trip_cli.print_event(e)

    def wait_proactive(self, limit: float = 300) -> list[dict]:
        """선제 턴(단계 3 queued)이 끝날 때까지. 실제 LLM 은 20~60초 걸린다."""
        print(f"{DIM}  Agent 선제 턴을 기다리는 중…{RESET}", flush=True)
        end = time.monotonic() + limit
        while True:
            events = self.c.trip_events(self.plan, 0, 200)
            if not any(e["status"] == "queued" and e["level"] == 3 for e in events):
                return events
            if time.monotonic() > end:
                print(f"{YELLOW}  선제 턴이 {limit:.0f}초 안에 끝나지 않았습니다.{RESET}")
                return events
            time.sleep(2)

    def wait_event(self, kind: str, limit: float = 150) -> None:
        """그 이벤트가 나올 때까지 trip 틱을 몇 초마다 부른다. AUTH_REQUIRED=true 인 서버는 planner 안전 점검을
        바로 돌릴 수 없어(POST /planner/safety/scan 404) 점검 주기(PLANNER_SAFETY_WATCH_SEC)를 기다려야 한다."""
        end, said = time.monotonic() + limit, False
        while not any(e["type"] == kind for e in self.c.trip_events(self.plan, 0, 200)):
            if time.monotonic() > end:
                print(f"{RED}  {kind} 이벤트가 {limit:.0f}초 안에 나오지 않았습니다.{RESET}")
                return
            if not said:
                print(f"{DIM}  planner 안전 점검 주기를 기다리는 중 (PLANNER_SAFETY_WATCH_SEC, 발표 서버 권장 10초)…{RESET}", flush=True)
                said = True
            time.sleep(5)
            for e in self.c.trip_tick(self.plan).get("events", []):
                self.on_event(e)

    def show_proactive(self, kind: str) -> dict | None:
        """가장 최근 kind 이벤트의 Agent 선제 메시지와 제안 카드를 보여준다."""
        events = self.wait_proactive()
        ev = next((e for e in reversed(events) if e["type"] == kind and e["level"] == 3), None)
        if ev is None:
            print(f"{RED}  {kind} 이벤트가 없습니다.{RESET}")
            return None
        trip_cli.print_event(ev)
        if ev["status"] == "failed" or not ev.get("agent"):
            print(f"{RED}  Agent 에 전하지 못했습니다 ({ev.get('last_error')}). docs/demo/README.md §4 대처.{RESET}")
            return ev
        msgs = self.c.get_session(ev["agent"]["session_id"])["messages"]
        msg = next((m for m in msgs if m["id"] == ev["agent"].get("message_id")), msgs[-1] if msgs else None)
        if msg:
            print(f"\n{CYAN}Agent:{RESET} {msg['content']}")
            for card in msg["cards"]:
                if card["type"] == "proposal":
                    cli.print_proposal(card["proposal"])
        self.p.session_id = ev["agent"]["session_id"]
        return ev

    # ---------- 대화 ----------
    def chat(self, message: str) -> list[dict]:
        """한 턴. --chat script 면 스크립트가 보내고 응답을 실시간으로 보여준다 (앱은 세션을 다시 불러 그린다).
        --chat app 이면 발표자가 앱에서 입력하고, 스크립트는 새 답변을 기다렸다가 보여준다. 반환: 답변의 카드."""
        sid = self.session()
        if self.a.chat == "app":
            before = len(self.c.get_session(sid)["messages"])
            print(f"{BOLD}앱에서 입력하세요:{RESET} «{message}»  {DIM}(답변을 기다립니다. 끝나면 계속){RESET}")
            return self._wait_reply(before)
        print(f"{CYAN}사용자:{RESET} {message}")
        printer = cli.ChatPrinter(show_thinking=False)
        for ev, d in self.c.chat_stream(sid, message):
            printer.handle(ev, d)
            if ev == "done" and d.get("usage"):
                for k in ("input", "cached", "output"):
                    self.usage[k] += d["usage"].get(k, 0) or 0
                self.usage["turns"] += 1
            if ev == "error":
                self.check(False, f"대화 오류 {d.get('code')}")
        return printer.cards

    def _wait_reply(self, before: int, limit: float = 600) -> list[dict]:
        end = time.monotonic() + limit
        while time.monotonic() < end:
            sid = self.session()
            msgs = self.c.get_session(sid)["messages"]
            if len(msgs) > before and msgs[-1]["role"] == "assistant":
                print(f"{CYAN}Agent:{RESET} {msgs[-1]['content']}")
                for card in msgs[-1]["cards"]:
                    if card["type"] == "proposal":
                        cli.print_proposal(card["proposal"])
                    elif card["type"] == "recommendation":
                        cli.print_recommendation(card["recommendation"])
                return msgs[-1]["cards"]
            time.sleep(1.5)
        print(f"{YELLOW}답변이 {limit:.0f}초 안에 오지 않았습니다.{RESET}")
        return []

    def pending_proposal(self) -> dict | None:
        cards = [c["proposal"] for m in self.c.get_session(self.session())["messages"] for c in m["cards"]
                 if c["type"] == "proposal"]
        pending = [p for p in cards if p["status"] == "pending"]
        return pending[-1] if pending else None

    def accept(self, proposal: dict | None, session_id: str | None = None) -> dict | None:
        """제안 승인 (앱의 [전체 적용]). --chat app 이면 발표자가 앱에서 누르고 스크립트는 기다린다."""
        if proposal is None:
            print(f"{RED}  승인할 제안이 없습니다.{RESET}")
            self.check(False, "승인할 제안")
            return None
        pid = proposal["id"]
        if self.a.chat == "app":
            print(f"{BOLD}앱에서 제안 #{pid} 의 [전체 적용] 을 누르세요.{RESET}")
            end = time.monotonic() + 600
            while time.monotonic() < end:
                p = self.c.get_proposal(self.plan, pid)
                if p["status"] != "pending":
                    print(f"  제안 #{pid}: {p['status']}")
                    return p
                time.sleep(1.5)
            return None
        try:
            r = self.c.accept_proposal(session_id or self.session(), pid)
        except PlannerApiError as e:
            print(f"{RED}  제안 #{pid} 승인 실패: {e}{RESET}")
            self.check(False, f"제안 #{pid} 승인")
            return None
        print(f"  제안 #{pid} 승인 ({r['proposal']['status']})")
        return r["proposal"]

    # ---------- 출력 ----------
    def show_plan_brief(self) -> dict:
        """일정 요약. 여행 중이면 지금 기준 재계산(진행·지연 반영) 시각으로."""
        p, label = self.c.get_plan(self.plan), "계획"
        try:
            if self.c.trip_state(self.plan)["active"]:
                p, label = self.c.projection(self.plan, day=1)["plan"], "지금 기준 예상"
        except PlannerApiError:
            pass
        print(f"  {DIM}({label}){RESET}")
        for d in p["days"]:
            items = [f"{i['place']['title'] if i['place'] else i['place_id']} {i['start_at'] or '건너뜀'}"
                     for i in d["items"] if i["status"] != "skipped"]
            print(f"  {d['day']}일차 {d['date']}: " + " → ".join(items) + f" (종료 {d['end_at']})")
        return p

    def show_safety(self) -> dict:
        s = self.c.safety_status()
        mode = s.get("mode", "?")
        label = "실제 데이터 (E-UM 디지털트윈·기상청)" if mode == "live" else f"가상 상황 {s.get('scenario')}"
        print(f"  안전 정보: {BOLD}{label}{RESET} · {s['now'][:16]} · 전체 {s['overall_risk']}")
        zones = [z for z in s["zones"] if z["kind"] == "breakwater"]
        print("  방파제: " + ", ".join(f"{z['name']} {z['risk']}" for z in zones))
        sensors = s["sensors"]
        raised = [x for x in sensors if x["risk"] in ("caution", "danger")]
        print(f"  침수 센서 {len(sensors)}개" + (f", 주의 이상 {len(raised)}개: " + ", ".join(
            f"{x['name']} {x['risk']}" for x in raised[:4]) if raised else ", 모두 정상"))
        act = s.get("breakwater_activity") or []
        if act:
            print("  방파제 CCTV (최근 1시간): " + ", ".join(
                f"{b['name']} 실족 {b['fall']}·침입 {b['intrusion']}·접근 {b['approach']}" for b in act))
        print(f"  특보: {', '.join(s['warnings']) or '없음'}")
        bad = [k for k, v in s["data_status"].items() if not v["ok"]]
        if bad:
            print(f"{YELLOW}  받지 못한 데이터: {', '.join(bad)} (판단 보류로 표시됨){RESET}")
        return s

    # ---------- 단계 ----------
    def _steps(self) -> list[Step]:
        prebuilt = lambda: self.a.plan == "prebuilt"  # noqa: E731
        return [
            Step("prep", "준비", "데모 사용자·계획·가이드·가상 시각 초기화", [
                "앱: 데모 사용자로 로그인한 홈. 계획 '엄마랑 구룡포' 하나 (새로고침)",
                "앱 설정: 데모 위치 모드 켜짐 (서버의 위치를 지도에 그림)",
            ], self.prep),
            Step("s1_chat", "장면 ①", "대화로 일정 짜기: «엄마랑 구룡포 일정 짜줘»", [
                "채팅: 진행 단계(기억하기 · 장소 찾기 · 조건 반영 · 일정 조정 · 일정 검토 → 다시 고침 · 변경안 만들기)",
                "제안 카드: 가옥거리 → 구룡포항 → 과메기문화관 → 북방파제 → 카페파도, 17:38 종료",
            ], self.s1_chat, when=lambda: not prebuilt()),
            Step("s1_answer", "장면 ①", "(Agent 가 먼저 물으면) 조건 답하기", [
                "채팅: 조건(걷기 부담·관심사·가고 싶은 곳)을 반영한 진행 단계 → 제안 카드",
                "(Agent 가 바로 제안했으면 이 단계는 건너뜀)",
            ], self.s1_answer, when=lambda: not prebuilt()),
            Step("s1_accept", "장면 ①", "제안 승인 → 플래너 반영", [
                "플래너: 5곳, 17:38 종료. 지도에 마커·경로선",
            ], self.s1_accept, when=lambda: not prebuilt()),
            Step("s1_plan", "장면 ①", "미리 만든 계획 보여주기", [
                "플래너: 가옥거리 12:30 → 구룡포항 13:40 → 과메기문화관 14:47 → 북방파제 16:00 → 카페파도 16:53, 17:38 종료",
                "(대화 기록에는 계획을 만든 대화가 없다 — 말로 설명)",
            ], self.s1_plan, when=prebuilt),
            Step("s1_cafe", "장면 ①", "(선택) «갈 만한 카페 추천해줘» → 추천 카드", [
                "추천 카드: 카페파도 [일정에 있음], 까멜리아인구룡포 [추가]. 누르지 않고 넘어간다",
            ], self.s1_cafe),
            Step("s2_start", "장면 ②", "여행 시작 (10/10 12:25, 가옥거리 근처)", [
                "앱: '여행 중' 동행 모드. 지도에 오늘 일정과 현재 위치(가옥거리 근처)",
                "(앱에서 [여행 시작] 을 눌러도 같다 — 같은 날이면 그대로 이어짐)",
            ], self.s2_start),
            Step("s2_arrive", "장면 ②", "일본인 가옥거리 도착 → 해설 카드", [
                "지도: 위치가 가옥거리로 걸어감",
                "해설 카드: '구룡포 일본인 가옥거리예요. 이야기 들어볼까요? [짧게] [자세히]'",
            ], self.s2_arrive),
            Step("s2_ask", "장면 ②", "«언제 지어진 집들이에요?»", [
                "채팅: 가이드 노트 확인 · 자료 찾기 → 관심사(역사·사진)를 반영한 해설",
            ], self.s2_ask),
            Step("s2_shorter", "장면 ②", "«다음부터는 더 짧게 해줘요» → 선호 기억", [
                "채팅: '기억했어요' 메모리 칩 (해설은 핵심만 짧게)",
            ], self.s2_shorter),
            Step("s3_delay", "장면 ③", "가옥거리에 30분 더 머묾 → 지연 감지 → Agent 가 먼저 제안", [
                "13:53 Agent 메시지가 먼저 옴: 18:01 종료로 18시 규칙 위반 + 추천안 1개 + 다른 방법 한 줄",
                "제안 카드 (각본: 과메기문화관 빼기 / 실제 LLM 은 예: 카페 체류 줄이기 — 문장·안은 매번 다를 수 있음)",
            ], self.s3_delay),
            Step("s3_keep", "장면 ③", "«과메기문화관은 유지해줘» → 다시 제안", [
                "채팅: 조건 반영(과메기문화관 꼭 포함) → 새 제안 (각본: 구룡포항 50분 → 30분, 17:43 종료)",
                "이전 제안 카드는 '대체됨'",
            ], self.s3_keep),
            Step("s3_accept", "장면 ③", "새 제안 승인 → 플래너·지도 갱신", [
                "플래너: 구룡포항 30분, 과메기문화관 유지 (18시 전 종료)",
            ], self.s3_accept),
            Step("s3_move", "장면 ③→④", "구룡포항 점심 → 과메기문화관 (이동)", [
                "지도: 가옥거리 → 구룡포항 → 과메기문화관으로 이동 (출발·도착 알림)",
                "15:18 과메기문화관 관람 중",
            ], self.s3_move, pace=3),
            Step("s4_live", "장면 ④", "실제 DT 데이터 화면", [
                "안전 화면: 실제 E-UM 데이터 — 침수 센서·방파제 CCTV·특보 (지금 구룡포의 실제 값)",
                "(가상 시각 10/10 15:20 에 맞춰 보여줌. 실제가 평온하면 모두 안전)",
            ], self.s4_live),
            Step("s4_storm", "장면 ④", "가상 악천후로 전환 → 위험 감지 → Agent 가 먼저 제안", [
                "안전 화면: 호우·강풍·풍랑주의보, 침수 센서 상승, 방파제 위험",
                "Agent 메시지: 북방파제 위험 근거(풍속 16m/s·파고 3.5m, 특보, CCTV 실족 감지) + 근대문화역사관(실내)으로 바꾸기 + 다른 방법",
                "비교 카드: 위험 방문 1곳 → 0곳 (북방파제 → 근대문화역사관), 종료 시각 약 8분 당겨짐",
            ], self.s4_storm),
            Step("s4_accept", "장면 ④", "제안 승인 (15:25)", [
                "플래너: 북방파제 → 근대문화역사관. 지도 경로 갱신",
            ], self.s4_accept),
            Step("s4_move", "장면 ④", "역사관 → 차로 카페 (위험 구역 접근 경고) → 카페 도착", [
                "지도: 역사관 도착 → 차로 이동 중 '침수 감시 구역(구룡포수협 주변) 근처예요' 즉시 경고 (LLM 없음)",
                "카페파도 도착",
            ], self.s4_move),
            Step("end", "마무리", "여행 종료·결과", [
                "앱: 여행 종료 (계획 모드로). 필요하면 대화·플래너로 Q&A",
            ], self.finish),
        ]

    def active_steps(self) -> list[Step]:
        return [s for s in self.steps if s.when()]

    # ----- 준비 -----
    def login(self) -> None:
        try:
            self.c.login(self.a.login, self.a.password)
        except PlannerApiError as e:
            if e.status != 401:
                raise
            self.c.signup(self.a.login, self.a.password, self.a.name)
            print(f"  데모 사용자 {self.a.login} 를 만들었습니다.")

    def preflight(self) -> None:
        """서버 상태와 LLM 공급자를 보여준다."""
        print(f"{BOLD}서버{RESET} {self.url}")
        try:
            info = self.c.agent_info()
        except Exception as e:
            raise SystemExit(f"{RED}서버에 접속할 수 없습니다: {e}{RESET}")
        self.provider = info.get("provider", "?")
        real = not self.provider.startswith("scripted")
        color = GREEN if real else YELLOW
        print(f"{BOLD}LLM{RESET}  {color}{self.provider}{RESET}  "
              + ("(실제 LLM — 키·크레딧 필요)" if real else "(각본 LLM — 정해진 문장에만 답함, 비상·리허설용)"))
        try:
            s = self.c.sim()
            self.switchable = bool(s.get("switchable"))
            print(f"{BOLD}안전{RESET} " + ("실제 <-> 가상 전환 가능 (SAFETY_SIM_ENABLED)" if self.switchable else
                                          f"{YELLOW}가상 상황만 (SAFETY_SOURCE=mock) — 장면 ④ 실제 데이터 단계는 건너뜀{RESET}"))
        except PlannerApiError:
            raise SystemExit(f"{RED}safety 가상 상황을 쓸 수 없습니다 (404). backend/.env 에 SAFETY_SIM_ENABLED=true 로 띄우세요.{RESET}")
        try:
            r = self.c.routing_status()
            ok = (r.get("engine") or {}).get("ok")
            print(f"{BOLD}길찾기{RESET} " + ("Valhalla 정상" if ok else f"{YELLOW}엔진 없음 — 직선 근사 ({r}){RESET}"))
        except Exception as e:
            print(f"{YELLOW}길찾기 상태를 못 받음: {e} (직선 근사로 동작){RESET}")
        self.login()
        print(f"{BOLD}사용자{RESET} {self.a.login} · 대화 {('스크립트가 보냄' if self.a.chat == 'script' else '발표자가 앱에서 입력')}"
              f" · 계획 {'미리 만든 계획' if self.a.plan == 'prebuilt' else '대화로 만듦'}"
              f" · 배속 이동 {self.a.speed:g} / 머무름 {self.a.stay_speed:g}")

    def prep(self) -> None:
        c = self.c
        for pl in c.list_plans():  # 이 사용자의 계획·여행 상태·메모리·노트를 지운다 (리허설마다 같게)
            try:
                c.trip_end(pl["id"])
            except PlannerApiError:
                pass
            c.delete_plan(pl["id"])
        for path in ("/trip/me", "/docent/me"):
            try:
                c._req("DELETE", path)
            except PlannerApiError as e:
                print(f"{YELLOW}  {path} 삭제 실패: {e}{RESET}")
        self.p = Progress()
        self.sim(source="mock", scenario="calm", now=PLAN_NOW)
        print(f"  가상 시각 {PLAN_NOW[:16]} (여행 전날), 가상 상황 calm")
        if self.a.plan == "prebuilt":
            self.p.plan_id = trip_cli._create_plan(c, DEMO_PLAN)
            self.p.session_id = c.create_session(self.plan)["id"]
            self.guide(wait=True)
        else:
            spec = DEMO_PLAN
            p = c.create_plan(spec["title"], spec["start_date"], spec.get("end_date"), spec["day_start"], spec["day_end"])
            self.p.plan_id = p["id"]
            self.p.session_id = c.create_session(self.plan)["id"]
            print(f"  빈 계획 #{self.plan} '{spec['title']}' ({spec['start_date']} {spec['day_start']}~{spec['day_end']})")
        self.p.t, self.p.pos = None, None

    def guide(self, wait: bool) -> None:
        """가이드 준비 (장소별 해설 노트). 실제 LLM 은 장소마다 웹 검색을 해 1~2분 걸린다."""
        job = self.c.guide_prepare(self.plan)
        self.p.guide_job = job.get("job_id")
        print(f"  가이드 준비 시작 ({job.get('total', '?')}곳)")
        if wait:
            self.wait_guide()

    def wait_guide(self, limit: float = 300) -> None:
        if not self.p.guide_job:
            return
        end = time.monotonic() + limit
        while time.monotonic() < end:
            try:
                job = self.c.guide_job(self.plan, self.p.guide_job)
            except PlannerApiError:
                return
            if job["status"] != "running":
                print(f"  가이드 노트 {job.get('ready')}/{job.get('total')} 준비됨")
                self.check(job.get("ready") == job.get("total"), "가이드 노트 준비")
                self.p.guide_job = None
                return
            time.sleep(2)
        print(f"{YELLOW}  가이드 준비가 아직 끝나지 않았습니다 (해설은 노트 없이도 동작).{RESET}")

    # ----- 장면 ① -----
    def s1_chat(self) -> None:
        cards = self.chat("엄마랑 구룡포 일정 짜줘")
        if not any(c["type"] == "proposal" for c in cards):
            print(f"{DIM}  (제안 대신 질문이면 다음 단계 s1_answer 에서 답한다){RESET}")

    def s1_answer(self) -> None:
        """실제 LLM 은 첫 요청에 조건을 되묻는다 (PROJECT.md §3 장면 ① '추가 질문'). 아직 제안이 없을 때만 페르소나의 답을 보낸다.
        가고 싶은 곳을 말해 데모 계획(§6.1)에 가깝게 만든다 (장면 ③④ 의 시각이 이 계획 기준)."""
        if self.pending_proposal():
            print(f"{DIM}  Agent 가 이미 제안했습니다. 건너뜁니다.{RESET}")
            return
        cards = self.chat(self.a.answer)
        self.check(any(c["type"] == "proposal" for c in cards), "제안 카드")

    def s1_accept(self) -> None:
        self.accept(self.pending_proposal())
        self.align_plan()
        self.show_plan_brief()
        self.guide(wait=False)  # 장면 ② 전까지 끝나면 된다

    def align_plan(self) -> None:
        """장면 ②~④ 의 시각은 데모 계획(§6.1) 기준이다. 대화로 만든 계획이 다르면(실제 LLM) 데모 계획으로 맞출지 묻는다."""
        p = self.c.get_plan(self.plan)
        items = [i for i in p["days"][0]["items"] if i["status"] != "skipped"]
        got = [(i["place_id"], i["stay_minutes"]) for i in items]
        want = [(i["place_id"], i["stay_minutes"]) for i in DEMO_PLAN["items"]]
        has_rule = any(c["type"] == "end_before" for c in p["constraints"])
        if got == want and has_rule:
            self.check(True, "데모 계획과 같음")
            return
        print(f"{YELLOW}  대화로 만든 계획이 데모 계획과 다릅니다 (장면 ③④ 의 시각·제안이 달라질 수 있음).{RESET}")
        print(f"{DIM}  지금: {got}\n  데모: {want}{RESET}")
        if not (self.a.auto or self.a.fast_to) and ask("  데모 계획으로 맞출까요? [Enter 예 / n 아니오] ") == "n":
            return
        for i in items:
            self.c.delete_item(self.plan, i["id"])
        for it in DEMO_PLAN["items"]:
            self.c.add_item(self.plan, it["place_id"], 1, stay_minutes=it["stay_minutes"], travel_mode=it.get("travel_mode"))
        if not has_rule:
            for r in DEMO_PLAN["constraints"]:
                self.c.add_constraint(self.plan, r["type"], r["params"], hard=r.get("hard", True), note=r.get("note", ""))
        print("  데모 계획으로 맞췄습니다.")

    def s1_plan(self) -> None:
        self.show_plan_brief()

    def s1_cafe(self) -> None:
        cards = self.chat("갈 만한 카페 추천해줘")
        self.check(any(c["type"] == "recommendation" for c in cards), "추천 카드")

    # ----- 장면 ② -----
    def s2_start(self) -> None:
        self.wait_guide(60)
        t0 = at("12:25")
        self.sim(source="mock", scenario="calm", now=t0.isoformat())
        trip_cli._wait_clock(self.c, self.plan, t0)
        state = self.c.trip_start(self.plan, self.session())
        self.check(state["active"], "여행일 시작")
        # 가옥거리에서 걸어서 약 6분 떨어진 곳에서 시작 (구룡포항 쪽 길 위, 12:30 쯤 도착 반경에 들어감)
        line = self.path(self.coords(PORT), self.coords(HOUSE)) or [self.coords(PORT), self.coords(HOUSE)]
        start = _back_along(line, 420)
        self.p.t, self.p.pos = t0.isoformat(), list(start)
        self.c.trip_location(self.plan, [trip_cli._point(start[0], start[1], t0, 10, "replay")])
        trip_cli.print_state(self.c.trip_state(self.plan))

    def s2_arrive(self) -> None:
        self.play([{"walk_to": HOUSE, "stay": 3}])
        self.expect({"type": "arrived", "level": 2, "place_id": HOUSE, "card": {"kind": "docent", "place_id": HOUSE}})
        self.expect({"type": "delay", "not": True})

    def s2_ask(self) -> None:
        self.chat("언제 지어진 집들이에요?")

    def s2_shorter(self) -> None:
        self.chat("다음부터는 더 짧게 해줘요")
        mem = self.c.memory(self.plan)
        self.check(mem.get("style_length") == "short", "해설 길이 선호 short")

    # ----- 장면 ③ -----
    def s3_delay(self) -> None:
        self.stay_until("13:55")
        self.expect({"type": "delay", "level": 3})
        ev = self.show_proactive("delay")
        self.check(bool(ev and (ev.get("agent") or {}).get("proposal_id")), "선제 턴 제안 카드")

    def s3_keep(self) -> None:
        old = self.pending_proposal()
        cards = self.chat("과메기문화관은 유지해줘")
        new = next((c["proposal"] for c in reversed(cards) if c["type"] == "proposal"), None)
        self.check(bool(new) and (old is None or new["id"] != old["id"]), "조건을 반영한 새 제안")

    def s3_accept(self) -> None:
        r = self.accept(self.pending_proposal())
        p = self.show_plan_brief()
        self.check(bool(r) and any(i["place_id"] == MUSEUM and i["status"] != "skipped" for i in p["days"][0]["items"]),
                   "과메기문화관 유지")

    def s3_move(self) -> None:
        self.play([{"at": "14:00"}, {"walk_to": PORT, "stay": 30}, {"walk_to": MUSEUM, "stay": 5}, {"at": "15:18"}])
        self.expect({"type": "arrived", "place_id": PORT})
        self.expect({"type": "arrived", "place_id": MUSEUM})

    # ----- 장면 ④ -----
    def s4_live(self) -> None:
        if not self.switchable:
            print(f"{YELLOW}  실제 데이터로 바꿀 수 없습니다 (SAFETY_SIM_ENABLED=false). 이 단계를 건너뜁니다.{RESET}")
            return
        t = at("15:20")
        self.sim(source="live", now=t.isoformat())
        self.p.t = t.isoformat()
        s = self.show_safety()
        self.check(s.get("mode") == "live", "실제 데이터 모드")

    def s4_storm(self) -> None:
        t = at("15:20")
        self.sim(source="mock", scenario="storm", now=t.isoformat())
        self.p.t = t.isoformat()
        self.show_safety()
        trip_cli._tick(self.c, self.plan, t, wait=True, on_event=self.on_event)  # planner 안전 점검 + trip 틱
        self.wait_event("safety_raised")
        self.expect({"type": "safety_raised", "level": 3})
        ev = self.show_proactive("safety_raised")
        self.check(bool(ev and (ev.get("agent") or {}).get("proposal_id")), "선제 턴 제안 카드")

    def s4_accept(self) -> None:
        self.stay_until("15:25")
        ev = next((e for e in reversed(self.c.trip_events(self.plan, 0, 200))
                   if e["type"] == "safety_raised" and (e.get("agent") or {}).get("proposal_id")), None)
        prop = self.c.get_proposal(self.plan, ev["agent"]["proposal_id"]) if ev else None
        if prop and prop["status"] != "pending":  # 이미 앱에서 승인했거나 대화로 대체됨
            prop = self.pending_proposal() or prop
        self.accept(prop if prop and prop["status"] == "pending" else None, ev["agent"]["session_id"] if ev else None)
        p = self.show_plan_brief()
        places = {i["place_id"] for i in p["days"][0]["items"] if i["status"] != "skipped"}
        self.check(BREAKWATER not in places and HISTORY in places, "북방파제 → 근대문화역사관")

    def s4_move(self) -> None:
        self.play([{"at": "15:47", "walk_to": HISTORY, "stay": 40}])
        self.expect({"type": "arrived", "place_id": HISTORY})
        self.play([{"drive_to": HAZARD_VIA}])
        self.expect({"type": "near_hazard", "level": 1})
        self.play([{"drive_to": CAFE, "stay": 5}])
        self.expect({"type": "arrived", "place_id": CAFE})

    def finish(self) -> None:
        trip_cli.print_state(self.c.trip_state(self.plan))
        self.c.trip_end(self.plan)  # 끝내지 않으면 백그라운드 틱이 선제 턴을 더 보낸다
        print("  여행일을 종료했습니다.")

    # ---------- 진행 ----------
    def run_step(self, s: Step, quiet: bool = False) -> None:
        print(f"\n{BOLD}{CYAN}== [{s.scene}] {s.title}{RESET}  {DIM}({s.id}{', 빨리 감기' if quiet else ''}){RESET}")
        self.results.append({"id": s.id, "title": s.title, "expect": [], "sec": 0.0})
        t0 = time.monotonic()
        try:
            s.run()
        except PlannerApiError as e:
            print(f"{RED}  API 오류: {e}{RESET}")
            self.check(False, f"API 오류 {e.code}")
        self.results[-1]["sec"] = round(time.monotonic() - t0, 1)
        if s.id not in self.p.done:
            self.p.done.append(s.id)
        self.save()
        print(f"\n{BOLD}앱 화면{RESET} ({self.results[-1]['sec']:.0f}초)")
        for line in ([] if quiet else s.screen):
            print(f"  {GREEN}▶{RESET} {line}")

    def run(self) -> None:
        steps = self.active_steps()
        i = 0
        ids = [s.id for s in steps]
        for sid in (self.a.from_step, self.a.fast_to):
            if sid and sid not in ids:
                raise SystemExit(f"단계가 없습니다: {sid} ({', '.join(ids)})")
        if self.a.from_step:
            self.load()
            i = ids.index(self.a.from_step)
        fast = ids.index(self.a.fast_to) if self.a.fast_to else -1
        speeds = (self.a.speed, self.a.stay_speed)
        while i < len(steps):
            s = steps[i]
            if i < fast:  # 빨리 감기: 기다리지 않고 묻지 않는다 (LLM 을 바꾼 뒤 그 장면 앞까지 다시 만들 때)
                self.a.speed = self.a.stay_speed = 0
                self.run_step(s, quiet=True)
                i += 1
                continue
            self.a.speed, self.a.stay_speed = speeds[0] * s.pace, speeds[1] * s.pace
            if not self.a.auto:
                k = ask(f"\n{DIM}다음:{RESET} {BOLD}[{s.scene}] {s.title}{RESET}  "
                        f"{DIM}Enter 실행 · s 건너뛰기 · b 이전 · l 목록 · q 끝{RESET} ")
                if k == "q":
                    break
                if k == "s":
                    i += 1
                    continue
                if k == "b":
                    i = max(0, i - 1)
                    continue
                if k == "l":
                    print_steps(steps, self.p.done)
                    continue
            self.run_step(s)
            if self.a.auto:
                time.sleep(self.a.pause)
                i += 1
                continue
            while ask(f"{DIM}Enter 다음 · r 이 단계 다시{RESET} ") == "r":
                self.run_step(s)
            i += 1
        self.report()

    def report(self) -> None:
        if not self.results:
            return
        print(f"\n{BOLD}결과{RESET} ({self.provider})")
        total_ok = total = 0
        for r in self.results:
            oks = sum(x["ok"] for x in r["expect"])
            total_ok, total = total_ok + oks, total + len(r["expect"])
            mark = "" if not r["expect"] else (f"{GREEN}{oks}/{len(r['expect'])}{RESET}" if oks == len(r["expect"])
                                               else f"{RED}{oks}/{len(r['expect'])}{RESET}")
            print(f"  {r['id']:<10} {r['sec']:>6.1f}초  {mark}  {r['title']}")
            for x in r["expect"]:
                if not x["ok"]:
                    print(f"    {RED}실패: {x['want']}{RESET}")
        print(f"  합계 {sum(r['sec'] for r in self.results):.0f}초, 확인 {total_ok}/{total}")
        if self.usage["turns"]:
            print(f"  대화 {self.usage['turns']}턴 토큰: 입력 {self.usage['input']:,} (캐시 {self.usage['cached']:,}), "
                  f"출력 {self.usage['output']:,} — 선제 턴·가이드 준비는 서버 로그 '[agent] … usage' 에서")
        if self.a.log:
            Path(self.a.log).write_text(json.dumps({"url": self.url, "provider": self.provider, "speed": self.a.speed,
                                                    "stay_speed": self.a.stay_speed, "plan": self.a.plan,
                                                    "chat": self.a.chat, "steps": self.results, "usage": self.usage,
                                                    "finished_at": datetime.now(KST).isoformat(timespec="seconds")},
                                                   ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"  기록: {self.a.log}")
        if total_ok < total:
            sys.exit(2)


def _back_along(line: list[tuple[float, float]], meters: float) -> tuple[float, float]:
    """경로선 끝에서 meters 만큼 거슬러 올라간 점."""
    from scene import _dist  # 같은 거리 계산
    left = meters
    pts = list(reversed(line))
    for a, b in zip(pts, pts[1:]):
        d = _dist(a, b)
        if d >= left:
            f = left / d
            return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
        left -= d
    return pts[-1]


def ask(prompt: str) -> str:
    print(prompt, end="", flush=True)
    if msvcrt and sys.stdin.isatty():
        ch = msvcrt.getwch()
        print()
        return "" if ch in ("\r", "\n", " ") else ch.lower()
    try:
        return input().strip().lower()
    except EOFError:
        return "q"


def print_steps(steps: list[Step], done: list[str]) -> None:
    for s in steps:
        mark = "v" if s.id in done else " "
        print(f"  [{mark}] {s.id:<10} [{s.scene}] {s.title}")


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    if os.name == "nt":
        os.system("")  # Windows 콘솔 ANSI 색
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="\n".join(__doc__.split("\n")[2:]))
    ap.add_argument("--url", default=os.environ.get("GURYONG_API_URL", "http://127.0.0.1:8000"), help="게이트웨이 주소")
    ap.add_argument("--login", default=os.environ.get("GURYONG_DEMO_LOGIN", "seoyun-demo"), help="데모 사용자 아이디 (앱 로그인과 같게)")
    ap.add_argument("--password", default=os.environ.get("GURYONG_DEMO_PASSWORD", "guryong2026"),
                    help="데모 사용자 비밀번호 (없으면 이 값으로 가입)")
    ap.add_argument("--name", default="서윤", help="가입할 때 이름")
    ap.add_argument("--admin-token", default=os.environ.get("GURYONG_ADMIN_TOKEN"),
                    help="공개 서버의 관리 토큰 (가상 상황·즉시 안전 점검·trip 틱·가입). 기본: 환경변수 GURYONG_ADMIN_TOKEN. "
                         "docs/deploy/README.md §3")
    ap.add_argument("--plan", choices=["chat", "prebuilt"], default="chat",
                    help="장면 ①: chat 대화로 일정 짜기 / prebuilt 미리 만든 계획 (기본 chat)")
    ap.add_argument("--answer", default=ANSWER, help="장면 ① 에서 Agent 가 조건을 되물을 때 보낼 답")
    ap.add_argument("--chat", choices=["script", "app"], default="script",
                    help="대화·승인을 script 스크립트가 보냄 / app 발표자가 앱에서 하고 스크립트는 기다림 (기본 script)")
    ap.add_argument("--speed", type=float, default=20, help="이동할 때 가상 시각 배속 (기본 20, 0 이면 기다리지 않음)")
    ap.add_argument("--stay-speed", type=float, default=120, help="한 자리에 머무는 동안 배속 (기본 120)")
    ap.add_argument("--auto", action="store_true", help="멈추지 않고 끝까지 (리허설·백업 영상)")
    ap.add_argument("--pause", type=float, default=3, help="--auto 일 때 단계 사이 대기 초 (기본 3)")
    ap.add_argument("--from", dest="from_step", help="이 단계부터 (진행 상태 파일에서 계획·위치·시각을 읽음)")
    ap.add_argument("--fast-to", help="처음(또는 --from)부터 이 단계 앞까지 기다리지 않고 진행한 뒤, 이 단계부터 평소대로 "
                                      "(LLM 을 각본으로 바꾼 뒤 그 장면을 다시 할 때)")
    ap.add_argument("--state", help=f"진행 상태 파일 (기본 {STATE_DIR / 'demo_state.json'})")
    ap.add_argument("--log", help="결과(단계별 소요 시간·기대)를 JSON 으로 저장")
    ap.add_argument("--list", action="store_true", help="단계 목록만 보여줌")
    a = ap.parse_args()
    demo = Demo(a)
    if a.list:
        demo.load()
        print_steps(demo.active_steps(), demo.p.done)
        return
    demo.preflight()
    try:
        demo.run()
    except KeyboardInterrupt:
        print(f"\n{YELLOW}중단했습니다. 이어서: --from <단계 id> (진행 상태: {demo.state_file}){RESET}")
        demo.save()
        demo.report()


if __name__ == "__main__":
    main()
