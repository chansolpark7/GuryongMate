"""Planner Agent 의 도구.

- 조회 도구(search_places, get_place, view_plan)는 바로 실행한다.
- 변경 도구는 계획을 바로 바꾸지 않는다. 이번 턴의 초안(Draft.ops)에 연산을 쌓고, planner preview 로
  적용 결과를 계산해 LLM 에 돌려준다. 턴이 끝나면 하네스가 초안을 제안(Proposal)으로 만든다.
- recommend_places 는 추천 카드를 바로 만든다 (사용자가 후보를 골라 직접 추가하므로 초안과 무관).

각 도구 결과에는 앱의 처리 단계 표시용 정보(kind, detail)가 붙는다. 문구는 LLM 이 아니라 여기서 만든다.
"""
import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.agent.api import ApiError, ServiceApi
from app.agent.review import LEVEL, review, safety_issues, safety_what, summarize

MAX_TRAVEL_TARGETS = 10
MAX_HAZARD_ROUTES = 3
CAR_OVERHEAD_MIN = 5  # 주차·승하차 (planner 일정 계산과 같은 값)
CONTENT_TYPES = ["관광지", "음식점", "문화시설", "쇼핑", "레포츠", "숙박", "축제공연행사"]
RULE_TYPES = ["must_include", "exclude", "end_before", "max_travel_minutes", "max_walk_minutes",
              "max_daily_walk_minutes"]
MODE_DESC = ("직전 장소에서 여기까지 오는 이동 수단 (walk 도보 | car 차량). 생략하면 계획 기본 수단. "
             "view_plan 의 travel 에 두 수단 시간이 있으니 먼 구간만 차량으로 바꿀 때 쓴다")


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
        "area": {"type": "string", "description": "읍면동 필터. 예: 구룡포읍, 호미곶면. 특정 가게·장소 이름을 찾을 때는 "
                                                 "쓰지 않는다 (다른 동에 있으면 결과에서 빠진다)"},
        "near_place_id": {"type": "string", "description": "이 장소 주변에서 가까운 순으로 검색"},
        "radius_m": {"type": "integer", "description": "near_place_id 기준 반경(m), 기본 1500. 결과마다 기준 장소와의 "
                                                      "직선거리(distance_m)가 붙으니, 먼 곳끼리 거리를 비교할 때는 넉넉히(예: 50000)"},
        "limit": {"type": "integer", "description": "최대 개수 (기본 8, 최대 15)"},
    }),
    _fn("travel_time", "장소 사이 실제 길 기준 이동시간·거리 (도보·차량). 일정에 없는 장소끼리도 된다. "
                       "\"차로 몇 분?\", \"걸어갈 만해?\", 어디가 더 가까운지 비교할 때 쓴다. "
                       "check_hazards=true 면 '지금 출발하면' 안전한지 본다: 경로와 도착지의 위험 단계(예보·특보·침수 센서), "
                       "지금 피할 위험 구역을 지나는지와 우회하면 몇 분 더인지. 지금 이동할지 묻는 질문에는 꼭 켠다.", {
        "from_place_id": {"type": "string", "description": "출발 장소 (search_places 로 찾은 id)"},
        "to_place_ids": {"type": "array", "items": {"type": "string"}, "description": "도착 장소 1~10곳"},
        "mode": {"type": "string", "enum": ["both", "walk", "car"], "description": "기본 both (도보·차량 모두)"},
        "check_hazards": {"type": "boolean", "description": "지금 출발 기준 안전 확인 (도착 3곳까지)"},
    }, ["from_place_id", "to_place_ids"]),
    _fn("check_safety", "지금 초안 일정(방문 + 장소 사이 이동 구간)의 안전 단계를 본다 (침수 센서, 방파제, 기상 예보·특보). "
                        "safe 안전 / caution 주의 / danger 위험 / 판단 안 함(먼 날짜·데이터 장애). "
                        "날씨·안전을 묻거나 야외·해안 일정을 넣었을 때 쓴다. review_plan 도 위험 항목을 함께 알려준다."),
    _fn("safety_outlook", "특정 날짜의 구룡포 날씨·바다 전망 (예보가 없는 먼 날짜는 만조 시각과 계절 경향만). "
                          "\"그날 날씨 어때?\", \"비 와?\" 같은 질문에 쓴다.", {
        "date": {"type": "string", "description": "YYYY-MM-DD"},
    }, ["date"]),
    _fn("get_place", "장소 상세 (주소, 운영시간, 휴무일, 요금, 메뉴, 소개).", {
        "place_id": {"type": "string"},
    }, ["place_id"]),
    _fn("view_plan", "현재 일정(이번 대화에서 제안 중인 변경 포함)을 시간표와 위반 사항과 함께 본다."),
    _fn("review_plan", "지금 초안 일정을 검토해 문제 목록을 받는다: 운영시간·휴무, 종료 시각 초과, 긴 이동, "
                       "식사 시간에 식당 없음, 중복, 사용자 규칙 위반, 안전(위험·주의 방문과 이동). "
                       "일정을 바꾼 뒤 답하기 전에 반드시 호출하고, "
                       "문제를 고친 뒤 다시 호출해 확인한다."),
    _fn("add_item", "일정에 장소를 추가한다 (변경 제안에 포함, 사용자 승인 후 반영). "
                    "시각은 앞 일정에 이어 자동 계산된다. 시각 고정이 꼭 필요하면 추가 후 update_item 으로.", {
        "place_id": {"type": "string", "description": "search_places 로 찾은 id"},
        "day": {"type": "integer", "description": "n일차 (1부터, 기본 1)"},
        "order": {"type": "integer", "description": "그날 몇 번째 (1부터). 생략하면 맨 뒤"},
        "stay_minutes": {"type": "integer", "description": "체류 시간(분), 기본 60"},
        "memo": {"type": "string", "description": "추천 이유 등 짧은 메모"},
        "travel_mode": {"type": "string", "enum": ["walk", "car"], "description": MODE_DESC},
    }, ["place_id"]),
    _fn("update_item", "일정 항목을 수정한다: 순서 변경, 다른 날로 이동, 체류 시간, 시작 시각 고정/해제, 메모, 상태, "
                       "여기까지 오는 이동 수단.", {
        "item_id": {"type": "integer", "description": "view_plan 의 item_id (방금 추가한 항목은 음수 임시 id)"},
        "day": {"type": "integer"},
        "order": {"type": "integer"},
        "stay_minutes": {"type": "integer"},
        "start_time": {"type": "string", "description": "HH:MM 고정 (사용자가 요구할 때만), \"none\" 이면 고정 해제"},
        "memo": {"type": "string"},
        "status": {"type": "string", "enum": ["planned", "visiting", "done", "skipped"]},
        "travel_mode": {"type": "string", "enum": ["walk", "car", "default"],
                        "description": MODE_DESC + ". default 면 계획 기본 수단으로 되돌린다"},
    }, ["item_id"]),
    _fn("remove_item", "일정 항목을 삭제한다.", {"item_id": {"type": "integer"}}, ["item_id"]),
    _fn("update_plan", "계획 설정 변경: 제목, 기간, 하루 시작/종료 시각, 기본 이동 수단(구간마다 따로 정하지 않은 곳에 적용).", {
        "title": {"type": "string"},
        "start_date": {"type": "string", "description": "YYYY-MM-DD"},
        "end_date": {"type": "string", "description": "YYYY-MM-DD"},
        "day_start": {"type": "string", "description": "HH:MM"},
        "day_end": {"type": "string", "description": "HH:MM"},
        "travel_mode": {"type": "string", "enum": ["walk", "car"]},
    }),
    _fn("add_rule", "사용자의 조건을 규칙으로 등록한다. 이후 모든 변경이 이 규칙으로 검사된다. "
                    "must_include(place_id: 꼭 포함), exclude(place_id: 제외), "
                    "end_before(time, day?: 그 시각 전 종료), max_travel_minutes(minutes: 구간 이동시간 상한), "
                    "max_walk_minutes(minutes: 도보 구간 하나의 상한, 예: 어르신 동행), "
                    "max_daily_walk_minutes(minutes: 하루 도보 합계 상한).", {
        "type": {"type": "string", "enum": RULE_TYPES},
        "place_id": {"type": "string"},
        "time": {"type": "string", "description": "HH:MM (end_before)"},
        "day": {"type": "integer", "description": "end_before 적용 일차 (생략 시 매일)"},
        "minutes": {"type": "integer", "description": "max_travel_minutes / max_walk_minutes / max_daily_walk_minutes"},
        "hard": {"type": "boolean", "description": "반드시 지킬 조건이면 true(기본), 선호면 false"},
        "note": {"type": "string", "description": "사용자가 말한 이유"},
    }, ["type"]),
    _fn("remove_rule", "규칙을 삭제한다.", {"rule_id": {"type": "integer"}}, ["rule_id"]),
    _fn("discard_changes", "이번 대화에서 제안 중인 변경을 모두 취소하고 현재 확정 일정으로 되돌린다."),
    _fn("recommend_places", "사용자가 직접 골라 넣을 장소 후보를 추천 카드로 보여준다 (바로 표시됨). "
                            "'추천해줘', '갈 만한 곳 알려줘'처럼 사용자가 고르고 싶어 할 때 쓴다. "
                            "사용자가 카드에서 후보마다 [추가]를 누르면 일정에 들어간다.", {
        "title": {"type": "string", "description": "카드 제목 (예: 비 올 때 갈 만한 실내 장소)"},
        "candidates": {"type": "array", "description": "1~10개", "items": {"type": "object", "properties": {
            "place_id": {"type": "string", "description": "search_places 로 찾은 id"},
            "day": {"type": "integer", "description": "넣을 n일차 (기본 1)"},
            "time_hint": {"type": "string", "description": "이 시각 무렵에 넣으면 좋음 HH:MM (선택)"},
            "stay_minutes": {"type": "integer", "description": "체류 시간(분), 기본 60"},
            "reason": {"type": "string", "description": "추천 이유 한 줄"},
        }, "required": ["place_id"]}},
    }, ["title", "candidates"]),
]

# 도구 -> 처리 단계 종류
STEP_KIND = {
    "search_places": "search", "get_place": "place", "view_plan": "plan", "review_plan": "review",
    "check_safety": "safety", "safety_outlook": "safety", "travel_time": "route",
    "add_item": "edit", "update_item": "edit", "remove_item": "edit", "update_plan": "edit", "discard_changes": "edit",
    "add_rule": "rule", "remove_rule": "rule", "recommend_places": "recommend",
}


@dataclass
class Draft:
    """이번 턴의 제안 초안.
    base_proposal_id: 새 제안이 대체할 이전 대기 제안. continued: 그 제안의 ops 에서 이어서 시작했는지."""

    ops: list[dict] = field(default_factory=list)
    base_proposal_id: int | None = None
    continued: bool = False
    changed: bool = False


@dataclass
class ToolOutcome:
    ok: bool
    content: str  # LLM 에 돌려줄 내용 (JSON 문자열)
    detail: str  # 처리 단계에 보여줄 짧은 결과
    history: str | None = None  # 대화 기록에 남길 짧은 형태 (없으면 content). 일정 스냅샷이 기록에 쌓이지 않게
    card: dict | None = None  # 이 도구가 만든 카드 {"type", "<type>": 내용}


# ---------- LLM 에 보여줄 압축 표현 ----------

def compact_plan(plan: dict, changes: list[str] | None = None, pending: int = 0) -> dict:
    days = []
    for d in plan["days"]:
        items = []
        for i in d["items"]:
            it = {"item_id": i["id"], "order": i["order"],
                  "place": i["place"]["title"] if i["place"] else i["place_id"], "place_id": i["place_id"],
                  "time": "건너뜀" if i["status"] == "skipped" else f"{i['start_at']}~{i['end_at']}",
                  "stay": i["stay_minutes"]}
            if i["travel_minutes"] is not None and i.get("leg_mode"):
                # 이 구간 수단과 시간, 다른 수단이면 얼마나 걸리는지 (구간별 수단 선택용)
                mode = {"walk": "도보", "car": "차량"}
                opts = i.get("travel_options") or {}
                other = "car" if i["leg_mode"] == "walk" else "walk"
                it["travel"] = f"{mode[i['leg_mode']]} {i['travel_minutes']}분" + (
                    f" ({mode[other]}면 {opts[other]['minutes']}분)" if other in opts else "")
                if i.get("travel_mode"):
                    it["travel_mode_set"] = i["travel_mode"]
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


def compact_safety(check: dict) -> dict:
    """안전 점검 결과의 LLM 용 압축: 주의·위험과 참고 신호가 있는 항목만 자세히, 나머지는 개수로."""
    items, quiet, far, missing = [], 0, [], 0
    for i in check["items"]:
        if i["risk"] is None:
            if i["unassessed_reason"] == "beyond_forecast":
                far.append(i)
            elif i["unassessed_reason"] == "data_missing":
                missing += 1
            continue
        if i["risk"] == "safe" and not i["hazards"]:
            quiet += 1
            continue
        it = {"item_id": i["item_id"], "what": safety_what(i), "time": f"{i['start_at']}~{i['end_at']}",
              "risk": LEVEL[i["risk"]], "reasons": [f"[{'참고' if h['level'] == 'info' else LEVEL[h['level']]}] "
                                                   f"{h['reason']}" for h in i["hazards"][:3]]}
        if i["risk"] != "safe":
            it["advice"] = i["advice"]
            if i["better_times"]:
                it["better_times"] = [f"{w['start'][5:16].replace('T', ' ')}~{w['end'][11:16]}"
                                      for w in i["better_times"]]
            if i.get("detour"):
                it["detour"] = i["detour"]
            if i.get("data_missing"):
                it["note"] = "근거 일부를 받지 못해 실제로는 더 높을 수 있다"
        items.append(it)
    out = {"overall": LEVEL.get(check["overall_risk"], "판단 안 함"), "guidance": check["guidance"], "items": items}
    if quiet:
        out["safe_without_signals"] = f"나머지 {quiet}곳(방문·이동)은 신호 없음"
    if far:
        recheck = min((i["recheck_from"] for i in far if i.get("recheck_from")), default=None)
        out["not_assessed"] = (f"{len(far)}곳은 예보 범위 밖이라 판단하지 않음 (안전하다는 뜻 아님, 안전을 이유로 바꾸지 마라)"
                               + (f". {recheck}부터 판단 가능" if recheck else ""))
    if missing:
        out["data_missing"] = f"{missing}곳은 데이터를 받지 못해 판단하지 못함 (안전하다고 말하지 마라)"
    return out


def compact_outlook(o: dict) -> dict:
    out = {"date": o["date"], "horizon": o["horizon"], "guidance": o["guidance"]}
    if o.get("summary"):
        out["summary"] = o["summary"]
    if o["hazards"]:
        out["signals"] = [f"[{'참고' if h['level'] == 'info' else LEVEL[h['level']]}] {h['reason']}" for h in o["hazards"][:6]]
    if o["safe_windows"]:
        kinds = {"breakwater": "방파제", "flood": "해안 저지대", "outdoor": "야외"}
        out["ok_windows"] = [f"{kinds[w['place_kind']]} {w['start'][11:16]}~{w['end'][11:16]}" for w in o["safe_windows"]]
    if o["warnings"]:
        out["warnings"] = o["warnings"]
    if o["tides"]:
        out["tides"] = [f"{'만조' if t['kind'] == 'high' else '간조'} {t['time'][11:16]}" for t in o["tides"]]
    if o["seasonal_notes"]:
        out["seasonal"] = o["seasonal_notes"]
    return out


def total_travel(plan: dict) -> int:
    return sum(i["travel_minutes"] or 0 for d in plan["days"] for i in d["items"])


def _item_title(plan: dict, item_id: int) -> str:
    for d in plan["days"]:
        for i in d["items"]:
            if i["id"] == item_id:
                return i["place"]["title"] if i["place"] else i["place_id"]
    return f"항목 {item_id}"


def _place_title(plan: dict, place_id: str) -> str:
    for d in plan["days"]:
        for i in d["items"]:
            if i["place_id"] == place_id and i["place"]:
                return i["place"]["title"]
    return place_id


def _norm(text: str) -> str:
    """이름 비교용: 공백·기호 제거, 소문자."""
    return re.sub(r"[\s\W_]+", "", text or "").lower()


def _blank(v) -> bool:
    return v is None or str(v).strip().lower() in ("", "none", "null")


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


class Refused(Exception):
    """동행 모드에서 하지 않는 변경 (완료 항목, 지난 시각 등). LLM 에 이유를 돌려준다."""


class Toolbox:
    def __init__(self, api: ServiceApi, plan_id: int, draft: Draft, session_id: str):
        self.api = api
        self.plan_id = plan_id
        self.draft = draft
        self.session_id = session_id
        self.last_plan: dict | None = None  # 가장 최근의 (초안 적용) 일정 — 단계 문구·이동시간 변화 계산용
        self.rev = 0  # 초안이 바뀔 때마다 +1
        self.reviewed_rev = 0  # 마지막으로 검토한 초안의 rev
        self.trip: dict | None = None  # 동행 모드면 {"day": 오늘 n일차, "now": "HH:MM"} — 지난 일을 바꾸지 않게

    def _guard(self, op: dict) -> None:
        """동행 모드: 완료 항목·지난 시각·진행 상태는 바꾸지 않는다 (docs/agent/README.md §11.3)."""
        if not self.trip or not self.last_plan or op["op"] not in ("update_item", "remove_item"):
            return
        item = next((i for d in self.last_plan["days"] for i in d["items"] if i["id"] == op["item_id"]), None)
        if item and item["status"] == "done":
            raise Refused("이미 다녀온(완료) 항목은 바꾸거나 지울 수 없습니다.")
        if op["op"] == "remove_item":
            return
        if op.get("status") in ("visiting", "done"):
            raise Refused("진행 상태(visiting·done)는 앱이 위치로 기록합니다. 직접 바꾸지 마세요.")
        if item and item["status"] == "visiting" and set(op) - {"op", "item_id", "stay_minutes", "memo"}:
            raise Refused("지금 머무는 곳은 체류 시간(stay_minutes)과 메모만 바꿀 수 있습니다.")
        day = op.get("day") or (item["day"] if item else None)
        start = op.get("start_time")
        if start and day == self.trip["day"] and start < self.trip["now"]:
            raise Refused(f"지난 시각({start})으로 고정할 수 없습니다. 지금은 {self.trip['now']}입니다.")

    async def current_view(self) -> dict:
        """초안이 있으면 초안 적용 결과, 없으면 확정 일정 (LLM 용 압축 표현). last_plan 도 갱신한다."""
        if self.draft.ops:
            r = await self.api.preview(self.plan_id, self.draft.ops)
            self.last_plan = r["plan"]
            return compact_plan(r["plan"], [c["message"] for c in r["changes"]], len(self.draft.ops))
        self.last_plan = await self.api.get_plan(self.plan_id)
        return compact_plan(self.last_plan)

    async def call(self, name: str, raw_args: str) -> ToolOutcome:
        try:
            args = json.loads(raw_args or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments 는 JSON 객체여야 합니다")
        except ValueError as e:
            return ToolOutcome(False, _dump({"error": f"인자 JSON 을 해석할 수 없습니다: {e}"}), "인자 오류")
        args = {k: v for k, v in args.items()
                if v is not None or (name == "update_item" and k in ("start_time", "travel_mode"))}
        handler = getattr(self, f"_t_{name}", None)
        if handler is None:
            return ToolOutcome(False, _dump({"error": f"알 수 없는 도구: {name}"}), "알 수 없는 도구")
        try:
            return await handler(**args)
        except (TypeError, ValueError, KeyError) as e:
            return ToolOutcome(False, _dump({"error": f"인자가 잘못됐습니다: {e}"}), "인자 오류")
        except ApiError as e:
            return ToolOutcome(False, _dump({"error": e.message, "code": e.code}), e.message[:60])
        except Refused as e:
            return ToolOutcome(False, _dump({"error": str(e), "code": "not_allowed_during_trip"}), str(e)[:60])

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
        q = _norm(query)

        def view(p: dict) -> dict:
            it = {"place_id": p["id"], "title": p["title"], "type": p["content_type"], "area": p["area"]}
            if p.get("distance_m") is not None:
                it["distance_m"] = round(p["distance_m"])
            if q and q in _norm(p["title"]):
                it["name_match"] = True
            return it

        items = [view(p) for p in r["items"]]
        out: dict = {"items": items}
        outside: list[dict] = []
        if q and not any(i.get("name_match") for i in items) and (area or content_type or near_place_id):
            # 지역·종류·주변 조건 때문에 이름이 같은 장소가 빠졌을 수 있다 -> 조건 없이 이름으로 다시 찾는다
            again = await self.api.search_places(q=query, limit=5)
            outside = [view(p) for p in again["items"] if q in _norm(p["title"])]
        if outside:
            cond = ", ".join(x for x in (area, content_type, "주변 반경" if near_place_id else "") if x)
            out["name_matches_outside_filter"] = outside
            out["hint"] = (f"'{query}' 와 이름이 같은 장소는 검색 조건({cond}) 밖에 있다 (name_matches_outside_filter). "
                           "사용자가 말한 곳이 이것이면 이 place_id 를 쓴다. items 는 조건 안의 다른 장소다.")
        elif q and items and not any(i.get("name_match") for i in items):
            out["hint"] = ("이름이 일치하는 장소는 없다. items 는 뜻이 비슷한 장소다. 사용자가 특정 가게 이름을 말했다면 "
                           "조건(area 등) 없이 이름만으로 다시 찾아보고, 그래도 없으면 데이터에 없다고 말한다.")
        if not items and not outside:
            out["hint"] = "결과가 없습니다. 다른 표현이나 content_type/area 만으로 다시 찾아보세요."
            return ToolOutcome(True, _dump(out), "결과 없음")
        if outside:
            o = outside[0]
            return ToolOutcome(True, _dump(out), f"{o['title']} ({o['area']}, 조건 밖)")
        first = next((i for i in items if i.get("name_match")), items[0])
        detail = first["title"] + (f" 외 {len(items) - 1}곳" if len(items) > 1 else "")
        return ToolOutcome(True, _dump(out), detail)

    async def _t_get_place(self, place_id: str) -> ToolOutcome:
        p = await self.api.get_place(place_id)
        keys = ["title", "content_type", "area", "addr", "open_hours", "rest_date", "fee", "menu"]
        out = {"place_id": p["id"], **{k: p[k].strip() if isinstance(p[k], str) else p[k] for k in keys if p.get(k)}}
        if p.get("overview"):
            out["overview"] = p["overview"][:500]
        return ToolOutcome(True, _dump(out), p["title"])

    async def _t_view_plan(self) -> ToolOutcome:
        view = await self.current_view()
        count = sum(len(d["items"]) for d in view["days"])
        return ToolOutcome(True, _dump(view), f"일정 {count}곳",
                           history=_dump({"result": "(당시 일정 조회 결과 생략. 최신 일정은 시스템 프롬프트 참고)"}))

    async def _t_travel_time(self, from_place_id: str, to_place_ids: list[str], mode: str = "both",
                             check_hazards: bool = False) -> ToolOutcome:
        if isinstance(to_place_ids, str):
            to_place_ids = [to_place_ids]
        if not 1 <= len(to_place_ids) <= MAX_TRAVEL_TARGETS:
            raise ValueError(f"to_place_ids 는 1~{MAX_TRAVEL_TARGETS}곳")
        if mode not in ("both", "walk", "car"):
            raise ValueError("mode 는 both | walk | car")
        places = await asyncio.gather(*(self.api.get_place(p) for p in [from_place_id, *to_place_ids]))
        origin, dests = places[0], places[1:]
        pt = lambda p: {"lat": p["lat"], "lng": p["lng"]}  # noqa: E731
        modes = ["walk", "car"] if mode == "both" else [mode]
        tables = await asyncio.gather(*(self.api.routing_matrix(m, [pt(origin)], [pt(d) for d in dests]) for m in modes))
        results = [{"to": d["title"], "place_id": d["id"], "area": d.get("area")} for d in dests]
        for m, table in zip(modes, tables):
            for r, leg in zip(results, table["matrix"][0]):
                extra = CAR_OVERHEAD_MIN if m == "car" and leg["distance_m"] > 0 else 0
                r[m] = {"minutes": leg["minutes"] + extra, "distance_m": leg["distance_m"]}
                if leg["source"] != "road":
                    r[m]["approx"] = True  # 길찾기를 못 써 직선거리로 추정
        if check_hazards:
            hz_mode = "walk" if "walk" in modes else "car"
            routes = await asyncio.gather(*(self.api.routing_route(hz_mode, [pt(origin), pt(d)])
                                            for d in dests[:MAX_HAZARD_ROUTES]))
            for r, route in zip(results, routes):
                h = route["hazards"]
                if not h["checked"]:
                    r["route_zones"] = "확인하지 못함 (안전하다는 뜻 아님)"
                elif not h["on_route"] and not h["avoided"]:
                    r["route_zones"] = "경로에 지금 막아야 할 구역 없음"
                else:
                    r["route_zones"] = {"avoided": [z["name"] for z in h["avoided"]],
                                        "still_on_route": [f"{z['name']} ({z['reason']})" for z in h["on_route"]],
                                        "detour_minutes": h.get("detour_minutes"), "note": h.get("note")}
            await self._safety_now(origin, dests[:MAX_HAZARD_ROUTES], routes, hz_mode, results)
        out = {"from": origin["title"], "results": results,
               "note": "차량은 주차·승하차 5분 포함. approx 는 실제 길이 아닌 직선거리 추정. "
                       "safety_now 는 지금 출발했을 때 경로·도착지의 위험 단계 (예보·특보·침수 센서). "
                       "route_zones 는 길찾기가 지금 막는 구역(실측이 오른 센서 주변 80m, 방파제)만 본 것이라 "
                       "그것만으로 안전하다고 말하지 마라. 미래 일정은 check_safety 로."}
        first = results[0]
        if len(results) == 1:
            parts = [f"{'도보' if m == 'walk' else '차량'} {first[m]['minutes']}분" for m in modes]
            detail = f"{first['to']} {'·'.join(parts)}"
        else:
            detail = f"{origin['title']}에서 {len(results)}곳"
        return ToolOutcome(True, _dump(out), detail)

    async def _safety_now(self, origin: dict, dests: list[dict], routes: list[dict], mode: str,
                          results: list[dict]) -> None:
        """지금 출발해 각 도착지로 가면: 이동 경로(경로선)와 도착 후 1시간 방문을 safety 로 평가해 results 에 붙인다."""
        try:
            now = datetime.fromisoformat((await self.api.safety_situation())["now"])
            visits = []
            for n, (d, route) in enumerate(zip(dests, routes)):
                coords = [(lat, lng) for lng, lat in route["geometry"]["coordinates"]]
                if len(coords) < 2:
                    coords = [(origin["lat"], origin["lng"]), (d["lat"], d["lng"])]
                step = max(1, len(coords) // 2000)
                coords = coords[::step] + ([coords[-1]] if (len(coords) - 1) % step else [])
                arrive = now + timedelta(minutes=max(1, route["minutes"]))
                leg = {"key": f"t{n}", "path": [list(c) for c in coords], "mode": mode, "date": now.date().isoformat(),
                       "start": f"{now:%H:%M}", "label": f"{origin['title']} → {d['title']}"}
                if arrive.date() == now.date() and arrive > now:
                    leg["end"] = f"{arrive:%H:%M}"
                visits.append(leg)
                visits.append({"key": f"v{n}", "place_id": d["id"], "lat": d["lat"], "lng": d["lng"], "label": d["title"],
                               "date": arrive.date().isoformat(), "start": f"{arrive:%H:%M}"})
            res = {v["key"]: v for v in (await self.api.safety_assess(visits))["visits"]}
        except ApiError:
            for r in results[:len(dests)]:
                r["safety_now"] = "안전 정보를 확인하지 못함 (안전하다는 뜻 아님)"
            return

        def brief(v: dict) -> dict:
            out = {"risk": LEVEL.get(v["risk"], "판단 안 함"),
                   "reasons": [f"[{'참고' if h['level'] == 'info' else LEVEL[h['level']]}] {h['reason']}"
                               for h in v["hazards"][:3]]}
            if v["risk"] in ("caution", "danger"):
                out["advice"] = v["advice"]
            return out

        for n, r in enumerate(results[:len(dests)]):
            r["safety_now"] = {"depart": f"{now:%H:%M}", "route": brief(res[f"t{n}"]),
                               "destination": brief(res[f"v{n}"])}

    async def _t_check_safety(self) -> ToolOutcome:
        check = await self.api.check_safety(self.plan_id, self.draft.ops)
        out = compact_safety(check)
        risky = [i for i in check["items"] if i["risk"] in ("caution", "danger")]
        if risky:
            worst = "위험" if any(i["risk"] == "danger" for i in risky) else "주의"
            detail = f"{worst} 포함 {len(risky)}곳"
        elif any(i["risk"] is None and i["unassessed_reason"] != "past" for i in check["items"]):
            detail = "판단할 수 없는 일정 있음"
        elif not any(i["unassessed_reason"] != "past" for i in check["items"]):
            detail = "점검할 앞으로의 일정 없음"
        else:
            detail = "위험 신호 없음"
        return ToolOutcome(True, _dump(out), detail)

    async def _t_safety_outlook(self, date: str) -> ToolOutcome:
        o = await self.api.safety_outlook(date)
        out = compact_outlook(o)
        worst = max((h["level"] for h in o["hazards"]), key=["info", "caution", "danger"].index, default=None)
        if o["horizon"] in ("medium_range", "long_range"):
            detail = f"{date[5:]} 예보 전 (만조·계절 경향만)"
        else:
            detail = f"{date[5:]} " + ({"danger": "위험 신호", "caution": "주의 신호", "info": "참고 신호"}.get(worst, "특이사항 없음"))
        return ToolOutcome(True, _dump(out), detail)

    # ----- 변경 (초안에 쌓음) -----
    async def review_now(self) -> list[dict]:
        """지금 초안을 검토 (하네스의 검토 관문도 이걸 쓴다). 일정 규칙 검토 + 안전 점검(위험·주의)."""
        await self.current_view()
        self.reviewed_rev = self.rev
        issues = review(self.last_plan)
        try:
            issues += safety_issues(await self.api.check_safety(self.plan_id, self.draft.ops))
        except ApiError:
            issues.append({"level": "info", "kind": "safety",
                           "message": "안전 정보를 확인하지 못했다 (서비스 장애). 안전하다고 단정하지 않는다."})
        return issues

    async def _t_review_plan(self) -> ToolOutcome:
        issues = await self.review_now()
        blocking = [i for i in issues if i["level"] in ("must", "should")]
        result = ("문제 없음. 답변해도 된다." if not blocking else
                  "must 는 반드시, should 는 가능하면 고친 뒤 review_plan 으로 다시 확인한다. "
                  "고칠 수 없는 문제는 답변에서 이유를 짧게 말한다.")
        return ToolOutcome(True, _dump({"result": result, "issues": issues}), summarize(issues))

    async def _stage(self, op: dict, label: str, notice: str = "") -> ToolOutcome:
        self._guard(op)
        candidate = [*self.draft.ops, op]
        r = await self.api.preview(self.plan_id, candidate)  # 적용 불가면 ApiError -> 초안에 넣지 않음
        before_travel = total_travel(self.last_plan) if self.last_plan else None
        self.draft.ops = candidate
        self.draft.changed = True
        self.rev += 1
        self.last_plan = r["plan"]
        changes = [c["message"] for c in r["changes"]]
        view = compact_plan(r["plan"], changes, len(candidate))
        note = "변경 제안 초안에 반영됨 (아직 실제 일정 아님. 응답이 끝나면 사용자에게 승인 요청됨)" + notice
        short = {"result": note, "changes": changes, "violations": view["violations"]}
        delta = total_travel(r["plan"]) - before_travel if before_travel is not None else 0
        detail = label + (f" · 총 이동 {delta:+d}분" if delta else "")
        return ToolOutcome(True, _dump({"result": note, "plan_after": view}), detail, history=_dump(short))

    async def _t_add_item(self, place_id: str, day: int = 1, order: int | None = None, stay_minutes: int = 60,
                          memo: str = "", travel_mode: str | None = None, **ignored) -> ToolOutcome:
        # 모델이 없는 인자(start_time, near_place_id 등)를 붙여도 실패시키지 않되, 무시했다고 알려준다
        op = {"op": "add_item", "place_id": place_id, "day": day, "order": order, "stay_minutes": stay_minutes,
              "memo": memo}
        if travel_mode and not _blank(travel_mode) and travel_mode != "default":
            op["travel_mode"] = travel_mode
        notice = ""
        if self.last_plan and 1 <= day <= len(self.last_plan["days"]) and any(
                i["place_id"] == place_id for i in self.last_plan["days"][day - 1]["items"]):
            notice = f". 주의: 이 장소는 이미 {day}일차 일정에 있어 중복으로 들어갔다 (의도가 아니면 remove_item)"
        if ignored:
            notice += f". 무시된 인자: {sorted(ignored)}"
            if "start_time" in ignored:
                notice += " (시작 시각 고정은 update_item 으로만 할 수 있다)"
        outcome = await self._stage(op, "", notice)
        title = _place_title(self.last_plan, place_id)
        outcome.detail = f"{title} 추가" + outcome.detail
        return outcome

    async def _t_update_item(self, item_id: int, **changes) -> ToolOutcome:
        allowed = {"day", "order", "stay_minutes", "start_time", "memo", "status", "travel_mode"}
        unknown = set(changes) - allowed
        if unknown:
            raise TypeError(f"알 수 없는 인자 {sorted(unknown)}")
        if not changes:
            raise TypeError("바꿀 값을 하나 이상 지정하세요")
        if "start_time" in changes and _blank(changes["start_time"]):
            changes["start_time"] = None
        if "travel_mode" in changes and (changes["travel_mode"] == "default" or _blank(changes["travel_mode"])):
            changes["travel_mode"] = None
        outcome = await self._stage({"op": "update_item", "item_id": item_id, **changes}, "")
        outcome.detail = f"{_item_title(self.last_plan, item_id)} 수정" + outcome.detail
        return outcome

    async def _t_remove_item(self, item_id: int) -> ToolOutcome:
        title = _item_title(self.last_plan, item_id) if self.last_plan else f"항목 {item_id}"
        return await self._stage({"op": "remove_item", "item_id": item_id}, f"{title} 삭제")

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
        outcome = await self._stage(op, "")
        name = _place_title(self.last_plan, params.get("place_id", "")) if "place_id" in params else ""
        outcome.detail = {
            "must_include": f"'{name}' 꼭 포함", "exclude": f"'{name}' 제외",
            "end_before": f"{params.get('time')} 전 종료", "max_travel_minutes": f"이동 {params.get('minutes')}분 이내",
            "max_walk_minutes": f"도보 구간 {params.get('minutes')}분 이내",
            "max_daily_walk_minutes": f"하루 도보 {params.get('minutes')}분 이내",
        }.get(type, type)
        return outcome

    async def _t_remove_rule(self, rule_id: int) -> ToolOutcome:
        return await self._stage({"op": "remove_constraint", "constraint_id": rule_id}, "조건 삭제")

    async def _t_discard_changes(self) -> ToolOutcome:
        self.draft.ops = []
        self.draft.changed = True
        self.rev += 1
        view = await self.current_view()
        return ToolOutcome(True, _dump({"result": "제안 중인 변경을 모두 취소했습니다.", "plan": view}), "변경 취소",
                           history=_dump({"result": "제안 중인 변경을 모두 취소했습니다."}))

    # ----- 추천 카드 (바로 생성) -----
    async def _t_recommend_places(self, title: str, candidates: list) -> ToolOutcome:
        if not isinstance(candidates, list) or not candidates:
            raise ValueError("candidates 는 1개 이상인 목록이어야 합니다")
        cleaned = []
        for c in candidates:
            if not isinstance(c, dict) or "place_id" not in c:
                raise ValueError("각 후보는 place_id 가 있는 객체여야 합니다")
            keep = {k: c[k] for k in ("place_id", "day", "time_hint", "stay_minutes", "reason")
                    if not _blank(c.get(k))}
            keep.setdefault("day", 1)
            cleaned.append(keep)
        rec = await self.api.create_recommendation(self.plan_id, str(title)[:100] or "추천", cleaned, self.session_id)
        # place_id 까지 남겨야 다음 턴에 "이렇게 코스 짜줘"를 다시 검색하지 않고 바로 처리할 수 있다
        summary = [{"place_id": c["place_id"], "title": (c["place"] or {}).get("title", c["place_id"]), "day": c["day"],
                    **({"time_hint": c["time_hint"]} if c["time_hint"] else {}), "stay_minutes": c["stay_minutes"],
                    "state": c["state"]} for c in rec["candidates"]]
        content = {"result": "추천 카드를 사용자에게 보여줬다. 사용자가 원하는 후보를 직접 추가한다 (승인 절차 없음).",
                   "candidates": summary}
        return ToolOutcome(True, _dump(content), f"{len(rec['candidates'])}곳",
                           card={"type": "recommendation", "recommendation": rec})


def tool_specs(review_enabled: bool) -> list[dict]:
    return TOOL_SPECS if review_enabled else [t for t in TOOL_SPECS if t["function"]["name"] != "review_plan"]
