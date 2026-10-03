# places — 관광지 정보 API

구룡포 일대 관광 자원을 한국관광공사 OpenAPI에서 수집해 DB화하고, 앱·Agent·Planner가 검색할 수 있는 API로 제공한다.

## 데이터 소스

| 소스 | 서비스 (data.go.kr) | 사용하는 오퍼레이션 | 용도 |
|---|---|---|---|
| 한국관광공사 국문 관광정보 | [KorService2 (15101578)](https://www.data.go.kr/data/15101578/openapi.do) | `areaBasedList2`, `detailCommon2`, `detailIntro2`, `detailImage2`, `detailInfo2`, `lclsSystmCode2` | 장소 목록·개요·운영시간·휴무·메뉴·이미지·분류 |
| 한국관광공사 오디오 가이드 Odii (콘텐츠랩 api.visitkorea.or.kr 목록) | [Odii (15101971)](https://www.data.go.kr/data/15101971/openapi.do) | `themeLocationBasedList`, `storyLocationBasedList` | 현장 해설용 대본·음성 (Docent RAG 근거 자료) |

- 두 서비스 모두 **data.go.kr 인증키 하나**로 호출하지만 **서비스별로 활용신청**이 필요하다. 개발계정은 자동 승인되고, 오퍼레이션별 하루 1,000건까지 호출할 수 있다.
- 수집 범위
  - 장소: **포항시 전체(남구+북구, `areaBasedList2`)**. KTO 데이터는 구룡포 반경 12km 안에 39건뿐(2026-10 기준)이라 넓게 수집하고, 구룡포 집중은 `area=구룡포읍` 필터나 위치 반경 검색으로 한다.
  - 오디오 가이드: 구룡포항(35.9890, 129.5560)에서 반경 20km(API 최대값).
- 매뉴얼: `docs/개방데이터_활용매뉴얼(국문)/`
- 콘텐츠랩의 다른 API 중 나중에 붙일 만한 것: 관광지별 연관 관광지(15128560 계열), 관광지 집중률 방문자 추이 예측, 무장애 여행 정보(15101897)

## 실행

```bash
cp backend/.env.example backend/.env       # DATA_GO_KR_SERVICE_KEY 입력 (load 만 쓸 거면 불필요)
docker compose up -d --build
docker compose run --rm api python -m app.places.ingest load    # 커밋된 스냅샷 -> DB (키 불필요)
docker compose run --rm api python -m app.places.ingest embed   # 의미 검색용 임베딩 생성 (약 3분, 최초 1회 모델 1.1GB 다운로드)
docker compose run --rm api python -m app.places.ingest fetch   # API 재수집 -> DB + 스냅샷 갱신 (키 필요)
docker compose run --rm api pytest tests/places
```

- `load`/`fetch`는 places 테이블을 **새로 만든다**. 스키마가 바뀌어도 이 명령만 다시 실행하면 된다. 임베딩도 지워지므로 필요하면 `--embed`를 붙이거나 `embed`를 다시 실행한다.
- 원본 응답은 `data/places/raw/`, 임베딩 모델은 `data/places/models/`에 캐시한다. 둘 다 커밋하지 않는다.
- `data/places/snapshot/*.json`은 정규화된 결과이며 커밋한다.

## API

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/places` | 장소 검색 (아래 참고) |
| GET | `/places/passages?q=` | **RAG용 근거 조각** 검색. 소개문·오디오 가이드 대본 조각을 의미 유사도 순으로 반환. `place_id`, `kind`(place/story), `lat`+`lng`+`radius_m`로 범위를 좁힐 수 있음 |
| GET | `/places/tags` | 태그 목록과 개수 |
| GET | `/places/{place_id}` | 상세 (운영시간 `open_hours`, 휴무 `rest_date`, 메뉴, 이미지, 반복정보, 원본 `intro`) |
| GET | `/places/{place_id}/stories` | 해당 장소의 오디오 가이드 이야기 |
| GET | `/places/stories/near?lat=&lng=&radius_m=` | 현재 위치 주변 이야기 |

### `GET /places` 검색

필터는 모든 모드에 공통으로 적용된다: `tags`(반복, 모두 포함), `content_type`, `area`, `lat`+`lng`(+`radius_m`), `limit`, `offset`.

| `mode` | 동작 | 예 |
|---|---|---|
| `keyword` (기본) | 여러 단어는 모두 포함(AND). 띄어쓰기 무시, 끝 조사 제거, 일부 동의어(맛집→음식점·식당, 숙소→숙박·펜션 …). 이름·분류·태그·주소·메뉴·개요·반복정보 대상. 제목 일치 > 가까운 순 > 유사도 순 | `구룡포 맛집` 16건, `일본인가옥` → 구룡포 일본인 가옥거리 |
| `semantic` | 질의 문장과 의미가 가까운 순. 장소별 소개문·오디오 가이드 대본 조각 중 가장 가까운 것 기준. `score` = 코사인 유사도 | `일제강점기 근대 역사를 느낄 수 있는 곳` → 일본인 가옥거리, 근대문화역사관 |
| `hybrid` | keyword 순위와 semantic 순위를 RRF로 결합. 정확한 이름도 맞히고 뜻도 반영 | `과메기` → 과메기 음식점, 과메기문화관, 구룡포항 |

- semantic의 `score`는 e5 모델 특성상 0.80~0.85 사이에 몰려 있다. 절대값보다 **순위**로 쓴다.
- `place_id` 형식은 `kto-{contentid}`이다. **다른 모듈은 이 ID로만 장소를 참조한다.**

## 임베딩 (로컬)

- 모델은 `intfloat/multilingual-e5-base`(768차원, CPU)를 쓴다. 장소 소개문은 머리말(이름·분류·읍면)을 붙여 500자 단위로, 오디오 가이드 대본은 100자씩 겹쳐 500자 단위로 자른다. 현재 430조각이다.
- 실측 값 (Docker Desktop, 12코어)

| 항목 | 값 |
|---|---|
| 모델 파일 | 1.1GB (`data/places/models/`) |
| API 서버 메모리 | 모델 로드 전 약 120MB → **로드 후 약 740MB** |
| `ingest embed` 최대 메모리 / 소요 | 1.15GB / 약 3분 |
| 의미 검색 응답 | 첫 호출 12~25초(모델 로드), 이후 **약 50ms** |
| Docker 이미지 | 약 300MB → 약 2GB (CPU torch 포함) |

- 모델은 semantic/hybrid/passages를 **처음 호출할 때만** 로드한다. keyword 검색만 쓰는 컨테이너는 메모리를 더 쓰지 않는다.
- Docker Desktop 메모리 한도가 약 3.8GB다. 여러 worktree 스택에서 동시에 의미 검색을 쓰면 각자 약 740MB씩 더 쓴다.

## 아직 안 한 것

- 운영시간·휴무는 원문 텍스트다. Planner의 결정론적 검증에 쓰려면 구조화(요일·시간 파싱)가 필요하다.
- 전화번호가 `intro`의 문의처 필드에, 요금이 `extra_info`에 흩어져 있다. `tel`/`fee`로 정리해야 한다.
- 음식점은 KTO 데이터에 없는 곳이 많다. Kakao 장소 검색으로 보완할지 검토한다.
