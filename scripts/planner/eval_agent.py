"""Planner Agent 품질 비교 실험. 같은 시나리오를 여러 번 돌려 최종 변경 제안을 같은 검토 규칙으로 채점한다.

    python scripts/planner/eval_agent.py --url http://127.0.0.1:8002 --label review --runs 2 --out results.jsonl

서버의 설정(AGENT_REVIEW_ROUNDS 등)을 바꿔 가며 --label 만 달리 해서 돌리고, 결과 파일을 --summary 로 비교한다.
"""
import argparse
import json
import pathlib
import sys
import time

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "backend"))
from app.agent.review import review  # noqa: E402  (순수 함수만 가져다 채점에 쓴다)

SCENARIOS = {
    "oneshot": {
        "plan": {"title": "엄마랑 구룡포", "start_date": "2026-10-10", "day_start": "12:30", "day_end": "18:00"},
        "messages": ["엄마랑 구룡포에 처음 가. 나는 옛날 건물이나 역사 이야기 좋아하고 엄마는 맛있는 거 먹는 걸 좋아해. "
                     "오래 걷는 건 힘들어. 걸어 다닐 거야. 일정 짜줘."],
    },
    "recommend_then_course": {
        "plan": {"title": "포항 당일", "start_date": "2026-10-10", "day_start": "10:00", "day_end": "18:00"},
        "messages": ["포항 여행지 추천해줘", "어 이렇게 코스 짜줘"],
    },
    "fix_existing": {
        "plan": {"title": "다듬기", "start_date": "2026-10-10", "day_start": "10:00", "day_end": "18:00"},
        # 운영시간 밖(피어라계단은 저녁 운영), 먼 곳(광남서원), 점심 없음
        "items": ["kto-1909659", "kto-3067962", "kto-1957992", "kto-2610501"],
        "messages": ["이 일정 좀 자연스럽게 다듬어줘. 문제 있으면 고쳐줘."],
    },
}


def run_one(http: httpx.Client, name: str, sc: dict) -> dict:
    plan = http.post("/planner/plans", json=sc["plan"]).raise_for_status().json()
    for pid in sc.get("items", []):
        http.post(f"/planner/plans/{plan['id']}/items", json={"place_id": pid, "stay_minutes": 60}).raise_for_status()
    sid = http.post("/agent/sessions", json={"plan_id": plan["id"]}).raise_for_status().json()["id"]
    started, turns = time.monotonic(), []
    for msg in sc["messages"]:
        t0 = time.monotonic()
        r = http.post(f"/agent/sessions/{sid}/chat", params={"stream": "false"}, json={"message": msg},
                      timeout=httpx.Timeout(10, read=900)).raise_for_status().json()
        turns.append({"seconds": round(time.monotonic() - t0), "steps": [s["kind"] for s in r["steps"]],
                      "reviews": [s["detail"] for s in r["steps"] if s["kind"] == "review"],
                      "cards": [c["type"] for c in r["cards"]], "error": r["error"], "text": r["text"][:300]})
    pending = http.get(f"/planner/plans/{plan['id']}/proposals", params={"status": "pending"}).json()
    out = {"scenario": name, "plan_id": plan["id"], "seconds": round(time.monotonic() - started), "turns": turns,
           "proposal": False}
    if pending:
        after = http.get(f"/planner/plans/{plan['id']}/proposals/{pending[0]['id']}").json()["after"]
        issues = review(after)
        out |= {
            "proposal": True,
            "items": [i["place"]["title"] if i["place"] else i["place_id"] for d in after["days"] for i in d["items"]],
            "errors": sum(v["severity"] == "error" for v in after["violations"]),
            "warnings": sum(v["severity"] == "warning" for v in after["violations"]),
            "must": sum(i["level"] == "must" for i in issues),
            "should": sum(i["level"] == "should" for i in issues),
            "issues": [f"[{i['level']}] {i['message']}" for i in issues if i["level"] != "info"],
        }
    return out


def summary(path: str) -> None:
    rows = [json.loads(line) for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    groups: dict[tuple, list] = {}
    for r in rows:
        groups.setdefault((r["label"], r["scenario"]), []).append(r)
    print(f"{'label':<10} {'scenario':<24} {'n':>2} {'제안':>4} {'must':>5} {'should':>6} {'항목':>5} {'초':>5} {'검토':>4}")
    for (label, scenario), rs in sorted(groups.items()):
        with_p = [r for r in rs if r["proposal"]]
        avg = lambda k: sum(r[k] for r in with_p) / len(with_p) if with_p else float("nan")  # noqa: E731
        reviews = sum(len(t["reviews"]) for r in rs for t in r["turns"]) / len(rs)
        print(f"{label:<10} {scenario:<24} {len(rs):>2} {len(with_p):>4} {avg('must'):>5.1f} {avg('should'):>6.1f} "
              f"{sum(len(r['items']) for r in with_p) / max(len(with_p), 1):>5.1f} "
              f"{sum(r['seconds'] for r in rs) / len(rs):>5.0f} {reviews:>4.1f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8002")
    ap.add_argument("--user", default="eval")
    ap.add_argument("--label", default="run")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--scenario", action="append", choices=list(SCENARIOS))
    ap.add_argument("--out", default="eval_results.jsonl")
    ap.add_argument("--summary", action="store_true", help="결과 파일만 요약")
    a = ap.parse_args()
    if a.summary:
        return summary(a.out)
    http = httpx.Client(base_url=a.url, headers={"X-User-Id": a.user}, timeout=30)
    info = http.get("/agent/info").json()
    for name in a.scenario or list(SCENARIOS):
        for n in range(a.runs):
            r = run_one(http, name, SCENARIOS[name]) | {"label": a.label, "run": n, "provider": info["provider"]}
            with open(a.out, "a", encoding="utf-8") as f:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            print(f"[{a.label}] {name} #{n}: 제안={r['proposal']} must={r.get('must')} should={r.get('should')} "
                  f"항목={len(r.get('items', []))} {r['seconds']}s", flush=True)


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8")
    main()
