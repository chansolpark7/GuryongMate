"""초안 일정 검토 (결정론적). LLM 이 계획 -> 검토 -> 수정 -> 재검토 하도록 문제 목록을 만든다.

level
- must   : 반드시 고친다 (planner error 위반: 종료 시각 초과, 고정 시각 지각, 사용자 규칙)
- should : 가능하면 고친다 (운영시간·휴무, 긴 이동, 식사 시간에 식당 없음, 같은 날 중복)
- info   : 참고 (하루가 일찍 끝나 시간이 많이 남음)
"""
LONG_TRAVEL_MIN = {"walk": 25, "car": 40}
MEALS = [("점심", 11 * 60 + 30, 13 * 60 + 30), ("저녁", 17 * 60 + 30, 19 * 60 + 30)]
FOOD_TYPES = {"음식점"}
SPARE_MIN = 120


def _min(hhmm: str | None) -> int | None:
    if not hhmm:
        return None
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _fmt(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def _title(item: dict) -> str:
    return item["place"]["title"] if item.get("place") else item["place_id"]


def review(plan: dict) -> list[dict]:
    """plan: planner PlanDetail (dict). 반환: [{"level", "message", "item_id"?, "day"?}]"""
    issues: list[dict] = []
    for v in plan["violations"]:
        if v["code"] == "place_unavailable":
            continue
        issues.append({"level": "must" if v["severity"] == "error" else "should", "message": v["message"],
                       **({"item_id": v["item_id"]} if v.get("item_id") else {}),
                       **({"day": v["day"]} if v.get("day") else {})})

    limit = LONG_TRAVEL_MIN.get(plan["travel_mode"], 25)
    day_end = _min(plan["day_end"])
    for d in plan["days"]:
        active = [i for i in d["items"] if i["status"] != "skipped"]
        if not active:
            # 일정을 짜 달라는 턴에 장소 없이 규칙만 넣고 끝나는 경우를 잡는다
            issues.append({"level": "should", "day": d["day"], "message": f"{d['day']}일차에 일정이 하나도 없습니다."})
            continue
        for i in active:
            if i["travel_minutes"] and i["travel_minutes"] > limit:
                issues.append({"level": "should", "day": d["day"], "item_id": i["id"],
                               "message": f"{d['day']}일차 '{_title(i)}'까지 이동 {i['travel_minutes']}분 "
                                          f"({'도보' if plan['travel_mode'] == 'walk' else '차량'} {limit}분 초과). "
                                          "가까운 곳으로 바꾸거나 순서를 조정하세요."})
        seen: dict[str, int] = {}
        for i in active:
            if i["place_id"] in seen:
                issues.append({"level": "should", "day": d["day"], "item_id": i["id"],
                               "message": f"{d['day']}일차에 '{_title(i)}'가 두 번 들어 있습니다."})
            seen[i["place_id"]] = i["id"]

        first, last = _min(active[0]["start_at"]), _min(d["end_at"])
        if first is None or last is None:
            continue
        for name, a, b in MEALS:
            if first > a or last < b:  # 그 식사 시간대 전체를 일정이 덮을 때만 확인
                continue
            has_food = any((i.get("place") or {}).get("content_type") in FOOD_TYPES
                           and _min(i["start_at"]) is not None and _min(i["start_at"]) < b and _min(i["end_at"]) > a
                           for i in active)
            if not has_food:
                issues.append({"level": "should", "day": d["day"],
                               "message": f"{d['day']}일차 {name} 시간({_fmt(a)}~{_fmt(b)})에 식당이 없습니다."})
        if day_end is not None and day_end - last >= SPARE_MIN:
            issues.append({"level": "info", "day": d["day"],
                           "message": f"{d['day']}일차 일정이 {d['end_at']}에 끝나 {plan['day_end']}까지 "
                                      f"{(day_end - last) // 60}시간 넘게 비어 있습니다."})
    return issues


def summarize(issues: list[dict]) -> str:
    counts = {k: sum(1 for i in issues if i["level"] == k) for k in ("must", "should")}
    if not counts["must"] and not counts["should"]:
        return "문제 없음"
    return f"문제 {counts['must'] + counts['should']}건"
