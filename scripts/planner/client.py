"""GuryongMate API 클라이언트 (planner·places·agent·trip·safety·routing·docent). CLI 가 쓰고, 다른 Python 도구에서도 재사용할 수 있다.

메서드는 API 응답 JSON(dict)을 그대로 돌려준다. 오류는 PlannerApiError (code·message·detail).
명세: docs/planner/README.md, docs/agent/README.md, docs/account/README.md, docs/trip/README.md,
      docs/docent/README.md §5, docs/safety/README.md §1, docs/routing/README.md §1, docs/places/README.md
"""
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx


class PlannerApiError(RuntimeError):
    def __init__(self, status: int, detail: Any):
        self.status = status
        self.detail = detail if isinstance(detail, dict) else {"code": "error", "message": str(detail)}
        super().__init__(f"HTTP {status} {self.code}: {self.message}")

    @property
    def code(self) -> str:
        return self.detail.get("code", "error")

    @property
    def message(self) -> str:
        return self.detail.get("message", "")


SESSION_FILE = Path.home() / ".guryongmate" / "session.json"


class Session:
    """서버 주소별 로그인 토큰 저장 (~/.guryongmate/session.json)."""

    def __init__(self, base_url: str, path: Path = SESSION_FILE):
        self.base_url, self.path = base_url.rstrip("/"), path

    def _all(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def load(self) -> dict | None:
        return self._all().get(self.base_url)

    def save(self, tokens: dict | None) -> None:
        data = self._all()
        if tokens is None:
            data.pop(self.base_url, None)
        else:
            data[self.base_url] = {k: tokens[k] for k in ("access_token", "refresh_token", "user")}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass


class PlannerClient:
    """로그인했으면(세션 파일) Authorization: Bearer 로, 아니면 개발 모드처럼 X-User-Id 로 부른다.
    access 토큰이 만료돼 401 이 오면 refresh 로 한 번 갱신하고 다시 보낸다."""

    def __init__(self, base_url: str = "http://127.0.0.1:8000", user_id: str = "demo", timeout: float = 10,
                 session: Session | None = None):
        self._http = httpx.Client(base_url=base_url, timeout=timeout)
        self._user_id = user_id
        self.session = session or Session(base_url)

    @property
    def logged_in(self) -> dict | None:
        return self.session.load()

    def _auth(self) -> dict[str, str]:
        s = self.session.load()
        return {"Authorization": f"Bearer {s['access_token']}"} if s else {"X-User-Id": self._user_id}

    def _try_refresh(self) -> bool:
        s = self.session.load()
        if not s:
            return False
        resp = self._http.post("/auth/refresh", json={"refresh_token": s["refresh_token"]})
        if resp.is_error:
            self.session.save(None)  # refresh 도 실패 -> 다시 로그인해야 함
            return False
        self.session.save(resp.json())
        return True

    @staticmethod
    def _detail(resp: httpx.Response) -> Any:
        try:
            body = resp.json()
        except ValueError:
            return resp.text
        return body.get("detail", body) if isinstance(body, dict) else body

    def _expired(self, resp: httpx.Response) -> bool:
        return resp.status_code == 401 and self.session.load() is not None

    def _req(self, method: str, path: str, **kw) -> Any:
        resp = self._http.request(method, path, headers=self._auth(), **kw)
        if self._expired(resp) and self._try_refresh():
            resp = self._http.request(method, path, headers=self._auth(), **kw)
        if resp.is_error:
            raise PlannerApiError(resp.status_code, self._detail(resp))
        return None if resp.status_code == 204 else resp.json()

    def _stream(self, path: str, body: dict | None = None) -> Iterator[tuple[str, dict]]:
        """SSE 이벤트를 (이름, 데이터) 로 오는 대로 내준다."""
        for attempt in range(2):
            with self._http.stream("POST", path, json=body, headers=self._auth(),
                                   timeout=httpx.Timeout(10, read=None)) as resp:
                if attempt == 0 and self._expired(resp) and self._try_refresh():
                    continue
                yield from self._sse(resp)
                return

    def _sse(self, resp: httpx.Response) -> Iterator[tuple[str, dict]]:
        if resp.is_error:
            resp.read()
            raise PlannerApiError(resp.status_code, self._detail(resp))
        event, data = "message", []
        for line in resp.iter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
            elif not line and data:
                yield event, json.loads("\n".join(data))
                event, data = "message", []

    # ----- 계정 (account) -----
    def _auth_flow(self, path: str, body: dict) -> dict:
        resp = self._http.post(path, json=body)
        if resp.is_error:
            raise PlannerApiError(resp.status_code, self._detail(resp))
        tokens = resp.json()
        self.session.save(tokens)
        return tokens

    def signup(self, login_id: str, password: str, display_name: str | None = None) -> dict:
        body = {"login_id": login_id, "password": password}
        if display_name:
            body["display_name"] = display_name
        return self._auth_flow("/auth/signup", body)

    def login(self, login_id: str, password: str) -> dict:
        return self._auth_flow("/auth/login", {"login_id": login_id, "password": password})

    def logout(self) -> None:
        s = self.session.load()
        if s:
            self._http.post("/auth/logout", json={"refresh_token": s["refresh_token"]})
        self.session.save(None)

    def me(self) -> dict:
        return self._req("GET", "/users/me")

    def change_password(self, current: str, new: str) -> dict:
        tokens = self._req("PUT", "/users/me/password", json={"current_password": current, "new_password": new})
        self.session.save(tokens)
        return tokens

    # ----- 장소 (places) -----
    def search_places(self, q: str | None = None, tags: list[str] | None = None, content_type: str | None = None,
                      area: str | None = None, lat: float | None = None, lng: float | None = None,
                      radius_m: float | None = None, limit: int = 20) -> dict:
        params = {"q": q, "tags": tags, "content_type": content_type, "area": area, "lat": lat, "lng": lng,
                  "radius_m": radius_m, "limit": limit}
        return self._req("GET", "/places", params={k: v for k, v in params.items() if v is not None})

    def get_place(self, place_id: str) -> dict:
        return self._req("GET", f"/places/{place_id}")

    def passages(self, q: str, place_id: str | None = None, kind: str | None = None, lat: float | None = None,
                 lng: float | None = None, radius_m: float | None = None, limit: int = 5) -> list[dict]:
        params = {"q": q, "place_id": place_id, "kind": kind, "lat": lat, "lng": lng, "radius_m": radius_m, "limit": limit}
        return self._req("GET", "/places/passages", params={k: v for k, v in params.items() if v is not None})

    def stories(self, place_id: str) -> list[dict]:
        return self._req("GET", f"/places/{place_id}/stories")

    def stories_near(self, lat: float, lng: float, radius_m: float | None = None, limit: int | None = None) -> list[dict]:
        params = {"lat": lat, "lng": lng, "radius_m": radius_m, "limit": limit}
        return self._req("GET", "/places/stories/near", params={k: v for k, v in params.items() if v is not None})

    # ----- 계획 -----
    def list_plans(self) -> list[dict]:
        return self._req("GET", "/planner/plans")

    def create_plan(self, title: str, start_date: str, end_date: str | None = None, day_start: str = "10:00",
                    day_end: str = "18:00", travel_mode: str = "walk", memo: str = "") -> dict:
        body = {"title": title, "start_date": start_date, "end_date": end_date, "day_start": day_start,
                "day_end": day_end, "travel_mode": travel_mode, "memo": memo}
        return self._req("POST", "/planner/plans", json=body)

    def get_plan(self, plan_id: int) -> dict:
        return self._req("GET", f"/planner/plans/{plan_id}")

    def update_plan(self, plan_id: int, **changes) -> dict:
        return self._req("PATCH", f"/planner/plans/{plan_id}", json=changes)

    def delete_plan(self, plan_id: int) -> None:
        self._req("DELETE", f"/planner/plans/{plan_id}")

    def validate(self, plan_id: int) -> dict:
        return self._req("GET", f"/planner/plans/{plan_id}/validate")

    # ----- 일정 항목 -----
    def add_item(self, plan_id: int, place_id: str, day: int = 1, order: int | None = None, stay_minutes: int = 60,
                 start_time: str | None = None, memo: str = "", travel_mode: str | None = None) -> dict:
        body = {"place_id": place_id, "day": day, "order": order, "stay_minutes": stay_minutes,
                "start_time": start_time, "memo": memo, "travel_mode": travel_mode}
        return self._req("POST", f"/planner/plans/{plan_id}/items", json=body)

    def update_item(self, plan_id: int, item_id: int, **changes) -> dict:
        """보낸 필드만 바뀐다 (day, order, stay_minutes, start_time, memo, status, travel_mode).
        start_time=None 은 고정 해제, travel_mode=None 은 계획 기본 수단으로."""
        return self._req("PATCH", f"/planner/plans/{plan_id}/items/{item_id}", json=changes)

    def delete_item(self, plan_id: int, item_id: int) -> dict:
        return self._req("DELETE", f"/planner/plans/{plan_id}/items/{item_id}")

    # ----- 여행 중: 진행 상태 · 현재 기준 재계산 (§8) -----
    def progress(self, plan_id: int, item_id: int, status: str, at: str | None = None) -> dict:
        """status: visiting | done | planned. version 은 그대로 (progress_version 만 오른다)."""
        body = {"status": status} | ({"at": at} if at else {})
        return self._req("POST", f"/planner/plans/{plan_id}/items/{item_id}/progress", json=body)

    def projection(self, plan_id: int, day: int | None = None, now: str | None = None,
                   position: dict | None = None) -> dict:
        """position: {"state": "visiting", "item_id", "arrived_at"?} | {"state": "moving", "lat", "lng"} | None (저장된 진행 상태)."""
        body = {k: v for k, v in {"day": day, "now": now, "position": position}.items() if v is not None}
        return self._req("POST", f"/planner/plans/{plan_id}/projection", json=body)

    # ----- 안전 점검 -----
    def check_safety(self, plan_id: int) -> dict:
        return self._req("GET", f"/planner/plans/{plan_id}/safety")

    def safety_alerts(self, plan_id: int, status: str | None = None) -> list[dict]:
        return self._req("GET", f"/planner/plans/{plan_id}/safety/alerts", params={"status": status} if status else None)

    def safety_alert_seen(self, plan_id: int, alert_id: int) -> dict:
        return self._req("POST", f"/planner/plans/{plan_id}/safety/alerts/{alert_id}/seen")

    def safety_preview(self, plan_id: int, ops: list[dict]) -> dict:
        """ops 를 적용했다고 치고 안전 점검 (저장 안 함) -> SafetyCheck."""
        return self._req("POST", f"/planner/plans/{plan_id}/safety/preview", json={"ops": ops})

    # ----- 규칙 -----
    def list_constraints(self, plan_id: int) -> list[dict]:
        return self._req("GET", f"/planner/plans/{plan_id}/constraints")

    def add_constraint(self, plan_id: int, type: str, params: dict, hard: bool = True, source: str = "user",
                       note: str = "") -> dict:
        body = {"type": type, "params": params, "hard": hard, "source": source, "note": note}
        return self._req("POST", f"/planner/plans/{plan_id}/constraints", json=body)

    def delete_constraint(self, plan_id: int, constraint_id: int) -> dict:
        return self._req("DELETE", f"/planner/plans/{plan_id}/constraints/{constraint_id}")

    # ----- 변경 제안 (planner 직접) -----
    def list_proposals(self, plan_id: int, status: str | None = None) -> list[dict]:
        return self._req("GET", f"/planner/plans/{plan_id}/proposals", params={"status": status} if status else None)

    def get_proposal(self, plan_id: int, proposal_id: int) -> dict:
        return self._req("GET", f"/planner/plans/{plan_id}/proposals/{proposal_id}")

    def plan_accept_proposal(self, plan_id: int, proposal_id: int, mode: str = "auto") -> dict:
        return self._req("POST", f"/planner/plans/{plan_id}/proposals/{proposal_id}/accept", json={"mode": mode})

    def plan_reject_proposal(self, plan_id: int, proposal_id: int) -> dict:
        return self._req("POST", f"/planner/plans/{plan_id}/proposals/{proposal_id}/reject")

    # ----- 장소 추천 (planner 직접) -----
    def list_recommendations(self, plan_id: int) -> list[dict]:
        return self._req("GET", f"/planner/plans/{plan_id}/recommendations")

    def get_recommendation(self, plan_id: int, rec_id: int) -> dict:
        return self._req("GET", f"/planner/plans/{plan_id}/recommendations/{rec_id}")

    def plan_candidate(self, plan_id: int, rec_id: int, cand_id: int, action: str) -> dict:
        return self._req("POST", f"/planner/plans/{plan_id}/recommendations/{rec_id}/candidates/{cand_id}/{action}")

    # ----- Agent -----
    def agent_info(self) -> dict:
        return self._req("GET", "/agent/info")

    def create_session(self, plan_id: int) -> dict:
        return self._req("POST", "/agent/sessions", json={"plan_id": plan_id})

    def list_sessions(self, plan_id: int | None = None) -> list[dict]:
        return self._req("GET", "/agent/sessions", params={"plan_id": plan_id} if plan_id is not None else None)

    def get_session(self, session_id: str) -> dict:
        return self._req("GET", f"/agent/sessions/{session_id}")

    def chat_stream(self, session_id: str, message: str) -> Iterator[tuple[str, dict]]:
        return self._stream(f"/agent/sessions/{session_id}/chat", {"message": message})

    def accept_proposal(self, session_id: str, proposal_id: int, mode: str = "auto") -> dict:
        """409 base_changed 면 PlannerApiError (detail 에 since_proposal·overwrite_effect)."""
        return self._req("POST", f"/agent/sessions/{session_id}/proposals/{proposal_id}/accept", json={"mode": mode})

    def reject_proposal(self, session_id: str, proposal_id: int) -> dict:
        return self._req("POST", f"/agent/sessions/{session_id}/proposals/{proposal_id}/reject")

    def repropose_stream(self, session_id: str, proposal_id: int) -> Iterator[tuple[str, dict]]:
        return self._stream(f"/agent/sessions/{session_id}/proposals/{proposal_id}/repropose")

    def candidate(self, session_id: str, rec_id: int, cand_id: int, action: str) -> dict:
        """action: add | remove"""
        return self._req("POST", f"/agent/sessions/{session_id}/recommendations/{rec_id}/candidates/{cand_id}/{action}")

    # ----- 가이드 준비 (agent) -----
    def guide_prepare(self, plan_id: int, place_ids: list[str] | None = None, force: bool = False) -> dict:
        body = {"force": force} | ({"place_ids": place_ids} if place_ids else {})
        return self._req("POST", f"/agent/plans/{plan_id}/guide/prepare", json=body)

    def guide_job(self, plan_id: int, job_id: str) -> dict:
        return self._req("GET", f"/agent/plans/{plan_id}/guide/jobs/{job_id}")

    # ----- 메모리 · 가이드 노트 (docent) -----
    def memory(self, plan_id: int | None = None) -> dict:
        return self._req("GET", "/docent/memory", params={"plan_id": plan_id} if plan_id is not None else None)

    def remember(self, scope: str, category: str, text: str, plan_id: int | None = None,
                 source: str = "user_edit") -> dict:
        body = {"scope": scope, "category": category, "text": text, "source": source}
        if plan_id is not None:
            body["plan_id"] = plan_id
        return self._req("POST", "/docent/memory", json=body)

    def forget(self, memory_id: int) -> None:
        self._req("DELETE", f"/docent/memory/{memory_id}")

    def notes(self, plan_id: int) -> list[dict]:
        return self._req("GET", f"/docent/plans/{plan_id}/notes")

    def note(self, plan_id: int, place_id: str) -> dict:
        return self._req("GET", f"/docent/plans/{plan_id}/notes/{place_id}")

    # ----- 여행 중 (trip) -----
    def trip_start(self, plan_id: int, session_id: str | None = None) -> dict:
        return self._req("POST", f"/trip/plans/{plan_id}/start", json={"session_id": session_id} if session_id else None)

    def trip_end(self, plan_id: int) -> dict:
        return self._req("POST", f"/trip/plans/{plan_id}/end")

    def trip_state(self, plan_id: int) -> dict:
        return self._req("GET", f"/trip/plans/{plan_id}/state")

    def trip_location(self, plan_id: int, points: list[dict]) -> dict:
        """points: [{lat, lng, accuracy?, recorded_at?, source?}] (오래된 순). -> {state, events, accepted, ignored}"""
        body = points[0] if len(points) == 1 else {"points": points}
        return self._req("POST", f"/trip/plans/{plan_id}/location", json=body)

    def trip_events(self, plan_id: int, after: int = 0, limit: int = 50) -> list[dict]:
        return self._req("GET", f"/trip/plans/{plan_id}/events", params={"after": after, "limit": limit})

    def trip_tick(self, plan_id: int, now: str | None = None) -> dict:
        return self._req("POST", f"/trip/plans/{plan_id}/tick", params={"now": now} if now else None)

    def trip_event_seen(self, plan_id: int, event_id: int) -> dict:
        return self._req("POST", f"/trip/plans/{plan_id}/events/{event_id}/seen")

    # ----- 가상 상황·시각 (safety, SAFETY_SOURCE=mock 또는 SAFETY_SIM_ENABLED) -----
    def sim(self) -> dict:
        return self._req("GET", "/safety/sim")

    def set_sim(self, scenario: str | None = None, now: str | None = None, reset_clock: bool = False,
                source: str | None = None) -> dict:
        """source: live(실제 데이터) | mock(가상 상황). SAFETY_SIM_ENABLED 일 때 live 로 바꿀 수 있다. scenario 를 주면 mock."""
        body = {k: v for k, v in {"scenario": scenario, "now": now, "source": source}.items() if v is not None}
        if reset_clock:
            body["reset_clock"] = True
        return self._req("PUT", "/safety/sim", json=body)

    def safety_scan(self, force: bool = False) -> dict:
        """planner 안전 점검을 지금 한 번 (개발 모드에서만)."""
        return self._req("POST", "/planner/safety/scan", params={"force": str(force).lower()})

    # ----- 안전 정보 (safety) -----
    def outlook(self, date: str, start: str | None = None, end: str | None = None) -> dict:
        params = {"date": date, "start": start, "end": end}
        return self._req("GET", "/safety/outlook", params={k: v for k, v in params.items() if v is not None})

    def safety_status(self) -> dict:
        return self._req("GET", "/safety/status")

    def safety_zones(self) -> list[dict]:
        return self._req("GET", "/safety/zones")

    def safety_situation(self) -> dict:
        return self._req("GET", "/safety/situation")

    def safety_avoid(self, min_risk: str = "caution") -> dict:
        return self._req("GET", "/safety/avoid", params={"min_risk": min_risk})

    # ----- 길찾기 (routing) -----
    @staticmethod
    def _pt(p: tuple[float, float]) -> dict:
        return {"lat": p[0], "lng": p[1]}

    def route(self, stops: list[tuple[float, float]], mode: str = "walk", hazards: str = "ignore",
              steps: bool = False) -> dict:
        """hazards: ignore | report | avoid (위험 구역 우회)."""
        body = {"mode": mode, "stops": [self._pt(p) for p in stops], "steps": steps, "hazards": hazards}
        return self._req("POST", "/routing/route", json=body)

    def legs(self, legs: list[tuple[tuple[float, float], tuple[float, float]]], mode: str = "walk") -> dict:
        body = {"mode": mode, "legs": [{"from": self._pt(a), "to": self._pt(b)} for a, b in legs]}
        return self._req("POST", "/routing/legs", json=body)

    def matrix(self, sources: list[tuple[float, float]], targets: list[tuple[float, float]],
               mode: str = "walk") -> dict:
        body = {"mode": mode, "sources": [self._pt(p) for p in sources], "targets": [self._pt(p) for p in targets]}
        return self._req("POST", "/routing/matrix", json=body)

    def routing_status(self) -> dict:
        return self._req("GET", "/routing/status")
