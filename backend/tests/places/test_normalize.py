import pytest

from app.places.embedding import split_text
from app.places.client import DataGoKrError, extract_items, parse_response
from app.places.ingest import parse_categories
from app.places.normalize import (build_place, build_story, clean_text, extract_area, extract_homepage, fill_missing_areas,
                                  match_story_place, place_chunks, query_terms, story_chunks)

LISTING = {
    "contentid": "126128",
    "contenttypeid": "12",
    "title": "구룡포 일본인가옥거리",
    "addr1": "경상북도 포항시 남구 구룡포읍 구룡포길 153-1",
    "addr2": "",
    "mapx": "129.5570",
    "mapy": "35.9880",
    "firstimage": "http://img/a.jpg",
    "firstimage2": "http://img/a_s.jpg",
    "cpyrhtDivCd": "Type3",
    "lclsSystm1": "HS",
    "lclsSystm2": "HS01",
    "lclsSystm3": "HS010100",
    "modifiedtime": "20250425092225",
}
COMMON = {
    **LISTING,
    "homepage": '<a href="https://www.pohang.go.kr/phtour" target="_blank">포항관광</a>',
    "overview": "근대 역사 거리&nbsp;입니다.<br>일제강점기 건물이 남아 있다.",
    "tel": "054-270-5837",
}
INTRO = {"contentid": "126128", "contenttypeid": "12", "usetime": "09:00~18:00<br />(입장마감 17:30)",
         "restdate": "매주 월요일", "parking": "가능", "chkpet": "불가"}
NAMES = {"HS": "역사관광", "HS01": "역사유적지", "HS010100": "근대유적"}


def test_clean_text_and_homepage():
    assert clean_text("a<br/>b &amp; <b>c</b>") == "a\nb & c"
    assert clean_text(None) == ""
    assert extract_homepage(COMMON["homepage"]) == "https://www.pohang.go.kr/phtour"


def test_extract_area():
    assert extract_area("경상북도 포항시 남구 구룡포읍 구룡포길 153-1") == "구룡포읍"
    assert extract_area("경상북도 포항시 남구 호미곶면 해맞이로 150") == "호미곶면"
    assert extract_area("경상북도 포항시 남구 운하로251번길 34-1 (송도동)") == "송도동"
    assert extract_area("부산광역시 사하구") == ""


def test_fill_missing_areas():
    a = {"area": "구룡포읍", "lat": 35.9880, "lng": 129.5570, "tags": []}
    b = {"area": "", "lat": 35.9900, "lng": 129.5560, "tags": []}  # 약 240m
    c = {"area": "", "lat": 36.2000, "lng": 129.3000, "tags": []}  # 멀리
    fill_missing_areas([a, b, c])
    assert (b["area"], b["tags"], c["area"]) == ("구룡포읍", ["구룡포읍"], "")


def test_build_place():
    p = build_place(LISTING, COMMON, INTRO, [{"originimgurl": "http://img/1.jpg", "smallimageurl": "s", "imgname": "n",
                                             "cpyrhtDivCd": "Type1"}], [{"infoname": "화장실", "infotext": "있음"}], NAMES)
    assert p["id"] == "kto-126128"
    assert p["content_type"] == "관광지"
    assert (p["lat"], p["lng"]) == (35.988, 129.557)
    assert p["area"] == "구룡포읍"
    assert p["open_hours"] == "09:00~18:00\n(입장마감 17:30)"
    assert p["rest_date"] == "매주 월요일"
    assert p["overview"] == "근대 역사 거리 입니다.\n일제강점기 건물이 남아 있다."
    assert p["tags"] == ["관광지", "역사관광", "역사유적지", "근대유적", "구룡포읍"]
    assert p["images"][0]["url"] == "http://img/1.jpg"
    assert p["extra_info"] == [{"name": "화장실", "text": "있음"}]
    assert "contentid" not in p["intro"]


def test_parking_fee_goes_to_parking_not_fee():
    """주차요금(parkingfee)은 이용요금(fee)에 섞지 않고 주차(parking) 끝에 "주차요금: …" 으로 붙인다 (예: 문화시설)."""
    culture = {"contentid": "1", "contenttypeid": "14", "usefee": "공연별 상이", "parkingculture": "가능", "parkingfee": "무료"}
    p = build_place(LISTING, COMMON, culture, [], [], NAMES)
    assert p["fee"] == "공연별 상이" and p["parking"] == "가능\n주차요금: 무료"
    only_fee = build_place(LISTING, COMMON, {"parkingfee": "1시간 1,000원"}, [], [], NAMES)
    assert only_fee["fee"] == "" and only_fee["parking"] == "주차요금: 1시간 1,000원"
    assert build_place(LISTING, COMMON, INTRO, [], [], NAMES)["parking"] == "가능"  # 주차요금이 없으면 그대로


def test_build_place_without_details():
    p = build_place(LISTING, None, None, [], [], {})
    assert p["title"] == "구룡포 일본인가옥거리"
    assert p["category_names"] == []


def test_story_matching():
    place = build_place(LISTING, COMMON, INTRO, [], [], NAMES)
    far = {**place, "id": "kto-2", "title": "다른곳", "lat": 35.9881, "lng": 129.5571}
    story = build_story({"tid": "9", "stid": "1", "stlid": "2", "title": "구룡포 일본인 가옥거리", "audioTitle": "가옥거리 이야기",
                         "script": "옛날<br>이야기", "playTime": "120", "mapX": "129.5575", "mapY": "35.9883"},
                        {"9": "구룡포 일본인 가옥거리"})
    assert story["id"] == "odii-1-2"
    assert story["script"] == "옛날\n이야기"
    assert match_story_place(story, [far, place]) == "kto-126128"  # 이름 일치 우선
    assert match_story_place({**story, "lat": 36.5}, [place]) is None  # 너무 멀면 연결 안 함
    paren = {**story, "odii_theme_title": "일본인가옥거리(근대문화역사거리)", "lat": 35.9895, "lng": 129.5575}  # 약 170m
    assert match_story_place(paren, [far, place]) == "kto-126128"  # 괄호·띄어쓰기 달라도 연결


def test_parse_response_errors():
    xml = ("<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>SERVICE ERROR</errMsg>"
           "<returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg><returnReasonCode>30</returnReasonCode>"
           "</cmmMsgHeader></OpenAPI_ServiceResponse>")
    with pytest.raises(DataGoKrError) as e:
        parse_response("op", 200, xml)
    assert e.value.code == "30"
    with pytest.raises(DataGoKrError) as e:
        parse_response("op", 403, '{"OpenAPI_ServiceResponse":{"cmmMsgHeader":{"errMsg":"SERVICE_KEY_IS_NOT_REGISTERED_ERROR",'
                                  '"returnReasonCode":"30"}}}')
    assert e.value.code == "30"
    with pytest.raises(DataGoKrError):
        parse_response("op", 401, "Unauthorized")
    with pytest.raises(DataGoKrError):
        parse_response("op", 200, '{"response":{"header":{"resultCode":"10","resultMsg":"INVALID"}}}')


def test_parse_response_items():
    body = parse_response("op", 200, '{"response":{"header":{"resultCode":"0000"},"body":{"items":{"item":{"a":1}},"totalCount":1}}}')
    assert extract_items(body) == [{"a": 1}]
    assert extract_items({"items": ""}) == []


def test_parse_categories():
    cats = parse_categories([{"lclsSystm1Cd": "HS", "lclsSystm1Nm": "역사관광", "lclsSystm2Cd": "HS01", "lclsSystm2Nm": "역사유적지",
                              "lclsSystm3Cd": "HS010100", "lclsSystm3Nm": "근대유적"}])
    assert [c["code"] for c in cats] == ["HS", "HS01", "HS010100"]
    assert cats[2]["parent_code"] == "HS01"


def test_query_terms():
    assert query_terms("구룡포에서  맛집") == [["구룡포에서", "구룡포"], ["맛집", "음식점", "식당"]]
    assert query_terms("일본인 가옥") == [["일본인"], ["가옥"]]
    assert query_terms("   ") == []


def test_search_compact():
    p = build_place(LISTING, COMMON, INTRO, [], [{"infoname": "화장실", "infotext": "있음"}], NAMES)
    assert "구룡포일본인가옥거리" in p["search_compact"]
    assert "화장실있음" in p["search_compact"]


def test_split_text():
    assert split_text("짧은 글") == ["짧은 글"]
    text = " ".join(f"문장{i}입니다." for i in range(200))
    chunks = split_text(text, size=100, overlap=20)
    assert all(len(c) <= 100 for c in chunks)
    assert chunks[0].startswith("문장0") and "문장199" in chunks[-1]
    assert all(len(c) <= 100 for c in split_text("가" * 350, size=100, overlap=20))


def test_chunks():
    p = build_place(LISTING, COMMON, INTRO, [], [], NAMES)
    c = place_chunks(p)
    assert c[0]["text"].startswith("구룡포 일본인가옥거리 (관광지, 역사관광, 역사유적지, 근대유적, 구룡포읍)")
    story = {"id": "odii-1-2", "place_id": "kto-1", "odii_theme_title": "가옥거리", "title": "이야기", "script": "가" * 1200,
             "lat": 1.0, "lng": 2.0}
    sc = story_chunks(story)
    assert len(sc) >= 3 and sc[0]["text"].startswith("가옥거리 - 이야기\n") and sc[0]["story_id"] == "odii-1-2"
