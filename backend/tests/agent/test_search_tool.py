"""search_places 도구: 이름 일치 표시, 조건 때문에 빠진 같은 이름 장소 다시 찾기 (places API 는 가짜)."""
import asyncio
import json

from app.agent.tools import Draft, Toolbox

PLACES = [
    {"id": "kto-403868", "title": "환여횟집", "content_type": "음식점", "area": "두호동"},
    {"id": "kto-1997227", "title": "철규분식", "content_type": "음식점", "area": "구룡포읍"},
    {"id": "kto-2834330", "title": "짬홍", "content_type": "음식점", "area": "구룡포읍"},
]


class FakeApi:
    """area 는 필터, q 는 이름 포함이면 앞에, hybrid 면 나머지도 채운다 (실제 places 와 비슷하게)."""

    def __init__(self):
        self.calls: list[dict] = []

    async def search_places(self, **params):
        self.calls.append(params)
        rows = [p for p in PLACES if not params.get("area") or p["area"] == params["area"]]
        q = params.get("q", "")
        hit = [p for p in rows if q and q in p["title"]]
        if params.get("mode") == "hybrid":
            hit += [p for p in rows if p not in hit]
        return {"total": len(hit), "items": hit[: params.get("limit", 8)]}


def run(api, **args):
    box = Toolbox(api, plan_id=1, draft=Draft(), session_id="s")
    out = asyncio.run(box.call("search_places", json.dumps(args)))
    return out, json.loads(out.content)


def test_name_match_found_directly():
    api = FakeApi()
    out, body = run(api, query="환여횟집")
    assert body["items"][0] == {"place_id": "kto-403868", "title": "환여횟집", "type": "음식점", "area": "두호동",
                                "name_match": True}
    assert "hint" not in body and out.detail.startswith("환여횟집") and len(api.calls) == 1


def test_area_filter_hides_named_place():
    """구룡포읍으로 좁혀 '환여횟집' 을 찾으면 엉뚱한 구룡포 식당만 나오던 문제 -> 조건 밖 같은 이름 장소를 함께 준다."""
    api = FakeApi()
    out, body = run(api, query="환여횟집", area="구룡포읍")
    assert [i["title"] for i in body["items"]] == ["철규분식", "짬홍"]
    assert not any(i.get("name_match") for i in body["items"])
    assert [i["place_id"] for i in body["name_matches_outside_filter"]] == ["kto-403868"]
    assert "조건(구룡포읍) 밖" in body["hint"] and out.detail == "환여횟집 (두호동, 조건 밖)"
    assert api.calls[1] == {"q": "환여횟집", "limit": 5}  # 조건 없이 이름으로 다시


def test_no_name_match_anywhere_says_similar_only():
    api = FakeApi()
    _, body = run(api, query="없는가게", area="구룡포읍")
    assert "name_matches_outside_filter" not in body
    assert "이름이 일치하는 장소는 없다" in body["hint"]
    # 조건 없이 찾은 경우엔 다시 찾지 않는다
    api = FakeApi()
    run(api, query="없는가게")
    assert len(api.calls) == 1


class NearApi:
    """근처 검색용 가짜: keyword 는 이름·소개에 검색어가 든 곳(제목 일치 우선), hybrid 는 반경 안 모든 곳을 채운다."""

    ROWS = [  # (id, 제목, 소개, 거리 m)
        {"id": "k1", "title": "바다 카페", "text": "카페", "distance_m": 900},
        {"id": "k2", "title": "골목집", "text": "카페 겸 소품샵", "distance_m": 200},
        {"id": "x1", "title": "철물점", "text": "공구", "distance_m": 50},
        {"id": "x2", "title": "주차장", "text": "주차", "distance_m": 100},
    ]

    def __init__(self):
        self.calls: list[dict] = []

    async def get_place(self, place_id):
        return {"id": place_id, "lat": 35.98, "lng": 129.55}

    async def search_places(self, **params):
        self.calls.append(params)
        rows = [{**r, "content_type": "음식점", "area": "구룡포읍"} for r in self.ROWS
                if "radius_m" not in params or r["distance_m"] <= params["radius_m"]]
        q = params.get("q", "")
        if params.get("mode") == "keyword":
            hit = sorted((r for r in rows if q in r["title"] + r["text"]), key=lambda r: q not in r["title"])
        else:  # hybrid: 반경 안 모든 곳에 의미 점수가 붙어 채워진다
            hit = sorted(rows, key=lambda r: q not in r["title"] + r["text"])
        return {"total": len(hit), "items": hit[: params.get("limit", 8)]}


def test_near_search_keyword_only_nearest_first():
    """D8: 근처 검색은 글자가 맞는 곳만 가까운 순으로 (반경 안 무관한 철물점·주차장으로 채우지 않는다)."""
    api = NearApi()
    out, body = run(api, query="카페", near_place_id="kto-c", radius_m=1000)
    assert [i["place_id"] for i in body["items"]] == ["k2", "k1"]
    assert [i["distance_m"] for i in body["items"]] == [200, 900]
    assert api.calls[0]["mode"] == "keyword" and api.calls[0]["radius_m"] == 1000
    assert out.detail == "바다 카페 외 1곳"  # 이름 일치를 먼저 보여준다


def test_near_search_falls_back_to_few_semantic():
    """글자가 맞는 곳이 없으면 뜻으로 몇 곳만(가까운 순) 주고 관련 없을 수 있다고 알린다."""
    api = NearApi()
    _, body = run(api, query="조용한 쉼터", near_place_id="kto-c", radius_m=1000, limit=8)
    assert [c.get("mode") for c in api.calls[:2]] == ["keyword", "hybrid"] and api.calls[1]["limit"] == 3
    assert [i["distance_m"] for i in body["items"]] == sorted(i["distance_m"] for i in body["items"])
    assert len(body["items"]) == 3 and "관련 없는 곳일 수 있으니" in body["hint"]


def test_search_without_near_still_hybrid():
    api = FakeApi()
    run(api, query="바다가 보이는 카페", area="구룡포읍")
    assert api.calls[0]["mode"] == "hybrid"


def test_name_match_ignores_spaces():
    PLACES.append({"id": "kto-1", "title": "구룡포 일본인 가옥거리", "content_type": "관광지", "area": "구룡포읍"})
    try:
        _, body = run(FakeApi(), query="구룡포 일본인 가옥거리", area="구룡포읍")
        assert body["items"][0]["name_match"] is True and "hint" not in body
    finally:
        PLACES.pop()
