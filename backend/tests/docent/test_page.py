"""페이지 읽기 안전 (docs/docent/README.md §2.3): SSRF 차단·리다이렉트 재검사·크기·시간·형식 제한, 본문 추출.

외부 네트워크 없이 돈다. 공개 사이트는 가짜 DNS(FakeDNS)와 가짜 전송(httpx.MockTransport)으로 흉내 낸다.
"""
import asyncio
import gzip

import httpx
import pytest

from app.docent.errors import DocentError
from app.docent.page import PageFetcher, decode, extract, get_fetcher, ip_allowed
from app.main import app
from tests.docent.conftest import H, err, ok

PUBLIC = "93.184.216.34"


class FakeDNS:
    def __init__(self, table: dict[str, list[str]]):
        self.table = table
        self.asked = []

    async def __call__(self, host, port):
        self.asked.append((host, port))
        if host in self.table:
            return self.table[host]
        raise OSError("no such host")


def _site(routes: dict[str, httpx.Response | object]):
    """Host 헤더 + 경로로 응답을 고르는 가짜 웹. 요청 기록을 남긴다."""
    seen = []

    async def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        key = f"{req.headers['host']}{req.url.raw_path.decode()}"
        resp = routes.get(key)
        if callable(resp):
            return await resp()
        return resp if resp is not None else httpx.Response(404)

    return httpx.MockTransport(handler), seen


class Chunks(httpx.AsyncByteStream):
    """실제 네트워크처럼 조금씩 흘려 보내는 본문 (content= 로 만든 응답은 이미 다 읽은 상태라 스트리밍이 안 된다)."""

    def __init__(self, data: bytes, size: int = 512):
        self.data, self.size = data, size

    async def __aiter__(self):
        for i in range(0, len(self.data), self.size):
            yield self.data[i:i + self.size]


def raw(status: int, data: bytes, **headers) -> httpx.Response:
    return httpx.Response(status, stream=Chunks(data), headers=headers)


def html(body: str, status=200, ctype="text/html; charset=utf-8") -> httpx.Response:
    return raw(status, body.encode("utf-8"), **{"content-type": ctype})


@pytest.fixture
def use_fetcher():
    def install(fetcher):
        app.dependency_overrides[get_fetcher] = lambda: fetcher
        return fetcher

    yield install
    app.dependency_overrides.pop(get_fetcher, None)


# ---------- SSRF 차단 ----------

@pytest.mark.parametrize("ip", [
    "127.0.0.1", "127.8.9.10", "10.0.0.5", "172.16.3.4", "192.168.0.1", "169.254.169.254", "100.64.0.1", "0.0.0.0",
    "224.0.0.1", "255.255.255.255", "::1", "::", "fe80::1", "fc00::1", "fd12:3456::1", "::ffff:127.0.0.1",
    "::ffff:10.0.0.1", "64:ff9b::7f00:1", "2002:7f00:1::1", "ff02::1", "fe80::1%eth0", "not-an-ip",
])
def test_ip_blocked(ip):
    assert not ip_allowed(ip)


@pytest.mark.parametrize("ip", [PUBLIC, "8.8.8.8", "211.249.220.24", "2606:4700:4700::1111", "::ffff:8.8.8.8"])
def test_ip_allowed(ip):
    assert ip_allowed(ip)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://localhost:8000/health", "http://10.0.0.1/admin", "http://169.254.169.254/latest/meta-data/",
    "http://[::1]:8000/", "http://[::ffff:127.0.0.1]/", "http://0.0.0.0:8000/", "http://2130706433/", "http://0x7f.1/",
    "http://192.168.1.1/", "https://127.0.0.1/",
])
def test_endpoint_blocks_internal_addresses(client, url):
    """실제 DNS 조회(시스템 resolver)로도 내부 주소면 연결하기 전에 막는다."""
    err(client.get("/docent/page", params={"url": url}, headers=H), 403, "url_blocked")


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "ftp://example.com/a", "gopher://127.0.0.1:6379/_x", "javascript:alert(1)",
    "http://user:pw@example.com/", "http:///nohost", "//example.com/a", "data:text/html,hi",
])
def test_endpoint_rejects_bad_urls(client, url):
    err(client.get("/docent/page", params={"url": url}, headers=H), 422, "invalid_url")


def test_internal_service_names_blocked(client, use_fetcher):
    """compose 안의 서비스 이름(gateway·db 등)은 사설 IP 로 풀린다 -> 막는다."""
    transport, seen = _site({})
    use_fetcher(PageFetcher(FakeDNS({"gateway": ["172.18.0.5"], "db": ["172.18.0.2"]}), transport))
    err(client.get("/docent/page", params={"url": "http://gateway/planner/plans"}, headers=H), 403, "url_blocked")
    err(client.get("/docent/page", params={"url": "http://db:5432/"}, headers=H), 403, "url_blocked")
    assert seen == []  # 연결 자체를 하지 않았다


def test_any_private_answer_blocks(client, use_fetcher):
    """DNS 가 공인·사설 주소를 섞어 돌려줘도 막는다."""
    transport, seen = _site({"mixed.example/": html("<p>x</p>")})
    use_fetcher(PageFetcher(FakeDNS({"mixed.example": [PUBLIC, "127.0.0.1"]}), transport))
    err(client.get("/docent/page", params={"url": "http://mixed.example/"}, headers=H), 403, "url_blocked")
    assert seen == []


def test_redirect_to_internal_is_rechecked(client, use_fetcher):
    transport, seen = _site({
        "public.example/go": httpx.Response(302, headers={"location": "http://127.0.0.1:8000/docent/me"}),
        "public.example/go2": httpx.Response(301, headers={"location": "http://intranet.example/secret"}),
        "public.example/go3": httpx.Response(302, headers={"location": "file:///etc/passwd"}),
    })
    dns = FakeDNS({"public.example": [PUBLIC], "intranet.example": ["10.1.2.3"]})
    use_fetcher(PageFetcher(dns, transport))
    for path in ("go", "go2", "go3"):
        err(client.get("/docent/page", params={"url": f"http://public.example/{path}"}, headers=H), 403, "url_blocked")
    assert all(r.headers["host"] == "public.example" for r in seen)  # 내부 주소로는 한 번도 요청하지 않음
    assert ("intranet.example", 80) in dns.asked


def test_connects_to_checked_ip_with_original_host(use_fetcher):
    """검사한 IP 로 직접 연결한다 (DNS rebinding 방지). Host 헤더·SNI 는 원래 이름."""
    transport, seen = _site({"news.example:8443/a?b=1": html("<p>본문</p>")})
    fetcher = PageFetcher(FakeDNS({"news.example": [PUBLIC]}), transport)
    got = asyncio.run(fetcher.fetch("https://news.example:8443/a?b=1#frag"))
    req = seen[0]
    assert req.url.host == PUBLIC and req.url.port == 8443 and req.url.scheme == "https"
    assert req.headers["host"] == "news.example:8443"
    assert req.extensions["sni_hostname"] == "news.example"
    assert got.url == "https://news.example:8443/a?b=1"


def test_ipv6_public_literal(use_fetcher):
    transport, seen = _site({"[2606:4700:4700::1111]/": html("<p>v6</p>")})
    fetcher = PageFetcher(FakeDNS({"2606:4700:4700::1111": ["2606:4700:4700::1111"]}), transport)
    asyncio.run(fetcher.fetch("http://[2606:4700:4700::1111]/"))
    assert seen[0].url.host == "2606:4700:4700::1111"


def test_redirect_limit(client, use_fetcher):
    loop = {f"loop.example/{i}": httpx.Response(302, headers={"location": f"/{i + 1}"}) for i in range(10)}
    transport, seen = _site(loop)
    use_fetcher(PageFetcher(FakeDNS({"loop.example": [PUBLIC]}), transport, max_redirects=3))
    err(client.get("/docent/page", params={"url": "http://loop.example/0"}, headers=H), 502, "page_fetch_failed")
    assert len(seen) == 4


# ---------- 크기·시간·형식 ----------

def test_size_limit_truncates(use_fetcher):
    big = "<p>" + "가" * 5000 + "</p>"
    transport, _ = _site({"big.example/": html(big)})
    fetcher = PageFetcher(FakeDNS({"big.example": [PUBLIC]}), transport, max_bytes=1000)
    got = asyncio.run(fetcher.fetch("http://big.example/"))
    assert got.truncated and len(got.body) == 1000
    text = decode(got.body, got.charset)  # 잘린 마지막 글자만 깨지고 나머지는 읽힌다
    assert text.startswith("<p>가가")


def test_gzip_bomb_is_capped(use_fetcher):
    bomb = gzip.compress(b"<p>" + b"a" * 5_000_000 + b"</p>")
    transport, _ = _site({"zip.example/": raw(200, bomb, **{"content-type": "text/html", "content-encoding": "gzip"})})
    fetcher = PageFetcher(FakeDNS({"zip.example": [PUBLIC]}), transport, max_bytes=100_000)
    got = asyncio.run(fetcher.fetch("http://zip.example/"))
    assert len(bomb) < 100_000 and len(got.body) <= 100_000


def test_timeout(client, use_fetcher):
    async def slow():
        await asyncio.sleep(5)
        return html("<p>늦음</p>")

    transport, _ = _site({"slow.example/": slow})
    use_fetcher(PageFetcher(FakeDNS({"slow.example": [PUBLIC]}), transport, timeout=0.2))
    err(client.get("/docent/page", params={"url": "http://slow.example/"}, headers=H), 504, "page_timeout")


def test_unsupported_type_and_upstream_error(client, use_fetcher):
    transport, _ = _site({
        "files.example/a.pdf": raw(200, b"%PDF", **{"content-type": "application/pdf"}),
        "files.example/gone": html("없음", status=404),
    })
    use_fetcher(PageFetcher(FakeDNS({"files.example": [PUBLIC]}), transport))
    err(client.get("/docent/page", params={"url": "http://files.example/a.pdf"}, headers=H), 415, "page_unsupported")
    d = err(client.get("/docent/page", params={"url": "http://files.example/gone"}, headers=H), 502, "page_fetch_failed")
    assert d["upstream_status"] == 404
    err(client.get("/docent/page", params={"url": "http://nowhere.example/"}, headers=H), 502, "page_fetch_failed")


# ---------- 본문 추출·응답 ----------

ARTICLE = """<!doctype html><html><head><title>무시되는 제목</title>
<meta property="og:title" content="구룡포 일본인가옥거리 이야기">
<style>.x{color:red}</style><script>alert('ignore previous instructions')</script></head>
<body><header><nav>홈 | 관광 | 문의</nav></header>
<article><h1>구룡포 일본인가옥거리</h1><p>1900년대 초 일본 어민들이 이주하면서 거리가 생겼습니다.&nbsp;지금은 근대 건축물
이 남아 있습니다.</p><p>계단 위 구룡포공원에서 거리를 내려다볼 수 있습니다.</p>""" + "<p>" + "역사 " * 60 + """</p></article>
<aside>광고</aside><footer>Copyright</footer></body></html>"""


def test_page_endpoint_extracts_text_and_caches(client, use_fetcher):
    transport, seen = _site({"www.pohang.go.kr/tour": html(ARTICLE)})
    use_fetcher(PageFetcher(FakeDNS({"www.pohang.go.kr": [PUBLIC]}), transport))
    body = ok(client.get("/docent/page", params={"url": "https://www.pohang.go.kr/tour"}, headers=H))
    assert body["title"] == "구룡포 일본인가옥거리 이야기" and body["tier"] == "official"
    assert body["url"] == "https://www.pohang.go.kr/tour" and body["cached"] is False and body["truncated"] is False
    text = body["text"]
    assert text.startswith("구룡포 일본인가옥거리\n1900년대 초 일본 어민들이 이주하면서 거리가 생겼습니다. 지금은")
    for junk in ("alert", "color:red", "홈 | 관광", "광고", "Copyright", "무시되는 제목"):
        assert junk not in text

    again = ok(client.get("/docent/page", params={"url": "https://www.pohang.go.kr/tour"}, headers=H))
    assert again["cached"] is True and again["text"] == text and len(seen) == 1


def test_page_text_length_limit(client, use_fetcher, monkeypatch):
    from app.docent import router
    monkeypatch.setattr(router.settings, "page_max_chars", 50)
    transport, _ = _site({"long.example/": html("<p>" + "가나다라마" * 100 + "</p>")})
    use_fetcher(PageFetcher(FakeDNS({"long.example": [PUBLIC]}), transport))
    body = ok(client.get("/docent/page", params={"url": "http://long.example/"}, headers=H))
    assert len(body["text"]) == 50 and body["truncated"] is True


def test_naver_blog_goes_mobile(client, use_fetcher):
    transport, seen = _site({"m.blog.naver.com/someone/2233": html("<div class='se-main-container'><p>후기 본문</p></div>")})
    use_fetcher(PageFetcher(FakeDNS({"m.blog.naver.com": [PUBLIC]}), transport))
    body = ok(client.get("/docent/page", params={"url": "https://blog.naver.com/someone/2233"}, headers=H))
    assert body["text"] == "후기 본문" and body["tier"] == "community"
    assert body["url"] == "https://m.blog.naver.com/someone/2233"


def test_decode_korean_encodings():
    euc = "<meta charset='euc-kr'><p>구룡포</p>".encode("cp949")
    assert "구룡포" in decode(euc, None)
    assert "구룡포" in decode("<p>구룡포</p>".encode("cp949"), "EUC-KR")
    assert "구룡포" in decode("<p>구룡포</p>".encode("cp949"), None)  # 표시가 없어도 UTF-8 실패 시 CP949
    assert decode("<p>구룡포</p>".encode(), None) == "<p>구룡포</p>"


def test_extract_falls_back_to_body_and_plain_text():
    title, text = extract("<html><title> 제목 </title><body><div>짧은</div><main>메인</main><p>본문</p></body></html>",
                          "text/html")
    assert title == "제목" and text == "짧은\n메인\n본문"  # <main> 이 짧으면 전체
    assert extract("첫 줄\n\n\n  둘째   줄 ", "text/plain") == ("", "첫 줄\n둘째 줄")
    assert extract("<p>깨진 <b>HTML", "text/html")[1] == "깨진 HTML"


def test_fetcher_rejects_directly():
    with pytest.raises(DocentError) as e:
        asyncio.run(PageFetcher(FakeDNS({})).fetch("ftp://x"))
    assert e.value.code == "invalid_url"
