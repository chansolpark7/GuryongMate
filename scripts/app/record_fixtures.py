"""앱 fixture 녹화 — 각본 LLM 으로 띄운 스택에 데모 흐름(F1~F11 의 P0)을 실제로 요청하고 응답을 파일로 남긴다.
형식과 앱에서 재생하는 방법: docs/app/fixtures.md

    # 스택: backend/.env 에 AGENT_PROVIDER=scripted, SAFETY_SOURCE=mock (AUTH_REQUIRED 는 상관없음)
    python scripts/app/record_fixtures.py --url http://127.0.0.1:8017            # 전부 -> app/test/fixtures/
    python scripts/app/record_fixtures.py --url http://127.0.0.1:8017 --only f1,f2
    python scripts/app/record_fixtures.py --url http://127.0.0.1:8017 --no-docker   # 503 예시(서비스 잠깐 멈춤) 빼고

- HTTP 응답(JSON)과 SSE 스트림(이벤트 순서·요청 시작부터의 ms)을 흐름마다 한 파일에 담는다.
- 여행 중 이벤트(도착·출발·지연·안전·위험 구역)는 scripts/trip/scenes 재생 파일을 실제 trip 에 보내 위치 응답·폴링을
  시간순으로 담은 재생 파일(trip/*.json)로 만든다.
- 토큰 값은 남기지 않는다 (<access_token>·<refresh_token>). id·시각은 녹화마다 달라진다 (앱은 fixture 안의 값을 읽어 쓴다).
- 백엔드가 바뀌면 다시 돌려 갱신한다 (docs/COLLABORATION.md). 실제 LLM 은 부르지 않는다 (각본 LLM 이 아니면 멈춘다).
"""
import argparse
import json
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "trip"))
from scene import expand, matches  # noqa: E402  재생 파일 펼침 (trip replay·pytest 와 같은 코드)

KST = timezone(timedelta(hours=9))
SCENES = ROOT / "scripts" / "trip" / "scenes"
OUT = ROOT / "app" / "test" / "fixtures"
LOGIN_ID, PASSWORD, NAME = "seoyun-demo", "guryong2026", "서윤"
PLAN_NOW = "2026-10-09T10:00:00+09:00"  # 계획 모드 흐름의 가상 시각 (여행 전날)
DEMO_PLAN = {"title": "엄마랑 구룡포", "start_date": "2026-10-10", "end_date": "2026-10-10", "day_start": "12:30",
             "day_end": "18:00", "travel_mode": "walk"}
TOKEN_KEYS = {"access_token": "<access_token>", "refresh_token": "<refresh_token>"}


class Failed(RuntimeError):
    pass


def _redact(obj):
    if isinstance(obj, dict):
        return {k: (TOKEN_KEYS[k] if k in TOKEN_KEYS and isinstance(v, str) else _redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


class Recorder:
    def __init__(self, base_url: str, out: Path):
        self.http = httpx.Client(base_url=base_url, timeout=180)
        self.out = out
        self.token: str | None = None
        self.refresh: str | None = None
        self.flow: dict | None = None
        self.files: list[dict] = []
        self.failures: list[str] = []

    # ----- 기록 -----
    def begin(self, file: str, flow: str, title: str, screens: str = "") -> None:
        self.flow = {"file": file, "flow": flow, "title": title, "screens": screens, "exchanges": []}

    def end(self) -> None:
        f = self.flow
        doc = {"flow": f["flow"], "title": f["title"], "screens": f["screens"], "recorded_at": _now_iso(),
               "backend": self.backend, "exchanges": f["exchanges"]}
        _write(self.out / f["file"], doc)
        self.files.append({"file": f["file"], "flow": f["flow"], "title": f["title"], "screens": f["screens"],
                           "exchanges": [e["name"] for e in f["exchanges"]]})
        print(f"  -> {f['file']} ({len(f['exchanges'])}개)")
        self.flow = None

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def call(self, name: str | None, method: str, route: str, *, body=None, params=None, expect=(200,), **ids):
        """route: '/agent/sessions/{session_id}/chat' 처럼 자리표시자. name 이 None 이면 기록하지 않는다 (준비 동작)."""
        path = route.format(**ids)
        started = time.monotonic()
        r = self.http.request(method, path, json=body, params=params, headers=self._headers())
        elapsed = round((time.monotonic() - started) * 1000)
        data = r.json() if r.content and r.headers.get("content-type", "").startswith("application/json") else None
        if r.status_code not in expect:
            raise Failed(f"{method} {path} -> {r.status_code} (기대 {expect}): {r.text[:300]}")
        if name and self.flow is not None:
            self.flow["exchanges"].append({"name": name, "method": method, "route": route, "path": path,
                                           "query": params or {}, "body": _redact(body), "status": r.status_code,
                                           "elapsed_ms": elapsed, "response": _redact(data)})
        return data

    def sse(self, name: str | None, route: str, body=None, *, on_first=None, **ids) -> list[dict]:
        """SSE 를 끝까지 받는다. 이벤트마다 요청 시작부터의 ms (앱이 같은 간격으로 재생할 수 있게)."""
        path = route.format(**ids)
        events, started = [], time.monotonic()
        with self.http.stream("POST", path, json=body, headers=self._headers()) as r:
            if r.status_code != 200:
                raise Failed(f"POST {path} -> {r.status_code}: {r.read()[:300]!r}")
            event = None
            for line in r.iter_lines():
                if line.startswith("event: "):
                    event = line[7:]
                elif line.startswith("data: "):
                    events.append({"t_ms": round((time.monotonic() - started) * 1000), "event": event,
                                   "data": json.loads(line[6:])})
                    if on_first and len(events) == 1:
                        on_first()
        if name and self.flow is not None:
            self.flow["exchanges"].append({"name": name, "method": "POST", "route": route, "path": path, "query": {},
                                           "body": body, "status": 200, "elapsed_ms": events[-1]["t_ms"] if events else 0,
                                           "content_type": "text/event-stream", "sse": events})
        return events

    def check(self, ok: bool, what: str) -> None:
        print(f"    {'통과' if ok else '실패'}: {what}")
        if not ok:
            self.failures.append(f"{self.flow['flow'] if self.flow else '-'}: {what}")

    # ----- 준비 -----
    def preflight(self) -> None:
        info = self.call(None, "GET", "/agent/info")
        if not info["provider"].startswith("scripted"):
            raise Failed(f"각본 LLM 이 아닙니다 ({info['provider']}). backend/.env 에 AGENT_PROVIDER=scripted 로 띄우세요 "
                         "(실제 LLM 을 부르지 않기 위해 멈춥니다).")
        r = self.http.get("/safety/sim")
        if r.status_code != 200:
            raise Failed("safety 가 mock 이 아닙니다. backend/.env 에 SAFETY_SOURCE=mock 으로 띄우세요.")
        sha = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
        self.backend = {"agent_provider": info["provider"], "safety_source": "mock", "git": sha}

    def login(self) -> None:
        """데모 사용자를 새로 만든다 (있으면 탈퇴 후 다시 — 메모리 등이 녹화마다 같게)."""
        r = self.http.post("/auth/login", json={"login_id": LOGIN_ID, "password": PASSWORD})
        if r.status_code == 200:
            self.token = r.json()["access_token"]
            self.call(None, "DELETE", "/users/me", expect=(204,))
            self.token = None
        tokens = self.call(None, "POST", "/auth/signup", body={"login_id": LOGIN_ID, "password": PASSWORD,
                                                               "display_name": NAME}, expect=(201,))
        self.token, self.refresh = tokens["access_token"], tokens["refresh_token"]

    def sim(self, scenario: str | None = None, now: str | None = None) -> dict:
        body = {k: v for k, v in {"scenario": scenario, "now": now}.items() if v}
        return self.call(None, "PUT", "/safety/sim", body=body)

    def wait_clock(self, plan_id: int, target: datetime, limit: float = 12) -> None:
        """trip·planner 는 가상 시각을 몇 초 기억한다 -> trip 의 지금이 재생 시각을 따라올 때까지."""
        end = time.monotonic() + limit
        while time.monotonic() < end:
            now = datetime.fromisoformat(self.call(None, "GET", "/trip/plans/{plan_id}/state", plan_id=plan_id)["now"])
            if abs((now - target).total_seconds()) < 60:
                return
            time.sleep(1)

    def create_plan(self, spec: dict, name: str | None = None) -> dict:
        p = self.call(name, "POST", "/planner/plans", body={k: spec[k] for k in DEMO_PLAN if k in spec}, expect=(201,))
        for it in spec.get("items", []):
            body = {"place_id": it["place_id"], "day": it.get("day", 1), "stay_minutes": it.get("stay_minutes", 60)}
            if it.get("travel_mode"):
                body["travel_mode"] = it["travel_mode"]
            self.call(None, "POST", "/planner/plans/{plan_id}/items", body=body, expect=(201,), plan_id=p["id"])
        for c in spec.get("constraints", []):
            self.call(None, "POST", "/planner/plans/{plan_id}/constraints", body={**c, "source": "user"}, expect=(201,),
                      plan_id=p["id"])
        return self.call(None, "GET", "/planner/plans/{plan_id}", plan_id=p["id"])

    def wait_level3(self, plan_id: int, limit: float = 120) -> list[dict]:
        """선제 턴(단계 3 queued)이 끝날 때까지."""
        end = time.monotonic() + limit
        while True:
            events = self.call(None, "GET", "/trip/plans/{plan_id}/events", params={"after": 0, "limit": 200}, plan_id=plan_id)
            if not any(e["status"] == "queued" and e["level"] == 3 for e in events) or time.monotonic() > end:
                return events
            time.sleep(1)


def _now_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def _write(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")


def _scene(name: str) -> dict:
    return json.loads((SCENES / f"{name}.json").read_text(encoding="utf-8"))


def _done(events: list[dict]) -> dict:
    return next(e["data"] for e in events if e["event"] == "done")


def _card(events: list[dict], kind: str) -> dict | None:
    return next((e["data"] for e in reversed(events) if e["event"] == kind), None)


# ---------- 흐름 ----------

def f11_login(rec: Recorder) -> None:
    rec.begin("f11_login.json", "F11", "로그인 (아이디)", "01·34·35")
    token, rec.token = rec.token, None
    rec.call("signup_taken", "POST", "/auth/signup", body={"login_id": LOGIN_ID, "password": PASSWORD}, expect=(409,))
    rec.call("signup_weak_password", "POST", "/auth/signup", body={"login_id": "seoyun-new", "password": "short"},
             expect=(422,))
    rec.call("login_wrong_password", "POST", "/auth/login", body={"login_id": LOGIN_ID, "password": "wrong-pass1"},
             expect=(401,))
    tokens = rec.call("login", "POST", "/auth/login", body={"login_id": LOGIN_ID, "password": PASSWORD})
    rec.token = tokens["access_token"]
    rec.call("me", "GET", "/users/me")
    new = rec.call("refresh", "POST", "/auth/refresh", body={"refresh_token": tokens["refresh_token"]})
    rec.call("refresh_reused", "POST", "/auth/refresh", body={"refresh_token": tokens["refresh_token"]}, expect=(401,))
    rec.token = token  # 재사용 감지로 위 로그인들은 끊겼다. 녹화는 처음 가입 토큰(다른 세션)으로 계속
    del new
    rec.end()


def f1_plan(rec: Recorder) -> dict:
    """F1 AI 로 계획 만들기 (장면 ①) + 06B 장소 추천."""
    rec.begin("f1_plan_with_ai.json", "F1", "AI 로 계획 만들기 + 장소 추천 (장면 ①)", "03·05·06·06B·08")
    rec.sim("calm", PLAN_NOW)
    plan = rec.call("create_plan", "POST", "/planner/plans", body=DEMO_PLAN, expect=(201,))
    pid = plan["id"]
    rec.call("list_sessions_empty", "GET", "/agent/sessions", params={"plan_id": pid})
    sid = rec.call("create_session", "POST", "/agent/sessions", body={"plan_id": pid}, expect=(201,))["id"]
    events = rec.sse("chat_make_plan", "/agent/sessions/{session_id}/chat", {"message": "엄마랑 구룡포 일정 짜줘"},
                     session_id=sid)
    prop = _card(events, "proposal")
    rec.check(bool(prop) and prop["status"] == "pending", "일정 초안 -> 제안 카드")
    rec.check(any(e["event"] == "step" and e["data"]["kind"] == "review" and e["data"]["detail"] != "문제 없음"
                  for e in events), "검토 관문이 문제를 찾아 다시 고침")
    rec.call("session_detail", "GET", "/agent/sessions/{session_id}", session_id=sid)
    rec.call("proposal_detail", "GET", "/planner/plans/{plan_id}/proposals/{proposal_id}", plan_id=pid, proposal_id=prop["id"])
    rec.call("accept", "POST", "/agent/sessions/{session_id}/proposals/{proposal_id}/accept", body={"mode": "auto"},
             session_id=sid, proposal_id=prop["id"])
    rec.call("plan_after_accept", "GET", "/planner/plans/{plan_id}", plan_id=pid)
    rec.call("plan_safety", "GET", "/planner/plans/{plan_id}/safety", plan_id=pid)
    events = rec.sse("chat_recommend_cafe", "/agent/sessions/{session_id}/chat", {"message": "갈 만한 카페 추천해줘"},
                     session_id=sid)
    card = _card(events, "recommendation")
    rec.check(bool(card), "추천 카드")
    cand = next(c for c in card["candidates"] if c["state"] == "not_in_plan")
    rec.call("candidate_add", "POST", "/agent/sessions/{session_id}/recommendations/{rec_id}/candidates/{cand_id}/add",
             session_id=sid, rec_id=card["id"], cand_id=cand["id"])
    rec.call("candidate_add_again", "POST", "/agent/sessions/{session_id}/recommendations/{rec_id}/candidates/{cand_id}/add",
             expect=(409,), session_id=sid, rec_id=card["id"], cand_id=cand["id"])
    rec.call("candidate_remove", "POST",
             "/agent/sessions/{session_id}/recommendations/{rec_id}/candidates/{cand_id}/remove",
             session_id=sid, rec_id=card["id"], cand_id=cand["id"])
    rec.call("session_detail_end", "GET", "/agent/sessions/{session_id}", session_id=sid)
    rec.end()
    return {"plan_id": pid, "session_id": sid}


def f2_actions(rec: Recorder, ctx: dict) -> None:
    """F2 승인·거절·base_changed·다시 제안 + 409 agent_busy·proposal_not_pending."""
    rec.begin("f2_proposal_actions.json", "F2", "제안 승인·거절·base_changed·다시 제안", "06·19·20·22·36")
    pid, sid = ctx["plan_id"], ctx["session_id"]
    events = rec.sse("chat_longer_lunch", "/agent/sessions/{session_id}/chat", {"message": "구룡포항 점심은 1시간으로 늘려줘"},
                     session_id=sid)
    prop = _card(events, "proposal")
    plan = rec.call(None, "GET", "/planner/plans/{plan_id}", plan_id=pid)
    first = plan["days"][0]["items"][0]
    rec.call("edit_plan_directly", "PATCH", "/planner/plans/{plan_id}/items/{item_id}", body={"stay_minutes": 70},
             plan_id=pid, item_id=first["id"])
    rec.call("accept_base_changed", "POST", "/agent/sessions/{session_id}/proposals/{proposal_id}/accept",
             body={"mode": "auto"}, expect=(409,), session_id=sid, proposal_id=prop["id"])
    events = rec.sse("repropose", "/agent/sessions/{session_id}/proposals/{proposal_id}/repropose", None,
                     session_id=sid, proposal_id=prop["id"])
    new = _card(events, "proposal")
    rec.check(bool(new) and new["id"] != prop["id"], "다시 제안 -> 새 카드")
    rec.call("old_proposal_superseded", "GET", "/planner/plans/{plan_id}/proposals/{proposal_id}", plan_id=pid,
             proposal_id=prop["id"])
    rec.call("reject", "POST", "/agent/sessions/{session_id}/proposals/{proposal_id}/reject", session_id=sid,
             proposal_id=new["id"])
    rec.call("reject_again", "POST", "/agent/sessions/{session_id}/proposals/{proposal_id}/reject", expect=(409,),
             session_id=sid, proposal_id=new["id"])
    # 같은 요청을 다시 하고, 그사이 일정을 바꾼 뒤 '카드에서 본 일정 그대로 반영'(overwrite)
    events = rec.sse("chat_longer_lunch_again", "/agent/sessions/{session_id}/chat", {"message": "점심 1시간으로 늘려줘"},
                     session_id=sid)
    again = _card(events, "proposal")
    rec.call(None, "PATCH", "/planner/plans/{plan_id}/items/{item_id}", body={"stay_minutes": 60}, plan_id=pid,
             item_id=first["id"])
    rec.call("accept_base_changed_2", "POST", "/agent/sessions/{session_id}/proposals/{proposal_id}/accept",
             body={"mode": "auto"}, expect=(409,), session_id=sid, proposal_id=again["id"])
    rec.call("accept_overwrite", "POST", "/agent/sessions/{session_id}/proposals/{proposal_id}/accept",
             body={"mode": "overwrite"}, session_id=sid, proposal_id=again["id"])
    # 응답 중에 다시 보내면 409 agent_busy
    busy: dict = {}

    def second():
        r = rec.http.post(f"/agent/sessions/{sid}/chat", json={"message": "갈 만한 카페 추천해줘"}, headers=rec._headers())
        busy["status"], busy["body"] = r.status_code, r.json()

    rec.sse("chat_while_busy_first", "/agent/sessions/{session_id}/chat", {"message": "갈 만한 카페 추천해줘"},
            on_first=second, session_id=sid)
    rec.check(busy.get("status") == 409, "응답 중 두 번째 요청 409 agent_busy")
    rec.flow["exchanges"].append({"name": "chat_agent_busy", "method": "POST", "route": "/agent/sessions/{session_id}/chat",
                                  "path": f"/agent/sessions/{sid}/chat", "query": {},
                                  "body": {"message": "갈 만한 카페 추천해줘"}, "status": busy.get("status"),
                                  "elapsed_ms": None, "response": busy.get("body")})
    rec.end()


def f10_guide(rec: Recorder, plan_id: int) -> None:
    rec.begin("f10_guide_prep.json", "F10", "가이드 준비 (여행 전)", "32·17")
    rec.call("notes_before", "GET", "/docent/plans/{plan_id}/notes", plan_id=plan_id)
    job = rec.call("prepare", "POST", "/agent/plans/{plan_id}/guide/prepare", body={}, expect=(202,), plan_id=plan_id)
    n = 0
    while job["status"] == "running":
        time.sleep(1.5)
        n += 1
        job = rec.call(f"job_poll_{n}", "GET", "/agent/plans/{plan_id}/guide/jobs/{job_id}", plan_id=plan_id,
                       job_id=job["job_id"])
    rec.check(job["ready"] == job["total"] > 0, f"노트 {job['ready']}/{job['total']} 준비")
    rec.call("notes_after", "GET", "/docent/plans/{plan_id}/notes", plan_id=plan_id)
    rec.call("note_house_street", "GET", "/docent/plans/{plan_id}/notes/{place_id}", plan_id=plan_id, place_id="kto-1909659")
    rec.call("note_breakwater", "GET", "/docent/plans/{plan_id}/notes/{place_id}", plan_id=plan_id,
             place_id="poi-gr-breakwater-north")
    rec.call("prepare_again_nothing_to_do", "POST", "/agent/plans/{plan_id}/guide/prepare", body={}, expect=(202,),
             plan_id=plan_id)
    rec.call("job_not_found", "GET", "/agent/plans/{plan_id}/guide/jobs/{job_id}", expect=(404,), plan_id=plan_id,
             job_id="0" * 32)
    rec.end()


def f3_trip_start(rec: Recorder) -> None:
    rec.begin("f3_trip_start.json", "F3", "여행 시작 (정상·다른 날·실수)", "11·12·14·30")
    spec = _scene("scene2_arrival")["plan"]
    rec.sim("calm", "2026-10-10T12:20:00+09:00")
    plan = rec.create_plan(spec)
    other = rec.create_plan({**spec, "title": "다음 주 구룡포", "start_date": "2026-10-17", "end_date": "2026-10-17"})
    sid = rec.call(None, "POST", "/agent/sessions", body={"plan_id": plan["id"]}, expect=(201,))["id"]
    rec.wait_clock(plan["id"], datetime.fromisoformat("2026-10-10T12:20:00+09:00"))
    rec.call("state_before", "GET", "/trip/plans/{plan_id}/state", plan_id=plan["id"])
    rec.call("start_not_trip_day", "POST", "/trip/plans/{plan_id}/start", body={}, expect=(409,), plan_id=other["id"])
    rec.call("start", "POST", "/trip/plans/{plan_id}/start", body={"session_id": sid}, plan_id=plan["id"])
    rec.call("start_again_same_day", "POST", "/trip/plans/{plan_id}/start", body={"session_id": sid}, plan_id=plan["id"])
    rec.call("events_day_started", "GET", "/trip/plans/{plan_id}/events", params={"after": 0, "limit": 50},
             plan_id=plan["id"])
    rec.call("end_by_mistake", "POST", "/trip/plans/{plan_id}/end", plan_id=plan["id"])
    rec.call("state_after_end", "GET", "/trip/plans/{plan_id}/state", plan_id=plan["id"])
    rec.end()


def replay_scene(rec: Recorder, name: str, hooks: dict | None = None) -> dict:
    """재생 파일을 실제 trip 에 보내고 trip/<name>.json 에 시간순으로 남긴다. hooks: {"after_level3": fn(ctx, event)}.
    반환: {"plan_id", "session_id", "events"}."""
    hooks = hooks or {}
    data = _scene(name)
    start = datetime.fromisoformat(data["start"])
    rec.sim(data.get("scenario", "calm"), start.isoformat())
    plan = rec.create_plan(data["plan"])
    pid = plan["id"]
    sid = rec.call(None, "POST", "/agent/sessions", body={"plan_id": pid}, expect=(201,))["id"]
    ctx = {"plan_id": pid, "session_id": sid}
    if hooks.get("before_start"):
        hooks["before_start"](ctx)
    places: dict[str, tuple[float, float]] = {}

    def coords(place_id: str):
        if place_id not in places:
            p = rec.call(None, "GET", "/places/{place_id}", place_id=place_id)
            places[place_id] = (p["lat"], p["lng"])
        return places[place_id]

    def path(src, dst, mode="walk"):
        r = rec.call(None, "POST", "/routing/route", body={"mode": mode, "stops": [
            {"lat": src[0], "lng": src[1]}, {"lat": dst[0], "lng": dst[1]}]})
        return [(lat, lng) for lng, lat in r["geometry"]["coordinates"]]

    timeline: list[dict] = []
    seen_level3: set[int] = set()
    started = False
    print(f"  재생 {name}")
    for act in expand(data, coords, path):
        rec.sim(act.data.get("scenario") if act.kind == "sim" else None, act.at.isoformat())
        at = act.at.isoformat()
        if act.kind == "sim":
            timeline.append({"at": at, "kind": "sim", "scenario": act.data.get("scenario")})
            if not started:
                rec.wait_clock(pid, act.at)
                state = rec.call(None, "POST", "/trip/plans/{plan_id}/start", body={"session_id": sid}, plan_id=pid)
                timeline.append({"at": at, "kind": "http", "method": "POST", "route": "/trip/plans/{plan_id}/start",
                                 "body": {"session_id": sid}, "status": 200, "response": state})
                started = True
            else:
                _tick(rec, pid, act.at, timeline)
        elif act.kind == "loc":
            point = {"lat": act.lat, "lng": act.lng, "accuracy": 10, "recorded_at": at, "source": "replay"}
            r = rec.call(None, "POST", "/trip/plans/{plan_id}/location", body=point, plan_id=pid)
            timeline.append({"at": at, "kind": "location", "body": point, "response": r})
        elif act.kind == "tick":
            _tick(rec, pid, act.at, timeline)
        elif act.kind == "skip":
            rec.wait_level3(pid)
            item = next(i for d in rec.call(None, "GET", "/planner/plans/{plan_id}", plan_id=pid)["days"]
                        for i in d["items"] if i["place_id"] == act.data["place_id"] and i["status"] == "planned")
            r = rec.call(None, "PATCH", "/planner/plans/{plan_id}/items/{item_id}", body={"status": "skipped"},
                         plan_id=pid, item_id=item["id"])
            timeline.append({"at": at, "kind": "http", "method": "PATCH", "route": "/planner/plans/{plan_id}/items/{item_id}",
                             "body": {"status": "skipped"}, "status": 200, "response": r})
        elif act.kind == "accept":
            events = rec.wait_level3(pid)
            ev = next(e for e in reversed(events) if e["level"] == 3 and (e.get("agent") or {}).get("proposal_id"))
            body = {"mode": "auto"}
            r = rec.call("accept_proactive" if rec.flow else None, "POST",
                         "/agent/sessions/{session_id}/proposals/{proposal_id}/accept", body=body,
                         session_id=ev["agent"]["session_id"], proposal_id=ev["agent"]["proposal_id"])
            timeline.append({"at": at, "kind": "http", "method": "POST",
                             "route": "/agent/sessions/{session_id}/proposals/{proposal_id}/accept",
                             "path": f"/agent/sessions/{ev['agent']['session_id']}/proposals/{ev['agent']['proposal_id']}/accept",
                             "body": body, "status": 200, "response": r})
            places_now = {i["place_id"] for d in r["plan"]["days"] for i in d["items"] if i["status"] != "skipped"}
            rec.check(act.data.get("remove") not in places_now and act.data.get("add") in places_now,
                      f"선제 제안 승인: {act.data}")
        elif act.kind == "expect":
            events = rec.wait_level3(pid)
            timeline.append({"at": at, "kind": "poll", "route": "/trip/plans/{plan_id}/events",
                             "query": {"after": 0, "limit": 200}, "response": events})
            rec.check(matches(act.data, events), f"{name} {act.at:%H:%M} 기대 {act.data}")
            for e in events:
                if e["level"] == 3 and e["status"] != "queued" and e["id"] not in seen_level3:
                    seen_level3.add(e["id"])
                    detail = rec.call(None, "GET", "/agent/sessions/{session_id}", session_id=e["agent"]["session_id"]) \
                        if e.get("agent") else None
                    timeline.append({"at": at, "kind": "http", "method": "GET", "route": "/agent/sessions/{session_id}",
                                     "status": 200, "response": detail, "for_event": e["id"]})
                    if hooks.get("after_level3"):
                        hooks["after_level3"](ctx, e)
    events = rec.call(None, "GET", "/trip/plans/{plan_id}/events", params={"after": 0, "limit": 200}, plan_id=pid)
    state = rec.call(None, "GET", "/trip/plans/{plan_id}/state", plan_id=pid)
    _write(rec.out / "trip" / f"{name}.json", {
        "scene": name, "title": data["name"], "description": data.get("description", ""), "recorded_at": _now_iso(),
        "backend": rec.backend, "plan_id": pid, "session_id": sid, "plan": plan, "timeline": timeline,
        "final": {"events": events, "state": state}})
    print(f"  -> trip/{name}.json (위치 {sum(1 for t in timeline if t['kind'] == 'location')}점, "
          f"이벤트 {len(events)}개: {', '.join(e['type'] for e in events)})")
    rec.files.append({"file": f"trip/{name}.json", "flow": "trip", "title": data["name"], "screens": "",
                      "exchanges": []})
    return {**ctx, "events": events}


def _tick(rec: Recorder, pid: int, at: datetime, timeline: list[dict]) -> None:
    rec.wait_clock(pid, at)
    try:
        rec.call(None, "POST", "/planner/safety/scan")  # 개발 모드에서 planner 안전 점검을 바로
    except Failed:
        pass
    r = rec.call(None, "POST", "/trip/plans/{plan_id}/tick", params={"now": at.isoformat()}, plan_id=pid)
    timeline.append({"at": at.isoformat(), "kind": "tick", "response": r})


def f4_arrival(rec: Recorder) -> None:
    rec.begin("f4_arrival_docent.json", "F4", "도착 -> 해설 카드 -> 가이드 노트 -> 추가 질문 -> 더 짧게 (장면 ②)", "14·16·17·18")
    ctx = replay_scene(rec, "scene2_arrival", {"before_start": lambda c: f10_guide_inline(rec, c["plan_id"])})
    pid, sid = ctx["plan_id"], ctx["session_id"]
    arrived = next(e for e in ctx["events"] if e["type"] == "arrived")
    rec.call("note", "GET", "/docent/plans/{plan_id}/notes/{place_id}", plan_id=pid, place_id=arrived["card"]["place_id"])
    rec.call("memory_before", "GET", "/docent/memory", params={"plan_id": pid})
    rec.call("event_seen", "POST", "/trip/plans/{plan_id}/events/{event_id}/seen", plan_id=pid, event_id=arrived["id"])
    rec.sse("chat_question", "/agent/sessions/{session_id}/chat", {"message": "언제 지어진 집들이에요?"}, session_id=sid)
    events = rec.sse("chat_shorter", "/agent/sessions/{session_id}/chat", {"message": "다음부터는 더 짧게 해줘요"},
                     session_id=sid)
    rec.check(any(e["event"] == "step" and e["data"]["kind"] == "memory" and e["data"]["status"] == "done" for e in events),
              "기억하기 단계 (메모리 칩)")
    mem = rec.call("memory_after", "GET", "/docent/memory", params={"plan_id": pid})
    rec.check(mem.get("style_length") == "short", "style_length short")
    rec.call("session_detail", "GET", "/agent/sessions/{session_id}", session_id=sid)
    rec.call(None, "POST", "/trip/plans/{plan_id}/end", plan_id=pid)
    rec.end()


def f10_guide_inline(rec: Recorder, plan_id: int) -> None:
    """장면 ② 계획의 노트를 미리 준비한다 (기록하지 않음. 가이드 준비 흐름은 f10)."""
    job = rec.call(None, "POST", "/agent/plans/{plan_id}/guide/prepare", body={}, expect=(202,), plan_id=plan_id)
    while job["status"] == "running":
        time.sleep(1)
        job = rec.call(None, "GET", "/agent/plans/{plan_id}/guide/jobs/{job_id}", plan_id=plan_id, job_id=job["job_id"])


def f5_delay(rec: Recorder) -> None:
    rec.begin("f5_delay.json", "F5", "지연 -> 선제 메시지 -> 제약 반영 -> 승인 (장면 ③)", "14-S3·19·20·21-S1")
    ctx = replay_scene(rec, "scene3_delay")
    pid, sid = ctx["plan_id"], ctx["session_id"]
    delay = next(e for e in ctx["events"] if e["type"] == "delay")
    rec.call("delay_event", "GET", "/trip/plans/{plan_id}/events", params={"after": delay["id"] - 1, "limit": 1},
             plan_id=pid)
    rec.call("session_with_proactive", "GET", "/agent/sessions/{session_id}", session_id=sid)
    old = delay["agent"]["proposal_id"]
    events = rec.sse("chat_keep_museum", "/agent/sessions/{session_id}/chat", {"message": "과메기문화관은 유지해줘"},
                     session_id=sid)
    new = _card(events, "proposal")
    rec.check(bool(new) and new["id"] != old, "조건 반영 -> 새 제안")
    old_now = rec.call("old_proposal_superseded", "GET", "/planner/plans/{plan_id}/proposals/{proposal_id}",
                       plan_id=pid, proposal_id=old)
    rec.check(old_now["status"] == "superseded", "이전 제안 superseded")
    rec.call("accept_old_not_pending", "POST", "/agent/sessions/{session_id}/proposals/{proposal_id}/accept",
             body={"mode": "auto"}, expect=(409,), session_id=sid, proposal_id=old)
    rec.call("accept_new", "POST", "/agent/sessions/{session_id}/proposals/{proposal_id}/accept", body={"mode": "auto"},
             session_id=sid, proposal_id=new["id"])
    rec.call("state_after", "GET", "/trip/plans/{plan_id}/state", plan_id=pid)
    rec.call(None, "POST", "/trip/plans/{plan_id}/end", plan_id=pid)
    rec.end()


def f6_f7_safety(rec: Recorder) -> None:
    rec.begin("f6_safety.json", "F6·F7", "안전 상승 -> 선제 메시지 -> 비교 -> 승인 (장면 ④) + 위험 구역 접근 경고", "22·21·23·28")
    holder: dict = {}

    def after_level3(ctx, ev):
        if ev["type"] != "safety_raised" or "done" in holder:
            return
        holder["done"] = True
        pid = ctx["plan_id"]
        rec.call("safety_raised_event", "GET", "/trip/plans/{plan_id}/events", params={"after": ev["id"] - 1, "limit": 1},
                 plan_id=pid)
        rec.call("session_with_proactive", "GET", "/agent/sessions/{session_id}", session_id=ev["agent"]["session_id"])
        prop = rec.call("proposal", "GET", "/planner/plans/{plan_id}/proposals/{proposal_id}", plan_id=pid,
                        proposal_id=ev["agent"]["proposal_id"])
        rec.call("compare_before_safety", "GET", "/planner/plans/{plan_id}/safety", plan_id=pid)
        after = rec.call("compare_after_safety", "POST", "/planner/plans/{plan_id}/safety/preview",
                         body={"ops": prop["ops"]}, plan_id=pid)
        rec.check(not any(i["risk"] == "danger" and i["kind"] == "visit" for i in after["items"]), "교체 후 위험 방문 없음")
        rec.call("safety_alerts", "GET", "/planner/plans/{plan_id}/safety/alerts", params={"status": "new"}, plan_id=pid)

    ctx = replay_scene(rec, "scene4_safety", {"after_level3": after_level3})
    pid = ctx["plan_id"]
    hazard = next(e for e in ctx["events"] if e["type"] == "near_hazard")
    rec.call("avoid_zones", "GET", "/safety/avoid", params={"min_risk": "caution"})
    plan = rec.call(None, "GET", "/planner/plans/{plan_id}", plan_id=pid)
    cafe = next(i for i in plan["days"][0]["items"] if i["place_id"] == "kto-2834344")
    loc = next(t for t in reversed(_scene_timeline(rec, "scene4_safety")) if t["kind"] == "location"
               and any(e["id"] == hazard["id"] for e in t["response"]["events"]))
    rec.call("detour_route", "POST", "/routing/route", body={
        "mode": "car", "stops": [{"lat": loc["body"]["lat"], "lng": loc["body"]["lng"]},
                                 {"lat": cafe["place"]["lat"], "lng": cafe["place"]["lng"]}],
        "hazards": "avoid", "steps": True})
    rec.call(None, "POST", "/trip/plans/{plan_id}/end", plan_id=pid)
    rec.end()


def _scene_timeline(rec: Recorder, name: str) -> list[dict]:
    return json.loads((rec.out / "trip" / f"{name}.json").read_text(encoding="utf-8"))["timeline"]


def errors(rec: Recorder, docker: bool) -> None:
    rec.begin("errors.json", "공통", "오류 예시 (LLM 오류 SSE, 503 서비스 장애, 404·422)", "05·States_Feedback")
    rec.sim("calm", PLAN_NOW)
    pid = rec.create_plan(_scene("scene2_arrival")["plan"])["id"]  # 데모 계획 (구룡포항 점심 50분)
    sid = rec.call(None, "POST", "/agent/sessions", body={"plan_id": pid}, expect=(201,))["id"]
    rec.sse("chat_llm_unavailable", "/agent/sessions/{session_id}/chat", {"message": "[테스트] LLM 오류"}, session_id=sid)
    rec.sse("chat_no_script", "/agent/sessions/{session_id}/chat", {"message": "오늘 저녁 메뉴 골라줘"}, session_id=sid)
    rec.call("chat_empty_message", "POST", "/agent/sessions/{session_id}/chat", body={"message": "  "}, expect=(422,),
             session_id=sid)
    rec.call("session_not_found", "GET", "/agent/sessions/{session_id}", expect=(404,), session_id="0" * 32)
    rec.call("plan_not_found", "POST", "/agent/sessions", body={"plan_id": 999999}, expect=(404,))
    if docker:
        events = rec.sse(None, "/agent/sessions/{session_id}/chat", {"message": "점심 1시간으로 늘려줘"}, session_id=sid)
        prop = _card(events, "proposal")
        if prop is None:
            raise Failed("503 예시용 제안이 만들어지지 않았습니다")
        _compose("stop", "safety")
        try:
            rec.call("planner_safety_unavailable", "GET", "/planner/plans/{plan_id}/safety", expect=(503,), plan_id=pid)
        finally:
            _compose("start", "safety")
        _compose("stop", "planner")
        try:
            rec.call("agent_upstream_unavailable", "POST", "/agent/sessions/{session_id}/proposals/{proposal_id}/accept",
                     body={"mode": "auto"}, expect=(503,), session_id=sid, proposal_id=prop["id"])
        finally:
            _compose("start", "planner")
            _wait_up(rec)
        rec.call(None, "POST", "/agent/sessions/{session_id}/proposals/{proposal_id}/reject", session_id=sid,
                 proposal_id=prop["id"])
    rec.end()


def _compose(*args: str) -> None:
    subprocess.run(["docker", "compose", *args], cwd=ROOT, check=True, capture_output=True)


def _wait_up(rec: Recorder, limit: float = 90) -> None:
    end = time.monotonic() + limit
    while time.monotonic() < end:
        if rec.http.get("/planner/plans", headers=rec._headers()).status_code == 200 and \
                rec.http.get("/safety/situation").status_code == 200:
            return
        time.sleep(2)
    raise Failed("planner·safety 가 다시 뜨지 않았습니다")


FLOWS = ["f11", "f1", "f2", "f10", "f3", "f4", "f5", "f6", "errors"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", default="http://127.0.0.1:8000", help="게이트웨이 주소 (기본 http://127.0.0.1:8000)")
    ap.add_argument("--out", default=str(OUT), help="저장할 곳 (기본 app/test/fixtures)")
    ap.add_argument("--only", default="", help=f"쉼표로 고른 흐름 ({','.join(FLOWS)})")
    ap.add_argument("--no-docker", action="store_true", help="503 예시(서비스를 잠깐 멈춤)를 녹화하지 않는다")
    a = ap.parse_args()
    only = set(filter(None, a.only.split(","))) or set(FLOWS)
    rec = Recorder(a.url, Path(a.out))
    try:
        rec.preflight()
        rec.login()
        print(f"녹화: {a.url} ({rec.backend['agent_provider']}, git {rec.backend['git']}) -> {rec.out}")
        if "f11" in only:
            f11_login(rec)
        plan_ctx = f1_plan(rec) if only & {"f1", "f2", "f10"} else None
        if "f2" in only:
            f2_actions(rec, plan_ctx)
        if "f10" in only:
            f10_guide(rec, plan_ctx["plan_id"])
        if "f3" in only:
            f3_trip_start(rec)
        if "f4" in only:
            f4_arrival(rec)
        if "f5" in only:
            f5_delay(rec)
        if "f6" in only:
            f6_f7_safety(rec)
        if "errors" in only:
            errors(rec, docker=not a.no_docker)
    except Failed as e:
        print(f"녹화 실패: {e}")
        sys.exit(1)
    finally:
        try:
            rec.sim("calm")
        except Exception:
            pass
    manifest_path = rec.out / "manifest.json"
    old = json.loads(manifest_path.read_text(encoding="utf-8"))["files"] if manifest_path.exists() else []
    files = {f["file"]: f for f in old} | {f["file"]: f for f in rec.files}
    _write(manifest_path, {"recorded_at": _now_iso(), "backend": rec.backend, "format": "docs/app/fixtures.md",
                           "files": sorted(files.values(), key=lambda f: f["file"])})
    if rec.failures:
        print("\n기대와 다른 것:\n  " + "\n  ".join(rec.failures))
        sys.exit(2)
    print(f"\n완료: {len(rec.files)}개 파일")


if __name__ == "__main__":
    main()
