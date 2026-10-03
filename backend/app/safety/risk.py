"""수집한 데이터를 '이 시각, 이 장소가 위험한가'로 해석한다. 외부 호출 없는 순수 함수만 둔다.

핵심 원칙
- 예보는 시점이 멀수록 의미가 없다. 단기예보(약 3일)가 없는 시점은 날씨 위험을 판단하지 않고 'unknown' 으로 둔다.
  'unknown' 은 안전하다는 뜻이 아니며, 그 시점에는 안전을 이유로 일정을 바꾸지 말라고 안내한다.
- 판단 근거가 없으면(데이터 장애) 'none' 이 아니라 'unknown'.
- 임계값 중 E-UM 등급 해석과 방파제 기준은 공식 기준이 없어 우리가 정한 가정이다 (ASSUMPTIONS).
"""
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.safety import geo
from app.safety.schemas import Confidence, Hazard, Horizon, Risk
from app.safety.sources import (
    ActiveWarning, BreakwaterHour, ForecastHour, SensorReading, TideEvent,
)

ORDER = ["none", "low", "caution", "danger"]
NOW_WINDOW = timedelta(hours=3)  # 이 안이면 실측 센서·현재 특보를 그대로 적용
SHORT_RANGE_NOMINAL = timedelta(days=3)  # 예보 조회가 실패했을 때 단기예보 범위로 간주하는 기간
MEDIUM_LIMIT = timedelta(days=10)  # 중기예보 범위. 아직 연동하지 않아 '예보 없음'으로 다룬다
RECHECK_LEAD_DAYS = 3  # 여행일 며칠 전부터 단기예보로 판단 가능한지

# (기준값, 등급). 위에서부터 처음 넘는 값
RAIN_MM = [(30, "danger"), (10, "caution"), (3, "low")]  # 시간당 강수량. 30mm/h 는 기상청 '매우 강한 비'
WIND_MS = [(14, "danger"), (9, "caution")]  # 방파제 기준. 14m/s 는 육상 강풍주의보 기준
WAVE_M = [(3.0, "danger"), (1.5, "caution")]  # 방파제 기준. 3m 는 풍랑주의보 기준

ASSUMPTIONS = [
    "E-UM 수위 등급: 1 평상시, 2 주의, 3 이상 위험으로 해석 (E-UM 이 기준을 공개하지 않음)",
    "방파제: 풍속 9m/s·파고 1.5m 이상 주의, 14m/s·3m 이상 위험 (특보 기준을 참고해 정한 값)",
    "침수 감시 구역: 수위 센서 반경 200m",
    "강수: 시간당 3mm 참고, 10mm 주의, 30mm 위험",
]

SKY = {1: "맑음", 3: "구름많음", 4: "흐림"}
PTY = {0: "없음", 1: "비", 2: "비/눈", 3: "눈", 4: "소나기", 5: "빗방울", 6: "빗방울눈날림", 7: "눈날림"}
SENS_LABEL = {"breakwater": "방파제", "flood": "침수 감시 구역"}


def worst(levels) -> Risk:
    known = [lv for lv in levels if lv in ORDER]
    return max(known, key=ORDER.index) if known else "none"


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
    covered = forecast_ok and forecast_last is not None and floor_hour(max(start, now)) <= forecast_last
    if lead <= NOW_WINDOW:
        return Timing("now", "high", covered, None)
    if covered or (not forecast_ok and lead <= SHORT_RANGE_NOMINAL):
        return Timing("short_range", "high" if lead <= timedelta(hours=24) else "medium", covered, None)
    if lead <= MEDIUM_LIMIT:
        return Timing("medium_range", "low", False, recheck)
    return Timing("long_range", "none", False, recheck)


def sensitivity(lat: float | None, lng: float | None, flood_zones=geo.STATIC_FLOOD_ZONES) -> set[str]:
    if lat is None or lng is None:
        return set()
    p = (lat, lng)
    out = set()
    if geo.zones_at(p, geo.BREAKWATERS):
        out.add("breakwater")
    if geo.zones_at(p, flood_zones):
        out.add("flood")
    return out


# ---------- 예보 ----------

def _hour_signals(h: ForecastHour, sens: set[str]) -> list[tuple[str, str, float, str, str]]:
    """한 시간 예보 -> (type, level, 값, 항목명, 단위). 장소 성질(sens)에 따라 등급을 조정한다."""
    out = []
    rain = grade(h.pcp_mm, RAIN_MM)
    if rain == "none" and h.pty in (1, 2, 4) and (h.pop or 0) >= 60:
        rain = "low"
    if rain != "none":
        hot = sens & {"flood", "breakwater"}
        lv = rain if hot else down(rain)
        if lv != "none":
            out.append(("flood" if "flood" in sens and ORDER.index(lv) >= 2 else "rain", lv, h.pcp_mm or 0.0,
                        "강수", "mm/h"))
    wind = grade(h.wsd, WIND_MS)
    if wind != "none":
        lv = wind if "breakwater" in sens else down(wind)
        if lv != "none":
            out.append(("wind", lv, h.wsd or 0.0, "풍속", "m/s"))
    wave = grade(h.wav, WAVE_M)
    if wave != "none" and "breakwater" in sens:
        out.append(("wave", wave, h.wav or 0.0, "파고", "m"))
    return out


def forecast_hazards(hours: list[ForecastHour], sens: set[str], scope: str = "") -> list[Hazard]:
    """시간별 예보를 같은 종류·등급끼리 묶어 '14~19시 풍속 최대 16m/s' 형태로 만든다."""
    groups: dict[tuple[str, str, str], list[tuple[datetime, float]]] = {}
    for h in sorted(hours, key=lambda x: x.time):
        for typ, lv, val, label, unit in _hour_signals(h, sens):
            groups.setdefault((typ, lv, f"{label}|{unit}"), []).append((h.time, val))
    out = []
    for (typ, lv, lu), pts in groups.items():
        label, unit = lu.split("|")
        first, last = pts[0][0], pts[-1][0]
        peak = max(v for _, v in pts)
        span = f"{first:%m/%d %H}시" if first == last else f"{first:%m/%d %H}~{(last + timedelta(hours=1)):%H}시"
        reason = f"{span} {label} {'최대 ' if len(pts) > 1 else ''}{peak:g}{unit} 예보"
        if scope:
            reason += f" ({scope})"
        out.append(Hazard(type=typ, level=lv, reason=reason, source="기상청 단기예보", at=first))
    return sorted(out, key=lambda z: (-ORDER.index(z.level), z.at))


# ---------- 특보 ----------

# 특보 종류 -> (방파제, 침수 감시 구역, 그 밖) 의 (주의보 등급, 경보 등급)
WARNING_IMPACT = {
    "호우": (("caution", "danger"), ("caution", "danger"), ("low", "caution")),
    "강풍": (("danger", "danger"), ("low", "caution"), ("low", "caution")),
    "풍랑": (("danger", "danger"), ("none", "none"), ("none", "none")),
    "태풍": (("danger", "danger"), ("danger", "danger"), ("caution", "danger")),
    "폭풍해일": (("danger", "danger"), ("danger", "danger"), ("caution", "caution")),
    "해일": (("danger", "danger"), ("danger", "danger"), ("caution", "caution")),
    "대설": (("low", "caution"), ("low", "caution"), ("low", "caution")),
}


def warning_hazards(active: list[ActiveWarning], sens: set[str], at: datetime | None = None) -> list[Hazard]:
    out = []
    for w in active:
        impact = WARNING_IMPACT.get(w.kind)
        idx = 1 if w.grade == "경보" else 0
        if impact is None:  # 건조·폭염·한파·황사 등: 참고로만
            lv = "low"
        else:
            levels = [impact[0][idx] if "breakwater" in sens else "none",
                      impact[1][idx] if "flood" in sens else "none",
                      impact[2][idx]]
            lv = worst(levels)
        if lv != "none":
            out.append(Hazard(type="warning", level=lv, reason=f"{w.name} 발효 중 ({w.areas[:40]})",
                              source="기상특보", at=at))
    return out


# ---------- 실측 ----------

def sensor_risk(r: SensorReading) -> tuple[Risk, str | None]:
    if r.type not in geo.FLOOD_SENSOR_TYPES:
        return "none", None
    if r.level is None:
        return "unknown", None
    if r.level >= 3:
        return "danger", f"{r.name} 수위 등급 {r.level} (위험)"
    if r.level == 2:
        return "caution", f"{r.name} 수위 등급 {r.level} (주의)"
    if r.type == "ROAD" and (r.value or 0) > 0:
        return "low", f"{r.name} 노면 수위 감지 ({r.value:g})"
    return "none", None


def sensor_hazards(readings: list[SensorReading], lat: float, lng: float, at: datetime | None) -> list[Hazard]:
    out = []
    for r in readings:
        if r.type not in geo.FLOOD_SENSOR_TYPES or geo.distance_m((lat, lng), (r.lat, r.lng)) > geo.FLOOD_RADIUS_M:
            continue
        lv, why = sensor_risk(r)
        if why and lv in ORDER and lv != "none":
            out.append(Hazard(type="flood", level=lv, reason=why, source="E-UM 수위센서 (실측)", at=at))
    return out


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
        return [Hazard(type="breakwater", level="low", source="E-UM AI CCTV (실측)", at=last.hour,
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

def visit_advice(t: Timing, risk: Risk, sens: set[str], now: datetime, start: datetime, hazards: list[Hazard],
                 data_missing: bool) -> str:
    sens_txt = ", ".join(SENS_LABEL[s] for s in sorted(sens))
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
    if data_missing and risk in ("none", "unknown"):
        return "필요한 데이터를 받지 못해 판단하지 못했다. 안전하다고 말하지 말고, 필요하면 나중에 다시 확인한다."
    top = "; ".join(h.reason for h in hazards[:3])
    forecast_word = "실측·예보" if t.horizon == "now" else "예보"
    if risk == "danger":
        return (f"위험: {top}. 이 시간대 방문은 피하는 것이 좋다. 이유를 설명하고 다른 시간대나 대체 장소를 제안하라."
                + (" 예보는 바뀔 수 있으니 단정하지 마라." if t.horizon == "short_range" else ""))
    if risk == "caution":
        return (f"주의: {top}. 사용자에게 알리고, 시간 조정이나 대안을 함께 제시하라."
                + (" 예보는 바뀔 수 있으니 단정하지 마라." if t.horizon == "short_range" else ""))
    if risk == "low":
        return f"참고: {top}. 일정을 바꿀 정도는 아니다."
    return f"{forecast_word}상 특별한 위험 신호가 없다." + (" 예보는 바뀔 수 있다." if t.horizon == "short_range" else "")


def assess_guidance(horizons: list[Horizon], risks: list[Risk], recheck: date | None, missing: list[str]) -> str:
    c = Counter(horizons)
    parts = []
    risky = sum(r in ("caution", "danger") for r in risks)
    if risky:
        parts.append(f"{risky}곳에 주의·위험 신호가 있다. 각 항목 advice 를 따르고, 바꾸기 전에 이유를 사용자에게 설명하라.")
    far = c["medium_range"] + c["long_range"]
    if far:
        parts.append(f"{far}곳은 예보 범위 밖이라 날씨 위험을 판단하지 않았다(risk=unknown, 안전하다는 뜻 아님). "
                     "이 항목들은 안전을 이유로 바꾸지 마라."
                     + (f" {recheck:%Y-%m-%d}부터 다시 확인하면 된다." if recheck else ""))
    if missing:
        parts.append(f"일부 데이터({', '.join(missing)})를 받지 못해 관련 판단은 unknown 이다.")
    if not parts:
        if c["now"] + c["short_range"]:
            parts.append("예보·실측상 특별한 위험 신호가 없다. 예보는 바뀔 수 있으니 '안전이 보장된다'고 말하지 마라.")
        else:
            parts.append("평가할 미래 일정이 없다.")
    return " ".join(parts)
