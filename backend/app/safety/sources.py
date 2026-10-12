"""외부 데이터 수집과 정규화.

- 실시간: E-UM 수위 센서·방파제 이상행동·자외선 (docs/dt/DT_API.md)
- 예보·특보·조위: 기상청 단기예보, 기상특보 발효 현황, 국립해양조사원 조석예보 (docs/external/WEATHER_WAVE_API.md)

각 호출은 예외를 던지지 않고 Fetched(값, 수집 시각, 오류)를 돌려준다. 한 소스가 죽어도 나머지로 판단하고,
판단할 근거가 없으면 위험 단계를 판단하지 않는다 (risk=null, risk.py).
"""
import json
import threading
import time as _time
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable, Generic, Protocol, TypeVar
from urllib.parse import quote, urlencode

import httpx

from app.safety import geo
from app.safety.config import SafetySettings

KST = timezone(timedelta(hours=9))
T = TypeVar("T")


def now_kst() -> datetime:
    return datetime.now(KST)


@dataclass
class Fetched(Generic[T]):
    value: T | None
    fetched_at: datetime | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class SourceError(RuntimeError):
    pass


@dataclass
class SensorReading:
    id: int
    type: str  # HOLE 맨홀 | ROAD 지표면 | RIVER 하천 | RAIN 강우
    name: str
    lat: float
    lng: float
    value: float | None
    level: int | None


@dataclass
class BreakwaterHour:
    side: str  # north | south
    hour: datetime
    fall: int  # 위험구역 실족 감지
    intrusion: int  # 위험구역 침입 감지
    approach: int  # 위험구역 접근 경고
    abnormal: int  # 보행자 이상 감지


@dataclass
class UvReading:
    at: datetime
    index: float


@dataclass
class ForecastHour:
    time: datetime
    pop: int | None = None  # 강수확률 %
    pcp_mm: float | None = None  # 1시간 강수량
    pty: int | None = None  # 0 없음 1 비 2 비/눈 3 눈 4 소나기
    sky: int | None = None  # 1 맑음 3 구름많음 4 흐림
    wsd: float | None = None  # 풍속 m/s
    wav: float | None = None  # 파고 m
    tmp: float | None = None  # 기온


@dataclass
class Forecast:
    base: datetime
    hours: dict[datetime, ForecastHour] = field(default_factory=dict)
    tmin: dict[date, float] = field(default_factory=dict)
    tmax: dict[date, float] = field(default_factory=dict)

    @property
    def last_hour(self) -> datetime | None:
        return max(self.hours) if self.hours else None

    @property
    def first_hour(self) -> datetime | None:
        return min(self.hours) if self.hours else None


def fill_now_gap(cur: Forecast, prev: Forecast, now: datetime) -> Forecast:
    """단기예보 기준시각 hh:00 발표는 hh+1 시부터라, 발표 직후(hh:15~hh:59)에는 지금 시각(hh) 예보가 빈다.
    빈 시각(지금 ~ 이번 발표 첫 시각 전)을 직전 발표(prev)의 같은 시각 예보로 채운 새 Forecast (cur 는 바꾸지 않는다).
    직전 발표는 3시간 전 것이라 같은 시각의 가장 최근 예보다."""
    hour = now.astimezone(KST).replace(minute=0, second=0, microsecond=0)
    first = cur.first_hour
    if first is None or first <= hour:
        return cur
    gap = {t: h for t, h in prev.hours.items() if hour <= t < first}
    return Forecast(base=cur.base, hours=dict(sorted({**gap, **cur.hours}.items())),
                    tmin={**prev.tmin, **cur.tmin}, tmax={**prev.tmax, **cur.tmax})


@dataclass
class ActiveWarning:
    name: str  # 예: 강풍주의보
    areas: str

    @property
    def kind(self) -> str:
        return self.name.removesuffix("주의보").removesuffix("경보")

    @property
    def grade(self) -> str:
        return "경보" if self.name.endswith("경보") else "주의보"


@dataclass
class Warnings:
    issued_at: datetime | None
    active: list[ActiveWarning]  # 구룡포(포항·경북남부앞바다·동해남부북쪽)에 해당하는 발효 중 특보
    preliminary: list[str]  # 구룡포 해당 예비특보 원문 줄


@dataclass
class TideEvent:
    time: datetime
    kind: str  # high | low
    level_cm: float


class Sources(Protocol):
    def now(self) -> datetime: ...
    def sensors(self) -> Fetched[list[SensorReading]]: ...
    def breakwater(self) -> Fetched[list[BreakwaterHour]]: ...
    def uv(self) -> Fetched[UvReading]: ...
    def forecast(self, now: datetime) -> Fetched[Forecast]: ...
    def warnings(self) -> Fetched[Warnings]: ...
    def tide(self, day: date) -> Fetched[list[TideEvent]]: ...


# ---------- 파싱 (실제 응답 형식 기준, tests/safety/fixtures 참고) ----------

def _kst(text: str) -> datetime:
    return datetime.fromisoformat(text.strip()).replace(tzinfo=KST)


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_sensors(data: list[dict]) -> list[SensorReading]:
    out = []
    for r in data:
        level = _num(r.get("level"))
        out.append(SensorReading(int(r["id"]), str(r.get("sensorType", "")).upper(), r.get("name", ""),
                                 float(r["latitude"]), float(r["longitude"]), _num(r.get("value")),
                                 None if level is None else int(level)))
    return out


def parse_breakwater(data: dict) -> list[BreakwaterHour]:
    out = []
    for side in ("north", "south"):
        rows = data.get(side) or []
        for r in rows if isinstance(rows, list) else [rows]:
            out.append(BreakwaterHour(side, _kst(r["hour"]), int(r.get("warningFallDetectionCount") or 0),
                                      int(r.get("warningInsideDetectionCount") or 0),
                                      int(r.get("warningApproachCount") or 0), int(r.get("normalFallDetectionCount") or 0)))
    return out


def parse_pcp(text) -> float | None:
    """기상청 PCP 문자열 -> mm. 범위는 아래값, '미만'은 그 절반으로 본다."""
    s = str(text or "").strip()
    if not s or s == "강수없음" or s == "0":
        return 0.0
    if "미만" in s:
        return (_num(s.replace("mm", "").replace("미만", "").strip()) or 1.0) / 2
    s = s.replace("mm", "").replace("이상", "").strip()
    if "~" in s:
        s = s.split("~")[0]
    return _num(s)


def parse_forecast(items: list[dict], base: datetime) -> Forecast:
    fc = Forecast(base=base)
    for it in items:
        cat, val = it["category"], it["fcstValue"]
        d = datetime.strptime(it["fcstDate"], "%Y%m%d").date()
        if cat in ("TMN", "TMX"):
            v = _num(val)
            if v is not None:
                (fc.tmin if cat == "TMN" else fc.tmax)[d] = v
            continue
        t = datetime.combine(d, time(int(it["fcstTime"][:2]))).replace(tzinfo=KST)
        h = fc.hours.setdefault(t, ForecastHour(t))
        if cat == "POP":
            h.pop = int(_num(val) or 0)
        elif cat == "PCP":
            h.pcp_mm = parse_pcp(val)
        elif cat == "PTY":
            h.pty = int(_num(val) or 0)
        elif cat == "SKY":
            h.sky = int(_num(val) or 0)
        elif cat == "WSD":
            h.wsd = _num(val)
        elif cat == "WAV":
            v = _num(val)
            h.wav = v if v is not None and v >= 0 else None  # 결측 -999 등
        elif cat == "TMP":
            h.tmp = _num(val)
    return fc


MIN_FORECAST_SPAN = timedelta(hours=24)  # 단기예보는 발표 후 약 3일을 덮는다. 이보다 짧으면 잘린 응답으로 본다


def check_forecast(fc: Forecast) -> Forecast:
    """받은 단기예보가 쓸 만한지. 시간 예보가 없거나(기상청 NODATA 03 포함) 기준시각 대비 너무 짧으면 SourceError
    (빈 예보를 정상 수신으로 받으면 지도는 안전, 일정은 '예보 범위 밖'이 된다. 실패로 보고 짧은 TTL 뒤 다시 받는다)."""
    if not fc.hours:
        raise SourceError(f"단기예보 없음 (기준 {fc.base:%m/%d %H}시, NODATA)")
    if fc.last_hour - fc.base < MIN_FORECAST_SPAN:
        raise SourceError(f"단기예보가 너무 짧음 (기준 {fc.base:%m/%d %H}시, 마지막 {fc.last_hour:%m/%d %H}시)")
    return fc


def parse_warning_lines(text: str, keywords: list[str]) -> list[tuple[str, str]]:
    """특보 현황 본문("o 풍랑주의보 : 동해남부북쪽안쪽먼바다, …") -> 키워드가 든 (특보명, 구역)."""
    entries: list[list[str]] = []
    for line in (text or "").replace("\r", "").split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("o ") or line.startswith("○"):
            body = line.lstrip("o○ ").strip()
            if body.replace(" ", "") == "없음":  # 실제로 "o 없 음" 으로 온다
                continue
            name, _, areas = body.partition(":")
            entries.append([name.strip(), areas.strip()])
        elif entries:  # 줄바꿈된 구역 목록
            entries[-1][1] += " " + line
    return [(n, a) for n, a in entries if n and any(k in a for k in keywords)]


def parse_warnings(item: dict | None, keywords: list[str]) -> Warnings:
    if not item:
        return Warnings(None, [], [])
    tm = str(item.get("tmFc") or "")
    issued = datetime.strptime(tm, "%Y%m%d%H%M").replace(tzinfo=KST) if len(tm) == 12 else None
    active = [ActiveWarning(n, a) for n, a in parse_warning_lines(item.get("t6", ""), keywords)]
    prelim = [f"{n} : {a}" for n, a in parse_warning_lines(item.get("t7", ""), keywords)]
    return Warnings(issued, active, prelim)


def parse_tide(items: list[dict]) -> list[TideEvent]:
    # extrSe: 홀수(1·3) 고조, 짝수(2·4) 저조 (실제 응답 관찰 기준)
    out = [TideEvent(_kst(i["predcDt"]), "high" if int(i["extrSe"]) % 2 == 1 else "low", float(i["predcTdlvVl"]))
           for i in items]
    return sorted(out, key=lambda e: e.time)


def portal_items(status_code: int, text: str) -> dict:
    """공공데이터포털 응답 -> body.items. 형식이 API 마다 조금씩 다르다.
    - 기상청: {"response": {"header", "body"}} / 조석예보: {"header", "body"}
    - 게이트웨이 오류(키 미등록·타임아웃): {"OpenAPI_ServiceResponse": {"cmmMsgHeader": {...}}} (XML 로도 온다)
    """
    try:
        body = json.loads(text)
    except ValueError:
        raise SourceError(f"HTTP {status_code}: {text[:150]}") from None
    gw = body.get("OpenAPI_ServiceResponse")
    if gw:
        h = gw.get("cmmMsgHeader") or {}
        raise SourceError(f"{h.get('errMsg', '')} {h.get('returnAuthMsg', '')}".strip() or f"HTTP {status_code}")
    body = body.get("response", body)
    header = body.get("header") or {}
    code = str(header.get("resultCode", ""))
    if code == "03":  # NODATA
        return {}
    if code != "00":
        raise SourceError(f"{code} {header.get('resultMsg', '')}".strip() or f"HTTP {status_code}")
    items = (body.get("body") or {}).get("items") or {}
    return items if isinstance(items, dict) else {}


def kma_base(now: datetime) -> datetime:
    """가장 최근에 발표된 단기예보 기준시각 (02·05·…·23시, 발표 후 약 10분 뒤 제공)."""
    t = now.astimezone(KST) - timedelta(minutes=15)
    for h in (23, 20, 17, 14, 11, 8, 5, 2):
        if t.hour >= h:
            return t.replace(hour=h, minute=0, second=0, microsecond=0)
    return (t - timedelta(days=1)).replace(hour=23, minute=0, second=0, microsecond=0)


# ---------- 실제 API ----------

class _Cache:
    def __init__(self):
        self._data: dict[tuple, tuple[float, Fetched]] = {}
        self._lock = threading.Lock()

    def get(self, key: tuple, ttl: float, err_ttl: float, load: Callable[[], object]) -> Fetched:
        with self._lock:
            hit = self._data.get(key)
            if hit and _time.monotonic() < hit[0]:
                return hit[1]
        try:
            result = Fetched(load(), now_kst())
        except Exception as e:  # noqa: BLE001 — 외부 장애는 값으로 돌려준다
            result = Fetched(None, now_kst(), f"{type(e).__name__}: {e}"[:200])
        with self._lock:
            self._data[key] = (_time.monotonic() + (ttl if result.ok else err_ttl), result)
        return result


def _key_param(key: str) -> str:
    """포털에서 받은 키가 이미 인코딩된 값(%2B…)이면 그대로, 아니면 인코딩한다."""
    return key if "%" in key else quote(key, safe="")


class LiveSources:
    def __init__(self, cfg: SafetySettings, http: httpx.Client | None = None):
        self.cfg = cfg
        self.http = http or httpx.Client(timeout=cfg.timeout_sec)
        self.cache = _Cache()
        self.keywords = [k.strip() for k in cfg.warn_keywords.split(",") if k.strip()]

    def now(self) -> datetime:
        return now_kst()

    def _get(self, url: str, key: str, params: dict | None = None) -> httpx.Response:
        if not key:
            raise SourceError("인증키가 설정되지 않았습니다")
        query = f"serviceKey={_key_param(key)}" + (f"&{urlencode(params)}" if params else "")
        return self.http.get(f"{url}?{query}")

    # E-UM: 성공 {"id","status":"success","data"}, 실패 {"rspns_rslt":{"rslt_cd","rslt_msg"}}
    def _dt(self, path: str):
        resp = self._get(f"{self.cfg.dt_base_url}{path}", self.cfg.dt_service_key)
        try:
            body = resp.json()
        except ValueError:
            raise SourceError(f"HTTP {resp.status_code}: {resp.text[:100]}") from None
        if resp.status_code != 200 or body.get("status") != "success":
            r = body.get("rspns_rslt") or {}
            raise SourceError(f"HTTP {resp.status_code} {r.get('rslt_cd', '')} {r.get('rslt_msg', resp.text[:100])}")
        return body["data"]

    def _portal(self, url: str, params: dict) -> dict:
        resp = self._get(url, self.cfg.data_go_kr_service_key, params)
        return portal_items(resp.status_code, resp.text)


    def sensors(self) -> Fetched[list[SensorReading]]:
        return self.cache.get(("sensors",), self.cfg.ttl_sensors, self.cfg.ttl_error,
                              lambda: parse_sensors(self._dt("/sensor/latest/sensorLevel")))

    def breakwater(self) -> Fetched[list[BreakwaterHour]]:
        return self.cache.get(("breakwater",), self.cfg.ttl_breakwater, self.cfg.ttl_error,
                              lambda: parse_breakwater(self._dt("/aicctv/avghour/datas")))

    def uv(self) -> Fetched[UvReading]:
        def load():
            d = self._dt("/sensor/latest/uvIndex")
            return UvReading(_kst(d["dateTime"]), float(d["uvIndex"]))
        return self.cache.get(("uv",), self.cfg.ttl_breakwater, self.cfg.ttl_error, load)

    def _forecast_at(self, base: datetime) -> Fetched[Forecast]:
        def load():
            items = self._portal("https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst", {
                "pageNo": 1, "numOfRows": 1500, "dataType": "JSON", "base_date": f"{base:%Y%m%d}",
                "base_time": f"{base:%H%M}", "nx": self.cfg.kma_nx, "ny": self.cfg.kma_ny})
            return check_forecast(parse_forecast(items.get("item") or [], base))
        return self.cache.get(("forecast", base), self.cfg.ttl_forecast, self.cfg.ttl_error, load)

    def forecast(self, now: datetime) -> Fetched[Forecast]:
        """가장 최근 발표. 지금 시각 예보가 비면(발표 직후) 직전 발표로 채운다 (fill_now_gap).
        직전 발표도 못 받으면 빈 채로 둔다 (risk.classify 가 그 시각을 판단하지 않는다)."""
        base = kma_base(now)
        cur = self._forecast_at(base)
        if cur.ok and cur.value and cur.value.first_hour > now.astimezone(KST).replace(minute=0, second=0, microsecond=0):
            prev = self._forecast_at(base - timedelta(hours=3))
            if prev.ok and prev.value:
                return Fetched(fill_now_gap(cur.value, prev.value, now), cur.fetched_at)
        return cur

    def warnings(self) -> Fetched[Warnings]:
        def load():
            items = self._portal("https://apis.data.go.kr/1360000/WthrWrnInfoService/getPwnStatus",
                                 {"pageNo": 1, "numOfRows": 10, "dataType": "JSON"})
            rows = items.get("item") or []
            rows = rows if isinstance(rows, list) else [rows]
            return parse_warnings(rows[0] if rows else None, self.keywords)
        return self.cache.get(("warnings",), self.cfg.ttl_warnings, self.cfg.ttl_error, load)

    def tide(self, day: date) -> Fetched[list[TideEvent]]:
        def load():
            items = self._portal("https://apis.data.go.kr/1192136/tideFcstHghLw/GetTideFcstHghLwApiService", {
                "obsCode": self.cfg.tide_obs_code, "reqDate": f"{day:%Y%m%d}", "type": "json", "numOfRows": 10})
            rows = items.get("item") or []
            return parse_tide(rows if isinstance(rows, list) else [rows])
        return self.cache.get(("tide", day), self.cfg.ttl_tide, self.cfg.ttl_error, load)


# ---------- 키 없이 개발·시연할 때 ----------

# 가상 날씨 시간표의 구간 종류. 구간 밖은 평상시(calm). 겹치면 KIND_PRIORITY 앞의 것(더 심한 것) 하나만 쓴다.
SIM_KINDS = {
    "storm": "폭풍: 호우·강풍·풍랑 특보 + 침수 센서 상승 + 비·강풍·높은 파도 (구간 시작 6~14시간째는 시간당 35mm)",
    "flood": "침수: 침수 센서 상승 (수협 맨홀 등급 3, 파출소 지표면 등급 2) + 약한 비. 침수 감시 구역만 위험",
    "wind": "강풍·높은 파도 (풍속 15m/s, 파고 3.2m). 특보 없음 -> 방파제 위험, 그 밖의 야외 주의",
    "rain": "약한 비 (시간당 5mm). 주의·위험은 없고 참고(info) 신호만",
}
KIND_PRIORITY = ("storm", "flood", "wind", "rain")
MAX_SEGMENTS = 20  # 정리(normalize_timeline)한 뒤 기준
MAX_INPUT_SEGMENTS = 200  # 정리 전 요청 하나에 담을 수 있는 구간 수 (터무니없이 큰 요청만 막는다)

# 시나리오 = 시간표 프리셋. 설정한 순간(가상 시각) 기준 절대 구간이 된다 (시각을 옮겨도 밀리지 않음)
MOCK_SCENARIOS = {
    "calm": "평상시. 시간표를 비운다",
    "rain": "약한 비: 지금부터 2~9시간 뒤 (시간당 5mm). 주의·위험은 없고 참고(info) 신호만",
    "wind_tomorrow": "내일 12~18시 강풍·높은 파도 (풍속 15m/s, 파고 3.2m). 특보 없음 -> 방파제 위험, 그 밖의 야외 주의",
    "flood": "침수: 지금부터 9시간 (수협 맨홀 등급 3, 파출소 지표면 등급 2) + 약한 비. 침수 감시 구역만 위험",
    "storm": "폭풍: 지금부터 30시간 (호우·강풍·풍랑 특보 + 침수 센서 상승 + 악천후)",
}


@dataclass(frozen=True)
class SimSegment:
    """가상 날씨 구간. start 포함, end 제외 (가상 시각 기준 절대 시각)."""
    kind: str
    start: datetime
    end: datetime

    def covers(self, t: datetime) -> bool:
        return self.start <= t < self.end

    def overlaps(self, a: datetime, b: datetime) -> bool:
        return self.start < b and a < self.end


def _hour(t: datetime) -> datetime:
    return t.replace(minute=0, second=0, microsecond=0)


def _worst(segments: list[SimSegment]) -> SimSegment | None:
    return min(segments, key=lambda s: (KIND_PRIORITY.index(s.kind), s.start)) if segments else None


def preset_timeline(scenario: str, now: datetime, until: datetime | None = None) -> tuple[SimSegment, ...]:
    """시나리오 프리셋 -> 시간표 (now 기준 절대 구간). until 이 있으면 그 시각에 끝난다 (calm 은 무시)."""
    if scenario not in MOCK_SCENARIOS:
        raise ValueError(f"알 수 없는 시나리오: {scenario}")
    h = timedelta(hours=1)
    if scenario == "calm":
        return ()
    if scenario == "storm":
        seg = SimSegment("storm", now, now + 30 * h)
    elif scenario == "flood":
        seg = SimSegment("flood", now, now + 9 * h)
    elif scenario == "rain":
        seg = SimSegment("rain", now + 2 * h, now + 9 * h)
    else:  # wind_tomorrow
        noon = datetime.combine(now.astimezone(KST).date() + timedelta(days=1), time(12), KST)
        seg = SimSegment("wind", noon, noon + 6 * h)
    if until is not None:
        if until <= seg.start:
            raise ValueError(f"until({until:%m/%d %H:%M})은 구간 시작({seg.start:%m/%d %H:%M})보다 뒤여야 합니다")
        seg = replace(seg, end=until)
    return (seg,)


def normalize_timeline(segments) -> tuple[SimSegment, ...]:
    """시간표 정리 (저장하기 전에 한 번). 시각별 날씨(segment_at 의 종류)는 정리 전후로 같다.
    - 겹치는 부분은 더 심한 종류가 차지한다 (storm > flood > wind > rain). 약한 구간은 잘리거나 둘로 나뉜다.
    - 같은 종류가 겹치거나 맞닿으면 하나로 합친다. 길이 0 구간은 없앤다. 시작 순으로 정렬한다.
    덮여 사라진 약한 구간은 기억하지 않는다 (센 구간을 지워도 돌아오지 않는다)."""
    edges = sorted({t for s in segments for t in (s.start, s.end)})
    out: list[SimSegment] = []
    for a, b in zip(edges, edges[1:]):
        # 경계 사이에서는 덮는 구간이 바뀌지 않으므로 a 시각의 가장 심한 종류가 [a, b) 전체의 종류다
        kinds = [s.kind for s in segments if s.covers(a)]
        if not kinds:
            continue
        kind = min(kinds, key=KIND_PRIORITY.index)
        if out and out[-1].kind == kind and out[-1].end == a:
            out[-1] = replace(out[-1], end=b)
        else:
            out.append(SimSegment(kind, a, b))
    return tuple(out)


def check_timeline(segments: list[SimSegment]) -> tuple[SimSegment, ...]:
    """구간 목록 검증 (모르는 kind, start >= end) 후 정리(normalize_timeline)하고, 정리한 결과로 개수를 본다.
    문제가 있으면 ValueError."""
    if len(segments) > MAX_INPUT_SEGMENTS:
        raise ValueError(f"한 번에 보낼 수 있는 구간은 최대 {MAX_INPUT_SEGMENTS}개입니다")
    for i, seg in enumerate(segments, 1):
        if seg.kind not in SIM_KINDS:
            raise ValueError(f"{i}번째 구간: 알 수 없는 종류 {seg.kind}")
        if not seg.start < seg.end:
            raise ValueError(f"{i}번째 구간: start 는 end 보다 앞이어야 합니다")
    out = normalize_timeline(segments)
    if len(out) > MAX_SEGMENTS:
        raise ValueError(f"구간은 (겹침·맞닿음을 정리한 뒤) 최대 {MAX_SEGMENTS}개입니다 — 지금 {len(out)}개")
    return out


_SIM_LOCK = threading.Lock()


class MockSources:
    """키 없이 쓰는 가짜 데이터. 가상 날씨 시간표(구간 목록)와 가상 시각을 실행 중에 바꿀 수 있다 (PUT /safety/sim).
    - 예보: 시각마다 그 시간(정시~1시간)과 겹치는 구간의 날씨. 구간을 넣는 즉시, 예보 범위(약 3일) 안이면 보인다.
    - 지금 관측값(센서·특보·방파제 CCTV·자외선): 가상 시각이 속한 구간의 값, 구간 밖이면 평상시.
    값은 실제 응답 형태를 따르되 내용은 지어낸 것이다. 응답의 data_status 에 mock 임이 표시된다."""

    def __init__(self, scenario: str = "calm", clock: Callable[[], datetime] = now_kst):
        self._clock = clock
        self.offset = timedelta(0)  # 가상 시각 = 실제(clock) + offset
        self.timeline: tuple[SimSegment, ...] = preset_timeline(scenario, self.now())

    def set(self, offset: timedelta | None = None, timeline: tuple[SimSegment, ...] | None = None) -> None:
        """시간표와 가상 시각을 함께 바꾼다 (둘 중 하나만 바뀐 상태를 다른 요청이 보지 않게)."""
        with _SIM_LOCK:
            if offset is not None:
                self.offset = offset
            if timeline is not None:
                self.timeline = timeline

    def segment_at(self, t: datetime) -> SimSegment | None:
        """t 가 속한 구간 (겹치면 더 심한 것)."""
        return _worst([s for s in self.timeline if s.covers(t)])

    @property
    def scenario(self) -> str:
        """지금(가상 시각) 적용 중인 구간 종류, 없으면 calm."""
        seg = self.segment_at(self.now())
        return seg.kind if seg else "calm"

    @scenario.setter
    def scenario(self, value: str) -> None:
        """프리셋으로 시간표를 바꾼다 (지금 기준)."""
        self.timeline = preset_timeline(value, self.now())

    def observed_until(self) -> datetime | None:
        """지금 관측값(센서·특보·CCTV·자외선)이 그대로인 끝 시각 = 지금 뒤 첫 구간 경계 (없으면 None).
        safety 는 이 시각 뒤 방문에 지금 관측값을 쓰지 않고 예보로만 판단한다 (폭풍이 17시에 끝나면 17:30 일정은 안전)."""
        now = self.now()
        edges = [t for x in self.timeline for t in (x.start, x.end) if t > now]
        return min(edges) if edges else None

    @property
    def storm(self) -> bool:
        return self.scenario == "storm"

    def now(self) -> datetime:
        return self._clock() + self.offset

    clock = now  # 이전 이름

    def _f(self, value) -> Fetched:
        return Fetched(value, self.now())

    def sensors(self) -> Fetched[list[SensorReading]]:
        raised = {"storm": {4: (3, 1650.0), 8: (2, 12.0), 1: (2, 1.8), 5: (2, 38.0)},
                  "flood": {4: (3, 1650.0), 8: (2, 12.0)}}.get(self.scenario, {})
        out = []
        for i, t, n, la, lo in geo.WATER_SENSORS:
            level, value = raised.get(i, (1, 1100.0 if t == "HOLE" else 0.0))
            out.append(SensorReading(i, t, n, la, lo, value, level))
        return self._f(out)

    def breakwater(self) -> Fetched[list[BreakwaterHour]]:
        storm = self.storm
        hour = _hour(self.now()) - timedelta(hours=1)
        k = 3 if storm else 1
        return self._f([BreakwaterHour("north", hour, 1 if storm else 0, 2 * k, 5 * k, 1),
                        BreakwaterHour("south", hour, 0, 3 * k, 7 * k, 1)])

    def uv(self) -> Fetched[UvReading]:
        return self._f(UvReading(self.now(), 0.5 if self.storm else 4.0))

    def _hour_weather(self, t: datetime) -> dict:
        """t 시(정시부터 1시간)의 날씨. 그 1시간과 겹치는 구간 중 가장 심한 것, 없으면 평상시."""
        calm = dict(pop=10, pcp_mm=0.0, pty=0, sky=1, wsd=3.0, wav=0.5, tmp=21.0)
        seg = _worst([s for s in self.timeline if s.overlaps(t, t + timedelta(hours=1))])
        if seg is None:
            return calm
        if seg.kind == "storm":
            i = int((t - _hour(seg.start)).total_seconds() // 3600)  # 구간 시작부터 몇 시간째
            return dict(pop=90, pcp_mm=35.0 if 6 <= i < 14 else 8.0, pty=1, sky=4, wsd=16.0, wav=3.5, tmp=18.0)
        if seg.kind in ("rain", "flood"):
            return dict(calm, pop=80, pcp_mm=5.0, pty=1, sky=4)
        return dict(calm, sky=3, wsd=15.0, wav=3.2)  # wind

    def forecast(self, now: datetime) -> Fetched[Forecast]:
        base = kma_base(now)
        fc = Forecast(base=base)
        # 실제처럼 이번 발표는 기준시각 + 1시부터, 발표 직후 빈 지금 시각은 직전 발표로 채운 것과 같게 (LiveSources.forecast)
        start = min(base + timedelta(hours=1), _hour(now.astimezone(KST)))
        t = start
        while t < base + timedelta(hours=71):
            fc.hours[t] = ForecastHour(t, **self._hour_weather(t))
            t += timedelta(hours=1)
        for d in {t.date() for t in fc.hours}:
            fc.tmin[d], fc.tmax[d] = 15.0, 23.0
        return self._f(fc)

    def warnings(self) -> Fetched[Warnings]:
        if not self.storm:
            return self._f(Warnings(self.now(), [], []))
        return self._f(Warnings(self.now(), [
            ActiveWarning("호우주의보", "경상북도(포항, 경주)"),
            ActiveWarning("강풍주의보", "경상북도(포항, 경주, 영덕)"),
            ActiveWarning("풍랑주의보", "경북남부앞바다, 동해남부북쪽안쪽먼바다"),
        ], []))

    def tide(self, day: date) -> Fetched[list[TideEvent]]:
        # 동해안 혼합조 흉내: 하루 고조·저조 한 번씩, 매일 약 50분씩 늦어진다
        shift = timedelta(minutes=50 * (day.toordinal() % 30))
        base = datetime.combine(day, time(7, 40)).replace(tzinfo=KST)
        return self._f([TideEvent(base + shift, "high", 33.0),
                        TideEvent(base + shift + timedelta(hours=10), "low", 15.0)])


# ---------- 실제 ↔ 가상 전환 (SAFETY_SIM_ENABLED, 발표 데모용) ----------

def _whole_hours(d: timedelta) -> timedelta:
    return timedelta(hours=round(d.total_seconds() / 3600))


class ShiftedLive:
    """실제 데이터를 가상 시각으로 옮겨 보여준다. 가상 시각이 실제와 다르면(예: 발표일에 계획일 10/10 으로 맞춤)
    실제 데이터의 시각이 판단 기준 시각과 맞지 않아 예보·CCTV 를 쓸 수 없다. 그래서 '지금 실제 관측·예보'를
    '가상 시각의 관측·예보'로 본다: 모든 시각을 offset(시간 단위로 반올림, 예보 시각이 정시라서)만큼 옮긴다."""

    def __init__(self, live: "LiveSources", offset: timedelta, clock: Callable[[], datetime]):
        self.live, self.offset, self._clock = live, offset, clock
        self.shift = _whole_hours(offset)

    @property
    def data_note(self) -> str | None:
        return "실제 데이터 (가상 시각에 맞춰 시각을 옮김)" if self.shift else None

    def now(self) -> datetime:
        return self._clock() + self.offset

    def _f(self, f: Fetched, value) -> Fetched:
        return Fetched(value if f.ok else f.value, f.fetched_at and f.fetched_at + self.shift, f.error)

    def _day(self, d: date, sign: int) -> date:
        return (datetime.combine(d, time(12)) + sign * self.shift).date()

    def sensors(self) -> Fetched[list[SensorReading]]:
        f = self.live.sensors()
        return self._f(f, f.value)

    def breakwater(self) -> Fetched[list[BreakwaterHour]]:
        f = self.live.breakwater()
        return self._f(f, f.value and [replace(b, hour=b.hour + self.shift) for b in f.value])

    def uv(self) -> Fetched[UvReading]:
        f = self.live.uv()
        return self._f(f, f.value and replace(f.value, at=f.value.at + self.shift))

    def forecast(self, now: datetime) -> Fetched[Forecast]:
        f = self.live.forecast(now - self.shift)
        if not f.value:
            return self._f(f, None)
        v = f.value
        return self._f(f, Forecast(base=v.base + self.shift,
                                   hours={t + self.shift: replace(h, time=h.time + self.shift) for t, h in v.hours.items()},
                                   tmin={self._day(d, 1): x for d, x in v.tmin.items()},
                                   tmax={self._day(d, 1): x for d, x in v.tmax.items()}))

    def warnings(self) -> Fetched[Warnings]:
        f = self.live.warnings()
        return self._f(f, f.value and replace(
            f.value, issued_at=f.value.issued_at and f.value.issued_at + self.shift))

    def tide(self, day: date) -> Fetched[list[TideEvent]]:
        f = self.live.tide(self._day(day, -1))
        return self._f(f, f.value and [replace(e, time=e.time + self.shift) for e in f.value])


class SimSources:
    """실제(live) 와 가상 상황(mock) 을 실행 중에 바꾼다 (PUT /safety/sim 의 source). 가상 시각은 두 모드 모두 쓴다.
    발표에서 '실제 DT 데이터 화면 -> 가상 악천후' 를 이어서 보여주기 위한 것이다.
    가상 시각·시간표는 mock 이 갖고, 요청마다 snapshot() 으로 지금 모드의 소스 하나를 꺼내 쓴다 (요청 도중 모드가 바뀌어도 섞이지 않게)."""

    def __init__(self, live: "LiveSources", mock: MockSources, mode: str = "live"):
        self.live, self.mock = live, mock
        self.mode = mode  # live | sim

    @property
    def scenario(self) -> str:
        return self.mock.scenario

    @property
    def timeline(self) -> "tuple[SimSegment, ...]":
        return self.mock.timeline

    def segment_at(self, t: datetime) -> "SimSegment | None":
        return self.mock.segment_at(t)

    @property
    def offset(self) -> timedelta:
        return self.mock.offset

    def _clock(self) -> datetime:
        return self.mock._clock()

    def now(self) -> datetime:
        return self.mock.now()

    def set(self, offset: timedelta | None = None, timeline: "tuple[SimSegment, ...] | None" = None,
            mode: str | None = None) -> None:
        """시간표·가상 시각·모드를 함께 바꾼다. 시간표는 live 에서도 기억해 두었다가 sim 으로 바꾸면 쓴다."""
        with _SIM_LOCK:
            if offset is not None:
                self.mock.offset = offset
            if timeline is not None:
                self.mock.timeline = timeline
            if mode is not None:
                self.mode = mode

    def snapshot(self) -> "MockSources | ShiftedLive":
        with _SIM_LOCK:
            if self.mode == "sim":
                return self.mock
            return ShiftedLive(self.live, self.mock.offset, self.mock._clock)

    # Sources 로도 쓸 수 있게 (보통은 snapshot() 을 쓴다)
    def __getattr__(self, name):
        if name in ("sensors", "breakwater", "uv", "forecast", "warnings", "tide"):
            return getattr(self.snapshot(), name)
        raise AttributeError(name)


def build_sources(cfg: SafetySettings) -> Sources:
    if cfg.sim_enabled:
        return SimSources(LiveSources(cfg), MockSources(cfg.mock_scenario),
                          mode="sim" if cfg.source == "mock" else "live")
    if cfg.source == "mock":
        return MockSources(cfg.mock_scenario)
    return LiveSources(cfg)
