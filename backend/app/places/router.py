from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import ColumnElement, Float, cast, func, literal, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.places.models import Place, PlaceStory
from app.places.schemas import PlaceDetail, PlaceSearchResult, PlaceSummary, Story, TagCount

router = APIRouter(prefix="/places", tags=["places"])

DbSession = Annotated[Session, Depends(get_db)]


def _distance_m(lat_col, lng_col, lat: float, lng: float) -> ColumnElement[float]:
    """하버사인 거리(m). 장소 수가 수백 건 수준이라 PostGIS 없이 계산."""
    dlat = func.radians(lat_col - lat)
    dlng = func.radians(lng_col - lng)
    a = func.power(func.sin(dlat / 2), 2) + func.cos(func.radians(literal(lat))) * func.cos(
        func.radians(lat_col)
    ) * func.power(func.sin(dlng / 2), 2)
    return cast(2 * 6_371_000 * func.asin(func.sqrt(a)), Float)


@router.get("", response_model=PlaceSearchResult)
def search_places(
    db: DbSession,
    q: Annotated[str | None, Query(description="키워드 (이름·개요·주소·태그·메뉴)")] = None,
    tags: Annotated[list[str] | None, Query(description="모두 포함해야 하는 태그 (반복 지정 가능)")] = None,
    content_type: Annotated[str | None, Query(description="관광지, 음식점, 숙박, 문화시설 ...")] = None,
    area: Annotated[str | None, Query(description="구룡포읍, 호미곶면 ...")] = None,
    lat: float | None = None,
    lng: float | None = None,
    radius_m: Annotated[float | None, Query(gt=0)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PlaceSearchResult:
    conds = []
    if q:
        like = f"%{q.strip()}%"
        conds.append(
            or_(
                Place.title.ilike(like),
                Place.overview.ilike(like),
                Place.addr.ilike(like),
                Place.menu.ilike(like),
                func.array_to_string(Place.tags, " ").ilike(like),
            )
        )
    if tags:
        conds.append(Place.tags.contains(tags))
    if content_type:
        conds.append(Place.content_type == content_type)
    if area:
        conds.append(Place.area == area)

    has_point = lat is not None and lng is not None
    if (lat is None) != (lng is None):
        raise HTTPException(422, "lat 과 lng 는 함께 지정해야 합니다.")
    if radius_m is not None and not has_point:
        raise HTTPException(422, "radius_m 는 lat/lng 와 함께 지정해야 합니다.")
    dist = _distance_m(Place.lat, Place.lng, lat, lng).label("distance_m") if has_point else literal(None).label("distance_m")
    if radius_m is not None:
        conds.append(_distance_m(Place.lat, Place.lng, lat, lng) <= radius_m)

    total = db.scalar(select(func.count()).select_from(Place).where(*conds)) or 0

    # 정렬: 제목에 키워드 포함 > (위치 지정 시) 가까운 순 > 제목 유사도 > 이름순
    order = []
    if q:
        order.append(Place.title.ilike(f"%{q.strip()}%").desc())
    if has_point:
        order.append(dist.asc())
    if q:
        order.append(func.similarity(Place.title, q).desc())
    order.append(Place.title)

    rows = db.execute(select(Place, dist).where(*conds).order_by(*order).limit(limit).offset(offset)).all()
    items = [PlaceSummary.model_validate(p).model_copy(update={"distance_m": d}) for p, d in rows]
    return PlaceSearchResult(total=total, items=items)


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
