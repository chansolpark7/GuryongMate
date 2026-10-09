"""trip 테스트용 가짜 planner·safety·agent (trip 은 HTTP 로만 부르므로 같은 메서드를 가진 객체로 바꾼다).

가짜 planner 는 실제 planner 의 시간 모델(docs/planner/README.md §2.1, §8.2)을 단순하게 흉내 낸다:
도보 이동시간 = 직선거리 × 1.3 / 70m, 차량 = 직선거리 × 1.3 / 400m + 주차 5분 (분 올림), 운영시간 밖·하루 초과·고정 시각 놓침만 본다.
도착지에 road(실제 길 / 직선 비율)가 있으면 1.3 대신 그 값 (항구를 돌아가는 방파제, 해안도로로 돌아가는 카페 — 실제 길찾기 거리로 맞춤).
"""
import math
from copy import deepcopy

from app.trip.clients import Clients, UpstreamError
from app.trip.geo import distance_m

# 장소 (실제 places 데이터의 좌표·운영시간). outdoor: storm 때 주의 / flood: 침수 감시 구역 반경 안 /
# breakwater: 방파제 (storm 의 풍랑 특보로 위험. 북방파제는 places 에 직접 정의한 장소, data/places/manual)
PLACES = {
    "kto-2834330": {"title": "짬홍", "lat": 35.9913466179, "lng": 129.558841269, "open": ("10:00", "20:00"), "flood": True},
    "kto-2610501": {"title": "구룡포항", "lat": 35.9893051205, "lng": 129.555641609, "outdoor": True},
    "kto-1909659": {"title": "구룡포 일본인 가옥거리", "lat": 35.9906209551, "lng": 129.5604550366, "outdoor": True},
    "kto-2660046": {"title": "구룡포과메기문화관", "lat": 35.9927402978, "lng": 129.5617992377, "open": ("09:00", "18:00")},
    "kto-2603493": {"title": "구룡포 근대문화역사관", "lat": 35.9905, "lng": 129.5616, "open": ("10:00", "17:30")},
    "kto-127214": {"title": "구룡포해수욕장", "lat": 35.9968322529, "lng": 129.5662849025, "open": ("08:00", "20:00"),
                   "outdoor": True},
    "kto-2834344": {"title": "카페파도", "lat": 35.9768342269, "lng": 129.5514895569, "open": ("10:00", "21:00"),
                    "road": 2.28},  # 북방파제에서 차량 3265m (직선 1429m)
    "poi-gr-breakwater-north": {"title": "구룡포 북방파제", "lat": 35.988078, "lng": 129.559279, "outdoor": True,
                                "breakwater": True, "road": 1.56},  # 과메기문화관에서 도보 886m (직선 566m)
}

# safety mock 의 침수 센서 (구룡포수협 스마트맨홀, 구룡포파출소 지표면 수위계)
SENSORS = {"flood_4": ("구룡포수협_스마트맨홀 주변", 35.991235, 129.557327),
           "flood_8": ("구룡포파출소_지표면 수위계 주변", 35.988698, 129.553056)}


def hm(s: str) -> int:
    h, m = s.split(":")[:2]
    return int(h) * 60 + int(m)


def fmt(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def walk_min(a: dict, b: dict) -> int:
    return math.ceil(distance_m(a["lat"], a["lng"], b["lat"], b["lng"]) * b.get("road", 1.3) / 70)


def leg_min(a: dict, b: dict, mode: str | None) -> int:
    if mode == "car":
        return math.ceil(distance_m(a["lat"], a["lng"], b["lat"], b["lng"]) * b.get("road", 1.3) / 400) + 5
    return walk_min(a, b)


def circle(lat: float, lng: float, r: float, n: int = 16) -> list[list[float]]:
    k = 111_320 * math.cos(math.radians(lat))
    pts = [[lng + r * math.cos(2 * math.pi * i / n) / k, lat + r * math.sin(2 * math.pi * i / n) / 111_320]
           for i in range(n)]
    return pts + [pts[0]]


class FakePlanner:
    def __init__(self):
        self.plans: dict[int, dict] = {}
        self.calls: list[tuple] = []
        self.alerts: dict[int, list[dict]] = {}
        self.down = False
        self._next_id = 100
        self._alert_id = 1

    # ----- 준비 (테스트) -----
    def create(self, user: str, plan: dict) -> int:
        """plan: 재생 파일의 "plan" 블록 {title, start_date, day_start, day_end, items: [{place_id, stay_minutes, start_time?}]}"""
        pid = self._next_id
        self._next_id += 1
        items = []
        for n, it in enumerate(plan["items"], start=1):
            items.append({"id": pid * 100 + n, "order": n, "place_id": it["place_id"], "stay": it.get("stay_minutes", 60),
                          "start_time": it.get("start_time"), "mode": it.get("travel_mode"), "status": "planned",
                          "arrived_at": None, "done_at": None})
        self.plans[pid] = {"id": pid, "user": user, "title": plan["title"], "start_date": plan["start_date"],
                           "end_date": plan.get("end_date", plan["start_date"]), "day_start": plan["day_start"],
                           "day_end": plan["day_end"], "items": items}
        return pid

    def item(self, plan_id: int, order: int) -> dict:
        return self.plans[plan_id]["items"][order - 1]

    def skip(self, plan_id: int, place_id: str) -> None:
        """사용자가 그 장소 항목을 건너뜀으로 바꿈 (PATCH status=skipped)."""
        it = next(i for i in self.plans[plan_id]["items"] if i["place_id"] == place_id and i["status"] == "planned")
        it["status"] = "skipped"

    def replace(self, plan_id: int, remove: str, add: str, stay: int = 40) -> None:
        """사용자가 선제 턴의 교체 제안(remove 장소 -> add 장소, 같은 순서)을 승인함."""
        it = next(i for i in self.plans[plan_id]["items"] if i["place_id"] == remove and i["status"] == "planned")
        it["place_id"], it["stay"] = add, stay

    def add_alert(self, plan_id: int, kind: str, changes: list[tuple[int, str, str]]) -> dict:
        """changes: (item_id, from_risk, to_risk)."""
        p = self.plans[plan_id]
        a = {"id": self._alert_id, "plan_id": plan_id, "level": "caution", "status": "new",
             "needs_action": kind == "raised", "situation": "fp", "message": "", "changes": []}
        self._alert_id += 1
        for item_id, frm, to in changes:
            it = next(i for i in p["items"] if i["id"] == item_id)
            place = PLACES[it["place_id"]]
            a["changes"].append({"kind": kind, "from_risk": frm, "to_risk": to, "item": {
                "item_id": item_id, "kind": "visit", "day": 1, "date": p["start_date"], "place_id": it["place_id"],
                "title": place["title"], "start_at": "13:00", "end_at": "14:00", "risk": to,
                "hazards": [{"type": "warning", "level": to, "reason": "호우·강풍 특보", "source": "kma"}]}})
        self.alerts.setdefault(plan_id, []).append(a)
        return a

    def on_scenario(self, scenario: str) -> None:
        """safety 가상 상황이 바뀌면 planner 주기 점검이 남길 경고를 흉내 낸다 (storm/flood: 남은 항목 위험 상승)."""
        if scenario not in ("storm", "flood"):
            return
        for pid, p in self.plans.items():
            changes = []
            for it in p["items"]:
                if it["status"] in ("done", "skipped"):
                    continue
                place = PLACES[it["place_id"]]
                if place.get("flood") or (place.get("breakwater") and scenario == "storm"):
                    changes.append((it["id"], "safe", "danger"))
                elif place.get("outdoor") and scenario == "storm":
                    changes.append((it["id"], "safe", "caution"))
            if changes:
                self.add_alert(pid, "raised", changes)

    # ----- 시간 모델 -----
    def _schedule(self, p: dict, actual: dict | None = None) -> tuple[list[dict], list[dict]]:
        """(항목별 {arrive, start, end}, 위반). actual: {now, anchor(order) | None, moving: (lat, lng) | None}."""
        items = p["items"]
        rows: list[dict] = []
        vs: list[dict] = []
        t = hm(p["day_start"])
        anchor = actual.get("anchor") if actual else None
        moving = actual.get("moving") if actual else None
        now = actual["now"] if actual else None
        # 기준점(머무는 곳 / 향하는 곳) 앞은 지난 항목
        pivot = None
        if actual:
            if anchor is not None:
                pivot = anchor
            else:
                last = max((i["order"] for i in items if i["status"] in ("done", "visiting")), default=0)
                pivot = next((i["order"] for i in items if i["order"] > last and i["status"] == "planned"), None)
        prev = None  # 직전에 다녀오는(건너뛰지 않은) 장소
        for it in items:
            place = PLACES[it["place_id"]]
            if it["status"] == "skipped":  # 시간을 쓰지 않는다
                rows.append({"id": it["id"], "arrive": t, "start": t, "end": t})
                continue
            past = actual is not None and pivot is not None and it["order"] < pivot
            if past:
                arrive = hm(it["arrived_at"]) if it["arrived_at"] else t + (leg_min(prev, place, it["mode"]) if prev else 0)
                end = hm(it["done_at"]) if it["done_at"] else arrive + it["stay"]
                rows.append({"id": it["id"], "arrive": arrive, "start": arrive, "end": end})
                t, prev = end, place
                continue
            if actual and it["order"] == pivot and anchor is not None:
                arrive = actual["arrived"]
                end = max(arrive + it["stay"], now)
                rows.append({"id": it["id"], "arrive": arrive, "start": arrive, "end": end})
                self._check_open(place, it, arrive, end, vs)
                t, prev = end, place
                continue
            if actual and it["order"] == pivot and moving is not None:
                arrive = now + leg_min({"lat": moving[0], "lng": moving[1]}, place, it["mode"])
            else:
                if actual and pivot is not None and it["order"] == pivot:
                    t = max(t, now)
                arrive = t + (leg_min(prev, place, it["mode"]) if prev else 0)
            start = max(arrive, hm(it["start_time"])) if it["start_time"] else arrive
            if it["start_time"] and arrive > hm(it["start_time"]):
                vs.append({"code": "fixed_time_missed", "severity": "error", "day": 1, "item_id": it["id"],
                           "constraint_id": None, "message": f"{place['title']} 고정 시각을 놓쳐요."})
            end = start + it["stay"]
            rows.append({"id": it["id"], "arrive": arrive, "start": start, "end": end})
            self._check_open(place, it, start, end, vs)
            t, prev = end, place
        if rows and rows[-1]["end"] > hm(p["day_end"]):
            vs.append({"code": "day_overflow", "severity": "error", "day": 1, "item_id": rows[-1]["id"],
                       "constraint_id": None, "message": f"일정이 {fmt(rows[-1]['end'])}에 끝나 종료 목표 {p['day_end']}를 넘어요."})
        return rows, vs

    @staticmethod
    def _check_open(place: dict, it: dict, start: int, end: int, vs: list[dict]) -> None:
        if "open" in place and (start < hm(place["open"][0]) or end > hm(place["open"][1])):
            vs.append({"code": "outside_open_hours", "severity": "warning", "day": 1, "item_id": it["id"],
                       "constraint_id": None,
                       "message": f"{place['title']} 방문({fmt(start)}~{fmt(end)})이 운영시간({place['open'][0]}~{place['open'][1]}) 밖이에요."})

    def _detail(self, p: dict, rows: list[dict], vs: list[dict]) -> dict:
        by = {r["id"]: r for r in rows}
        items = []
        for it in p["items"]:
            r, place = by[it["id"]], PLACES[it["place_id"]]
            items.append({"id": it["id"], "day": 1, "order": it["order"], "place_id": it["place_id"],
                          "place": {"id": it["place_id"], "title": place["title"], "lat": place["lat"], "lng": place["lng"]},
                          "stay_minutes": it["stay"], "start_time": it["start_time"], "status": it["status"],
                          "leg_mode": (it["mode"] or "walk") if it["order"] > 1 else None, "arrive_at": fmt(r["arrive"]),
                          "start_at": fmt(r["start"]), "end_at": fmt(r["end"]), "arrived_at": it["arrived_at"],
                          "done_at": it["done_at"]})
        return {"id": p["id"], "title": p["title"], "start_date": p["start_date"], "end_date": p["end_date"],
                "day_start": p["day_start"], "day_end": p["day_end"], "version": 1,
                "days": [{"day": 1, "date": p["start_date"], "end_at": fmt(rows[-1]["end"]) if rows else None,
                          "items": items}], "violations": vs}

    # ----- PlannerClient -----
    def _plan(self, plan_id: int) -> dict:
        self.calls.append(("get", plan_id))
        if self.down:
            raise UpstreamError("planner", None, "down")
        if plan_id not in self.plans:
            raise UpstreamError("planner", 404, {"code": "plan_not_found", "message": "없음"})
        return self.plans[plan_id]

    def get_plan(self, plan_id, auth):
        p = self._plan(plan_id)
        return self._detail(p, *self._schedule(p))

    def progress(self, plan_id, item_id, status, at, auth):
        p = self._plan(plan_id)
        self.calls.append(("progress", item_id, status, at))
        it = next(i for i in p["items"] if i["id"] == item_id)
        it["status"] = status
        if status == "visiting":
            it["arrived_at"], it["done_at"] = at, None
        elif status == "done":
            it["done_at"] = at
        return self.get_plan(plan_id, auth)

    def projection(self, plan_id, body, auth):
        p = self._plan(plan_id)
        self.calls.append(("projection", deepcopy(body)))
        now = hm(body["now"])
        pos = body.get("position") or {}
        actual: dict = {"now": now, "anchor": None, "moving": None}
        if pos.get("state") == "visiting":
            it = next(i for i in p["items"] if i["id"] == pos["item_id"])
            actual["anchor"], actual["arrived"] = it["order"], hm(pos.get("arrived_at") or it["arrived_at"] or body["now"])
        elif pos.get("state") == "moving":
            actual["moving"] = (pos["lat"], pos["lng"])
        else:
            visiting = [i for i in p["items"] if i["status"] == "visiting"]
            if visiting:
                actual["anchor"], actual["arrived"] = visiting[-1]["order"], hm(visiting[-1]["arrived_at"] or body["now"])
        base_rows, base_vs = self._schedule(p)
        rows, vs = self._schedule(p, actual)
        base_keys = {(v["code"], v["item_id"]) for v in base_vs}
        new = [v for v in vs if (v["code"], v["item_id"]) not in base_keys]
        # 다음 항목: 머무는 곳 뒤의 첫 planned / 이동 중이면 향하는 곳 / 아니면 첫 planned
        if actual["anchor"] is not None:
            nxt = next((i for i in p["items"] if i["order"] > actual["anchor"] and i["status"] == "planned"), None)
        else:
            last = max((i["order"] for i in p["items"] if i["status"] in ("done", "visiting")), default=0)
            nxt = next((i for i in p["items"] if i["order"] > last and i["status"] == "planned"), None)
        by, base = {r["id"]: r for r in rows}, {r["id"]: r for r in base_rows}
        if nxt:
            delay = by[nxt["id"]]["start"] - base[nxt["id"]]["start"]
            nxt_view = {"item_id": nxt["id"], "planned_start": fmt(base[nxt["id"]]["start"]),
                        "eta": fmt(by[nxt["id"]]["arrive"]), "start_at": fmt(by[nxt["id"]]["start"])}
        else:
            delay, nxt_view = rows[-1]["end"] - base_rows[-1]["end"], None
        return {"day": 1, "now": body["now"], "plan": self._detail(p, rows, vs), "baseline_violations": base_vs,
                "new_violations": new, "delay_minutes": delay, "next": nxt_view}

    def safety_alerts(self, plan_id, auth):
        self._plan(plan_id)
        return [a for a in reversed(self.alerts.get(plan_id, [])) if a["status"] == "new"]

    def alert_seen(self, plan_id, alert_id, auth):
        self.calls.append(("seen", alert_id))
        for a in self.alerts.get(plan_id, []):
            if a["id"] == alert_id:
                a["status"] = "seen"


class FakeSafety:
    def __init__(self):
        self.scenario = "calm"
        self.down = False

    def situation(self):
        if self.down:
            raise UpstreamError("safety", None, "down")
        return {"fingerprint": self.scenario}

    def avoid(self):
        zones = []
        if self.scenario in ("storm", "flood"):
            for zid, (name, lat, lng) in SENSORS.items():
                zones.append({"id": zid, "name": name, "kind": "flood", "risk": "danger" if zid == "flood_4" else "caution",
                              "reason": f"{name} 수위 상승", "polygon": circle(lat, lng, 80)})
        return {"zones": zones}


class FakeAgent:
    def __init__(self):
        self.sessions: dict[int, list[str]] = {}
        self.received: list[tuple[str, dict, dict]] = []
        self.busy = False  # 409 agent_busy
        self.missing = False  # 엔드포인트가 아직 없음 (404 Not Found)
        self.during_send = None  # 보내는 도중 실행할 함수 (동시 전송 확인)

    def latest_session(self, plan_id, auth):
        s = self.sessions.get(plan_id)
        return s[-1] if s else None

    def create_session(self, plan_id, auth):
        sid = f"sess-{plan_id}-{len(self.sessions.get(plan_id, []))}"
        self.sessions.setdefault(plan_id, []).append(sid)
        return sid

    def send_event(self, session_id, event, trip, auth):
        if self.missing:
            raise UpstreamError("agent", 404, "Not Found")
        if self.busy:
            raise UpstreamError("agent", 409, {"code": "agent_busy", "message": "응답 중"})
        if self.during_send:
            fn, self.during_send = self.during_send, None
            fn()
        self.received.append((session_id, event, trip))
        return {"text": "제안드려요", "message_id": 500 + len(self.received),
                "cards": [{"type": "proposal", "proposal": {"id": 900 + len(self.received)}}], "error": None}


def fakes() -> Clients:
    return Clients(FakePlanner(), FakeSafety(), FakeAgent())
