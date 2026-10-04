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


def test_name_match_ignores_spaces():
    PLACES.append({"id": "kto-1", "title": "구룡포 일본인 가옥거리", "content_type": "관광지", "area": "구룡포읍"})
    try:
        _, body = run(FakeApi(), query="구룡포 일본인 가옥거리", area="구룡포읍")
        assert body["items"][0]["name_match"] is True and "hint" not in body
    finally:
        PLACES.pop()
