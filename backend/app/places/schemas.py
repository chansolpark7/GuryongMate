"""places 모듈의 공개 응답 스키마. 다른 모듈(planner 등)은 이 형태로만 장소 정보를 사용한다."""
from pydantic import BaseModel, ConfigDict, Field


class PlaceSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    content_type: str
    category_names: list[str]
    tags: list[str]
    lat: float
    lng: float
    addr: str
    area: str
    thumbnail_url: str
    distance_m: float | None = None
    score: float | None = Field(None, description="semantic: 코사인 유사도, hybrid: 순위 결합 점수")


class PlaceImage(BaseModel):
    url: str
    thumbnail: str = ""
    name: str = ""
    copyright_type: str = ""


class ExtraInfo(BaseModel):
    name: str
    text: str


class PlaceDetail(PlaceSummary):
    overview: str
    tel: str
    homepage: str
    open_hours: str
    rest_date: str
    parking: str
    fee: str
    menu: str
    image_url: str
    images: list[PlaceImage]
    copyright_type: str
    extra_info: list[ExtraInfo]
    intro: dict[str, str]
    story_count: int = 0
    source: str
    source_modified_at: str


class PlaceSearchResult(BaseModel):
    total: int
    items: list[PlaceSummary]


class Story(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    place_id: str | None
    title: str
    odii_theme_title: str
    script: str
    play_time_sec: int
    audio_url: str
    image_url: str
    lat: float
    lng: float
    distance_m: float | None = None


class TagCount(BaseModel):
    tag: str
    count: int


class Passage(BaseModel):
    """RAG 근거 조각. title 은 출처(장소명 또는 '관광지 - 이야기 제목')."""

    id: int
    kind: str  # place | story
    place_id: str | None
    story_id: str | None
    title: str
    text: str
    score: float
    distance_m: float | None = None
    source: str = Field("kto", description="kto 한국관광공사(소개문·오디오 가이드) | manual 직접 정의한 장소의 소개문 (data/places/manual)")
