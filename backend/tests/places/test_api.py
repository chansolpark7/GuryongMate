"""places API 테스트. 실제 DB(docker compose 의 db)에 test- 접두사 데이터를 넣고 지운다.

    docker compose run --rm api pytest tests/places
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.db import SessionLocal, init_db
from app.main import app
from app.places import router as places_router
from app.places.embedding import DIM
from app.places.models import Place, PlaceChunk, PlaceStory
from app.places.normalize import build_search_compact

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
for _p in PLACES:
    _p.search_compact = build_search_compact({c: getattr(_p, c) for c in
                                             ("title", "content_type", "category_names", "tags", "area", "addr", "menu",
                                              "overview", "extra_info")})


def onehot(i: int) -> list[float]:
    v = [0.0] * DIM
    v[i] = 1.0
    return v


# 가짜 임베딩: 축 0 = '역사', 축 1 = '음식', 축 2 = '바다'
CHUNKS = [
    dict(kind="place", place_id="test-house", story_id=None, seq=0, title="테스트 일본인가옥거리", text="근대 역사 거리",
         lat=35.9880, lng=129.5570, embedding=onehot(0)),
    dict(kind="place", place_id="test-food", story_id=None, seq=0, title="테스트 물회집", text="물회 과메기",
         lat=35.9900, lng=129.5560, embedding=onehot(1)),
    dict(kind="place", place_id="test-homi", story_id=None, seq=0, title="테스트 호미곶 광장", text="바다 해돋이",
         lat=36.0765, lng=129.5670, embedding=[0.6 if i in (0, 2) else 0.0 for i in range(DIM)]),
    dict(kind="story", place_id="test-house", story_id="test-story", seq=0, title="가옥거리 이야기", text="옛날 이야기",
         lat=35.9881, lng=129.5571, embedding=[0.9 if i == 0 else (0.1 if i == 1 else 0.0) for i in range(DIM)]),
]
FAKE_QUERIES = {"역사": onehot(0), "음식": onehot(1), "테스트 음식": onehot(1), "바다": onehot(2)}

STORY = PlaceStory(id="test-story", place_id="test-house", title="가옥거리 이야기", script="옛날 이야기", play_time_sec=90,
                   lat=35.9881, lng=129.5571, odii_theme_title="가옥거리", audio_url="", image_url="")


@pytest.fixture(scope="module")
def client():
    init_db()

    def cleanup():
        with SessionLocal.begin() as s:
            s.execute(delete(PlaceChunk).where(PlaceChunk.place_id.like("test-%")))
            s.execute(delete(PlaceStory).where(PlaceStory.id.like("test-%")))
            s.execute(delete(Place).where(Place.id.like("test-%")))

    cleanup()
    with SessionLocal.begin() as s:
        s.add_all(PLACES)
        s.flush()
        s.add(STORY)
        s.flush()
        s.add_all(PlaceChunk(**c) for c in CHUNKS)
    mp = pytest.MonkeyPatch()
    mp.setattr(places_router, "encode_query", lambda q: FAKE_QUERIES[q])  # 모델 로드 없이 검색 로직만 검증
    with TestClient(app) as c:
        yield c
    mp.undo()
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


def test_keyword_multi_word_spacing_josa_synonym(client):
    assert ids(client.get("/places", params={"q": "테스트 구룡포읍 물회"})) == ["test-food"]  # 여러 단어 AND
    assert ids(client.get("/places", params={"q": "테스트일본인가옥"})) == ["test-house"]  # 띄어쓰기 무시
    assert ids(client.get("/places", params={"q": "테스트 호미곶에서"})) == ["test-homi"]  # 조사 제거
    assert ids(client.get("/places", params={"q": "테스트 맛집"})) == ["test-food"]  # 맛집 -> 음식점
    assert ids(client.get("/places", params={"q": "테스트 없는단어"})) == []


def test_semantic_search(client):
    r = client.get("/places", params={"q": "역사", "mode": "semantic"})
    got = [i for i in r.json()["items"] if i["id"].startswith("test-")]
    assert [i["id"] for i in got][:2] == ["test-house", "test-homi"]
    assert got[0]["score"] == pytest.approx(1.0, abs=1e-3)
    r = client.get("/places", params={"q": "바다", "mode": "semantic", "content_type": "관광지"})
    assert ids(r)[0] == "test-homi"
    assert "test-food" not in ids(r)  # 필터 적용


def test_hybrid_search(client):
    r = client.get("/places", params={"q": "테스트 음식", "mode": "hybrid", "area": "구룡포읍"})
    assert ids(r)[0] == "test-food"
    assert client.get("/places", params={"mode": "hybrid"}).status_code == 422


def test_passages(client):
    r = client.get("/places/passages", params={"q": "역사", "limit": 50})
    got = [p for p in r.json() if (p["place_id"] or "").startswith("test-")]
    assert [p["kind"] for p in got[:2]] == ["place", "story"]
    r = client.get("/places/passages", params={"q": "역사", "kind": "story", "lat": 35.9881, "lng": 129.5571,
                                                "radius_m": 30})
    assert [p["story_id"] for p in r.json()] == ["test-story"]
