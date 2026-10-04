"""이동시간·경로 계산. 엔진(Valhalla)이 없거나 길을 못 찾으면 그 값만 직선거리 근사로 채운다."""
import math
import time as _time
from collections import OrderedDict
from datetime import datetime

from app.routing import narrative
from app.routing.config import settings
from app.routing.estimate import estimate
from app.routing.hazards import AvoidZone, HazardLookup, HazardsUnavailable, line_hits, point_in
from app.routing.schemas import (
    EngineStatus, HazardReport, LegResult, LegsResult, LineString, MatrixRequest, MatrixResult, Mode, Point,
    RouteLeg, RouteRequest, RouteResult, RouteSummary, Step, WalkOptions,
)
from app.routing.valhalla import EngineUnavailable, NoRoute, ValhallaClient, costing, decode_polyline6

Pair = tuple[Point, Point]


def minutes(seconds: float) -> int:
    return math.ceil(seconds / 60 - 1e-9)


class TtlCache:
    """(키 -> 값) LRU + 만료. 엔진이 계산한 값만 넣는다 (근사값은 넣지 않아 엔진이 돌아오면 바로 바뀐다)."""

    def __init__(self, size: int, ttl: float):
        self.size, self.ttl = size, ttl
        self._data: OrderedDict = OrderedDict()

    def get(self, key):
        hit = self._data.get(key)
        if hit is None or _time.monotonic() - hit[0] > self.ttl:
            return None
        self._data.move_to_end(key)
        return hit[1]

    def put(self, key, value) -> None:
        self._data[key] = (_time.monotonic(), value)
        self._data.move_to_end(key)
        while len(self._data) > self.size:
            self._data.popitem(last=False)

    def clear(self) -> None:
        self._data.clear()


cache = TtlCache(settings.cache_size, settings.cache_ttl_sec)


def _key(p: Point) -> tuple[float, float]:
    return round(p.lat, 6), round(p.lng, 6)


def _leg(seconds: float, meters: float, source: str) -> LegResult:
    if meters < 1:
        return LegResult(minutes=0, distance_m=0, source=source)
    return LegResult(minutes=minutes(seconds), distance_m=round(meters), source=source)


class RoutingService:
    def __init__(self, engine: ValhallaClient, hazards: HazardLookup):
        self.engine = engine
        self.hazards = hazards

    # ---------- 구간·행렬 ----------
    def pairs(self, pairs: list[Pair], mode: Mode, walk: WalkOptions) -> tuple[list[LegResult], EngineStatus]:
        cost, opts = costing(mode, walk.walking_speed_kmh, walk.easy_walk)
        prefix = (cost, tuple(sorted(opts.items())))
        out: list[LegResult | None] = [None] * len(pairs)
        missing: dict[tuple, list[int]] = {}
        for n, (a, b) in enumerate(pairs):
            k = (*prefix, _key(a), _key(b))
            hit = cache.get(k)
            if hit is not None:
                out[n] = hit
            elif _key(a) == _key(b):
                out[n] = _leg(0, 0, "road")
            else:
                missing.setdefault(k, []).append(n)

        status = EngineStatus(ok=True)
        if missing:
            todo = [pairs[ns[0]] for ns in missing.values()]
            try:
                found = self._engine_pairs(todo, cost, opts)
            except EngineUnavailable as e:
                found, status = [None] * len(todo), EngineStatus(ok=False, error=str(e))
            for (k, ns), (a, b), f in zip(missing.items(), todo, found):
                if f is not None:
                    leg = _leg(*f, "road")
                    cache.put(k, leg)
                else:
                    leg = _leg(*estimate(a, b, mode, opts.get("walking_speed", settings.walking_speed_kmh)), "estimate")
                for n in ns:
                    out[n] = leg
        return out, status

    def _engine_pairs(self, pairs: list[Pair], cost: str, opts: dict) -> list[tuple[float, float] | None]:
        """출발지·도착지 고유 지점으로 행렬 한 번. 한 지점이 도로에서 너무 멀어 전체가 실패하면 구간별로 다시."""
        srcs = list({_key(a): a for a, _ in pairs}.values())
        dsts = list({_key(b): b for _, b in pairs}.values())
        si = {_key(p): i for i, p in enumerate(srcs)}
        di = {_key(p): i for i, p in enumerate(dsts)}
        try:
            m = self.engine.matrix(srcs, dsts, cost, opts)
            return [m[si[_key(a)]][di[_key(b)]] for a, b in pairs]
        except NoRoute:
            if len(pairs) == 1:
                return [None]
        out = []
        for a, b in pairs:
            try:
                out.append(self.engine.matrix([a], [b], cost, opts)[0][0])
            except NoRoute:
                out.append(None)
        return out

    def legs(self, pairs: list[Pair], mode: Mode, walk: WalkOptions) -> LegsResult:
        legs, status = self.pairs(pairs, mode, walk)
        return LegsResult(mode=mode, legs=legs, engine=status)

    def matrix(self, req: MatrixRequest) -> MatrixResult:
        flat, status = self.pairs([(a, b) for a in req.sources for b in req.targets], req.mode, req)
        n = len(req.targets)
        return MatrixResult(mode=req.mode, matrix=[flat[i * n:(i + 1) * n] for i in range(len(req.sources))],
                            engine=status)

    # ---------- 경로 ----------
    def route(self, req: RouteRequest) -> RouteResult:
        cost, opts = costing(req.mode, req.walking_speed_kmh, req.easy_walk)
        speed = opts.get("walking_speed", settings.walking_speed_kmh)
        try:
            base = self._trip(req, cost, opts)
        except (EngineUnavailable, NoRoute) as e:
            down = isinstance(e, EngineUnavailable)
            fallback = self._straight(req, speed)
            fallback.engine = EngineStatus(ok=not down, error=str(e) if down else None)
            fallback.hazards = HazardReport(
                checked=False, on_route=[], avoided=[],
                note=("길찾기 엔진 응답이 없어" if down else "도로에서 길을 찾지 못해")
                     + " 직선거리로 근사했다. 위험 구역은 확인하지 않았다.")
            return fallback

        if req.hazards == "ignore":
            return base
        try:
            zones = self.hazards.zones(req.now)
        except HazardsUnavailable as e:
            base.hazards = HazardReport(checked=False, on_route=[], avoided=[],
                                        note=f"안전 정보를 받지 못해 위험 구역을 확인하지 않았다 ({e}).")
            return base

        on = self._on_route(base, zones)
        base.hazards = HazardReport(checked=True, on_route=[z.hit() for z in on], avoided=[])
        if req.hazards == "report" or not on:
            return base

        # 출발·경유·도착 지점이 들어 있는 구역은 막을 수 없다 (막으면 길 자체가 없다)
        blockable = [z for z in zones if not any(point_in(z, s) for s in req.stops)]
        targets = [z for z in on if z in blockable]
        if not targets:
            base.hazards.note = "출발지·경유지·도착지가 위험 구역 안이라 우회할 수 없다."
            return base
        try:
            alt = self._trip(req, cost, opts, exclude=[list(z.polygon) for z in blockable])
        except (EngineUnavailable, NoRoute) as e:
            base.hazards.note = f"위험 구역을 피하는 길을 찾지 못해 원래 경로를 돌려준다 ({e})."
            return base

        alt.hazards = HazardReport(
            checked=True, on_route=[z.hit() for z in self._on_route(alt, zones)], avoided=[z.hit() for z in targets],
            baseline=RouteSummary(minutes=base.minutes, distance_m=base.distance_m),
            detour_minutes=alt.minutes - base.minutes)
        if alt.hazards.on_route:
            alt.hazards.note = "출발지·경유지·도착지가 들어 있는 위험 구역은 우회하지 못했다."
        return alt

    @staticmethod
    def _on_route(r: RouteResult, zones: list[AvoidZone]) -> list[AvoidZone]:
        line = [tuple(c) for c in r.geometry.coordinates]
        return [z for z in zones if line_hits(line, z.polygon)]

    def _trip(self, req: RouteRequest, cost: str, opts: dict, exclude=None) -> RouteResult:
        trip = self.engine.route(req.stops, cost, opts, exclude)
        coords: list[tuple[float, float]] = []
        legs: list[RouteLeg] = []
        for leg in trip["legs"]:
            shape = decode_polyline6(leg["shape"])
            offset = max(len(coords) - 1, 0)  # 다음 구간의 첫 점 = 앞 구간의 끝 점
            coords += shape if not coords else shape[1:]
            steps = []
            if req.steps:
                for m in leg.get("maneuvers", []):
                    kind, text = narrative.describe(m)
                    names = m.get("street_names") or []
                    steps.append(Step(type=kind, instruction=text, street=names[0] if names else None,
                                      distance_m=round(float(m.get("length", 0)) * 1000),
                                      seconds=round(float(m.get("time", 0))),
                                      begin_index=offset + int(m.get("begin_shape_index", 0))))
            s = leg["summary"]
            legs.append(RouteLeg(minutes=minutes(s["time"]), distance_m=round(s["length"] * 1000), steps=steps))
        s = trip["summary"]
        return RouteResult(mode=req.mode, source="road", minutes=minutes(s["time"]), distance_m=round(s["length"] * 1000),
                           geometry=LineString(coordinates=coords), legs=legs,
                           hazards=HazardReport(checked=False, on_route=[], avoided=[]), engine=EngineStatus(ok=True))

    @staticmethod
    def _straight(req: RouteRequest, speed: float) -> RouteResult:
        legs, total_s, total_m = [], 0.0, 0.0
        for a, b in zip(req.stops, req.stops[1:]):
            sec, m = estimate(a, b, req.mode, speed)
            total_s, total_m = total_s + sec, total_m + m
            legs.append(RouteLeg(minutes=minutes(sec), distance_m=round(m), steps=[]))
        return RouteResult(mode=req.mode, source="estimate", minutes=minutes(total_s), distance_m=round(total_m),
                           geometry=LineString(coordinates=[(p.lng, p.lat) for p in req.stops]), legs=legs,
                           hazards=HazardReport(checked=False, on_route=[], avoided=[]), engine=EngineStatus(ok=True))

    def status(self) -> dict:
        try:
            s = self.engine.status()
        except EngineUnavailable as e:
            return {"engine": {"ok": False, "error": str(e)}}
        return {"engine": {"ok": True, "version": s.get("version"),
                           "tileset_last_modified": datetime.fromtimestamp(s["tileset_last_modified"]).isoformat()
                           if s.get("tileset_last_modified") else None}}
