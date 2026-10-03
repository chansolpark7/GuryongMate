from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.places.embedding import DIM


class Place(Base):
    """관광 자원 1건. id 는 다른 모듈이 참조하는 공개 식별자 (예: 'kto-126128')."""

    __tablename__ = "place"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    source: Mapped[str] = mapped_column(String(20))  # kto
    content_id: Mapped[str] = mapped_column(String(20), index=True)
    content_type_id: Mapped[str] = mapped_column(String(4), index=True)
    content_type: Mapped[str] = mapped_column(String(20))  # 관광지, 음식점 ...
    title: Mapped[str] = mapped_column(String(200))

    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    addr: Mapped[str] = mapped_column(String(300), default="")
    area: Mapped[str] = mapped_column(String(40), default="", index=True)  # 구룡포읍, 호미곶면 ...
    tel: Mapped[str] = mapped_column(String(100), default="")
    homepage: Mapped[str] = mapped_column(Text, default="")

    overview: Mapped[str] = mapped_column(Text, default="")
    open_hours: Mapped[str] = mapped_column(Text, default="")
    rest_date: Mapped[str] = mapped_column(Text, default="")
    parking: Mapped[str] = mapped_column(Text, default="")
    fee: Mapped[str] = mapped_column(Text, default="")
    menu: Mapped[str] = mapped_column(Text, default="")  # 음식점 대표/취급 메뉴

    category_codes: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    category_names: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    # 키워드 검색용: 이름·분류·태그·주소·메뉴·개요·반복정보를 소문자+공백 제거해 이어붙인 문자열
    search_compact: Mapped[str] = mapped_column(Text, default="")

    image_url: Mapped[str] = mapped_column(Text, default="")
    thumbnail_url: Mapped[str] = mapped_column(Text, default="")
    images: Mapped[list[dict]] = mapped_column(JSONB, default=list)
    copyright_type: Mapped[str] = mapped_column(String(10), default="")  # Type1 / Type3 (공공누리)

    intro: Mapped[dict] = mapped_column(JSONB, default=dict)  # detailIntro2 원본 (타입별 필드 상이)
    extra_info: Mapped[list[dict]] = mapped_column(JSONB, default=list)  # detailInfo2 반복정보

    source_modified_at: Mapped[str] = mapped_column(String(14), default="")
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_place_tags", "tags", postgresql_using="gin"),
        Index("ix_place_title_trgm", "title", postgresql_using="gin", postgresql_ops={"title": "gin_trgm_ops"}),
    )


class PlaceStory(Base):
    """오디(Odii) 오디오 가이드 이야기. 현장 해설(Docent)의 근거 자료."""

    __tablename__ = "place_story"

    id: Mapped[str] = mapped_column(String(60), primary_key=True)  # odii-{stid}-{stlid}
    place_id: Mapped[str | None] = mapped_column(ForeignKey("place.id", ondelete="SET NULL"), index=True)
    odii_theme_id: Mapped[str] = mapped_column(String(20), default="")
    odii_theme_title: Mapped[str] = mapped_column(String(200), default="")
    title: Mapped[str] = mapped_column(String(200))
    script: Mapped[str] = mapped_column(Text, default="")
    play_time_sec: Mapped[int] = mapped_column(Integer, default=0)
    audio_url: Mapped[str] = mapped_column(Text, default="")
    image_url: Mapped[str] = mapped_column(Text, default="")
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    lang: Mapped[str] = mapped_column(String(5), default="ko")


class PlaceCategory(Base):
    """한국관광공사 신분류체계 코드 (lclsSystm 1~3)."""

    __tablename__ = "place_category"

    code: Mapped[str] = mapped_column(String(12), primary_key=True)
    name: Mapped[str] = mapped_column(String(60))
    depth: Mapped[int] = mapped_column(Integer)
    parent_code: Mapped[str] = mapped_column(String(12), default="")


class PlaceChunk(Base):
    """임베딩 검색 단위. 장소 소개 조각(kind=place) 또는 오디오 가이드 대본 조각(kind=story)."""

    __tablename__ = "place_chunk"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(10))
    place_id: Mapped[str | None] = mapped_column(ForeignKey("place.id", ondelete="CASCADE"), index=True)
    story_id: Mapped[str | None] = mapped_column(ForeignKey("place_story.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(200))  # 출처 표시용 (장소명 또는 이야기 제목)
    text: Mapped[str] = mapped_column(Text)
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    embedding: Mapped[list[float]] = mapped_column(Vector(DIM))
