"""일정 검토(review_plan)와 검토 관문 테스트. 검토 규칙은 순수 함수, 관문은 가짜 LLM 으로."""
from app.agent.review import review, summarize


def item(id_, place_id, start, end, travel=None, content_type="관광지", status="planned", title=None):
    return {"id": id_, "place_id": place_id, "place": {"title": title or place_id, "content_type": content_type},
            "start_at": start, "end_at": end, "travel_minutes": travel, "status": status}


def plan(items, day_end="20:00", mode="walk", violations=()):
    return {"travel_mode": mode, "day_end": day_end, "violations": list(violations),
            "days": [{"day": 1, "end_at": items[-1]["end_at"] if items else None, "items": items}]}


def levels(issues):
    return sorted((i["level"], i["message"].split(" ")[0]) for i in issues)


def test_clean_plan():
    p = plan([item(1, "a", "11:00", "12:00"), item(2, "food", "12:05", "13:05", 5, "음식점"),
              item(3, "b", "13:10", "18:30", 5)], day_end="19:00")
    assert review(p) == [] and summarize([]) == "문제 없음"


def test_violations_map_to_levels():
    p = plan([item(1, "a", "10:00", "11:00")], day_end="11:30", violations=[
        {"code": "day_overflow", "severity": "error", "message": "초과", "day": 1, "item_id": 1},
        {"code": "outside_open_hours", "severity": "warning", "message": "운영시간 밖", "item_id": 1},
        {"code": "place_unavailable", "severity": "warning", "message": "모름"}])
    issues = review(p)
    assert [(i["level"], i["message"]) for i in issues] == [("must", "초과"), ("should", "운영시간 밖")]


def test_long_car_leg_and_walk_left_to_planner():
    items = [item(1, "a", "10:00", "11:00"), item(2, "b", "11:50", "12:50", 50)]
    # 도보 구간은 planner 검증(long_walk 위반)이 구간 수단 기준으로 낸다 -> 여기서 따로 만들지 않는다
    assert not any("이동" in i["message"] for i in review(plan(items, day_end="12:50")))
    # 차량 구간은 40분 넘으면 검토 문제. 구간 수단(leg_mode)이 계획 기본보다 우선
    assert any("차량 이동 50분" in i["message"] for i in review(plan(items, day_end="12:50", mode="car")))
    items[1]["leg_mode"] = "walk"
    assert not any("이동" in i["message"] for i in review(plan(items, day_end="12:50", mode="car")))
    # 직선거리 근사 경고는 일정을 고쳐서 해결할 문제가 아니라 검토 문제로 넣지 않는다
    p = plan(items, day_end="12:50", violations=[{"code": "travel_estimated", "severity": "warning", "message": "추정"}])
    assert review(p) == []


def test_meal_without_restaurant_and_duplicates_and_spare_time():
    p = plan([item(1, "a", "11:00", "12:00"), item(2, "a", "12:05", "13:40", 2), item(3, "b", "13:45", "14:00", 2)],
             day_end="20:00")
    msgs = [(i["level"], i["message"]) for i in review(p)]
    assert any(lv == "should" and "점심" in m for lv, m in msgs)
    assert any(lv == "should" and "두 번" in m for lv, m in msgs)
    assert any(lv == "info" and "비어 있습니다" in m for lv, m in msgs)
    assert summarize(review(p)) == "문제 2건"  # info 는 세지 않는다


def test_meal_only_checked_when_schedule_covers_window():
    p = plan([item(1, "a", "14:00", "16:00")], day_end="16:30")  # 점심 시간대 이후 시작
    assert not any("점심" in i["message"] for i in review(p))


def test_skipped_items_ignored():
    p = plan([item(1, "a", "11:00", "12:00"), item(2, "a", None, None, status="skipped")], day_end="12:30")
    assert not any("두 번" in i["message"] for i in review(p))


def test_empty_day_is_flagged():
    p = plan([], day_end="18:00")
    assert [(i["level"], i["message"]) for i in review(p)] == [("should", "1일차에 일정이 하나도 없습니다.")]
