"""API 원본 응답 -> DB 레코드 변환. 네트워크/DB 에 의존하지 않는 순수 함수만 둔다."""
import html
import math
import re
from typing import Any

CONTENT_TYPES = {
    "12": "관광지",
    "14": "문화시설",
    "15": "축제공연행사",
    "25": "여행코스",
    "28": "레포츠",
    "32": "숙박",
    "38": "쇼핑",
    "39": "음식점",
}

# detailIntro2 는 타입마다 필드명이 다르다 -> 공통 항목으로 모음
_INTRO_FIELDS = {
    "open_hours": ["usetime", "usetimeculture", "usetimeleports", "opentime", "opentimefood", "playtime"],
    "rest_date": ["restdate", "restdateculture", "restdateleports", "restdateshopping", "restdatefood"],
    "parking": ["parking", "parkingculture", "parkingleports", "parkingshopping", "parkingfood", "parkinglodging"],
    "fee": ["usefee", "usefeeleports", "usetimefestival", "parkingfee"],
    "menu": ["firstmenu", "treatmenu"],
}

_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_AREA_RE = re.compile(r"포항시\s+[남북]구\s+(\S+?[읍면동])(?:\s|$)")
_PAREN_DONG_RE = re.compile(r"\((\S+?[읍면동])[,)]")  # 도로명 주소 끝의 (송도동)


def clean_text(value: Any) -> str:
    """HTML 태그 제거 및 엔티티 디코딩 (<br> 은 줄바꿈으로)."""
    if value is None:
        return ""
    text = html.unescape(str(value)).replace("\xa0", " ")
    text = _BR_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln).strip()


def extract_homepage(value: Any) -> str:
    """homepage 는 <a href="..."> 형태로 오는 경우가 많아 첫 URL 만 추출."""
    if not value:
        return ""
    text = html.unescape(str(value))
    m = re.search(r"https?://[^\s\"'<>]+", text)
    return m.group(0) if m else clean_text(text)


def extract_area(addr: str) -> str:
    m = _AREA_RE.search(addr + " ") or _PAREN_DONG_RE.search(addr)
    return m.group(1) if m else ""


def fill_missing_areas(places: list[dict[str, Any]], max_m: float = 1500) -> None:
    """주소에 읍면동이 없는 장소는 가장 가까운(max_m 이내) 장소의 읍면동으로 채운다."""
    known = [p for p in places if p["area"]]
    for p in places:
        if p["area"]:
            continue
        nearest = min(known, key=lambda k: haversine_m(p["lat"], p["lng"], k["lat"], k["lng"]), default=None)
        if nearest and haversine_m(p["lat"], p["lng"], nearest["lat"], nearest["lng"]) <= max_m:
            p["area"] = nearest["area"]
            p["tags"].append(nearest["area"])
            p["search_compact"] = build_search_compact(p)


def pick_intro(intro: dict[str, Any]) -> dict[str, str]:
    result = {}
    for key, fields in _INTRO_FIELDS.items():
        values = [clean_text(intro.get(f)) for f in fields]
        result[key] = "\n".join(dict.fromkeys(v for v in values if v))  # 중복 제거, 순서 유지
    return result


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def build_place(
    listing: dict[str, Any],
    common: dict[str, Any] | None,
    intro: dict[str, Any] | None,
    images: list[dict[str, Any]],
    infos: list[dict[str, Any]],
    category_names: dict[str, str],
) -> dict[str, Any]:
    src = {**listing, **(common or {})}
    content_id = str(src["contentid"])
    type_id = str(src.get("contenttypeid", ""))
    addr = " ".join(p for p in [clean_text(src.get("addr1")), clean_text(src.get("addr2"))] if p)
    codes = [c for c in (src.get("lclsSystm1"), src.get("lclsSystm2"), src.get("lclsSystm3")) if c]
    names = [category_names[c] for c in codes if c in category_names]
    area = extract_area(addr)
    intro = {k: v for k, v in (intro or {}).items() if k not in ("contentid", "contenttypeid") and v not in ("", None)}

    tags = [CONTENT_TYPES.get(type_id, ""), *names, area]
    picked = pick_intro(intro)
    if "가능" in clean_text(intro.get("chkpet") or intro.get("chkpetculture") or ""):
        tags.append("반려동물동반")

    place = {
        "id": f"kto-{content_id}",
        "source": "kto",
        "content_id": content_id,
        "content_type_id": type_id,
        "content_type": CONTENT_TYPES.get(type_id, type_id),
        "title": clean_text(src.get("title")),
        "lat": float(src.get("mapy") or 0),
        "lng": float(src.get("mapx") or 0),
        "addr": addr,
        "area": area,
        "tel": clean_text(src.get("tel")),
        "homepage": extract_homepage(src.get("homepage")),
        "overview": clean_text(src.get("overview")),
        **picked,
        "category_codes": codes,
        "category_names": names,
        "tags": list(dict.fromkeys(t for t in tags if t)),
        "image_url": src.get("firstimage") or "",
        "thumbnail_url": src.get("firstimage2") or "",
        "images": [
            {"url": i.get("originimgurl", ""), "thumbnail": i.get("smallimageurl", ""), "name": i.get("imgname", ""),
             "copyright_type": i.get("cpyrhtDivCd", "")}
            for i in images
            if i.get("originimgurl")
        ],
        "copyright_type": src.get("cpyrhtDivCd") or "",
        "intro": intro,
        "extra_info": [
            {"name": clean_text(i.get("infoname")), "text": clean_text(i.get("infotext"))}
            for i in infos
            if i.get("infoname") or i.get("infotext")
        ],
        "source_modified_at": str(src.get("modifiedtime") or ""),
    }
    place["search_compact"] = build_search_compact(place)
    return place


def build_story(item: dict[str, Any], theme_titles: dict[str, str]) -> dict[str, Any]:
    tid = str(item.get("tid", ""))
    return {
        "id": f"odii-{item.get('stid')}-{item.get('stlid')}",
        "odii_theme_id": tid,
        "odii_theme_title": theme_titles.get(tid, ""),
        "title": clean_text(item.get("audioTitle") or item.get("title")),
        "script": clean_text(item.get("script")),
        "play_time_sec": int(item.get("playTime") or 0),
        "audio_url": item.get("audioUrl") or "",
        "image_url": item.get("imageUrl") or "",
        "lat": float(item.get("mapY") or 0),
        "lng": float(item.get("mapX") or 0),
        "lang": item.get("langCode") or "ko",
    }


def _norm_name(name: str) -> str:
    return re.sub(r"[\s()\[\]·,.]", "", name)


def _name_variants(name: str) -> set[str]:
    """'포항 일본인가옥거리(근대문화역사거리)' -> 전체, 괄호 제거, '포항' 접두어 제거 버전."""
    base = re.sub(r"\(.*?\)", "", name)
    variants = {name, base, re.sub(r"^포항\s*", "", base)}
    return {v for v in map(_norm_name, variants) if len(v) >= 2}


def match_story_place(story: dict[str, Any], places: list[dict[str, Any]], max_m: float = 1500) -> str | None:
    """이야기를 가장 그럴듯한 KTO 장소에 연결. 이름이 겹치면 max_m 이내에서 가장 가까운 곳, 아니면 아주 가까운(80m) 장소만."""
    names = _name_variants(story["odii_theme_title"] or story["title"])
    best: tuple[float, str] | None = None
    for p in places:
        d = haversine_m(story["lat"], story["lng"], p["lat"], p["lng"])
        if d > max_m:
            continue
        titles = _name_variants(p["title"])
        name_match = any(n in t or t in n for n in names for t in titles)
        if not name_match and d > 80:
            continue
        score = d - (1000 if name_match else 0)
        if best is None or score < best[0]:
            best = (score, p["id"])
    return best[1] if best else None


# --- 키워드 검색 ---

# 여행자가 자주 쓰는 말 -> 데이터에 실제로 쓰이는 말. 필요한 만큼만 추가한다.
SYNONYMS = {
    "맛집": ["맛집", "음식점", "식당"],
    "밥집": ["음식점", "식당"],
    "식당": ["식당", "음식점"],
    "숙소": ["숙박", "숙소", "펜션", "호텔", "모텔"],
    "해변": ["해변", "해수욕장"],
    "바닷가": ["바닷가", "해변", "해수욕장", "해안"],
    "박물관": ["박물관", "전시관", "역사관", "기념관"],
    "커피": ["커피", "카페"],
}
_JOSA = ("에서", "으로", "에게", "까지", "부터", "에", "의", "을", "를", "은", "는", "이", "가", "와", "과", "도", "로", "랑")


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


MANUAL_TYPE_IDS = {v: k for k, v in CONTENT_TYPES.items()}


def build_manual_place(raw: dict[str, Any]) -> dict[str, Any]:
    """직접 정의한 장소(data/places/manual/*.json 한 건) -> DB 레코드. 관광공사 데이터에 없는 곳(방파제 등)을 넣는다.
    필수: id(kto- 가 아닌 접두사, 예: poi-gr-…), title, lat, lng, content_type. 나머지는 비우면 빈 값. note 는 기록용(적재 안 함)."""
    for key in ("id", "title", "lat", "lng", "content_type"):
        if raw.get(key) in (None, ""):
            raise ValueError(f"수동 장소에 {key} 가 없습니다: {raw.get('id') or raw.get('title')}")
    if raw["id"].startswith("kto-"):
        raise ValueError(f"수동 장소 id 는 kto- 로 시작할 수 없습니다 (관광공사 데이터와 겹침): {raw['id']}")
    if raw["content_type"] not in MANUAL_TYPE_IDS:
        raise ValueError(f"content_type 은 {sorted(MANUAL_TYPE_IDS)} 중 하나: {raw['id']}")
    names = list(raw.get("category_names") or [])
    addr = raw.get("addr", "")
    area = raw.get("area") or extract_area(addr)
    place = {
        "id": raw["id"],
        "source": "manual",
        "content_id": "",  # 관광공사 contentid 없음
        "content_type_id": MANUAL_TYPE_IDS[raw["content_type"]],
        "content_type": raw["content_type"],
        "title": raw["title"],
        "lat": float(raw["lat"]),
        "lng": float(raw["lng"]),
        "addr": addr,
        "area": area,
        "tel": raw.get("tel", ""),
        "homepage": raw.get("homepage", ""),
        "overview": raw.get("overview", ""),
        "open_hours": raw.get("open_hours", ""),
        "rest_date": raw.get("rest_date", ""),
        "parking": raw.get("parking", ""),
        "fee": raw.get("fee", ""),
        "menu": raw.get("menu", ""),
        "category_codes": [],
        "category_names": names,
        "tags": list(dict.fromkeys(t for t in [raw["content_type"], *names, area, *(raw.get("tags") or [])] if t)),
        "image_url": raw.get("image_url", ""),
        "thumbnail_url": raw.get("thumbnail_url", ""),
        "images": [],
        "copyright_type": "",
        "intro": {},
        "extra_info": [],
        "source_modified_at": "",
    }
    place["search_compact"] = build_search_compact(place)
    return place


def build_search_compact(p: dict[str, Any]) -> str:
    g = p.get
    parts = [g("title", ""), g("content_type", ""), *g("category_names", []), *g("tags", []), g("area", ""), g("addr", ""),
             g("menu", ""), g("overview", ""), *(f'{i["name"]} {i["text"]}' for i in g("extra_info", []))]
    return compact(" ".join(x for x in parts if x))


def query_terms(q: str) -> list[list[str]]:
    """질의를 단어별 후보 목록으로. 모든 단어가 맞아야 하고(AND), 각 단어는 후보 중 하나만 맞으면 된다(OR).

    '구룡포에서 맛집' -> [['구룡포에서', '구룡포'], ['맛집', '음식점', '식당']]
    """
    terms = []
    for word in q.split():
        word = compact(word)
        variants = [word]
        for j in _JOSA:
            if word.endswith(j) and len(word) - len(j) >= 2:
                variants.append(word[: -len(j)])
                break
        for v in list(variants):
            variants += [compact(s) for s in SYNONYMS.get(v, [])]
        terms.append(list(dict.fromkeys(variants)))
    return [t for t in terms if t and t[0]]


# --- 임베딩 조각 ---

def place_chunks(p: dict[str, Any]) -> list[dict[str, Any]]:
    """장소 1곳 -> 임베딩 조각들. 각 조각 앞에 장소 정보 머리말을 붙여 문맥을 유지한다."""
    from app.places.embedding import split_text

    labels = [p["content_type"], *p["category_names"], p["area"]]
    head = f'{p["title"]} ({", ".join(x for x in labels if x)})'
    body = p["overview"]
    if p["menu"]:
        body = f'메뉴: {p["menu"]}\n{body}'
    pieces = split_text(body) or [""]
    return [{"kind": "place", "place_id": p["id"], "story_id": None, "seq": i, "title": p["title"],
             "text": f"{head}\n{piece}".strip(), "lat": p["lat"], "lng": p["lng"]} for i, piece in enumerate(pieces)]


def story_chunks(s: dict[str, Any]) -> list[dict[str, Any]]:
    from app.places.embedding import split_text

    head = f'{s["odii_theme_title"]} - {s["title"]}'.strip(" -")
    return [{"kind": "story", "place_id": s["place_id"], "story_id": s["id"], "seq": i, "title": head,
             "text": f"{head}\n{piece}", "lat": s["lat"], "lng": s["lng"]} for i, piece in enumerate(split_text(s["script"]))]
