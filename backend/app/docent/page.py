"""페이지 읽기 (§2.3). 서버가 사용자 대신 외부 URL 을 가져오므로 SSRF 를 막는다.

- http/https 만. 주소에 사용자 정보(user:pass@) 금지.
- 호스트 이름을 직접 DNS 조회해서 나온 IP 가 **하나라도** 공인 주소가 아니면 막는다
  (사설·루프백·링크로컬·CGNAT·예약·멀티캐스트, IPv4-mapped·NAT64·6to4 안의 IPv4 포함).
- 조회한 IP 로 바로 연결한다 (Host 헤더·TLS SNI·인증서 확인은 원래 이름). 검사와 연결 사이에 DNS 가 바뀌어도(rebinding) 안전.
- 리다이렉트는 자동으로 따라가지 않고 매번 위 검사를 다시 한다 (최대 DOCENT_PAGE_MAX_REDIRECTS 번).
- 리다이렉트 포함 전체 시간 제한, 본문 크기 제한(넘으면 거기까지만 읽음), HTML·텍스트만.
"""
import asyncio
import codecs
import ipaddress
import re
import socket
import zlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from html.parser import HTMLParser

import httpx

from app.docent.config import settings
from app.docent.errors import DocentError

Resolver = Callable[[str, int], Awaitable[list[str]]]

USER_AGENT = "Mozilla/5.0 (compatible; GuryongMateDocent/1.0)"
ALLOWED_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


async def system_resolve(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


def ip_allowed(ip: str) -> bool:
    """공인(global) 유니캐스트 주소만 허용."""
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address):
        embedded = addr.ipv4_mapped or addr.sixtofour or (
            ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF) if addr in _NAT64 else None)
        if embedded is not None and not ip_allowed(str(embedded)):
            return False
    return addr.is_global and not addr.is_multicast


def _blocked(message: str) -> DocentError:
    return DocentError(403, "url_blocked", message)


def check_url(url: str) -> httpx.URL:
    """형식 검사 (IP 검사는 fetch 가 DNS 조회 후에 한다)."""
    try:
        u = httpx.URL(url)
    except (httpx.InvalidURL, TypeError, ValueError) as e:
        raise DocentError(422, "invalid_url", f"주소 형식이 잘못됐습니다: {e}") from e
    if u.scheme not in ("http", "https"):
        raise DocentError(422, "invalid_url", "http/https 주소만 읽을 수 있습니다.")
    if not u.host:
        raise DocentError(422, "invalid_url", "주소에 호스트가 없습니다.")
    if u.userinfo:
        raise DocentError(422, "invalid_url", "사용자 정보(user:pass@)가 들어간 주소는 읽지 않습니다.")
    return u


def rewrite(u: httpx.URL) -> httpx.URL:
    """본문이 iframe 안에 있는 사이트는 본문이 바로 나오는 주소로 바꾼다 (네이버 블로그 -> 모바일)."""
    if u.host == "blog.naver.com":
        return u.copy_with(host="m.blog.naver.com")
    return u


@dataclass
class Fetched:
    url: str  # 최종 주소
    body: bytes
    content_type: str
    charset: str | None
    truncated: bool  # 크기 제한으로 잘림


class PageFetcher:
    def __init__(self, resolve: Resolver = system_resolve, transport: httpx.AsyncBaseTransport | None = None,
                 timeout: float | None = None, max_bytes: int | None = None, max_redirects: int | None = None):
        self._resolve = resolve
        self._timeout = timeout or settings.page_timeout_sec
        self._max_bytes = max_bytes or settings.page_max_bytes
        self._max_redirects = settings.page_max_redirects if max_redirects is None else max_redirects
        # trust_env=False: 환경변수 프록시를 거치지 않는다 (직접 조회한 IP 로만 연결)
        self._client = httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False, timeout=self._timeout,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1",
                     "Accept-Language": "ko,en;q=0.5", "Accept-Encoding": "gzip, deflate"},
        )

    async def fetch(self, url: str) -> Fetched:
        try:
            async with asyncio.timeout(self._timeout):
                return await self._fetch(rewrite(check_url(url)))
        except (TimeoutError, httpx.TimeoutException) as e:
            raise DocentError(504, "page_timeout", f"{self._timeout:g}초 안에 페이지를 받지 못했습니다.") from e

    async def _connect_ip(self, u: httpx.URL) -> str:
        host = u.raw_host.decode("ascii")
        port = u.port or (443 if u.scheme == "https" else 80)
        try:
            ips = [str(ipaddress.ip_address(host))]  # IP 를 직접 쓴 주소는 조회하지 않는다
        except ValueError:
            ips = None
        try:
            ips = ips or await self._resolve(host, port)
        except OSError as e:
            raise DocentError(502, "page_fetch_failed", f"호스트를 찾을 수 없습니다: {host}") from e
        if not ips:
            raise DocentError(502, "page_fetch_failed", f"호스트를 찾을 수 없습니다: {host}")
        if any(not ip_allowed(ip) for ip in ips):
            raise _blocked(f"내부·사설 주소로 연결되는 URL 은 읽을 수 없습니다: {host}")
        return ips[0].split("%", 1)[0]

    async def _fetch(self, u: httpx.URL) -> Fetched:
        for _ in range(self._max_redirects + 1):
            u = u.copy_with(fragment=None)
            ip = await self._connect_ip(u)
            ext = {"sni_hostname": u.raw_host.decode("ascii")} if u.scheme == "https" else {}
            req = self._client.build_request("GET", u.copy_with(host=ip), headers={"Host": u.netloc.decode("ascii")},
                                             extensions=ext)
            try:
                resp = await self._client.send(req, stream=True)
            except httpx.TimeoutException:
                raise
            except httpx.HTTPError as e:
                raise DocentError(502, "page_fetch_failed", f"페이지에 연결하지 못했습니다: {type(e).__name__}") from e
            try:
                location = resp.headers.get("location")
                if resp.is_redirect and location:
                    try:
                        u = rewrite(check_url(str(u.join(location))))
                    except DocentError as e:
                        raise _blocked(f"허용되지 않는 주소로 리다이렉트됩니다: {e.message}") from e
                    continue
                if not resp.is_success:
                    raise DocentError(502, "page_fetch_failed", f"페이지가 오류를 돌려줬습니다: {resp.status_code}",
                                      upstream_status=resp.status_code)
                ctype = resp.headers.get("content-type", "text/html").split(";")[0].strip().lower()
                if ctype not in ALLOWED_TYPES:
                    raise DocentError(415, "page_unsupported", f"HTML·텍스트 페이지만 읽을 수 있습니다 ({ctype}).")
                body, truncated = bytearray(), False
                async for chunk in resp.aiter_raw():
                    body += chunk
                    if len(body) > self._max_bytes:
                        del body[self._max_bytes:]
                        truncated = True
                        break
                return Fetched(str(u), self._decompress(resp, bytes(body)), ctype, resp.charset_encoding, truncated)
            finally:
                await resp.aclose()
        raise DocentError(502, "page_fetch_failed", f"리다이렉트가 {self._max_redirects}번을 넘었습니다.")

    def _decompress(self, resp: httpx.Response, raw: bytes) -> bytes:
        """크기는 압축된 그대로 센 뒤 푼다. 압축 폭탄을 막으려고 푼 결과도 크기 제한까지만 쓴다."""
        encoding = resp.headers.get("content-encoding", "").lower().strip()
        if not encoding or encoding == "identity":
            return raw
        if encoding in ("gzip", "x-gzip"):
            d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        elif encoding == "deflate":
            d = zlib.decompressobj()
        else:
            raise DocentError(415, "page_unsupported", f"지원하지 않는 압축 형식입니다: {encoding}")
        try:
            return d.decompress(raw, self._max_bytes)
        except zlib.error as e:
            raise DocentError(502, "page_fetch_failed", "압축된 본문을 풀지 못했습니다.") from e


# ---------- 본문 텍스트 추출 ----------

_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_.:-]+)""", re.I)
_KOREAN_ALIASES = {"euc-kr", "euc_kr", "ks_c_5601-1987", "ksc5601", "x-windows-949", "windows-949"}


def decode(body: bytes, charset: str | None) -> str:
    """헤더 charset -> <meta charset> -> UTF-8 -> CP949 순서로 시도. 한국 사이트의 EUC-KR 은 CP949 로 읽는다."""
    meta = _META_CHARSET.search(body[:4096])
    candidates = []
    for name in (charset, meta.group(1).decode("ascii", "ignore") if meta else None, "utf-8", "cp949"):
        if not name:
            continue
        name = "cp949" if name.lower() in _KOREAN_ALIASES else name
        try:
            codecs.lookup(name)
        except LookupError:
            continue
        candidates.append(name)
    for name in candidates:
        try:
            return body.decode(name)
        except UnicodeDecodeError as e:
            if e.start >= len(body) - 4:  # 크기 제한으로 잘린 마지막 글자만 깨짐
                return body.decode(name, errors="replace")
    return body.decode("utf-8", errors="replace")


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg", "iframe", "canvas", "nav", "footer", "header", "aside",
            "form", "button", "select", "head", "object", "video", "audio"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "main",
             "blockquote", "pre", "dd", "dt", "table", "ul", "ol", "figcaption", "hr", "td", "th"}
    VOID = {"br", "hr", "img", "meta", "link", "input", "area", "base", "col", "embed", "source", "track", "wbr"}
    MAIN = {"article", "main"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.main_parts: list[str] = []
        self.title = ""
        self.og_title = ""
        self._skip = 0
        self._main = 0
        self._in_title = False

    def _emit(self, text: str) -> None:
        self.parts.append(text)
        if self._main:
            self.main_parts.append(text)

    def handle_starttag(self, tag, attrs):
        if tag == "meta":
            a = dict(attrs)
            if (a.get("property") or a.get("name") or "").lower() == "og:title":
                self.og_title = (a.get("content") or "").strip()
            return
        if tag == "title":
            self._in_title = True
        if tag in self.VOID:
            if tag in self.BLOCK:
                self._emit("\n")
            return
        if tag in self.SKIP:
            self._skip += 1
        elif tag in self.MAIN and not self._skip:
            self._main += 1
        if tag in self.BLOCK:
            self._emit("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in self.VOID:
            return
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in self.MAIN and self._main and not self._skip:
            self._main -= 1
        if tag in self.BLOCK:
            self._emit("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if not self._skip:
            self._emit(data)


_SPACES = re.compile(r"[ \t\r\f\v\u00a0\u200b\ufeff]+")  # NBSP·폭 없는 공백·BOM 포함


def _normalize(text: str) -> str:
    lines = (_SPACES.sub(" ", line).strip() for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


MIN_MAIN_CHARS = 200  # <article>/<main> 본문이 이보다 짧으면 전체 본문을 쓴다


def extract(html_text: str, content_type: str) -> tuple[str, str]:
    """(제목, 본문 텍스트). 스크립트·스타일·메뉴·머리말·꼬리말을 뺀다."""
    if content_type == "text/plain":
        return "", _normalize(html_text)
    p = _TextExtractor()
    try:
        p.feed(html_text)
        p.close()
    except Exception:  # noqa: BLE001 — 깨진 HTML 이어도 읽은 데까지는 쓴다
        pass
    main = _normalize("".join(p.main_parts))
    body = main if len(main) >= MIN_MAIN_CHARS else _normalize("".join(p.parts))
    title = re.sub(r"\s+", " ", p.og_title or p.title).strip()
    return title[:300], body


_fetcher: PageFetcher | None = None


def get_fetcher() -> PageFetcher:
    """FastAPI 의존성. 테스트에서는 가짜 DNS·전송으로 만든 PageFetcher 로 바꾼다."""
    global _fetcher
    if _fetcher is None:
        _fetcher = PageFetcher()
    return _fetcher
