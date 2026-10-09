"""출처 등급 (§2.2). 도메인 규칙으로만 정한다 (결정론적).

official  공공·기관 (정부·지자체·공공기관·연구기관·대학, 관광공사·박물관)
press     언론사
community 블로그·카페·SNS·위키·질문답변
unknown   그 밖의 모든 곳
"""
from urllib.parse import urlsplit

# 이 접미사로 끝나는 도메인 (자기 자신 포함)
OFFICIAL_SUFFIXES = (
    "go.kr", "or.kr", "re.kr", "ac.kr", "mil.kr",
    "grandculture.net",  # 한국향토문화전자대전 (한국학중앙연구원)
)
PRESS_DOMAINS = (
    # 통신·종합지·경제지
    "yna.co.kr", "newsis.com", "news1.kr", "chosun.com", "joongang.co.kr", "joins.com", "donga.com", "hani.co.kr",
    "khan.co.kr", "kmib.co.kr", "seoul.co.kr", "hankookilbo.com", "munhwa.com", "segye.com", "mk.co.kr",
    "hankyung.com", "sedaily.com", "edaily.co.kr", "asiae.co.kr", "heraldcorp.com", "fnnews.com", "mt.co.kr",
    "nocutnews.co.kr", "ohmynews.com", "pressian.com", "newspim.com", "etnews.com",
    # 방송
    "kbs.co.kr", "imbc.com", "sbs.co.kr", "ytn.co.kr", "jtbc.co.kr", "mbn.co.kr", "ichannela.com", "tvchosun.com",
    # 대구·경북 지역지
    "imaeil.com", "yeongnam.com", "kyongbuk.co.kr", "kbmaeil.com", "idaegu.com", "idaegu.co.kr", "ksilbo.co.kr",
    "tbc.co.kr", "pohangmbc.com",
    # 포털 뉴스
    "news.naver.com", "n.news.naver.com", "v.daum.net",
)
COMMUNITY_DOMAINS = (
    "blog.naver.com", "cafe.naver.com", "post.naver.com", "kin.naver.com", "tistory.com", "brunch.co.kr",
    "blog.daum.net", "cafe.daum.net", "velog.io", "egloos.com", "blogspot.com", "wordpress.com", "medium.com",
    "instagram.com", "facebook.com", "youtube.com", "youtu.be", "x.com", "twitter.com", "threads.net",
    "namu.wiki", "wikipedia.org", "dcinside.com", "tripadvisor.co.kr", "tripadvisor.com",
)


def _matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def tier_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    if not host:
        return "unknown"
    if host.startswith("m."):  # 모바일 주소 (m.blog.naver.com 등)
        host = host[2:]
    if any(_matches(host, d) for d in OFFICIAL_SUFFIXES):
        return "official"
    if any(_matches(host, d) for d in PRESS_DOMAINS):
        return "press"
    if any(_matches(host, d) for d in COMMUNITY_DOMAINS):
        return "community"
    return "unknown"
