"""Planner Agent 의 도구.

- 조회 도구(search_places, get_place, view_plan)는 바로 실행한다.
- 변경 도구는 계획을 바로 바꾸지 않는다. 이번 대화의 초안(Draft.ops)에 연산을 쌓고, planner 미리보기 API 로
  적용 결과를 계산해 LLM 에 돌려준다. 턴이 끝나면 하네스가 초안을 제안(Proposal)으로 만들고,
  사용자가 승인해야 실제로 반영된다.
"""
import json
from dataclasses import dataclass, field

from app.agent.api import ApiError, ServiceApi

CONTENT_TYPES = ["관광지", "음식점", "문화시설", "쇼핑", "레포츠", "숙박", "축제공연행사"]
RULE_TYPES = ["must_include", "exclude", "end_before", "max_travel_minutes"]


def _fn(name: str, description: str, properties: dict | None = None, required: list[str] | None = None) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties or {}, "required": required or []},
    }}


TOOL_SPECS = [
    _fn("search_places", "포항(구룡포 중심) 관광지·음식점 등을 검색한다. 일정에 넣을 장소의 place_id 를 얻을 때 쓴다.", {
        "query": {"type": "string", "description": "키워드나 원하는 느낌을 설명하는 문장. "
                                                   "예: 물회, 일본인가옥, 바다가 보이는 조용한 카페, 근대 역사를 느낄 수 있는 곳"},
        "content_type": {"type": "string", "enum": CONTENT_TYPES},
        "area": {"type": "string", "description": "읍면동. 예: 구룡포읍, 호미곶면"},
        "near_place_id": {"type": "string", "description": "이 장소 주변에서 가까운 순으로 검색"},
        "radius_m": {"type": "integer", "description": "near_place_id 기준 반경(m), 기본 1500"},
        "limit": {"type": "integer", "description": "최대 개수 (기본 8, 최대 15)"},
    }),
    _fn("get_place", "장소 상세 (주소, 운영시간, 휴무일, 요금, 메뉴, 소개).", {
        "place_id": {"type": "string"},
    }, ["place_id"]),
    _fn("view_plan", "현재 일정(이번 대화에서 제안 중인 변경 포함)을 시간표와 위반 사항과 함께 본다."),
    _fn("add_item", "일정에 장소를 추가한다 (제안에 포함, 사용자 승인 후 반영). "
                    "시각은 앞 일정에 이어 자동 계산된다. 시각 고정이 꼭 필요하면 추가 후 update_item 으로.", {
        "place_id": {"type": "string", "description": "search_places 로 찾은 id"},
        "day": {"type": "integer", "description": "n일차 (1부터, 기본 1)"},
        "order": {"type": "integer", "description": "그날 몇 번째 (1부터). 생략하면 맨 뒤"},
        "stay_minutes": {"type": "integer", "description": "체류 시간(분), 기본 60"},
        "memo": {"type": "string", "description": "추천 이유 등 짧은 메모"},
    }, ["place_id"]),
    _fn("update_item", "일정 항목을 수정한다: 순서 변경, 다른 날로 이동, 체류 시간, 시작 시각 고정/해제, 메모, 상태.", {
        "item_id": {"type": "integer", "description": "view_plan 의 item_id (방금 추가한 항목은 음수 임시 id)"},
        "day": {"type": "integer"},
        "order": {"type": "integer"},
        "stay_minutes": {"type": "integer"},
        "start_time": {"type": "string", "description": "HH:MM 고정 (사용자가 요구할 때만), \"none\" 이면 고정 해제"},
        "memo": {"type": "string"},
        "status": {"type": "string", "enum": ["planned", "visiting", "done", "skipped"]},
    }, ["item_id"]),
    _fn("remove_item", "일정 항목을 삭제한다.", {"item_id": {"type": "integer"}}, ["item_id"]),
    _fn("update_plan", "계획 설정 변경: 제목, 기간, 하루 시작/종료 시각, 이동 수단.", {
        "title": {"type": "string"},
        "start_date": {"type": "string", "description": "YYYY-MM-DD"},
        "end_date": {"type": "string", "description": "YYYY-MM-DD"},
        "day_start": {"type": "string", "description": "HH:MM"},
        "day_end": {"type": "string", "description": "HH:MM"},
        "travel_mode": {"type": "string", "enum": ["walk", "car"]},
    }),
    _fn("add_rule", "사용자의 조건을 규칙으로 등록한다. 이후 모든 변경이 이 규칙으로 검사된다. "
                    "must_include(place_id: 꼭 포함), exclude(place_id: 제외), "
                    "end_before(time, day?: 그 시각 전 종료), max_travel_minutes(minutes: 이동시간 상한).", {
        "type": {"type": "string", "enum": RULE_TYPES},
        "place_id": {"type": "string"},
        "time": {"type": "string", "description": "HH:MM (end_before)"},
        "day": {"type": "integer", "description": "end_before 적용 일차 (생략 시 매일)"},
        "minutes": {"type": "integer", "description": "max_travel_minutes"},
        "hard": {"type": "boolean", "description": "반드시 지킬 조건이면 true(기본), 선호면 false"},
        "note": {"type": "string", "description": "사용자가 말한 이유"},
    }, ["type"]),
    _fn("remove_rule", "규칙을 삭제한다.", {"rule_id": {"type": "integer"}}, ["rule_id"]),
    _fn("discard_changes", "이번 대화에서 제안 중인 변경을 모두 취소하고 현재 확정 일정으로 되돌린다."),
]

MUTATING = {"add_item", "update_item", "remove_item", "update_plan", "add_rule", "remove_rule", "discard_changes"}


@dataclass
class Draft:
    """이번 세션의 제안 초안. base_proposal_id 는 이어서 수정 중인 대기 제안."""

    ops: list[dict] = field(default_factory=list)
    base_proposal_id: int | None = None
    changed: bool = False


@dataclass
class ToolOutcome:
    ok: bool
    content: str  # LLM 에 돌려줄 내용 (JSON 문자열)
    summary: str  # 앱·CLI 에 보여줄 한 줄


# ---------- LLM 에 보여줄 압축 표현 ----------

def compact_plan(plan: dict, changes: list[dict] | None = None, pending: int = 0) -> dict:
    days = []
    for d in plan["days"]:
        items = []
        for i in d["items"]:
            it = {"item_id": i["id"], "order": i["order"],
                  "place": i["place"]["title"] if i["place"] else i["place_id"], "place_id": i["place_id"],
                  "time": "건너뜀" if i["status"] == "skipped" else f"{i['start_at']}~{i['end_at']}",
                  "stay": i["stay_minutes"]}
            if i["travel_minutes"]:
                it["travel_min"] = i["travel_minutes"]
            if i["start_time"]:
                it["fixed_start"] = i["start_time"]
            if i["status"] not in ("planned", "skipped"):
                it["status"] = i["status"]
            if i["memo"]:
                it["memo"] = i["memo"]
            items.append(it)
        days.append({"day": d["day"], "date": f"{d['date']}({d['weekday']})", "end": d["end_at"], "items": items})
    out = {
        "title": plan["title"], "dates": f"{plan['start_date']}~{plan['end_date']}",
        "daily_hours": f"{plan['day_start']}~{plan['day_end']}", "travel_mode": plan["travel_mode"],
        "days": days,
        "rules": [{"rule_id": c["id"], "type": c["type"], **c["params"], "hard": c["hard"]} for c in plan["constraints"]],
        "violations": [f"[{v['severity']}] {v['message']}" for v in plan["violations"]],
    }
    if plan["memo"]:
        out["memo"] = plan["memo"]
    if pending:
        out["pending_changes"] = changes if changes is not None else pending
    return out


def _blank(v) -> bool:
    return v is None or str(v).strip().lower() in ("", "none", "null")


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


class Toolbox:
    def __init__(self, api: ServiceApi, plan_id: int, draft: Draft):
        self.api = api
        self.plan_id = plan_id
        self.draft = draft

    async def current_view(self) -> dict:
        """초안이 있으면 초안 적용 결과, 없으면 확정 일정."""
        if self.draft.ops:
            r = await self.api.preview(self.plan_id, self.draft.ops)
            return compact_plan(r["plan"], [c["message"] for c in r["changes"]], len(self.draft.ops))
        return compact_plan(await self.api.get_plan(self.plan_id))

    async def call(self, name: str, raw_args: str) -> ToolOutcome:
        try:
            args = json.loads(raw_args or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments 는 JSON 객체여야 합니다")
        except ValueError as e:
            return ToolOutcome(False, _dump({"error": f"인자 JSON 을 해석할 수 없습니다: {e}"}), "인자 오류")
        args = {k: v for k, v in args.items() if v is not None}
        handler = getattr(self, f"_t_{name}", None)
        if handler is None:
            return ToolOutcome(False, _dump({"error": f"알 수 없는 도구: {name}"}), "알 수 없는 도구")
        try:
            return await handler(**args)
        except (TypeError, ValueError, KeyError) as e:
            return ToolOutcome(False, _dump({"error": f"인자가 잘못됐습니다: {e}"}), "인자 오류")
        except ApiError as e:
            return ToolOutcome(False, _dump({"error": e.message}), f"실패: {e.message[:80]}")

    # ----- 조회 -----
    async def _t_search_places(self, query: str = "", content_type: str = "", area: str = "", near_place_id: str = "",
                               radius_m: int = 1500, limit: int = 8) -> ToolOutcome:
        params = {"q": query, "content_type": content_type, "area": area, "limit": max(1, min(int(limit), 15))}
        if query.strip():
            params["mode"] = "hybrid"  # 이름 일치(키워드) + 뜻(임베딩)을 함께 반영
        if near_place_id:
            center = await self.api.get_place(near_place_id)
            params |= {"lat": center["lat"], "lng": center["lng"], "radius_m": radius_m}
        r = await self.api.search_places(**params)
        items = [{"place_id": p["id"], "title": p["title"], "type": p["content_type"], "area": p["area"],
                  **({"distance_m": round(p["distance_m"])} if p.get("distance_m") is not None else {})}
                 for p in r["items"]]
        out: dict = {"items": items}
        if not items:
            out["hint"] = "결과가 없습니다. 다른 표현이나 content_type/area 만으로 다시 찾아보세요."
        return ToolOutcome(True, _dump(out), f"{len(items)}건")

    async def _t_get_place(self, place_id: str) -> ToolOutcome:
        p = await self.api.get_place(place_id)
        keys = ["title", "content_type", "area", "addr", "open_hours", "rest_date", "fee", "menu"]
        out = {"place_id": p["id"], **{k: p[k].strip() if isinstance(p[k], str) else p[k] for k in keys if p.get(k)}}
        if p.get("overview"):
            out["overview"] = p["overview"][:500]
        return ToolOutcome(True, _dump(out), p["title"])

    async def _t_view_plan(self) -> ToolOutcome:
        return ToolOutcome(True, _dump(await self.current_view()), "일정 조회")

    # ----- 변경 (초안에 쌓음) -----
    async def _stage(self, op: dict, label: str) -> ToolOutcome:
        candidate = [*self.draft.ops, op]
        r = await self.api.preview(self.plan_id, candidate)  # 적용 불가면 ApiError -> 초안에 넣지 않음
        self.draft.ops = candidate
        self.draft.changed = True
        view = compact_plan(r["plan"], [c["message"] for c in r["changes"]], len(candidate))
        note = "제안 초안에 반영됨 (아직 실제 일정 아님. 응답이 끝나면 사용자에게 승인 요청됨)"
        return ToolOutcome(True, _dump({"result": note, "plan_after": view}), label)

    async def _t_add_item(self, place_id: str, day: int = 1, order: int | None = None, stay_minutes: int = 60,
                          memo: str = "", **ignored) -> ToolOutcome:
        # 모델이 없는 인자(start_time, near_place_id 등)를 붙여도 실패시키지 않는다
        op = {"op": "add_item", "place_id": place_id, "day": day, "order": order, "stay_minutes": stay_minutes,
              "memo": memo}
        return await self._stage(op, f"추가 {place_id} ({day}일차)")

    async def _t_update_item(self, item_id: int, **changes) -> ToolOutcome:
        allowed = {"day", "order", "stay_minutes", "start_time", "memo", "status"}
        unknown = set(changes) - allowed
        if unknown:
            raise TypeError(f"알 수 없는 인자 {sorted(unknown)}")
        if not changes:
            raise TypeError("바꿀 값을 하나 이상 지정하세요")
        if "start_time" in changes and _blank(changes["start_time"]):
            changes["start_time"] = None
        return await self._stage({"op": "update_item", "item_id": item_id, **changes}, f"수정 항목 {item_id}")

    async def _t_remove_item(self, item_id: int) -> ToolOutcome:
        return await self._stage({"op": "remove_item", "item_id": item_id}, f"삭제 항목 {item_id}")

    async def _t_update_plan(self, **changes) -> ToolOutcome:
        allowed = {"title", "start_date", "end_date", "day_start", "day_end", "travel_mode"}
        if set(changes) - allowed or not changes:
            raise TypeError(f"허용 인자: {sorted(allowed)}")
        return await self._stage({"op": "update_plan", **changes}, "계획 설정 변경")

    async def _t_add_rule(self, type: str, hard: bool = True, note: str = "", **params) -> ToolOutcome:
        allowed = {"place_id", "time", "day", "minutes"}
        if set(params) - allowed:
            raise TypeError(f"알 수 없는 인자 {sorted(set(params) - allowed)}")
        op = {"op": "add_constraint", "type": type, "params": params, "hard": hard, "source": "llm", "note": note}
        return await self._stage(op, f"규칙 추가 {type}")

    async def _t_remove_rule(self, rule_id: int) -> ToolOutcome:
        return await self._stage({"op": "remove_constraint", "constraint_id": rule_id}, f"규칙 삭제 {rule_id}")

    async def _t_discard_changes(self) -> ToolOutcome:
        self.draft.ops = []
        self.draft.changed = True
        return ToolOutcome(True, _dump({"result": "제안 중인 변경을 모두 취소했습니다.",
                                        "plan": await self.current_view()}), "변경 취소")
