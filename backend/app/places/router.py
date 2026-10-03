from enum import Enum
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import ColumnElement, Float, case, cast, func, literal, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.places.embedding import EmbeddingUnavailable, encode_query
from app.places.models import Place, PlaceChunk, PlaceStory
from app.places.normalize import query_terms
from app.places.schemas import Passage, PlaceDetail, PlaceSearchResult, PlaceSummary, Story, TagCount

router = APIRouter(prefix="/places", tags=["places"])

DbSession = Annotated[Session, Depends(get_db)]

RRF_K = 60  # hybrid 순위 결합(Reciprocal Rank Fusion) 상수


class SearchMode(str, Enum):
    keyword = "keyword"
    semantic = "semantic"
    hybrid = "hybrid"


def _distance_m(lat_col, lng_col, lat: float, lng: float) -> ColumnElement[float]:
    """하버사인 거리(m). 장소 수가 수백 건 수준이라 PostGIS 없이 계산."""
    dlat = func.radians(lat_col - lat)
    dlng = func.radians(lng_col - lng)
    a = func.power(func.sin(dlat / 2), 2) + func.cos(func.radians(literal(lat))) * func.cos(
        func.radians(lat_col)
    ) * func.power(func.sin(dlng / 2), 2)
    return cast(2 * 6_371_000 * func.asin(func.sqrt(a)), Float)


def _check_point(lat: float | None, lng: float | None, radius_m: float | None) -> bool:
    if (lat is None) != (lng is None):
        raise HTTPException(422, "lat 과 lng 는 함께 지정해야 합니다.")
    if radius_m is not None and lat is None:
        raise HTTPException(422, "radius_m 는 lat/lng 와 함께 지정해야 합니다.")
    return lat is not None


def _require_embeddings(db: Session) -> None:
    if not db.scalar(select(PlaceChunk.id).limit(1)):
        raise HTTPException(503, "임베딩이 없습니다. `python -m app.places.ingest embed` 를 먼저 실행하세요.")


def _query_vector(q: str) -> list[float]:
    try:
        return encode_query(q)
    except EmbeddingUnavailable as e:
        raise HTTPException(503, str(e)) from e


def _keyword_ranked(db: Session, q: str, conds: list, dist) -> list[str]:
    """모든 단어가 (동의어·조사 제거 포함) 어딘가에 들어 있는 장소. 제목에 많이 맞을수록 앞."""
    terms = query_terms(q)
    if not terms:
        return []
    title_compact = func.regexp_replace(func.lower(Place.title), r"\s+", "", "g")
    match = [or_(*(Place.search_compact.contains(v, autoescape=True) for v in t)) for t in terms]
    title_hits = sum(
        case((or_(*(title_compact.contains(v, autoescape=True) for v in t)), 1), else_=0) for t in terms
    )
    order = [title_hits.desc()]
    if dist is not None:
        order.append(dist.asc())
    order += [func.similarity(Place.title, q).desc(), Place.title]
    return list(db.scalars(select(Place.id).where(*conds, *match).order_by(*order)))


def _semantic_ranked(db: Session, q: str, conds: list) -> list[tuple[str, float]]:
    """장소별로 가장 가까운 조각(소개문·오디오 가이드 대본)의 코사인 유사도 순."""
    _require_embeddings(db)
    d = PlaceChunk.embedding.cosine_distance(_query_vector(q))
    best = (
        select(PlaceChunk.place_id.label("pid"), func.min(d).label("d"))
        .where(PlaceChunk.place_id.is_not(None))
        .group_by(PlaceChunk.place_id)
        .subquery()
    )
    rows = db.execute(select(Place.id, best.c.d).join(best, best.c.pid == Place.id).where(*conds).order_by(best.c.d))
    return [(pid, 1 - float(dist)) for pid, dist in rows]


@router.get("", response_model=PlaceSearchResult)
def search_places(
    db: DbSession,
    q: Annotated[str | None, Query(description="검색어. 여러 단어는 모두 포함(AND), 띄어쓰기·조사·일부 동의어 무시")] = None,
    mode: Annotated[
        SearchMode, Query(description="keyword: 글자 일치 / semantic: 의미 유사도 / hybrid: 둘을 순위 결합")
    ] = SearchMode.keyword,
    tags: Annotated[list[str] | None, Query(description="모두 포함해야 하는 태그 (반복 지정 가능)")] = None,
    content_type: Annotated[str | None, Query(description="관광지, 음식점, 숙박, 문화시설 ...")] = None,
    area: Annotated[str | None, Query(description="구룡포읍, 호미곶면 ...")] = None,
    lat: float | None = None,
    lng: float | None = None,
    radius_m: Annotated[float | None, Query(gt=0)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PlaceSearchResult:
    has_point = _check_point(lat, lng, radius_m)
    q = (q or "").strip()
    if mode != SearchMode.keyword and not q:
        raise HTTPException(422, f"mode={mode.value} 는 q 가 필요합니다.")

    conds = []
    if tags:
        conds.append(Place.tags.contains(tags))
    if content_type:
        conds.append(Place.content_type == content_type)
    if area:
        conds.append(Place.area == area)
    dist = _distance_m(Place.lat, Place.lng, lat, lng) if has_point else None
    if radius_m is not None:
        conds.append(dist <= radius_m)

    scores: dict[str, float] = {}
    if not q:  # 필터만: 가까운 순 또는 이름순
        order = [dist.asc()] if dist is not None else []
        ranked = list(db.scalars(select(Place.id).where(*conds).order_by(*order, Place.title)))
    elif mode == SearchMode.keyword:
        ranked = _keyword_ranked(db, q, conds, dist)
    elif mode == SearchMode.semantic:
        sem = _semantic_ranked(db, q, conds)
        ranked = [pid for pid, _ in sem]
        scores = dict(sem)
    else:
        kw = _keyword_ranked(db, q, conds, dist)
        sem = _semantic_ranked(db, q, conds)
        for rank, pid in enumerate(kw):
            scores[pid] = scores.get(pid, 0) + 1 / (RRF_K + rank + 1)
        for rank, (pid, _) in enumerate(sem):
            scores[pid] = scores.get(pid, 0) + 1 / (RRF_K + rank + 1)
        ranked = sorted(scores, key=lambda pid: -scores[pid])

    page = ranked[offset : offset + limit]
    dist_col = dist.label("d") if dist is not None else literal(None).label("d")
    found = {p.id: (p, d) for p, d in db.execute(select(Place, dist_col).where(Place.id.in_(page)))}
    items = [
        PlaceSummary.model_validate(found[pid][0]).model_copy(
            update={"distance_m": found[pid][1], "score": round(scores[pid], 4) if pid in scores else None}
        )
        for pid in page
    ]
    return PlaceSearchResult(total=len(ranked), items=items)


@router.get("/passages", response_model=list[Passage])
def search_passages(
    db: DbSession,
    q: Annotated[str, Query(min_length=1, description="질문이나 관심사 (예: '이 거리가 왜 보존됐어?')")],
    place_id: str | None = None,
    kind: Annotated[str | None, Query(pattern="^(place|story)$", description="place: 소개문, story: 오디오 가이드 대본")] = None,
    lat: float | None = None,
    lng: float | None = None,
    radius_m: Annotated[float | None, Query(gt=0)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 8,
) -> list[Passage]:
    """현장 해설·질의응답의 근거가 될 원문 조각을 의미 유사도 순으로 반환 (RAG 용)."""
    has_point = _check_point(lat, lng, radius_m)
    _require_embeddings(db)
    sim = (1 - PlaceChunk.embedding.cosine_distance(_query_vector(q))).label("score")
    dist = _distance_m(PlaceChunk.lat, PlaceChunk.lng, lat, lng) if has_point else None
    conds = []
    if place_id:
        conds.append(PlaceChunk.place_id == place_id)
    if kind:
        conds.append(PlaceChunk.kind == kind)
    if radius_m is not None:
        conds.append(dist <= radius_m)
    dist_col = dist.label("d") if dist is not None else literal(None).label("d")
    rows = db.execute(select(PlaceChunk, sim, dist_col).where(*conds).order_by(sim.desc()).limit(limit)).all()
    return [
        Passage(id=c.id, kind=c.kind, place_id=c.place_id, story_id=c.story_id, title=c.title, text=c.text,
                score=round(float(s), 4), distance_m=d)
        for c, s, d in rows
    ]


@router.get("/tags", response_model=list[TagCount])
def list_tags(db: DbSession) -> list[TagCount]:
    tag = func.unnest(Place.tags).label("tag")
    sub = select(tag).subquery()
    rows = db.execute(select(sub.c.tag, func.count()).group_by(sub.c.tag).order_by(func.count().desc(), sub.c.tag)).all()
    return [TagCount(tag=t, count=c) for t, c in rows]


@router.get("/stories/near", response_model=list[Story])
def stories_near(
    db: DbSession,
    lat: float,
    lng: float,
    radius_m: Annotated[float, Query(gt=0, le=20_000)] = 300,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[Story]:
    """현재 위치 주변의 오디오 가이드 이야기 (현장 해설용)."""
    dist = _distance_m(PlaceStory.lat, PlaceStory.lng, lat, lng)
    rows = db.execute(
        select(PlaceStory, dist.label("d")).where(dist <= radius_m).order_by(dist).limit(limit)
    ).all()
    return [Story.model_validate(s).model_copy(update={"distance_m": d}) for s, d in rows]


@router.get("/{place_id}", response_model=PlaceDetail)
def get_place(place_id: str, db: DbSession) -> PlaceDetail:
    place = db.get(Place, place_id)
    if place is None:
        raise HTTPException(404, f"장소 '{place_id}' 를 찾을 수 없습니다.")
    count = db.scalar(select(func.count()).select_from(PlaceStory).where(PlaceStory.place_id == place_id)) or 0
    detail = PlaceDetail.model_validate(place, from_attributes=True)
    return detail.model_copy(update={"story_count": count, "intro": {k: str(v) for k, v in place.intro.items()}})


@router.get("/{place_id}/stories", response_model=list[Story])
def get_place_stories(place_id: str, db: DbSession) -> list[Story]:
    if db.get(Place, place_id) is None:
        raise HTTPException(404, f"장소 '{place_id}' 를 찾을 수 없습니다.")
    rows = db.scalars(select(PlaceStory).where(PlaceStory.place_id == place_id).order_by(PlaceStory.id)).all()
    return [Story.model_validate(s) for s in rows]
