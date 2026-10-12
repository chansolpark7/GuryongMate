"""초안 일정 검토 (결정론적). LLM 이 계획 -> 검토 -> 수정 -> 재검토 하도록 문제 목록을 만든다.

level
- must   : 반드시 고친다 (planner error 위반: 종료 시각 초과, 고정 시각 지각, 사용자 규칙)
- should : 가능하면 고친다 (운영시간·휴무, 긴 이동, 식사 시간에 식당 없음, 같은 날 중복)
- info   : 참고 (하루가 일찍 끝나 시간이 많이 남음)
"""
LONG_CAR_MIN = 40  # 차량 구간 (도보 구간은 planner 검증의 long_walk·daily_walk_total 이 본다)
SKIP_CODES = {"place_unavailable", "travel_estimated"}  # 일정을 바꿔서 고칠 수 있는 문제가 아님
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


def review(plan: dict, trip: dict | None = None) -> list[dict]:
    """plan: planner PlanDetail (dict). 반환: [{"level", "message", "item_id"?, "day"?}]
    trip: 동행 모드면 {"day": 오늘 n일차, "now": "HH:MM"}. 고칠 수 없는 지난 일은 검토하지 않는다:
    지난 날 전체, 오늘의 다녀온(done)·머무는(visiting) 곳까지의 이동·중복, 이미 끝난 식사 시간대."""
    issues: list[dict] = []
    today = (trip or {}).get("day") if isinstance((trip or {}).get("day"), int) else None
    now = _min((trip or {}).get("now")) if today is not None else None
    for v in plan["violations"]:
        if v["code"] in SKIP_CODES:
            continue
        issues.append({"level": "must" if v["severity"] == "error" else "should", "message": v["message"],
                       **({"item_id": v["item_id"]} if v.get("item_id") else {}),
                       **({"day": v["day"]} if v.get("day") else {})})

    day_end = _min(plan["day_end"])
    for d in plan["days"]:
        if today is not None and d["day"] < today:
            continue  # 지난 날은 바꿀 수 없다
        on_trip = today is not None and d["day"] == today
        active = [i for i in d["items"] if i["status"] != "skipped"]
        # 다녀온·머무는 곳(까지 오는 길)은 이미 지난 일이라 고칠 수 없다
        fixable = [i for i in active if i["status"] not in ("done", "visiting")] if on_trip else active
        if not active:
            # 일정을 짜 달라는 턴에 장소 없이 규칙만 넣고 끝나는 경우를 잡는다
            issues.append({"level": "should", "day": d["day"], "message": f"{d['day']}일차에 일정이 하나도 없습니다."})
            continue
        for i in fixable:
            mode = i.get("leg_mode") or plan["travel_mode"]
            if mode == "car" and i["travel_minutes"] and i["travel_minutes"] > LONG_CAR_MIN:
                issues.append({"level": "should", "day": d["day"], "item_id": i["id"],
                               "message": f"{d['day']}일차 '{_title(i)}'까지 차량 이동 {i['travel_minutes']}분 "
                                          f"({LONG_CAR_MIN}분 초과). 가까운 곳으로 바꾸거나 순서를 조정하세요."})
        can_fix = {i["id"] for i in fixable}
        seen: dict[str, dict] = {}
        for i in active:
            if (prev := seen.get(i["place_id"])) and (i["id"] in can_fix or prev["id"] in can_fix):
                target = i if i["id"] in can_fix else prev  # 다녀온 쪽이 아니라 고칠 수 있는 쪽을 가리킨다
                issues.append({"level": "should", "day": d["day"], "item_id": target["id"],
                               "message": f"{d['day']}일차에 '{_title(i)}'가 두 번 들어 있습니다."})
            seen[i["place_id"]] = i

        first, last = _min(active[0]["start_at"]), _min(d["end_at"])
        if first is None or last is None:
            continue
        for name, a, b in MEALS:
            if first > a or last < b:  # 그 식사 시간대 전체를 일정이 덮을 때만 확인
                continue
            if on_trip and ((now is not None and now >= b) or not any(
                    (s := _min(i["start_at"])) is not None and s < b for i in fixable)):
                continue  # 여행 중: 이미 지났거나 남은 일정으로는 닿지 않는 식사 시간대
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


LEVEL = {"safe": "안전", "caution": "주의", "danger": "위험"}


def safety_what(i: dict) -> str:
    if i["kind"] == "travel":
        return f"{i['day']}일차 {i['start_at']} 이동({'도보' if i['mode'] == 'walk' else '차량'}) {i['title']}"
    return f"{i['day']}일차 {i['start_at']} '{i['title']}'"


def safety_issues(check: dict) -> list[dict]:
    """planner 안전 점검(SafetyCheck) -> 검토 문제. 위험은 should(가능하면 고친다), 주의는 info(사용자에게 알린다).
    판단하지 않은 항목(먼 날짜·데이터 장애)은 문제로 만들지 않는다 (안전을 이유로 바꾸지 않는다)."""
    out = []
    for i in check.get("items", []):
        if i["risk"] not in ("caution", "danger"):
            continue
        why = "; ".join(h["reason"] for h in i["hazards"] if h["level"] != "info")[:160]
        if i["kind"] == "travel":
            fix = "이동 시간·수단을 바꾸거나 순서를 바꿔 그 길을 피한다"
            if i.get("detour") and i["detour"].get("detour_minutes") is not None:
                fix += f" (우회하면 {i['detour']['detour_minutes']}분 더)"
        else:
            wins = ", ".join(f"{w['start'][5:16].replace('T', ' ')}~{w['end'][11:16]}" for w in i["better_times"][:2])
            fix = (f"신호가 없는 시간대({wins})로 옮기거나 실내 장소로 바꾼다" if wins
                   else "실내 장소 같은 대체 장소로 바꾼다")
        if i["risk"] == "danger":
            out.append({"level": "should", "kind": "safety", "day": i["day"], "item_id": i["item_id"],
                        "message": f"{safety_what(i)} 안전 위험: {why}. {fix}. 못 바꾸면 이유를 사용자에게 설명한다."})
        else:
            out.append({"level": "info", "kind": "safety", "day": i["day"], "item_id": i["item_id"],
                        "message": f"{safety_what(i)} 안전 주의: {why}. 사용자에게 알리고, 필요하면 {fix}."})
    return out


def summarize(issues: list[dict]) -> str:
    counts = {k: sum(1 for i in issues if i["level"] == k) for k in ("must", "should")}
    if not counts["must"] and not counts["should"]:
        return "문제 없음"
    return f"문제 {counts['must'] + counts['should']}건"
