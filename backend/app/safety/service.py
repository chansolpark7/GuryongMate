"""수집(sources) + 해석(risk) 을 묶어 API 응답을 만든다."""
import hashlib
import json
from datetime import date, datetime, time, timedelta

from app.safety import geo, risk
from app.safety.config import settings
from app.safety.places import PlaceLookup, PlacesUnavailable
from app.safety.schemas import (
    AssessRequest, AssessResult, AvoidResult, AvoidZone, BreakwaterActivity, DaySummary, Hazard, HourForecastView,
    Level, Outlook, SafeWindow, SensorView, Situation, SituationHour, SourceStatus, Status, TideView, TimeWindow,
    UvView, VisitAssessment, ZoneStatus,
)
from app.safety.sources import KST, ActiveWarning, Fetched, Forecast, MockSources, Sources

SOURCE_LABEL = {"sensors": "침수 센서", "breakwater": "방파제 CCTV", "uv": "자외선", "forecast": "기상 예보",
                "warnings": "기상특보", "tide": "조위", "places": "장소 좌표"}
MAX_BETTER_TIMES = 3


def _status(f: Fetched, mock: bool) -> SourceStatus:
    return SourceStatus(ok=f.ok, fetched_at=f.fetched_at, error=f.error,
                        note="mock 데이터 (실제 관측·예보 아님)" if mock else None)


def _overall(risks: list[Level | None]) -> Level | None:
    """주의·위험이 있으면 그 단계. 없는데 판단하지 않은 항목이 있으면 None (전체가 안전하다고 읽히지 않게)."""
    if any(r in ("caution", "danger") for r in risks):
        return risk.worst_public([r for r in risks if r])
    if None in risks:
        return None
    return "safe"


def _scoped(hazards: list[Hazard]) -> list[Hazard]:
    """방파제·저지대를 가정하고 낸 예보 위험임을 사유에 붙인다 (특정 장소가 아닌 지역 단위 조회용)."""
    for h in hazards:
        if h.source == "기상청 단기예보" and "기준)" not in h.reason:
            h.reason += " (방파제 기준)" if h.type in ("wind", "wave") else " (해안 저지대 기준)"
    return hazards


class SafetyService:
    def __init__(self, sources: Sources, places: PlaceLookup):
        self.src = sources
        self.places = places
        self.mock = isinstance(sources, MockSources)

    def _now(self, now: datetime | None) -> datetime:
        """기준 시각. 주지 않으면 소스의 시각 (mock 시뮬레이션 중이면 가상 시각)."""
        now = now or self.src.now()
        return now.replace(tzinfo=KST) if now.tzinfo is None else now.astimezone(KST)

    def _forecast(self, now: datetime) -> tuple[Fetched[Forecast], datetime | None]:
        fc = self.src.forecast(now)
        return fc, (fc.value.last_hour if fc.ok and fc.value else None)

    @staticmethod
    def _hours(fc: Fetched[Forecast], start: datetime, end: datetime) -> list:
        if not fc.ok or not fc.value:
            return []
        first = risk.floor_hour(start)
        return [h for t, h in fc.value.hours.items() if first <= t < end or t == first]

    @staticmethod
    def _slot_level(fc: Forecast, start: datetime, end: datetime, sens: set[str], indoor: bool,
                    active: list[ActiveWarning], now: datetime, sensor_hz: list[Hazard]) -> str | None:
        """한 시간대의 최고 등급. 예보가 빠진 시각이 있으면 None (판단 불가)."""
        levels = []
        t = risk.floor_hour(start)
        while t < end:
            h = fc.hours.get(t)
            if h is None:
                return None
            levels.append(risk.hour_level(h, sens, indoor))
            t += timedelta(hours=1)
        levels += [w.level for w in risk.timed_warning_hazards(active, sens, start, now, indoor=indoor)]
        if start - now <= risk.NOW_WINDOW:
            levels += [h.level for h in sensor_hz]
        return risk.worst(levels)

    def _better_times(self, fc: Forecast, duration: timedelta, sens: set[str], indoor: bool,
                      active: list[ActiveWarning], now: datetime, sensor_hz: list[Hazard]):
        """예보 범위 안에서 같은 장소·체류 시간으로 주의·위험이 없는 시간대 (관광 시간대 안, 이른 순)."""
        first, last = risk.TOUR_HOURS
        levels: dict[datetime, str] = {}
        s = risk.floor_hour(now) + timedelta(hours=1)
        while s + duration <= (fc.last_hour or s) + timedelta(hours=1):
            end = s + duration
            if s.hour >= first and end.date() == s.date() and end <= s.replace(hour=last):
                lv = self._slot_level(fc, s, end, sens, indoor, active, now, sensor_hz)
                if lv is not None:
                    levels[s] = lv
            s += timedelta(hours=1)
        return risk.safe_windows(levels, duration)[:MAX_BETTER_TIMES]

    # ---------- 지금 상태 ----------
    def status(self, now: datetime | None = None) -> Status:
        now = self._now(now)
        sensors, bw, uv, warns = self.src.sensors(), self.src.breakwater(), self.src.uv(), self.src.warnings()
        fc, _ = self._forecast(now)
        active = warns.value.active if warns.ok and warns.value else []
        soon = self._hours(fc, now, now + timedelta(hours=3))

        zones: list[ZoneStatus] = []
        for side, zone in geo.BREAKWATER_SIDE.items():
            hz = (risk.warning_hazards(active, {"breakwater"}, warns.fetched_at)
                  + risk.forecast_hazards(soon, {"breakwater"})
                  + (risk.breakwater_hazards(bw.value, side, now) if bw.ok and bw.value else []))
            gaps = not (warns.ok and fc.ok and bw.ok)
            lv = risk.public(risk.worst(h.level for h in hz))
            zones.append(ZoneStatus(id=zone.id, name=zone.name, kind="breakwater",
                                    risk=None if gaps and lv == "safe" else lv,
                                    reasons=[h.reason for h in hz] or (["데이터 일부를 받지 못함"] if gaps else [])))

        flood_common = (risk.warning_hazards(active, {"flood"}, warns.fetched_at)
                        + risk.forecast_hazards(soon, {"flood"}))
        sensor_views: list[SensorView] = []
        if sensors.ok and sensors.value:
            for r in sensors.value:
                lv, why = risk.sensor_risk(r)
                sensor_views.append(SensorView(id=r.id, name=r.name, type=r.type, lat=r.lat, lng=r.lng,
                                               value=r.value, level=r.level,
                                               risk=None if lv == "unknown" else risk.public(lv),
                                               note=why if lv == "info" else None))
                if r.type in geo.FLOOD_SENSOR_TYPES:
                    reasons = ([why] if why else []) + [h.reason for h in flood_common]
                    z = geo.flood_zone(r.id, r.name, r.lat, r.lng)
                    zlv = risk.public(risk.worst([lv, *(h.level for h in flood_common)]))
                    zones.append(ZoneStatus(id=z.id, name=z.name, kind="flood", reasons=reasons,
                                            risk=None if lv == "unknown" and zlv == "safe" else zlv))
        else:
            for z in geo.STATIC_FLOOD_ZONES:
                reasons = [h.reason for h in flood_common] or ["침수 센서 데이터를 받지 못함"]
                zlv = risk.public(risk.worst(h.level for h in flood_common))
                zones.append(ZoneStatus(id=z.id, name=z.name, kind="flood", reasons=reasons,
                                        risk=zlv if zlv != "safe" else None))

        activity = []
        if bw.ok and bw.value:
            for side in ("north", "south"):
                mine = [r for r in bw.value if r.side == side]
                if mine:
                    r = max(mine, key=lambda x: x.hour)
                    activity.append(BreakwaterActivity(side=side, name=geo.BREAKWATER_SIDE[side].name, hour=r.hour,
                                                       fall=r.fall, intrusion=r.intrusion, approach=r.approach,
                                                       abnormal=r.abnormal))

        overall = _overall([z.risk for z in zones])
        data = {"sensors": sensors, "breakwater": bw, "uv": uv, "forecast": fc, "warnings": warns}
        missing = [SOURCE_LABEL[k] for k, f in data.items() if not f.ok]
        risky = [z.name for z in zones if z.risk in ("caution", "danger")]
        if risky:
            guidance = (f"지금 {', '.join(risky[:4])}{' 등' if len(risky) > 4 else ''}에 주의·위험 신호가 있다. "
                        "이 구역이 포함된 오늘 일정은 시간 조정이나 대안을 고려하라.")
        elif overall is None:
            guidance = "확인된 위험 신호는 없지만 판단하지 못한 구역이 있다(risk=null). 안전하다고 단정하지 마라."
        else:
            guidance = "현재 실측·특보·예보상 특별한 위험 신호가 없다."
        if missing:
            guidance += f" 일부 데이터({', '.join(missing)})를 받지 못해 관련 판단을 하지 못했다."
        guidance += " 이 결과는 지금 상태다. 다른 날짜의 일정은 /safety/assess 나 /safety/outlook 으로 판단하라."
        return Status(
            now=now, overall_risk=overall, zones=zones, sensors=sensor_views, breakwater_activity=activity,
            uv=UvView(index=uv.value.index, at=uv.value.at) if uv.ok and uv.value else None,
            warnings=[f"{w.name} : {w.areas}" for w in active],
            preliminary_warnings=warns.value.preliminary if warns.ok and warns.value else [],
            next_hours=_scoped(risk.forecast_hazards(self._hours(fc, now, now + timedelta(hours=12)),
                                                     {"breakwater", "flood"})),
            guidance=guidance, data_status={k: _status(f, self.mock) for k, f in data.items()},
        )

    # ---------- 길찾기 우회 구역 ----------
    def avoid(self, now: datetime | None = None, min_risk: str = "caution") -> AvoidResult:
        """지금 길찾기에서 피할 구역. 그 구역에 해당하는 근거가 있을 때만 넣는다.
        - 침수: 그 센서 자신의 실측 등급. 지역 전체에 같은 값인 예보·특보로는 넣지 않는다 (넣으면 중심가가 전부 막힌다)
        - 방파제: /status 의 구역 위험도 (특보·예보·CCTV)
        """
        st = self.status(now)
        floor = risk.ORDER.index(min_risk)
        hit = lambda lv: lv in risk.ORDER and risk.ORDER.index(lv) >= floor  # noqa: E731

        breakwaters = {z.id: z for z in geo.BREAKWATERS}
        zones = [AvoidZone(id=z.id, name=z.name, kind="breakwater", risk=z.risk, reason="; ".join(z.reasons),
                           polygon=geo.zone_polygon(breakwaters[z.id]))
                 for z in st.zones if z.kind == "breakwater" and hit(z.risk)]
        for s in st.sensors:
            if s.type in geo.FLOOD_SENSOR_TYPES and hit(s.risk):
                z = geo.flood_zone(s.id, s.name, s.lat, s.lng)
                zones.append(AvoidZone(id=z.id, name=z.name, kind="flood", risk=s.risk,
                                       reason=f"{s.name} 수위 등급 {s.level} ({'위험' if s.risk == 'danger' else '주의'})",
                                       polygon=geo.zone_polygon(z, settings.avoid_flood_radius_m)))

        data = {k: st.data_status[k] for k in ("sensors", "breakwater", "forecast", "warnings")}
        missing = [SOURCE_LABEL[k] for k, s in data.items() if not s.ok]
        if zones:
            guidance = f"{', '.join(z.name for z in zones)}을(를) 지나지 않는 길로 안내하라."
        else:
            guidance = "지금 길찾기에서 피해야 할 구역은 없다."
        if missing:
            guidance += f" 일부 데이터({', '.join(missing)})를 받지 못해 그 구역은 판단하지 못했다."
        return AvoidResult(now=st.now, min_risk=min_risk, zones=zones, guidance=guidance, data_status=data)

    # ---------- 날짜별 전망 ----------
    def outlook(self, day: date, now: datetime | None = None, day_start: time = time(9), day_end: time = time(18),
                detail: bool = False) -> Outlook:
        now = self._now(now)
        start = datetime.combine(day, day_start, KST)
        end = datetime.combine(day, day_end, KST)
        fc, last = self._forecast(now)
        t = risk.classify(start, end, now, last, fc.ok)
        data: dict[str, Fetched] = {"forecast": fc}

        hours = self._hours(fc, max(start, now), end) if t.forecast_available else []
        hazards = _scoped(risk.forecast_hazards(hours, {"breakwater", "flood"}))
        warnings: list[str] = []
        active: list[ActiveWarning] = []
        if t.horizon != "past" and start - now <= risk.WARNING_WINDOW:
            warns = self.src.warnings()
            data["warnings"] = warns
            if warns.ok and warns.value:
                active = warns.value.active
                warnings = [f"{w.name} : {w.areas}" for w in active]
                hazards = risk.timed_warning_hazards(active, {"breakwater", "flood"}, max(start, now), now,
                                                     warns.fetched_at) + hazards

        safe: list[SafeWindow] = []
        for kind, sens in (("breakwater", {"breakwater"}), ("flood", {"flood"}), ("outdoor", set())):
            levels = {h.time: risk.worst([risk.hour_level(h, sens), *(
                w.level for w in risk.timed_warning_hazards(active, sens, max(h.time, now), now))]) for h in hours}
            safe += [SafeWindow(place_kind=kind, start=a, end=b, has_info=info)
                     for a, b, info in risk.safe_windows(levels, timedelta(hours=1))]

        tides: list[TideView] = []
        if t.horizon != "past":
            td = self.src.tide(day)
            data["tide"] = td
            if td.ok and td.value:
                tides = [TideView(time=e.time, kind=e.kind, level_cm=e.level_cm) for e in td.value]

        summary = None
        if hours:
            skies = [risk.SKY.get(h.sky) for h in hours if h.sky in risk.SKY]
            vals = lambda attr: [getattr(h, attr) for h in hours if getattr(h, attr) is not None]  # noqa: E731
            summary = DaySummary(
                max_pop=max(vals("pop"), default=None), total_pcp_mm=round(sum(vals("pcp_mm")), 1),
                max_wind_ms=max(vals("wsd"), default=None), max_wave_m=max(vals("wav"), default=None),
                temp_min=fc.value.tmin.get(day), temp_max=fc.value.tmax.get(day),
                sky=max(set(skies), key=skies.count) if skies else None)

        days = (day - now.date()).days
        if t.horizon == "past":
            guidance = "지난 날짜라 평가하지 않았다."
        elif t.horizon in ("medium_range", "long_range"):
            guidance = (f"{days}일 뒤라 " + ("날씨·바다 상태를 예측할 수 없다." if t.horizon == "long_range"
                                            else "아직 시간별 예보 범위 밖이다.")
                        + " 날씨를 이유로 일정을 바꾸지 마라. 만조·간조 시각과 계절 경향만 참고로 쓸 수 있다."
                        f" {t.recheck_from:%Y-%m-%d}부터 다시 확인하라.")
        elif not t.forecast_available:
            guidance = "기상 예보를 받지 못해 판단하지 못했다. 안전하다고 말하지 마라."
        elif any(h.level in ("caution", "danger") for h in hazards):
            guidance = ("주의·위험 신호가 있다. 방파제·해안 일정은 신호가 없는 시간대(safe_windows)로 옮기거나 "
                        "실내 대안을 고려하라. 실내 장소는 이동할 때만 영향을 받는다."
                        + (" 예보는 바뀔 수 있으니 단정하지 마라." if t.horizon == "short_range" else ""))
        else:
            guidance = "예보상 특별한 위험 신호가 없다. 예보는 바뀔 수 있다."

        return Outlook(
            date=day, now=now, horizon=t.horizon, forecast_available=t.forecast_available, confidence=t.confidence,
            summary=summary, hazards=hazards, safe_windows=safe,
            hourly=[HourForecastView(time=h.time, pop=h.pop, pcp_mm=h.pcp_mm, sky=risk.SKY.get(h.sky),
                                     precip=risk.PTY.get(h.pty), wind_ms=h.wsd, wave_m=h.wav, temp=h.tmp)
                    for h in sorted(hours, key=lambda x: x.time)] if detail else [],
            tides=tides, warnings=warnings,
            seasonal_notes=risk.seasonal_notes(day) if t.horizon in ("medium_range", "long_range") else [],
            guidance=guidance, recheck_from=t.recheck_from,
            data_status={k: _status(f, self.mock) for k, f in data.items()},
        )

    # ---------- 일정 항목 평가 ----------
    def assess(self, req: AssessRequest) -> AssessResult:
        now = self._now(req.now)
        fc, last = self._forecast(now)
        data: dict[str, Fetched | SourceStatus] = {"forecast": fc}
        place_error: str | None = None

        prepared = []
        for v in req.visits:
            lat, lng, label, notes, indoor = v.lat, v.lng, v.label, [], v.indoor
            if v.path is not None:  # 이동 경로: 장소를 찾지 않는다. 차 안은 실내처럼 (비·바람은 참고까지)
                lat, lng = v.path[0]
                indoor = v.mode == "car"
            if indoor is None and v.content_type:
                indoor = v.content_type in risk.INDOOR_TYPES
            if v.path is None and v.place_id and (lat is None or lng is None or indoor is None):
                try:
                    p = self.places.get(v.place_id)
                except PlacesUnavailable as e:
                    p, place_error = None, str(e)
                if p:
                    if lat is None or lng is None:
                        lat, lng = p.lat, p.lng
                    label = label or p.title
                    if indoor is None and p.content_type:
                        indoor = p.content_type in risk.INDOOR_TYPES
                elif lat is None or lng is None:
                    notes.append("장소 좌표를 알 수 없어 지역 공통 정보로만 판단했다."
                                 if p is None and place_error is None else
                                 "장소 정보를 받지 못해 방파제·침수 구역인지 알 수 없다.")
            start = datetime.combine(v.date, v.start, KST)
            end = datetime.combine(v.date, v.end, KST) if v.end else start + timedelta(minutes=60)
            prepared.append((v, lat, lng, label, notes, indoor, start, end,
                             risk.classify(start, end, now, last, fc.ok)))
        if any(v.place_id and v.path is None for v, *_ in prepared):
            data["places"] = SourceStatus(ok=place_error is None, error=place_error)

        # 필요한 데이터만 부른다 (먼 날짜만 있으면 실측·특보는 부르지 않는다)
        near = [t.horizon in ("now", "short_range") for *_, t in prepared]
        sensors = self.src.sensors() if any(t.horizon == "now" for *_, t in prepared) else None
        bw = self.src.breakwater() if sensors is not None else None
        warns = self.src.warnings() if any(near) else None
        for k, f in (("sensors", sensors), ("breakwater", bw), ("warnings", warns)):
            if f is not None:
                data[k] = f
        active = warns.value.active if warns is not None and warns.ok and warns.value else []
        tides: dict[date, Fetched] = {}

        results = []
        for v, lat, lng, label, notes, indoor, start, end, t in prepared:
            pts = geo.densify(v.path) if v.path is not None else None
            if pts is not None:
                sens = risk.path_sensitivity(pts)
                if v.mode == "car":
                    sens &= {"flood"}  # 차는 방파제에 들어가지 않는다. 침수 구역(잠긴 길)은 그대로
            else:
                sens = risk.sensitivity(lat, lng)
            hazards: list[Hazard] = []
            sensor_hz: list[Hazard] = []
            # 장소 조회 장애로 좌표를 모르면 방파제·침수 구역 여부를 판단할 수 없다 -> 낮게 판단하지 않고 판단 보류
            missing = lat is None and v.place_id is not None and place_error is not None
            if t.horizon in ("now", "short_range"):
                if t.forecast_available:
                    hazards += risk.forecast_hazards(self._hours(fc, max(start, now), end), sens, bool(indoor))
                else:
                    missing = True
                if start - now <= risk.WARNING_WINDOW:
                    if warns is not None and warns.ok:
                        hazards += risk.timed_warning_hazards(active, sens, max(start, now), now, warns.fetched_at,
                                                              bool(indoor))
                    else:
                        missing = True
                if lat is not None and sensors is not None:
                    if sensors.ok and sensors.value:
                        sensor_hz = risk.sensor_hazards(sensors.value, lat, lng, sensors.fetched_at, pts)
                        if t.horizon == "now" and risk.sensor_unknown_near(sensors.value, lat, lng, pts):
                            missing = True  # 반경 안 센서가 등급을 주지 않음
                    elif t.horizon == "now" and "flood" in sens:
                        missing = True
                if t.horizon == "now":
                    hazards += sensor_hz
                    if lat is not None and "breakwater" in sens:
                        if bw is not None and bw.ok and bw.value is not None:
                            at = next((q for q in pts if geo.zones_at(q, geo.BREAKWATERS)), (lat, lng)) if pts else (lat, lng)
                            hazards += risk.breakwater_hazards(bw.value, risk.nearest_breakwater_side(*at), now)
                        else:
                            missing = True  # 방파제 CCTV 를 받지 못함
            hazards.sort(key=lambda h: -risk.ORDER.index(h.level))

            if t.horizon != "past" and sens:
                if start.date() not in tides:
                    tides[start.date()] = self.src.tide(start.date())
                td = tides[start.date()]
                if td.ok and td.value:
                    notes += risk.tide_notes(td.value, start, end)
            if t.horizon == "long_range":
                notes += risk.seasonal_notes(start.date())

            # 주의·위험 근거가 있으면 그 단계. 없으면 판단할 수 있었을 때만 safe (참고 신호는 hazards 에 남는다)
            level: Level | None = risk.public(risk.worst(h.level for h in hazards))
            unassessed = None
            if level == "safe":
                if t.horizon == "past":
                    unassessed = "past"
                elif t.horizon in ("medium_range", "long_range"):
                    unassessed = "beyond_forecast"
                elif missing:
                    unassessed = "data_missing"
                if unassessed:
                    level = None

            better, searched = [], False
            if level in ("caution", "danger") and fc.ok and fc.value:
                searched = True
                better = self._better_times(fc.value, end - start, sens, bool(indoor), active, now, sensor_hz)
            results.append(VisitAssessment(
                key=v.key, kind="travel" if v.path is not None else "visit", mode=v.mode,
                place_id=v.place_id, label=label, start=start, end=end, indoor=None if v.path is not None else indoor,
                horizon=t.horizon, forecast_available=t.forecast_available, confidence=t.confidence, risk=level,
                unassessed_reason=unassessed, data_missing=missing, hazards=hazards,
                sensitivity=[risk.SENS_LABEL[s] for s in sorted(sens)] + (["실내"] if indoor and v.path is None else []),
                notes=notes,
                advice=risk.visit_advice(t, level, sens, now, start, hazards, missing, bool(indoor), better, searched,
                                         travel=v.mode),
                better_times=[TimeWindow(start=a, end=b, has_info=info) for a, b, info in better],
                recheck_from=t.recheck_from))
        if tides:
            data["tide"] = next(iter(tides.values())) if len(tides) == 1 else min(
                tides.values(), key=lambda f: f.ok)  # 하나라도 실패했으면 그것을 보여준다

        status = {k: (f if isinstance(f, SourceStatus) else _status(f, self.mock)) for k, f in data.items()}
        future = [r for r in results if r.horizon != "past"]
        rechecks = [r.recheck_from for r in results if r.recheck_from]
        return AssessResult(
            now=now, overall_risk=_overall([r.risk for r in future]),
            guidance=risk.assess_guidance([r.horizon for r in results], [r.risk for r in results],
                                          min(rechecks) if rechecks else None,
                                          [SOURCE_LABEL[k] for k, s in status.items() if not s.ok]),
            visits=results, data_status=status, assumptions=risk.ASSUMPTIONS,
        )

    # ---------- 상황 요약 (변화 감지용) ----------
    def situation(self, now: datetime | None = None) -> Situation:
        """지금 상황을 요약하고 fingerprint 를 붙인다. 이용자(planner)는 fingerprint 가 바뀌었을 때만 일정을 다시 점검한다.
        시각별 단계는 예보 + 그 시각에 적용되는 특보로 계산한다 (실측 센서·CCTV 는 zones 에 반영된다)."""
        now = self._now(now)
        st = self.status(now)
        fc, last = self._forecast(now)
        warns = self.src.warnings()
        active = warns.value.active if warns.ok and warns.value else []
        kinds = {"breakwater": ({"breakwater"}, False), "flood": ({"flood"}, False),
                 "outdoor": (set(), False), "indoor": (set(), True)}
        hours: list[SituationHour] = []
        if fc.ok and fc.value:
            for t in sorted(x for x in fc.value.hours if x >= risk.floor_hour(now)):
                h = fc.value.hours[t]
                raw = {k: risk.worst([risk.hour_level(h, sens, indoor), *(
                    w.level for w in risk.timed_warning_hazards(active, sens, t, now, indoor=indoor))])
                       for k, (sens, indoor) in kinds.items()}
                hours.append(SituationHour(time=t, levels={k: risk.public(v) for k, v in raw.items()},
                                           info=[k for k, v in raw.items() if v == "info"]))
        zones = {z.id: z.risk for z in st.zones}
        data_ok = {k: v.ok for k, v in st.data_status.items()}
        # 해시에는 신호가 있는 시각만 넣는다. 아무 일 없는 시각이 지나가는 것만으로는 fingerprint 가 바뀌지 않게
        notable = [[h.time.isoformat(), h.levels, h.info] for h in hours
                   if h.info or any(v != "safe" for v in h.levels.values())]
        basis = {"until": last.isoformat() if last else None, "zones": zones, "warnings": st.warnings,
                 "hours": notable, "ok": data_ok}
        digest = hashlib.sha256(json.dumps(basis, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
        return Situation(now=now, fingerprint=digest, forecast_until=last, zones=zones, warnings=st.warnings,
                         hours=hours, data_ok=data_ok)
