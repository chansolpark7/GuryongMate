"""GuryongMate API 클라이언트 (planner·places·agent). CLI 가 쓰고, 다른 Python 도구에서도 재사용할 수 있다.

메서드는 API 응답 JSON(dict)을 그대로 돌려준다. 오류는 PlannerApiError.
"""
import json
from collections.abc import Iterator
from typing import Any

import httpx


class PlannerApiError(RuntimeError):
    def __init__(self, status: int, detail: Any):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


class PlannerClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8000", user_id: str = "demo", timeout: float = 10):
        self._http = httpx.Client(base_url=base_url, headers={"X-User-Id": user_id}, timeout=timeout)

    def _req(self, method: str, path: str, **kw) -> Any:
        resp = self._http.request(method, path, **kw)
        if resp.is_error:
            try:
                detail = resp.json().get("detail", resp.text)
            except ValueError:
                detail = resp.text
            raise PlannerApiError(resp.status_code, detail)
        return None if resp.status_code == 204 else resp.json()

    # ----- 장소 (places 모듈) -----
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

    # ----- 제안 (planner) -----
    def list_proposals(self, plan_id: int, status: str | None = None) -> list[dict]:
        return self._req("GET", f"/planner/plans/{plan_id}/proposals", params={"status": status} if status else None)

    def get_proposal(self, plan_id: int, proposal_id: int) -> dict:
        return self._req("GET", f"/planner/plans/{plan_id}/proposals/{proposal_id}")

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
        """SSE 이벤트를 (이름, 데이터) 로 오는 대로 내준다. 이벤트 종류는 backend/app/agent/schemas.py 참고."""
        with self._http.stream("POST", f"/agent/sessions/{session_id}/chat", json={"message": message},
                               timeout=httpx.Timeout(10, read=None)) as resp:
            if resp.is_error:
                resp.read()
                try:
                    detail = resp.json().get("detail", resp.text)
                except ValueError:
                    detail = resp.text
                raise PlannerApiError(resp.status_code, detail)
            event, data = "message", []
            for line in resp.iter_lines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].strip())
                elif not line and data:
                    yield event, json.loads("\n".join(data))
                    event, data = "message", []

    def accept_proposal(self, session_id: str, proposal_id: int) -> dict:
        return self._req("POST", f"/agent/sessions/{session_id}/proposals/{proposal_id}/accept")

    def reject_proposal(self, session_id: str, proposal_id: int) -> dict:
        return self._req("POST", f"/agent/sessions/{session_id}/proposals/{proposal_id}/reject")
