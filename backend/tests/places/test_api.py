"""places API 테스트. 실제 DB(docker compose 의 db)에 test- 접두사 데이터를 넣고 지운다.

    docker compose run --rm api pytest tests/places
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.db import SessionLocal, init_db
from app.main import app
from app.places.models import Place, PlaceStory

BASE = dict(source="test", content_id="0", addr="", tel="", homepage="", overview="", open_hours="", rest_date="",
            parking="", fee="", menu="", category_codes=[], category_names=[], image_url="", thumbnail_url="",
            images=[], copyright_type="", intro={}, extra_info=[], source_modified_at="")
PLACES = [
    Place(**{**BASE, "overview": "근대 역사 거리"}, id="test-house", content_type_id="12", content_type="관광지",
          title="테스트 일본인가옥거리", lat=35.9880, lng=129.5570, area="구룡포읍", tags=["관광지", "근대유적", "구룡포읍"]),
    Place(**{**BASE, "menu": "물회, 과메기"}, id="test-food", content_type_id="39", content_type="음식점",
          title="테스트 물회집", lat=35.9900, lng=129.5560, area="구룡포읍", tags=["음식점", "구룡포읍"]),
    Place(**BASE, id="test-homi", content_type_id="12", content_type="관광지", title="테스트 호미곶 광장",
          lat=36.0765, lng=129.5670, area="호미곶면", tags=["관광지", "호미곶면"]),
]
STORY = PlaceStory(id="test-story", place_id="test-house", title="가옥거리 이야기", script="옛날 이야기", play_time_sec=90,
                   lat=35.9881, lng=129.5571, odii_theme_title="가옥거리", audio_url="", image_url="")


@pytest.fixture(scope="module")
def client():
    init_db()

    def cleanup():
        with SessionLocal.begin() as s:
            s.execute(delete(PlaceStory).where(PlaceStory.id.like("test-%")))
            s.execute(delete(Place).where(Place.id.like("test-%")))

    cleanup()
    with SessionLocal.begin() as s:
        s.add_all(PLACES)
        s.flush()
        s.add(STORY)
    with TestClient(app) as c:
        yield c
    cleanup()


def ids(resp):
    assert resp.status_code == 200, resp.text
    return [i["id"] for i in resp.json()["items"] if i["id"].startswith("test-")]


def test_keyword_search(client):
    assert ids(client.get("/places", params={"q": "테스트 물회"})) == ["test-food"]
    assert ids(client.get("/places", params={"q": "과메기"})) == ["test-food"]  # 메뉴 검색
    assert "test-house" in ids(client.get("/places", params={"q": "근대 역사"}))  # 개요 검색


def test_tag_and_type_filter(client):
    assert ids(client.get("/places", params=[("tags", "관광지"), ("tags", "구룡포읍")])) == ["test-house"]
    assert ids(client.get("/places", params={"content_type": "음식점", "q": "테스트"})) == ["test-food"]
    assert ids(client.get("/places", params={"area": "호미곶면", "q": "테스트"})) == ["test-homi"]


def test_near_search_sorted_by_distance(client):
    r = client.get("/places", params={"lat": 35.9880, "lng": 129.5570, "radius_m": 1000, "q": "테스트"})
    assert ids(r) == ["test-house", "test-food"]
    first = r.json()["items"][0]
    assert first["distance_m"] == pytest.approx(0, abs=1)


def test_detail_and_stories(client):
    r = client.get("/places/test-house")
    assert r.status_code == 200
    assert r.json()["story_count"] == 1
    assert client.get("/places/test-house/stories").json()[0]["id"] == "test-story"
    assert client.get("/places/nope").status_code == 404


def test_stories_near(client):
    r = client.get("/places/stories/near", params={"lat": 35.9881, "lng": 129.5571, "radius_m": 50})
    assert [s["id"] for s in r.json() if s["id"].startswith("test-")] == ["test-story"]


def test_tags(client):
    tags = {t["tag"]: t["count"] for t in client.get("/places/tags").json()}
    assert tags["구룡포읍"] >= 2


def test_bad_params(client):
    assert client.get("/places", params={"lat": 35.9}).status_code == 422
    assert client.get("/places", params={"radius_m": 100}).status_code == 422
