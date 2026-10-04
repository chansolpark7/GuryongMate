"""직선거리 근사(이전 planner 방식)와 실제 길(routing) 이동시간 비교.

    python scripts/routing/compare.py                       # 기본: 구룡포 주요 장소
    python scripts/routing/compare.py kto-2610501 kto-1909659 --mode car
    GURYONG_API_URL=http://127.0.0.1:8006 python scripts/routing/compare.py

호스트 Python 에 httpx 만 있으면 된다. Windows 에서는 localhost 대신 127.0.0.1 을 쓴다.
"""
import argparse
import math
import os
import sys

import httpx

DEFAULT = [
    "kto-2610501",   # 구룡포항
    "kto-132115",    # 구룡포시장
    "kto-1909659",   # 구룡포 일본인가옥거리
    "kto-2603493",   # 구룡포 근대문화역사관
    "kto-2660046",   # 구룡포과메기문화관
    "kto-127214",    # 구룡포해수욕장
    "kto-127131",    # 호미곶 해맞이광장
]


def estimate(a, b, mode):
    """이전 planner 근사: 직선거리 × 1.3, 도보 70m/분, 차량 500m/분 (주차 시간 제외)."""
    lat1, lng1, lat2, lng2 = map(math.radians, (a["lat"], a["lng"], b["lat"], b["lng"]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    m = 2 * 6_371_000 * math.asin(math.sqrt(h)) * 1.3
    return math.ceil(m / (500 if mode == "car" else 70)), round(m)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("place_ids", nargs="*", default=DEFAULT)
    ap.add_argument("--mode", choices=["walk", "car"], default="walk")
    ap.add_argument("--all-pairs", action="store_true", help="연속 구간 대신 모든 쌍")
    args = ap.parse_args()

    api = httpx.Client(base_url=os.environ.get("GURYONG_API_URL", "http://127.0.0.1:8000"), timeout=30)
    places = []
    for pid in args.place_ids:
        r = api.get(f"/places/{pid}")
        if r.status_code != 200:
            sys.exit(f"{pid}: {r.status_code} {r.text[:200]}")
        places.append(r.json())

    pairs = ([(a, b) for i, a in enumerate(places) for b in places[i + 1:]] if args.all_pairs
             else list(zip(places, places[1:])))
    body = {"mode": args.mode, "legs": [{"from": {"lat": a["lat"], "lng": a["lng"]},
                                         "to": {"lat": b["lat"], "lng": b["lng"]}} for a, b in pairs]}
    res = api.post("/routing/legs", json=body)
    res.raise_for_status()
    data = res.json()
    if not data["engine"]["ok"]:
        print(f"주의: 길찾기 엔진 응답 없음 ({data['engine']['error']}). 아래 '실제 길' 값도 근사다.")

    print(f"| 구간 ({args.mode}) | 근사 분 | 근사 m | 실제 길 분 | 실제 길 m | 차이 |")
    print("|---|---:|---:|---:|---:|---:|")
    for (a, b), leg in zip(pairs, data["legs"]):
        em, ed = estimate(a, b, args.mode)
        mark = "" if leg["source"] == "road" else " (근사)"
        print(f"| {a['title']} → {b['title']} | {em} | {ed} | {leg['minutes']}{mark} | {leg['distance_m']} | "
              f"{leg['minutes'] - em:+d} |")


if __name__ == "__main__":
    main()
