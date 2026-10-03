"""구룡포 관광 정보 수집 -> DB 적재.

사용법 (docker):
    docker compose run --rm api python -m app.places.ingest fetch   # API 호출 -> DB + 스냅샷 저장
    docker compose run --rm api python -m app.places.ingest load    # 커밋된 스냅샷 -> DB (키 불필요)
    docker compose run --rm api python -m app.places.ingest embed   # 의미 검색용 임베딩 생성 (fetch/load 에 --embed 도 가능)

API 원본 응답은 data/places/raw/ 에 캐시되므로, 일일 호출 한도(개발계정 오퍼레이션별 1,000건)에
걸려 중단돼도 다시 실행하면 이어서 진행된다. 캐시를 무시하려면 --refresh.
"""
import argparse
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.config import settings
from app.db import Base, SessionLocal, engine, init_db
from app.places.client import DataGoKrError, TourApiClient
from app.places.models import Place, PlaceCategory, PlaceChunk, PlaceStory
from app.places.normalize import (build_place, build_search_compact, build_story, fill_missing_areas, match_story_place,
                                  place_chunks, story_chunks)

log = logging.getLogger("places.ingest")

# KTO 데이터가 구룡포 반경 12km 안에 39건뿐이라(2026-10 기준) 포항시 전체를 수집하고,
# 구룡포 집중은 area(읍면동) 필터나 위치 반경 검색으로 한다.
POHANG_SIGUNGU = {"47": ["111", "113"]}  # 경상북도: 포항시 남구, 북구 (법정동 코드)

# 오디오 가이드(Odii)는 위치 기반으로만 수집. 구룡포항 중심 20km(API 최대) = 포항 남부 대부분.
GURYONGPO_CENTER = (35.9890, 129.5560)
ODII_RADIUS_M = 20_000

DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "places"
RAW_DIR = DATA_DIR / "raw"
SNAPSHOT_DIR = DATA_DIR / "snapshot"


class RawCache:
    def __init__(self, root: Path, refresh: bool = False):
        self.root = root
        self.refresh = refresh

    def get_or_fetch(self, group: str, key: str, fetch: Callable[[], Any]) -> Any:
        path = self.root / group / f"{key}.json"
        if path.exists() and not self.refresh:
            return json.loads(path.read_text(encoding="utf-8"))
        data = fetch()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data


def parse_categories(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cats: dict[str, dict[str, Any]] = {}
    for it in items:
        parent = ""
        for depth in (1, 2, 3):
            code, name = it.get(f"lclsSystm{depth}Cd"), it.get(f"lclsSystm{depth}Nm")
            if code and name:
                cats.setdefault(code, {"code": code, "name": name, "depth": depth, "parent_code": parent})
                parent = code
    return list(cats.values())


def fetch_all(client: TourApiClient, cache: RawCache, lat: float, lng: float, radius_m: int) -> dict[str, list]:
    """lat/lng/radius_m 는 오디오 가이드 수집 범위. 장소 목록은 POHANG_SIGUNGU 전체."""
    categories = parse_categories(cache.get_or_fetch("categories", "all", client.category_codes))
    names = {c["code"]: c["name"] for c in categories}

    listings = []
    for regn, signgus in POHANG_SIGUNGU.items():
        for sg in signgus:
            listings += cache.get_or_fetch("list", f"area_{regn}_{sg}", lambda: list(client.area_based(regn, sg)))
    log.info("목록 %d건", len(listings))

    places = []
    for i, item in enumerate(listings, 1):
        cid, ctype = str(item["contentid"]), str(item["contenttypeid"])
        common = cache.get_or_fetch("detailCommon2", cid, lambda: client.detail("detailCommon2", cid))
        intro = cache.get_or_fetch("detailIntro2", cid, lambda: client.detail("detailIntro2", cid, contentTypeId=ctype))
        images = cache.get_or_fetch("detailImage2", cid, lambda: client.detail("detailImage2", cid, imageYN="Y"))
        infos = cache.get_or_fetch("detailInfo2", cid, lambda: client.detail("detailInfo2", cid, contentTypeId=ctype))
        places.append(build_place(item, common[0] if common else None, intro[0] if intro else None, images, infos, names))
        if i % 25 == 0:
            log.info("상세 %d/%d (이번 실행 API 호출 %d회)", i, len(listings), client.calls)

    fill_missing_areas(places)

    stories = []
    try:
        themes = cache.get_or_fetch("odii", f"themes_{lat}_{lng}_{radius_m}", lambda: list(client.odii_themes_near(lat, lng, radius_m)))
        raw_stories = cache.get_or_fetch("odii", f"stories_{lat}_{lng}_{radius_m}", lambda: list(client.odii_stories_near(lat, lng, radius_m)))
        theme_titles = {str(t.get("tid")): t.get("title", "") for t in themes}
        for s in raw_stories:
            story = build_story(s, theme_titles)
            story["place_id"] = match_story_place(story, places)
            stories.append(story)
    except DataGoKrError as e:
        log.warning("오디오 가이드(Odii) 수집 실패 — 활용신청 여부 확인: %s", e)

    for p in places:
        if any(s["place_id"] == p["id"] for s in stories):
            p["tags"].append("오디오가이드")
    return {"categories": categories, "places": places, "stories": stories}


def save_snapshot(data: dict[str, list]) -> None:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    for name, rows in data.items():
        rows = sorted(rows, key=lambda r: r.get("id") or r.get("code"))
        (SNAPSHOT_DIR / f"{name}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


def load_snapshot() -> dict[str, list]:
    return {n: json.loads((SNAPSHOT_DIR / f"{n}.json").read_text(encoding="utf-8")) for n in ("categories", "places", "stories")}


def _upsert(session, model, rows: list[dict], key: str) -> None:
    if not rows:
        return
    stmt = insert(model).values(rows)
    cols = {c: stmt.excluded[c] for c in rows[0] if c != key}
    session.execute(stmt.on_conflict_do_update(index_elements=[key], set_=cols))


def write_db(data: dict[str, list]) -> None:
    """places 테이블을 새로 만들고 적재. 전부 스냅샷에서 재생성 가능한 데이터라 스키마 변경 시에도 안전."""
    tables = [PlaceChunk.__table__, PlaceStory.__table__, Place.__table__, PlaceCategory.__table__]
    Base.metadata.drop_all(engine, tables=tables)
    init_db()
    for p in data["places"]:
        p["search_compact"] = build_search_compact(p)  # 예전 스냅샷 호환
    with SessionLocal.begin() as s:
        _upsert(s, PlaceCategory, data["categories"], "code")
        _upsert(s, Place, data["places"], "id")
        _upsert(s, PlaceStory, data["stories"], "id")
    log.info("DB 적재: 장소 %d, 이야기 %d, 분류 %d", len(data["places"]), len(data["stories"]), len(data["categories"]))


def embed_db() -> None:
    """DB 의 장소·이야기를 조각내어 임베딩 (처음 실행 시 모델 다운로드 약 1.1GB)."""
    from app.places.embedding import MODEL_NAME, encode_passages

    with SessionLocal() as s:
        places = [{c.name: getattr(p, c.name) for c in Place.__table__.columns} for p in s.scalars(select(Place))]
        stories = [{c.name: getattr(t, c.name) for c in PlaceStory.__table__.columns} for t in s.scalars(select(PlaceStory))]
    chunks = [c for p in places for c in place_chunks(p)] + [c for t in stories for c in story_chunks(t)]
    log.info("임베딩 %d조각 (%s) ...", len(chunks), MODEL_NAME)
    vectors = encode_passages([c["text"] for c in chunks])
    with SessionLocal.begin() as s:
        s.execute(PlaceChunk.__table__.delete())
        s.execute(insert(PlaceChunk), [{**c, "embedding": v} for c, v in zip(chunks, vectors)])
    log.info("임베딩 완료: %d조각", len(chunks))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # 요청 URL 에 서비스키가 포함되므로 로그 금지
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["fetch", "load", "embed"])
    ap.add_argument("--embed", action="store_true", help="fetch/load 후 임베딩까지 생성")
    ap.add_argument("--radius", type=int, default=ODII_RADIUS_M, help="오디오 가이드 수집 반경(m)")
    ap.add_argument("--lat", type=float, default=GURYONGPO_CENTER[0])
    ap.add_argument("--lng", type=float, default=GURYONGPO_CENTER[1])
    ap.add_argument("--refresh", action="store_true", help="원본 캐시를 무시하고 다시 호출")
    args = ap.parse_args()

    if args.command == "embed":
        embed_db()
        return
    if args.command == "load":
        write_db(load_snapshot())
        if args.embed:
            embed_db()
        return

    client = TourApiClient(settings.data_go_kr_service_key, settings.mobile_app)
    try:
        data = fetch_all(client, RawCache(RAW_DIR, args.refresh), args.lat, args.lng, args.radius)
    except DataGoKrError as e:
        log.error("API 오류로 중단: %s (캐시된 부분은 유지되므로 다시 실행하면 이어서 진행)", e)
        raise SystemExit(1)
    finally:
        log.info("이번 실행 API 호출 %d회", client.calls)
        client.close()
    save_snapshot(data)
    write_db(data)
    if args.embed:
        embed_db()


if __name__ == "__main__":
    main()
