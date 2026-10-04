"""GuryongMate API 클라이언트 (planner·places·agent). CLI 가 쓰고, 다른 Python 도구에서도 재사용할 수 있다.

메서드는 API 응답 JSON(dict)을 그대로 돌려준다. 오류는 PlannerApiError (code·message·detail).
명세: docs/planner/README.md, docs/agent/README.md, docs/account/README.md
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
                 start_time: str | None = None, memo: str = "") -> dict:
        body = {"place_id": place_id, "day": day, "order": order, "stay_minutes": stay_minutes,
                "start_time": start_time, "memo": memo}
        return self._req("POST", f"/planner/plans/{plan_id}/items", json=body)

    def update_item(self, plan_id: int, item_id: int, **changes) -> dict:
        """보낸 필드만 바뀐다 (day, order, stay_minutes, start_time, memo, status). start_time=None 은 고정 해제."""
        return self._req("PATCH", f"/planner/plans/{plan_id}/items/{item_id}", json=changes)

    def delete_item(self, plan_id: int, item_id: int) -> dict:
        return self._req("DELETE", f"/planner/plans/{plan_id}/items/{item_id}")

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
