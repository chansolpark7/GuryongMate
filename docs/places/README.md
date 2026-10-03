# places — 관광지 정보 API

구룡포 일대 관광 자원을 한국관광공사 OpenAPI에서 수집해 DB화하고, 앱·Agent·Planner가 검색할 수 있는 API로 제공한다.

## 데이터 소스

| 소스 | 서비스 (data.go.kr) | 사용하는 오퍼레이션 | 용도 |
|---|---|---|---|
| 한국관광공사 국문 관광정보 | [KorService2 (15101578)](https://www.data.go.kr/data/15101578/openapi.do) | `locationBasedList2`, `detailCommon2`, `detailIntro2`, `detailImage2`, `detailInfo2`, `lclsSystmCode2` | 장소 목록·개요·운영시간·휴무·메뉴·이미지·분류 |
| 한국관광공사 오디오 가이드 Odii (콘텐츠랩 api.visitkorea.or.kr 목록) | [Odii (15101971)](https://www.data.go.kr/data/15101971/openapi.do) | `themeLocationBasedList`, `storyLocationBasedList` | 현장 해설용 대본·음성 (Docent RAG 근거 자료) |

- 두 서비스 모두 **data.go.kr 인증키 하나**로 호출하지만 **서비스별로 활용신청**이 필요하다. 개발계정은 자동 승인되고, 오퍼레이션별 하루 1,000건까지 호출할 수 있다.
- 수집 범위는 구룡포항(35.9890, 129.5560)에서 반경 12km이다. 구룡포읍 전체와 호미곶까지 들어간다.
- 매뉴얼: `docs/개방데이터_활용매뉴얼(국문)/`
- 콘텐츠랩의 다른 API 중 나중에 붙일 만한 것: 관광지별 연관 관광지(15128560 계열), 관광지 집중률 방문자 추이 예측, 무장애 여행 정보(15101897)

## 실행

```bash
cp backend/.env.example backend/.env       # DATA_GO_KR_SERVICE_KEY 입력
docker compose up -d --build
docker compose run --rm api python -m app.places.ingest fetch   # API 수집 -> DB + data/places/snapshot/ 갱신
docker compose run --rm api python -m app.places.ingest load    # 키 없이 커밋된 스냅샷으로 DB 채우기
docker compose run --rm api pytest tests/places
```

- 원본 응답은 `data/places/raw/`에 캐시한다(커밋 안 됨). 호출 한도에 걸려 중단돼도 다시 실행하면 이어서 진행된다. 캐시를 무시하려면 `--refresh`를 붙인다.
- `data/places/snapshot/*.json`은 정규화된 결과이며 커밋한다. 키가 없는 팀원이나 다른 모듈은 `load`로 바로 DB를 채울 수 있다.

## API

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/places` | 검색. `q`(키워드: 이름·개요·주소·태그·메뉴), `tags`(반복, 모두 포함), `content_type`, `area`, `lat`+`lng`(+`radius_m`), `limit`, `offset` |
| GET | `/places/tags` | 태그 목록과 개수 |
| GET | `/places/{place_id}` | 상세 (운영시간 `open_hours`, 휴무 `rest_date`, 메뉴, 이미지, 반복정보, 원본 `intro`) |
| GET | `/places/{place_id}/stories` | 해당 장소의 오디오 가이드 이야기 |
| GET | `/places/stories/near?lat=&lng=&radius_m=` | 현재 위치 주변 이야기 (현장 해설) |

- `place_id` 형식은 `kto-{contentid}`이다. **다른 모듈은 이 ID로만 장소를 참조한다.**
- 정렬은 제목 키워드 일치 → (위치를 줬으면) 가까운 순 → 제목 유사도(pg_trgm) → 이름순이다.
- 태그는 콘텐츠 타입(관광지/음식점…), 신분류체계 대·중·소 이름, 읍면(구룡포읍/호미곶면…), `오디오가이드`, `반려동물동반`으로 구성한다.

## 아직 안 한 것

- **임베딩 검색**: DB 이미지에 pgvector가 들어 있다. LLM/임베딩 모델이 정해지면 `place` 테이블에 embedding 컬럼을 추가한다.
- 운영시간·휴무는 원문 텍스트다. Planner의 결정론적 검증에 쓰려면 구조화(요일·시간 파싱)가 필요하다.
- 음식점은 KTO 데이터에 없는 곳이 많을 수 있다. Kakao 장소 검색으로 보완할지 검토한다.
