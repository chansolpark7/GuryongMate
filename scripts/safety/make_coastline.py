"""safety 의 해안선 데이터(backend/app/safety/coastline.json)를 다시 만든다. docs/safety/README.md §3 해안.

OpenStreetMap natural=coastline 을 Overpass API 로 받아 구룡포읍 구간(위도 35.925~36.045)만 잘라
Douglas-Peucker 5m 로 단순화한다. 해안선이 바뀌었을 때만 다시 돌리면 된다 (키 필요 없음).

    python scripts/safety/make_coastline.py
"""
import json
import math
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "backend" / "app" / "safety" / "coastline.json"
BBOX = (35.93, 129.51, 36.04, 129.59)  # 조회 범위 (남, 서, 북, 동)
LAT = (35.925, 36.045)  # 남길 위도 범위
TOLERANCE_M = 5
QUERY = f'[out:json][timeout:60];way["natural"="coastline"]{BBOX};out geom;'


def to_segment(p, a, b) -> float:
    k = 111_320 * math.cos(math.radians(p[0]))
    ax, ay = (a[1] - p[1]) * k, (a[0] - p[0]) * 111_320
    bx, by = (b[1] - p[1]) * k, (b[0] - p[0]) * 111_320
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / (dx * dx + dy * dy)))
    return math.hypot(ax + t * dx, ay + t * dy)


def simplify(pts, tol):
    if len(pts) < 3:
        return pts
    i, d = max(((i, to_segment(pts[i], pts[0], pts[-1])) for i in range(1, len(pts) - 1)), key=lambda x: x[1])
    if d <= tol:
        return [pts[0], pts[-1]]
    return simplify(pts[:i + 1], tol)[:-1] + simplify(pts[i:], tol)


def main() -> None:
    req = urllib.request.Request("https://overpass-api.de/api/interpreter",
                                 data=urllib.parse.urlencode({"data": QUERY}).encode(),
                                 headers={"User-Agent": "GuryongMate-dev (safety coastline)", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read().decode("utf-8"))
    lines = []
    for e in data["elements"]:
        run = []
        for g in e.get("geometry", []):
            if LAT[0] <= g["lat"] <= LAT[1]:
                run.append((g["lat"], g["lon"]))
            else:
                if len(run) > 1:
                    lines.append(run)
                run = []
        if len(run) > 1:
            lines.append(run)
    lines = [[[round(a, 6), round(b, 6)] for a, b in simplify(r, TOLERANCE_M)] for r in lines]
    doc = {"source": f"OpenStreetMap natural=coastline (© OpenStreetMap contributors, ODbL), {date.today()} Overpass 조회",
           "note": f"구룡포읍 해안 (위도 {LAT[0]}~{LAT[1]}). Douglas-Peucker {TOLERANCE_M}m 로 단순화. 좌표 [lat, lng]. "
                   "만드는 법: scripts/safety/make_coastline.py",
           "lines": lines}
    OUT.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{OUT}: 선 {len(lines)}개, 점 {sum(len(x) for x in lines)}개")


if __name__ == "__main__":
    main()
