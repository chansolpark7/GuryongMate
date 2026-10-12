"""외부 검색: 공급자 중립 응답·출처 등급·캐시·키 없음 503, 네이버 응답 해석 (docs/docent/README.md §2.1~2.2)."""
import asyncio

import httpx
import pytest

from app.docent.search import NaverSearch, RawResult, SearchUnavailable, get_search_provider
from app.docent.tiers import tier_of
from app.main import app
from tests.docent.conftest import H, err, ok


class FakeProvider:
    name = "fake"

    def __init__(self, results=None, fail=False):
        self.calls = []
        self.results = results if results is not None else [
            RawResult("구룡포 근대문화역사거리", "https://www.pohang.go.kr/tour/1", "포항시 공식 안내", None),
            RawResult("일본인가옥거리의 100년", "https://www.yna.co.kr/view/AKR1", "연합뉴스 기사", "2024-05-01"),
            RawResult("구룡포 여행 후기", "https://blog.naver.com/someone/2233", "다녀왔어요", "2025-03-02"),
            RawResult("어떤 사이트", "https://example.com/a", "…", None),
        ]
        self.fail = fail

    async def search(self, q, limit):
        self.calls.append((q, limit))
        if self.fail:
            raise SearchUnavailable("boom")
        return self.results[:limit]


@pytest.fixture
def use_provider():
    def install(provider):
        app.dependency_overrides[get_search_provider] = lambda: provider
        return provider

    yield install
    app.dependency_overrides.pop(get_search_provider, None)


def test_search_shape_tier_and_cache(client, use_provider):
    fake = use_provider(FakeProvider())
    body = ok(client.get("/docent/search", params={"q": "구룡포  일본인가옥거리 역사", "limit": 4}, headers=H))
    assert body["provider"] == "fake" and body["cached"] is False
    assert [r["tier"] for r in body["results"]] == ["official", "press", "community", "unknown"]
    assert body["results"][1] == {"title": "일본인가옥거리의 100년", "url": "https://www.yna.co.kr/view/AKR1",
                                  "snippet": "연합뉴스 기사", "published": "2024-05-01", "tier": "press"}
    assert fake.calls == [("구룡포 일본인가옥거리 역사", 4)]  # 공백 정리

    again = ok(client.get("/docent/search", params={"q": "구룡포 일본인가옥거리 역사", "limit": 4}, headers=H))
    assert again["cached"] is True and again["results"] == body["results"]
    assert len(fake.calls) == 1  # 공급자를 다시 부르지 않음

    ok(client.get("/docent/search", params={"q": "구룡포 일본인가옥거리 역사", "limit": 2}, headers=H))
    assert len(fake.calls) == 2  # limit 이 다르면 다른 질의


def test_search_unavailable(client, use_provider):
    use_provider(None)  # 키 없음
    err(client.get("/docent/search", params={"q": "구룡포"}, headers=H), 503, "search_unavailable")
    use_provider(FakeProvider(fail=True))
    d = err(client.get("/docent/search", params={"q": "구룡포 실패"}, headers=H), 503, "search_unavailable")
    assert "boom" in d["reason"]
    err(client.get("/docent/search", params={"q": ""}, headers=H), 422, "invalid_request")
    err(client.get("/docent/search", params={"q": "x", "limit": 11}, headers=H), 422, "invalid_request")


def test_per_user_limits(client, use_provider, monkeypatch):
    """외부 검색·페이지 읽기는 사용자당 1시간·하루 상한 (§2.4). 캐시 결과는 세지 않고, 사용자마다 따로 센다."""
    from app.docent.config import settings
    from app.docent.page import Fetched, get_fetcher

    monkeypatch.setattr(settings, "limit_search_hour", 2)
    monkeypatch.setattr(settings, "limit_page_hour", 1)
    fake = use_provider(FakeProvider())
    me, other = {"X-User-Id": "test-docent-limit-a"}, {"X-User-Id": "test-docent-limit-b"}
    ok(client.get("/docent/search", params={"q": "한도 1"}, headers=me))
    ok(client.get("/docent/search", params={"q": "한도 1"}, headers=me))  # 캐시 -> 세지 않음
    ok(client.get("/docent/search", params={"q": "한도 2"}, headers=me))
    d = err(client.get("/docent/search", params={"q": "한도 3"}, headers=me), 429, "rate_limited")
    assert d["kind"] == "search" and d["limit"] == 2 and d["retry_after"] > 0
    assert len(fake.calls) == 2  # 넘은 요청은 공급자를 부르지 않는다
    assert ok(client.get("/docent/search", params={"q": "한도 1"}, headers=me))["cached"] is True  # 캐시는 한도 뒤에도
    ok(client.get("/docent/search", params={"q": "한도 3"}, headers=other))  # 다른 사용자는 따로

    class Pages:
        calls = 0

        async def fetch(self, url):
            Pages.calls += 1
            return Fetched(url, b"<html><title>t</title><body>body</body></html>", "text/html", "utf-8", False)

    app.dependency_overrides[get_fetcher] = lambda: Pages()
    try:
        ok(client.get("/docent/page", params={"url": "https://example.com/limit-1"}, headers=me))
        ok(client.get("/docent/page", params={"url": "https://example.com/limit-1"}, headers=me))  # 캐시
        d = err(client.get("/docent/page", params={"url": "https://example.com/limit-2"}, headers=me), 429, "rate_limited")
        assert d["kind"] == "page" and Pages.calls == 1
    finally:
        app.dependency_overrides.pop(get_fetcher, None)


def test_missing_keys_means_no_provider(monkeypatch):
    from app.docent import search
    monkeypatch.setattr(search, "_provider", None)
    monkeypatch.setattr(search.settings, "naver_client_id", "")
    assert search.get_search_provider() is None
    with pytest.raises(SearchUnavailable):
        NaverSearch("", "", "https://openapi.naver.com", 5)


NAVER = {
    "webkr": {"items": [
        {"title": "<b>구룡포</b> 근대문화역사거리 - 포항시", "link": "https://www.pohang.go.kr/phtour/1",
         "description": "1920년대 &quot;일본인&quot; 가옥 <b>거리</b>"},
        {"title": "중복", "link": "https://blog.naver.com/a/1", "description": "같은 주소"},
    ]},
    "news": {"items": [
        {"title": "<b>구룡포</b> 새 단장", "originallink": "https://www.kbmaeil.com/news/1",
         "link": "https://n.news.naver.com/mnews/article/1", "description": "기사 요약",
         "pubDate": "Mon, 06 May 2024 10:00:00 +0900"},
    ]},
    "blog": {"items": [
        {"title": "구룡포 다녀옴", "link": "https://blog.naver.com/a/1", "description": "후기", "postdate": "20250302"},
        {"title": "후기2", "link": "https://myblog.tistory.com/9", "description": "또 후기", "postdate": "20250101"},
        {"title": "링크 없음", "link": "", "description": "버린다"},
    ]},
}


def test_naver_parsing():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        kind = req.url.path.rsplit("/", 1)[-1].removesuffix(".json")
        return httpx.Response(200, json=NAVER[kind])

    p = NaverSearch("id", "secret", "https://openapi.naver.com", 5, transport=httpx.MockTransport(handler))
    results = asyncio.run(p.search("구룡포", 10))

    assert {r.url.path for r in seen} == {"/v1/search/webkr.json", "/v1/search/news.json", "/v1/search/blog.json"}
    assert all(r.headers["X-Naver-Client-Id"] == "id" and r.headers["X-Naver-Client-Secret"] == "secret" for r in seen)
    assert seen[0].url.params["query"] == "구룡포" and seen[0].url.params["display"] == "10"
    # 웹문서·뉴스·블로그를 번갈아, 같은 주소는 한 번만, 링크 없는 항목은 버린다
    assert [r.url for r in results] == [
        "https://www.pohang.go.kr/phtour/1", "https://www.kbmaeil.com/news/1", "https://blog.naver.com/a/1",
        "https://myblog.tistory.com/9",
    ]
    assert results[0].title == "구룡포 근대문화역사거리 - 포항시"
    assert results[0].snippet == '1920년대 "일본인" 가옥 거리' and results[0].published is None
    assert results[1].published == "2024-05-06"  # 뉴스: 언론사 원문 주소·발행일
    assert results[2].title == "구룡포 다녀옴" and results[2].published == "2025-03-02"  # 블로그 작성일
    assert results[3].published == "2025-01-01"
    assert len(asyncio.run(p.search("구룡포", 2))) == 2


def test_naver_error_is_unavailable():
    p = NaverSearch("id", "bad", "https://openapi.naver.com", 5,
                    transport=httpx.MockTransport(lambda req: httpx.Response(401, json={"errorCode": "024"})))
    with pytest.raises(SearchUnavailable, match="401"):
        asyncio.run(p.search("구룡포", 5))


@pytest.mark.parametrize("url,tier", [
    ("https://www.pohang.go.kr/x", "official"),
    ("https://korean.visitkorea.or.kr/detail", "official"),
    ("https://www.museum.go.kr", "official"),
    ("https://encykorea.aks.ac.kr/Article/E1", "official"),
    ("http://pohang.grandculture.net/pohang/toc", "official"),
    ("https://www.yna.co.kr/view/1", "press"),
    ("https://n.news.naver.com/article/1", "press"),
    ("https://m.kbmaeil.com/news/1", "press"),
    ("https://blog.naver.com/a/1", "community"),
    ("https://m.blog.naver.com/a/1", "community"),
    ("https://abc.tistory.com/3", "community"),
    ("https://namu.wiki/w/구룡포", "community"),
    ("https://ko.wikipedia.org/wiki/구룡포읍", "community"),
    ("https://notgo.kr.example.com", "unknown"),
    ("https://fakego.kr", "unknown"),
    ("not a url", "unknown"),
])
def test_tier_rules(url, tier):
    assert tier_of(url) == tier
