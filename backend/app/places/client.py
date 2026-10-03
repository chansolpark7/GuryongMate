"""공공데이터포털(data.go.kr) 한국관광공사 API 클라이언트.

- KorService2: 국문 관광정보 (관광지·음식점·숙박 등 목록/상세)
- Odii: 관광지 오디오 가이드 (해설 대본·음성)
"""
import json
import re
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import unquote

import httpx

KOR_SERVICE = "https://apis.data.go.kr/B551011/KorService2"
ODII_SERVICE = "https://apis.data.go.kr/B551011/Odii"

OK_CODES = {"0000", "00", "0"}


class DataGoKrError(RuntimeError):
    def __init__(self, operation: str, code: str, message: str):
        super().__init__(f"{operation}: [{code}] {message}")
        self.operation = operation
        self.code = code
        self.message = message


def parse_response(operation: str, status_code: int, text: str) -> dict[str, Any]:
    """응답 본문을 검사해 body 를 반환. 포털 게이트웨이 오류는 XML/평문으로 오므로 따로 처리."""
    body = text.strip()
    if body.startswith("<"):
        code = _xml_tag(body, "returnReasonCode") or _xml_tag(body, "resultCode") or str(status_code)
        msg = _xml_tag(body, "returnAuthMsg") or _xml_tag(body, "resultMsg") or _xml_tag(body, "errMsg") or body[:200]
        raise DataGoKrError(operation, code, msg)
    if not body.startswith("{"):
        raise DataGoKrError(operation, str(status_code), body[:200] or "empty response")

    data = json.loads(body)
    if "OpenAPI_ServiceResponse" in data:  # 게이트웨이 오류의 JSON 형태
        header = data["OpenAPI_ServiceResponse"].get("cmmMsgHeader", {})
        raise DataGoKrError(operation, str(header.get("returnReasonCode", status_code)),
                            header.get("errMsg") or header.get("returnAuthMsg") or "")
    if status_code != 200:
        raise DataGoKrError(operation, str(status_code), body[:200])
    response = data.get("response", data)
    header = response.get("header", {})
    code = str(header.get("resultCode", "0000"))
    if code not in OK_CODES:
        raise DataGoKrError(operation, code, header.get("resultMsg", ""))
    return response.get("body", {}) or {}


def extract_items(body: dict[str, Any]) -> list[dict[str, Any]]:
    items = body.get("items")
    if not items:  # 결과 없음이면 "" 로 오는 경우가 있음
        return []
    item = items.get("item", [])
    return [item] if isinstance(item, dict) else list(item)


def _xml_tag(text: str, tag: str) -> str | None:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.S)
    return m.group(1).strip() if m else None


class TourApiClient:
    def __init__(self, service_key: str, mobile_app: str = "GuryongMate", timeout: float = 20.0):
        if not service_key:
            raise ValueError("DATA_GO_KR_SERVICE_KEY 가 비어 있습니다. backend/.env 를 확인하세요.")
        # 포털의 'Encoding' 키를 넣어도 동작하도록 디코딩해서 보관 (httpx 가 다시 인코딩함)
        self.service_key = unquote(service_key) if "%" in service_key else service_key
        self.mobile_app = mobile_app
        self.http = httpx.Client(timeout=timeout)
        self.calls = 0

    def close(self) -> None:
        self.http.close()

    def get(self, base: str, operation: str, **params: Any) -> dict[str, Any]:
        query = {
            "serviceKey": self.service_key,
            "MobileOS": "ETC",
            "MobileApp": self.mobile_app,
            "_type": "json",
            **{k: v for k, v in params.items() if v is not None},
        }
        for attempt in range(3):
            try:
                r = self.http.get(f"{base}/{operation}", params=query)
            except httpx.TransportError:
                if attempt == 2:
                    raise
                time.sleep(1 + attempt)
                continue
            self.calls += 1
            if r.status_code >= 500 and attempt < 2:
                time.sleep(1 + attempt)
                continue
            return parse_response(operation, r.status_code, r.text)
        raise AssertionError("unreachable")

    def paginate(self, base: str, operation: str, page_size: int = 100, **params: Any) -> Iterator[dict[str, Any]]:
        page = 1
        seen = 0
        while True:
            body = self.get(base, operation, numOfRows=page_size, pageNo=page, **params)
            items = extract_items(body)
            yield from items
            seen += len(items)
            total = int(body.get("totalCount") or 0)
            if not items or seen >= total:
                return
            page += 1

    # --- KorService2 ---
    def location_based(self, lat: float, lng: float, radius_m: int, **params: Any) -> Iterator[dict[str, Any]]:
        return self.paginate(KOR_SERVICE, "locationBasedList2", mapX=lng, mapY=lat, radius=radius_m, arrange="E", **params)

    def area_based(self, regn_cd: str, signgu_cd: str, **params: Any) -> Iterator[dict[str, Any]]:
        return self.paginate(KOR_SERVICE, "areaBasedList2", lDongRegnCd=regn_cd, lDongSignguCd=signgu_cd, arrange="C", **params)

    def detail(self, operation: str, content_id: str, **params: Any) -> list[dict[str, Any]]:
        return extract_items(self.get(KOR_SERVICE, operation, contentId=content_id, numOfRows=100, pageNo=1, **params))

    def category_codes(self) -> list[dict[str, Any]]:
        return list(self.paginate(KOR_SERVICE, "lclsSystmCode2", page_size=1000, lclsSystmListYn="Y"))

    # --- Odii (오디오 가이드) ---
    def odii_themes_near(self, lat: float, lng: float, radius_m: int) -> Iterator[dict[str, Any]]:
        return self.paginate(ODII_SERVICE, "themeLocationBasedList", mapX=lng, mapY=lat, radius=radius_m, langCode="ko")

    def odii_stories_near(self, lat: float, lng: float, radius_m: int) -> Iterator[dict[str, Any]]:
        return self.paginate(ODII_SERVICE, "storyLocationBasedList", mapX=lng, mapY=lat, radius=radius_m, langCode="ko")
