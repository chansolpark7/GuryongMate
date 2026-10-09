"""외부 검색 공급자 (§2.1). agent 는 공급자를 모르고 GET /docent/search 의 같은 형식만 받는다.

공급자를 바꾸거나 늘리려면 SearchProvider 를 구현하고 get_search_provider 에서 고른다.
"""
import asyncio
import html
import re
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Protocol

import httpx

from app.docent.config import settings


class SearchUnavailable(RuntimeError):
    """공급자 키가 없거나, 공급자에 접근할 수 없거나, 공급자가 오류를 돌려줬다."""


@dataclass
class RawResult:
    title: str
    url: str
    snippet: str
    published: str | None = None  # YYYY-MM-DD


class SearchProvider(Protocol):
    name: str

    async def search(self, q: str, limit: int) -> list[RawResult]:
        """관련도 순 결과 최대 limit 개. 실패하면 SearchUnavailable."""
        ...


_TAG = re.compile(r"<[^>]+>")


def clean(text: str) -> str:
    """네이버 결과의 <b> 강조 태그·HTML 엔티티를 지운다."""
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub("", text or ""))).strip()


def _news_date(value: str) -> str | None:  # "Mon, 06 May 2024 10:00:00 +0900"
    try:
        return parsedate_to_datetime(value).date().isoformat()
    except (TypeError, ValueError):
        return None


def _blog_date(value: str) -> str | None:  # "20240501"
    if value and len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return None


class NaverSearch:
    """네이버 검색 API (웹문서·뉴스·블로그). 결과는 요약 조각뿐이라 본문은 GET /docent/page 로 읽는다.
    호출 한도: 애플리케이션당 하루 25,000회 (검색 한 번에 3회 호출, 결과는 하루 캐시)."""

    name = "naver"
    # 웹문서(공식 사이트가 많음) → 뉴스 → 블로그(후기) 순서로 번갈아 섞는다
    KINDS = ("webkr", "news", "blog")

    def __init__(self, client_id: str, client_secret: str, base_url: str, timeout: float,
                 transport: httpx.AsyncBaseTransport | None = None):
        if not client_id or not client_secret:
            raise SearchUnavailable("네이버 검색 API 키(DOCENT_NAVER_CLIENT_ID/SECRET)가 설정되지 않았습니다.")
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=timeout, transport=transport,
            headers={"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret},
        )

    async def _kind(self, kind: str, q: str, limit: int) -> list[RawResult]:
        try:
            resp = await self._client.get(f"/v1/search/{kind}.json", params={"query": q, "display": limit})
        except httpx.HTTPError as e:
            raise SearchUnavailable(f"naver {kind}: {e}") from e
        if not resp.is_success:
            raise SearchUnavailable(f"naver {kind} -> {resp.status_code} {resp.text[:200]}")
        out = []
        for it in resp.json().get("items", []):
            url = (it.get("originallink") or it.get("link") or "").strip()  # 뉴스는 언론사 원문 주소
            if not url.startswith(("http://", "https://")):
                continue
            published = _news_date(it.get("pubDate", "")) if kind == "news" else _blog_date(it.get("postdate", ""))
            out.append(RawResult(clean(it.get("title", "")), url, clean(it.get("description", "")), published))
        return out

    async def search(self, q: str, limit: int) -> list[RawResult]:
        lists = await asyncio.gather(*(self._kind(k, q, limit) for k in self.KINDS))
        merged, seen = [], set()
        for i in range(max(map(len, lists), default=0)):
            for lst in lists:
                if i < len(lst) and lst[i].url not in seen:
                    seen.add(lst[i].url)
                    merged.append(lst[i])
        return merged[:limit]


_provider: SearchProvider | None = None


def get_search_provider() -> SearchProvider | None:
    """FastAPI 의존성. 키가 없으면 None (라우터가 503 search_unavailable). 테스트에서는 가짜로 바꾼다."""
    global _provider
    if _provider is None:
        if not settings.naver_client_id or not settings.naver_client_secret:
            return None
        _provider = NaverSearch(settings.naver_client_id, settings.naver_client_secret, settings.naver_base_url,
                                settings.search_timeout_sec)
    return _provider
