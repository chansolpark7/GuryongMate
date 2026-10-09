"""직접 정의한 장소 (data/places/manual, docs/places/README.md "직접 정의한 장소"). DB 없이."""
import pytest

from app.places.ingest import load_manual, with_manual
from app.places.normalize import build_manual_place, place_chunks


def test_build_manual_place():
    p = build_manual_place({"id": "poi-x", "title": "테스트 방파제", "lat": 35.98, "lng": 129.55, "content_type": "관광지",
                            "addr": "경상북도 포항시 남구 구룡포읍 구룡포리", "category_names": ["방파제"], "tags": ["야외"],
                            "overview": "바다", "note": "기록용"})
    assert (p["source"], p["content_type_id"], p["area"]) == ("manual", "12", "구룡포읍")
    assert p["tags"] == ["관광지", "방파제", "구룡포읍", "야외"] and "note" not in p
    assert "테스트방파제" in p["search_compact"]
    assert place_chunks(p)[0]["text"].startswith("테스트 방파제 (관광지, 방파제, 구룡포읍)")
    for bad in ({"id": "kto-1", "title": "a", "lat": 1, "lng": 1, "content_type": "관광지"},  # 관광공사 id 와 겹침
                {"id": "poi-y", "title": "a", "lat": 1, "lng": 1, "content_type": "방파제"},  # 모르는 종류
                {"id": "poi-z", "title": "", "lat": 1, "lng": 1, "content_type": "관광지"}):
        with pytest.raises(ValueError):
            build_manual_place(bad)


def test_breakwaters_on_safety_lines():
    """방파제 장소는 safety 의 방파제 기준선(60m) 위에 있어야 '방파제' 성질(풍랑 특보 적용)을 받는다."""
    from app.safety import geo

    manual = {p["id"]: p for p in load_manual()}
    for pid, zone in (("poi-gr-breakwater-north", geo.BREAKWATER_NORTH), ("poi-gr-breakwater-south", geo.BREAKWATER_SOUTH)):
        p = manual[pid]
        assert p["content_type"] == "관광지"  # 야외 (실내로 추정되지 않게)
        assert geo.distance_to_zone((p["lat"], p["lng"]), zone) <= zone.radius_m


def test_with_manual_overrides_same_id():
    data = {"categories": [], "stories": [], "places": [{"id": "poi-gr-breakwater-north", "title": "옛 값"},
                                                        {"id": "kto-1", "title": "그대로"}]}
    places = {p["id"]: p["title"] for p in with_manual(data)["places"]}
    assert places["poi-gr-breakwater-north"] == "구룡포 북방파제" and places["kto-1"] == "그대로"
