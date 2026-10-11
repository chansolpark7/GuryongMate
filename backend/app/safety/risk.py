"""수집한 데이터를 '이 시각, 이 장소가 위험한가'로 해석한다. 외부 호출 없는 순수 함수만 둔다.

위험 단계는 3단계다: safe(안전) / caution(주의) / danger(위험).
- 근거(Hazard)의 등급은 info(참고) / caution / danger. info 는 단계를 올리지 않지만 사람·LLM 이 일정을 조정할 때
  참고하도록 그대로 돌려준다 (예: 약한 비, 실내라 이동할 때만 영향).
- 내부 계산은 ORDER = none < info < caution < danger 로 하고, 응답에서 none·info 를 safe 로 바꾼다 (public).

핵심 원칙
- 예보는 시점이 멀수록 의미가 없다. 단기예보(약 3일)가 없는 시점은 날씨 위험을 판단하지 않는다 (risk=null).
  판단하지 않음은 안전하다는 뜻이 아니며, 그 시점에는 안전을 이유로 일정을 바꾸지 말라고 안내한다.
- 판단 근거가 없으면(데이터 장애) safe 가 아니라 판단하지 않음.
- 임계값 중 E-UM 등급 해석과 방파제 기준은 공식 기준이 없어 우리가 정한 가정이다 (ASSUMPTIONS).
"""
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.safety import geo
from app.safety.schemas import Confidence, Hazard, Horizon, Level
from app.safety.sources import (
    ActiveWarning, BreakwaterHour, ForecastHour, SensorReading, TideEvent,
)

ORDER = ["none", "info", "caution", "danger"]  # 내부 신호 등급. none·info 는 응답에서 safe
LEVELS = ["safe", "caution", "danger"]
NOW_WINDOW = timedelta(hours=3)  # 이 안이면 실측 센서를 그대로 적용
# 특보는 '지금' 상태다. 가까운 시각에만 그대로 적용하고, 그 뒤는 예보로 판단한다 (특보는 참고로만 남긴다)
WARNING_FULL = timedelta(hours=6)
WARNING_WINDOW = timedelta(hours=24)
TOUR_HOURS = (8, 19)  # 다른 시간대를 찾을 때 볼 관광 시간대 (시)
# places 의 장소 종류 중 실내로 보는 것. 관광지·레포츠(해수욕장·캠핑장 등)·축제는 야외로 본다
INDOOR_TYPES = {"문화시설", "음식점", "쇼핑", "숙박"}
INDOOR_NOTE = "실내라 이동할 때만 영향"
SHORT_RANGE_NOMINAL = timedelta(days=3)  # 예보 조회가 실패했을 때 단기예보 범위로 간주하는 기간
MEDIUM_LIMIT = timedelta(days=10)  # 중기예보 범위. 아직 연동하지 않아 '예보 없음'으로 다룬다
RECHECK_LEAD_DAYS = 3  # 여행일 며칠 전부터 단기예보로 판단 가능한지

# (기준값, 등급). 위에서부터 처음 넘는 값
RAIN_MM = [(30, "danger"), (10, "caution"), (3, "info")]  # 시간당 강수량. 30mm/h 는 기상청 '매우 강한 비'
WIND_MS = [(14, "danger"), (9, "caution")]  # 방파제·해안 기준. 14m/s 는 육상 강풍주의보 기준
WAVE_M = [(3.0, "danger"), (1.5, "caution")]  # 방파제·해안 기준. 3m 는 풍랑주의보 기준

# 해안: 바다와 맞닿은 야외 (항구·포구, 해수욕장·해변, 해안 산책로, 등대 …). 파도가 넘치고 물보라가 치는 곳이라
# 바람·파고를 방파제와 같은 기준으로 본다 (그 밖의 야외처럼 낮추지 않는다). 비는 그 밖의 야외와 같다.
# 판단 순서: places 분류(category_names, 한국관광공사 분류의 마지막 단계) -> 장소 이름 -> 좌표(해안선 30m, geo.near_coast)
COAST_CATEGORIES = {
    "항구/포구": "항구", "해변. 해수욕장": "해수욕장", "해안절경": "해안절경", "등대": "등대",
    "방파제": "방파제",  # 구룡포 북·남방파제 밖의 방파제 (두 방파제는 좌표로 방파제 구역이 된다)
    "바다낚시": "바다낚시", "윈드서핑/제트스키": "해상 레저",
}
# 분류로 모를 때 이름으로 본다. '항' 으로 끝나는 이름은 쓰지 않는다 ('포항' 이 들어간 이름이 많다)
COAST_TITLE_WORDS = {"해수욕장": "해수욕장", "해변": "해변", "포구": "포구", "등대": "등대", "방파제": "방파제",
                     "해안": "해안 산책로", "낚시공원": "바다낚시공원"}

ASSUMPTIONS = [
    "E-UM 수위 등급: 1 평상시, 2 주의, 3 이상 위험으로 해석 (E-UM 이 기준을 공개하지 않음)",
    "방파제: 풍속 9m/s·파고 1.5m 이상 주의, 14m/s·3m 이상 위험 (특보 기준을 참고해 정한 값)",
    "해안(항구·해수욕장·해안 산책로·등대 등 바다와 맞닿은 야외, 또는 해안선 30m 안): 풍속·파고를 방파제와 같은 기준으로 본다",
    "침수 감시 구역: 수위 센서 반경 200m",
    "강수: 시간당 3mm 참고, 10mm 주의, 30mm 위험",
]

SKY = {1: "맑음", 3: "구름많음", 4: "흐림"}
PTY = {0: "없음", 1: "비", 2: "비/눈", 3: "눈", 4: "소나기", 5: "빗방울", 6: "빗방울눈날림", 7: "눈날림"}
SENS_LABEL = {"breakwater": "방파제", "flood": "침수 감시 구역", "coast": "해안"}
COAST = "해안"  # 근거 문구 앞에 붙이는 성질 이름 (예: '해안(항구) — 풍속 16m/s 예보')


def sens_labels(sens: set[str]) -> list[str]:
    """성질 이름을 우선순위 순서로 (방파제, 침수 감시 구역, 해안)."""
    return [label for s, label in SENS_LABEL.items() if s in sens]


def worst(levels) -> str:
    """내부 신호 등급 중 최고 (ORDER 에 없는 값은 무시, 없으면 none)."""
    known = [lv for lv in levels if lv in ORDER]
    return max(known, key=ORDER.index) if known else "none"


def public(level: str) -> Level:
    """내부 신호 등급 -> 응답 3단계. none·info 는 safe."""
    return level if level in ("caution", "danger") else "safe"


def worst_public(levels) -> Level:
    return max(levels, key=LEVELS.index) if levels else "safe"


def down(level: str, steps: int = 1) -> str:
    return ORDER[max(0, ORDER.index(level) - steps)] if level in ORDER else level


def grade(value: float | None, table) -> str:
    if value is None:
        return "none"
    for limit, lv in table:
        if value >= limit:
            return lv
    return "none"


def floor_hour(t: datetime) -> datetime:
    return t.replace(minute=0, second=0, microsecond=0)


# ---------- 시점 ----------

@dataclass
class Timing:
    horizon: Horizon
    confidence: Confidence
    forecast_available: bool
    recheck_from: date | None


def classify(start: datetime, end: datetime, now: datetime, forecast_last: datetime | None,
             forecast_ok: bool) -> Timing:
    """forecast_last: 받은 단기예보의 마지막 시각. 예보 조회가 실패했으면 forecast_ok=False."""
    lead = start - now
    recheck = start.date() - timedelta(days=RECHECK_LEAD_DAYS)
    if end <= now:
        return Timing("past", "none", False, None)
    # 방문 마지막 시각까지 예보가 있어야 판단한다 (일부만 덮이면 나머지 시간을 모른 채 safe 가 될 수 있다)
    last_needed = floor_hour(max(end - timedelta(minutes=1), now))
    covered = forecast_ok and forecast_last is not None and last_needed <= forecast_last
    if lead <= NOW_WINDOW:
        return Timing("now", "high", covered, None)
    if covered or (not forecast_ok and lead <= SHORT_RANGE_NOMINAL):
        return Timing("short_range", "high" if lead <= timedelta(hours=24) else "medium", covered, None)
    if lead <= MEDIUM_LIMIT:
        return Timing("medium_range", "low", False, recheck)
    return Timing("long_range", "none", False, recheck)


def path_sensitivity(points: list[tuple[float, float]], flood_zones=geo.STATIC_FLOOD_ZONES) -> set[str]:
    """경로(촘촘히 한 점들)가 지나는 장소 성질의 합."""
    out: set[str] = set()
    for p in points:
        out |= sensitivity(*p, flood_zones=flood_zones)
        if {"breakwater", "flood"} <= out:  # 방파제가 있으면 해안은 보지 않는다 (settle)
            break
    return settle(out)


def sensitivity(lat: float | None, lng: float | None, flood_zones=geo.STATIC_FLOOD_ZONES) -> set[str]:
    """좌표로 본 장소 성질. 해안은 해안선 30m 안 (분류·이름으로 본 해안은 coast_kind)."""
    if lat is None or lng is None:
        return set()
    p = (lat, lng)
    out = set()
    if geo.zones_at(p, geo.BREAKWATERS):
        out.add("breakwater")
    if geo.zones_at(p, flood_zones):
        out.add("flood")
    if geo.near_coast(p):
        out.add("coast")
    return settle(out)


def settle(sens: set[str], indoor: bool = False) -> set[str]:
    """성질 우선순위 정리: 방파제 > 침수 감시 구역 > 해안 > 그 밖의 야외.
    - 방파제면 해안은 뺀다 (방파제 기준이 바람·파고·특보 모두 같거나 더 엄격하다)
    - 실내면 해안은 뺀다 (바다가 보이는 카페도 실내 규칙). 침수 감시 구역은 실내여도 그대로 (앞길이 잠길 수 있음)
    - 침수 감시 구역이면서 해안이면 둘 다 남겨 근거를 합친다 (가장 높은 단계)"""
    if "breakwater" in sens or indoor:
        return sens - {"coast"}
    return sens


def coast_kind(categories: list[str] | None, title: str | None) -> str | None:
    """장소 분류·이름으로 본 해안 종류 (항구·해수욕장 …). 해안이 아니거나 모르면 None."""
    for c in reversed(categories or []):  # 마지막(가장 세부) 분류부터
        if c in COAST_CATEGORIES:
            return COAST_CATEGORIES[c]
    for word, kind in COAST_TITLE_WORDS.items():
        if word in (title or ""):
            return kind
    return None


def coast_label(kind: str | None) -> str:
    """근거 문구 앞에 붙일 성질 이름. 종류를 모르면(좌표로 본 해안) '해안(바닷가)'."""
    return f"{COAST}({kind or '바닷가'})"


# ---------- 예보 ----------

def _cap_info(level: str) -> str:
    return "info" if ORDER.index(level) > 1 else level


def _hour_signals(h: ForecastHour, sens: set[str], indoor: bool = False) -> list[tuple[str, str, float, str, str, bool]]:
    """한 시간 예보 -> (type, level, 값, 항목명, 단위, 실내라 낮췄는지). 장소 성질에 따라 등급을 조정한다.

    - 방파제: 바람·파고·비 그대로 / 침수 감시 구역: 비 그대로 (실내여도 앞길이 잠기면 위험)
    - 해안: 바람·파고 그대로, 비는 그 밖과 같게
    - 그 밖: 비·바람 한 단계 낮게 (비는 낮춰도 '참고'로 남긴다), 파고 무시
    - 그 밖의 실내: 비·바람은 '참고'까지만 (이동할 때만 영향)
    """
    out = []
    rain = grade(h.pcp_mm, RAIN_MM)
    if rain == "none" and h.pty in (1, 2, 4) and (h.pop or 0) >= 60:
        rain = "info"
    if rain != "none":
        hot = bool(sens & {"flood", "breakwater"})
        lv = rain if hot else worst([down(rain), "info"])  # 비가 오면 어디든 최소 '참고'
        capped = indoor and not hot and lv != _cap_info(lv)
        lv = _cap_info(lv) if indoor and not hot else lv
        if lv != "none":
            out.append(("flood" if "flood" in sens and ORDER.index(lv) >= 2 else "rain", lv, h.pcp_mm or 0.0,
                        "강수", "mm/h", capped))
    wind = grade(h.wsd, WIND_MS)
    if wind != "none":
        hot = bool(sens & {"breakwater", "coast"})
        lv = wind if hot else down(wind)
        capped = indoor and not hot and lv != _cap_info(lv)
        lv = _cap_info(lv) if indoor and not hot else lv
        if lv != "none":
            out.append(("wind", lv, h.wsd or 0.0, "풍속", "m/s", capped))
    wave = grade(h.wav, WAVE_M)
    if wave != "none" and sens & {"breakwater", "coast"}:
        out.append(("wave", wave, h.wav or 0.0, "파고", "m", False))
    return out


def hour_level(h: ForecastHour, sens: set[str], indoor: bool = False) -> str:
    return worst(s[1] for s in _hour_signals(h, sens, indoor))


def forecast_hazards(hours: list[ForecastHour], sens: set[str], indoor: bool = False,
                     coast: str | None = None) -> list[Hazard]:
    """시간별 예보를 같은 종류·등급끼리 묶어 '14~19시 풍속 최대 16m/s' 형태로 만든다.
    해안이라 그대로 적용한 바람·파고는 사유 앞에 성질을 붙인다 ('해안(항구) — …', coast: coast_label 값)."""
    by_coast = "coast" in sens and "breakwater" not in sens
    groups: dict[tuple, list[tuple[datetime, float]]] = {}
    for h in sorted(hours, key=lambda x: x.time):
        for typ, lv, val, label, unit, capped in _hour_signals(h, sens, indoor):
            groups.setdefault((typ, lv, label, unit, capped), []).append((h.time, val))
    out = []
    for (typ, lv, label, unit, capped), pts in groups.items():
        first, last = pts[0][0], pts[-1][0]
        peak = max(v for _, v in pts)
        span = f"{first:%m/%d %H}시" if first == last else f"{first:%m/%d %H}~{(last + timedelta(hours=1)):%H}시"
        reason = f"{span} {label} {'최대 ' if len(pts) > 1 else ''}{peak:g}{unit} 예보"
        if by_coast and typ in ("wind", "wave"):
            reason = f"{coast or COAST} — {reason}"
        if capped:
            reason += f" ({INDOOR_NOTE})"
        out.append(Hazard(type=typ, level=lv, reason=reason, source="기상청 단기예보", at=first))
    return sorted(out, key=lambda z: (-ORDER.index(z.level), z.at))


# ---------- 특보 ----------

# 특보 종류 -> (방파제, 침수 감시 구역, 해안, 그 밖) 의 (주의보 등급, 경보 등급)
# 해안: 강풍·풍랑 주의보 주의 / 경보 위험. 태풍·해일은 바닷가에 바로 닿으므로 방파제와 같게. 호우·대설은 그 밖과 같게
WARNING_IMPACT = {
    "호우": (("caution", "danger"), ("caution", "danger"), ("info", "caution"), ("info", "caution")),
    "강풍": (("danger", "danger"), ("info", "caution"), ("caution", "danger"), ("info", "caution")),
    "풍랑": (("danger", "danger"), ("none", "none"), ("caution", "danger"), ("none", "none")),
    "태풍": (("danger", "danger"), ("danger", "danger"), ("danger", "danger"), ("caution", "danger")),
    "폭풍해일": (("danger", "danger"), ("danger", "danger"), ("danger", "danger"), ("caution", "caution")),
    "해일": (("danger", "danger"), ("danger", "danger"), ("danger", "danger"), ("caution", "caution")),
    "대설": (("info", "caution"), ("info", "caution"), ("info", "caution"), ("info", "caution")),
}


def warning_hazards(active: list[ActiveWarning], sens: set[str], at: datetime | None = None,
                    indoor: bool = False, coast: str | None = None) -> list[Hazard]:
    """지금 발효 중인 특보의 영향. 실내(방파제·침수 구역 아님)는 '그 밖' 영향을 참고까지만 본다.
    해안 기준이 단계를 올렸으면 사유 앞에 성질을 붙인다 (coast: coast_label 값)."""
    out = []
    for w in active:
        impact = WARNING_IMPACT.get(w.kind)
        idx = 1 if w.grade == "경보" else 0
        note = prefix = ""
        if impact is None:  # 건조·폭염·한파·황사 등: 참고로만
            lv = "info"
        else:
            other = impact[3][idx]
            if indoor and other != _cap_info(other):
                other, note = _cap_info(other), f", {INDOOR_NOTE}"
            levels = [impact[0][idx] if "breakwater" in sens else "none",
                      impact[1][idx] if "flood" in sens else "none", other]
            lv = worst(levels)
            if "coast" in sens and ORDER.index(impact[2][idx]) > ORDER.index(lv):
                lv, prefix = impact[2][idx], f"{coast or COAST} — "
        if lv != "none":
            out.append(Hazard(type="warning", level=lv, reason=f"{prefix}{w.name} 발효 중 ({w.areas[:40]}{note})",
                              source="기상특보", at=at))
    return out


def timed_warning_hazards(active: list[ActiveWarning], sens: set[str], start: datetime, now: datetime,
                          at: datetime | None = None, indoor: bool = False, coast: str | None = None) -> list[Hazard]:
    """특보를 방문 시각에 맞춰 적용한다. 특보는 '지금' 상태라
    - 6시간 안: 그대로 / 24시간 안: 참고(info)로만, 그 시각 위험은 예보로 판단 / 그 뒤: 쓰지 않는다."""
    lead = start - now
    if lead > WARNING_WINDOW:
        return []
    hz = warning_hazards(active, sens, at, indoor, coast)
    if lead <= WARNING_FULL:
        return hz
    return [h.model_copy(update={"level": "info", "reason": h.reason.replace(
        " 발효 중 (", f" 현재 발효 중, {start:%m/%d %H}시에는 해제될 수 있음 (")}) for h in hz]


def safe_windows(levels: dict[datetime, str], duration: timedelta) -> list[tuple[datetime, datetime, bool]]:
    """시작 시각별 신호 등급 {시각: 등급} -> 주의·위험이 없는 연속 구간 [(시작, 끝, 참고 신호가 있는지)].
    끝은 마지막 시작 시각 + duration."""
    out: list[tuple[datetime, datetime, bool]] = []
    run: list[datetime] = []

    def flush():
        if run:
            out.append((run[0], run[-1] + duration, any(levels[t] == "info" for t in run)))

    for t in sorted(levels):
        ok = levels[t] in ("none", "info")
        if ok and run and t - run[-1] == timedelta(hours=1):
            run.append(t)
        else:
            flush()
            run = [t] if ok else []
    flush()
    return out


# ---------- 실측 ----------

def sensor_risk(r: SensorReading) -> tuple[str, str | None]:
    """(내부 신호 등급 또는 unknown, 사유)."""
    if r.type not in geo.FLOOD_SENSOR_TYPES:
        return "none", None
    if r.level is None:
        return "unknown", None
    if r.level >= 3:
        return "danger", f"{r.name} 수위 등급 {r.level} (위험)"
    if r.level == 2:
        return "caution", f"{r.name} 수위 등급 {r.level} (주의)"
    if r.type == "ROAD" and (r.value or 0) > 0:
        return "info", f"{r.name} 노면 수위 감지 ({r.value:g})"
    return "none", None


def sensor_hazards(readings: list[SensorReading], lat: float, lng: float, at: datetime | None,
                   points: list[tuple[float, float]] | None = None) -> list[Hazard]:
    """반경 안 침수 센서의 실측 근거. points 를 주면 경로의 어느 점이든 반경 안이면 본다."""
    pts = points or [(lat, lng)]
    out = []
    for r in readings:
        if r.type not in geo.FLOOD_SENSOR_TYPES or not geo.near_any(pts, (r.lat, r.lng), geo.FLOOD_RADIUS_M):
            continue
        lv, why = sensor_risk(r)
        if why and lv in ORDER and lv != "none":
            out.append(Hazard(type="flood", level=lv, reason=why, source="E-UM 수위센서 (실측)", at=at))
    return out


def sensor_unknown_near(readings: list[SensorReading], lat: float, lng: float,
                        points: list[tuple[float, float]] | None = None) -> bool:
    """반경 안 침수 센서 중 등급 값이 없는 것이 있는지 (있으면 그 근거로는 판단할 수 없다)."""
    pts = points or [(lat, lng)]
    return any(r.type in geo.FLOOD_SENSOR_TYPES and r.level is None
               and geo.near_any(pts, (r.lat, r.lng), geo.FLOOD_RADIUS_M) for r in readings)


def breakwater_hazards(rows: list[BreakwaterHour], side: str, now: datetime) -> list[Hazard]:
    """가장 최근 1시간 집계. 3시간보다 오래된 집계는 현재 상황으로 보지 않는다."""
    mine = [r for r in rows if r.side == side]
    if not mine:
        return []
    last = max(mine, key=lambda r: r.hour)
    if now - last.hour > timedelta(hours=3):
        return []
    name = geo.BREAKWATER_SIDE[side].name
    if last.fall:
        return [Hazard(type="breakwater", level="caution", source="E-UM AI CCTV (실측)", at=last.hour,
                       reason=f"{name} 위험구역 실족 감지 {last.fall}건 ({last.hour:%H}시)")]
    if last.intrusion + last.approach >= 20:
        return [Hazard(type="breakwater", level="info", source="E-UM AI CCTV (실측)", at=last.hour,
                       reason=f"{name} 위험구역 접근·침입 {last.intrusion + last.approach}건 ({last.hour:%H}시)")]
    return []


def nearest_breakwater_side(lat: float, lng: float) -> str:
    return min(geo.BREAKWATER_SIDE, key=lambda s: geo.distance_to_zone((lat, lng), geo.BREAKWATER_SIDE[s]))


# ---------- 참고 정보 ----------

def tide_notes(tides: list[TideEvent], start: datetime, end: datetime) -> list[str]:
    near = [t for t in tides if t.kind == "high" and start - timedelta(minutes=90) <= t.time <= end + timedelta(minutes=90)]
    return [f"만조 {t.time:%H:%M} (약 {t.level_cm:g}cm) 전후 방문. 구룡포는 조차가 작아 단독 위험 요인은 아니지만, "
            "높은 파도·호우와 겹치면 해안가는 더 위험해진다." for t in near]


def seasonal_notes(d: date) -> list[str]:
    m = d.month
    notes = []
    if m in (6, 7):
        notes.append("6월 하순~7월은 장마철이라 비 오는 날이 많다 (평년 경향이며 예보가 아님).")
    if m in (7, 8, 9):
        notes.append("7~9월은 태풍이 올 수 있는 시기다 (평년 경향이며 예보가 아님).")
    if m == 10:
        notes.append("10월에도 드물게 태풍 영향이 있다 (평년 경향이며 예보가 아님).")
    if m in (12, 1, 2):
        notes.append("겨울철 동해안은 강풍과 높은 파도가 잦다 (평년 경향이며 예보가 아님).")
    return notes


# ---------- 행동 지침 ----------

def windows_text(wins: list[tuple[datetime, datetime, bool]]) -> str:
    return ", ".join(f"{a:%m/%d %H:%M}~{b:%H:%M}" if a.date() == b.date() else f"{a:%m/%d %H:%M}~{b:%m/%d %H:%M}"
                     for a, b, _ in wins)


def visit_advice(t: Timing, risk: Level | None, sens: set[str], now: datetime, start: datetime,
                 hazards: list[Hazard], data_missing: bool, indoor: bool = False,
                 better: list[tuple[datetime, datetime, bool]] | None = None, searched: bool = False,
                 travel: str | None = None) -> str:
    """risk: 3단계 또는 None(판단하지 않음). searched: 다른 시간대를 찾아봤는지 (예보가 있어 찾을 수 있었을 때만 True).
    travel: 이동 경로 평가면 수단 (walk | car). 문구를 '방문' 대신 '이동' 으로 바꾼다."""
    if travel and t.horizon not in ("past", "medium_range", "long_range") and risk is not None:
        return _travel_advice(t, risk, sens, hazards, travel, better)
    sens_txt = ", ".join(sens_labels(sens))
    if t.horizon == "past":
        return "이미 지난 시간이라 평가하지 않았다."
    if t.horizon in ("medium_range", "long_range"):
        days = (start.date() - now.date()).days
        head = (f"{days}일 뒤 일정이라 날씨·바다 상태를 예측할 수 없다." if t.horizon == "long_range"
                else f"{days}일 뒤 일정으로 아직 시간별 예보 범위 밖이다.")
        tail = f" {t.recheck_from:%Y-%m-%d}부터 다시 확인하면 예보로 판단할 수 있다."
        if sens:
            tail = (f" {sens_txt}처럼 날씨에 민감한 곳이라 사용자에게 날씨에 따라 바뀔 수 있다고만 알려도 된다."
                    + tail)
        return head + " 안전을 이유로 이 일정을 바꾸거나 빼지 마라." + tail
    if risk is None:
        return "필요한 데이터를 받지 못해 판단하지 못했다. 안전하다고 말하지 말고, 필요하면 나중에 다시 확인한다."
    top = "; ".join(h.reason for h in hazards[:3])
    forecast_word = "실측·예보" if t.horizon == "now" else "예보"
    hedge = " 예보는 바뀔 수 있으니 단정하지 마라." if t.horizon == "short_range" else ""
    if risk in ("danger", "caution"):
        if better:
            alt = f" 이 장소에 주의·위험 신호가 없는 시간대: {windows_text(better)} (better_times)."
        elif searched:
            alt = " 예보 범위 안에 이 장소가 괜찮은 시간대가 없다. 실내 장소 같은 대체 장소를 제안하라."
        else:
            alt = ""
        if risk == "danger":
            return (f"위험: {top}. 이 시간대 방문은 피하는 것이 좋다. 이유를 설명하고 다른 시간대나 대체 장소를 제안하라."
                    + alt + hedge)
        return f"주의: {top}. 사용자에게 알리고, 시간 조정이나 대안을 함께 제시하라." + alt + hedge
    if indoor and not sens:
        weather = any(INDOOR_NOTE in h.reason for h in hazards)
        return "실내 장소라 방문 자체는 괜찮다." + (
            " 이동할 때 비·바람만 조심하면 되고, 야외 일정의 대안으로 적합하다." if weather
            else " 날씨가 나쁜 날 야외 일정의 대안으로 쓸 수 있다.")
    if hazards:  # safe 인데 참고(info) 신호가 있다
        return f"안전 단계지만 참고할 것: {top}. 일정을 바꿀 정도는 아니며, 일정을 짤 때 참고하면 된다."
    return f"{forecast_word}상 특별한 위험 신호가 없다." + (" 예보는 바뀔 수 있다." if t.horizon == "short_range" else "")


def _travel_advice(t: Timing, risk: Level, sens: set[str], hazards: list[Hazard], mode: str,
                   better: list[tuple[datetime, datetime, bool]] | None) -> str:
    top = "; ".join(h.reason for h in hazards[:3])
    hedge = " 예보는 바뀔 수 있으니 단정하지 마라." if t.horizon == "short_range" else ""
    where = ", ".join(sens_labels(sens)) or "야외"
    how = "걸어서" if mode == "walk" else "차로"
    if risk in ("danger", "caution"):
        alt = f" 이 경로에 신호가 없는 시간대: {windows_text(better)}." if better else ""
        fix = ("다른 길로 돌아가거나(우회), 이동 시간을 바꾸거나, 차량으로 바꾸는 것을 제안하라." if mode == "walk"
               else "다른 길로 돌아가거나 이동 시간을 바꾸는 것을 제안하라.")
        head = "위험" if risk == "danger" else "주의"
        return f"{head}: {how} 이동하는 경로가 {where}을(를) 지난다. {top}. {fix}" + alt + hedge
    if hazards:
        return f"이동은 안전 단계지만 참고할 것: {top}. 일정을 바꿀 정도는 아니다."
    return f"{how} 이동하는 경로에 특별한 위험 신호가 없다." + (" 예보는 바뀔 수 있다." if t.horizon == "short_range" else "")


def assess_guidance(horizons: list[Horizon], risks: list[Level | None], recheck: date | None,
                    missing: list[str]) -> str:
    c = Counter(horizons)
    parts = []
    risky = sum(r in ("caution", "danger") for r in risks)
    if risky:
        parts.append(f"{risky}곳에 주의·위험 신호가 있다. 각 항목 advice 를 따르고, 바꾸기 전에 이유를 사용자에게 설명하라.")
    far = c["medium_range"] + c["long_range"]
    if far:
        parts.append(f"{far}곳은 예보 범위 밖이라 날씨 위험을 판단하지 않았다(risk=null, 안전하다는 뜻 아님). "
                     "이 항목들은 안전을 이유로 바꾸지 마라."
                     + (f" {recheck:%Y-%m-%d}부터 다시 확인하면 된다." if recheck else ""))
    if missing:
        parts.append(f"일부 데이터({', '.join(missing)})를 받지 못해 관련 판단을 하지 못했다(risk=null).")
    if not parts:
        if c["now"] + c["short_range"]:
            parts.append("예보·실측상 특별한 위험 신호가 없다. 예보는 바뀔 수 있으니 '안전이 보장된다'고 말하지 마라.")
        else:
            parts.append("평가할 미래 일정이 없다.")
    return " ".join(parts)
