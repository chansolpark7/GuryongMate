# GuryongMate

**포항 구룡포 여행 동행 AI Agent** — 2026 디지털 트윈 AI Agent 해커톤 출품작.

여행 전에는 사용자의 취향·조건에 맞춰 일정을 함께 만들고, 여행 중에는 위치·일정·지역 정보와 **구룡포 디지털 트윈 안전 데이터(침수·방파제)**를 바탕으로 안내와 일정 변경을 제안한다. 모든 일정 변경은 Agent가 제안하고 **사용자가 승인해야** 반영된다.

- 기획·방향: [PROJECT.md](PROJECT.md)
- 개발 규칙(서비스 구성, 담당, 브랜치, 실행): [docs/COLLABORATION.md](docs/COLLABORATION.md)

---

## 서비스 구성

```
Flutter 앱 (예정) ──► gateway :8000 (Caddy)
                      ├─ /auth, /users ──► account   로그인·토큰
                      ├─ /places       ──► places    관광지 검색·상세·근거 검색
                      ├─ /planner      ──► planner   일정·검증·변경 제안·장소 추천
                      ├─ /safety       ──► safety    안전 정보 (디지털 트윈 + 기상·조위)
                      ├─ /routing      ──► routing   길찾기 (이동시간·경로선·위험 구역 우회)
                      └─ /agent        ──► agent     LLM 채팅 (SSE)·도구 실행
                     embedding    임베딩 모델 서버 (내부 전용)
                     valhalla     길찾기 엔진, OSM 포항 데이터 (내부 전용)
                     places-init  시작 시 장소 데이터·임베딩 자동 적재
                     db           PostgreSQL + pgvector
LLM: 호스트의 Ollama 또는 OpenAI API (Docker 밖)
```

- 백엔드는 FastAPI 프로젝트 하나(`backend/`)를 서비스마다 컨테이너로 나눠 띄운다(`SERVICE` 환경변수).
- 서비스끼리는 코드를 import하지 않고 **HTTP API(게이트웨이 경유)로만** 호출한다.
- 앱은 게이트웨이 주소 하나만 쓴다. 각 서비스의 Swagger 문서는 `http://localhost:8000/<서비스>/docs`에 있다.

## 개발된 기능

| 서비스 | 하는 일 | 주요 기능 | 상세 문서 |
|---|---|---|---|
| **places** | 관광 자원 DB·검색 | 한국관광공사 TourAPI로 포항시 관광지·음식점 등 324곳과 오디오 가이드 해설 37편을 수집·DB화 / 키워드 검색(여러 단어·띄어쓰기·조사·동의어), **의미 검색·하이브리드 검색**(로컬 임베딩), 위치 반경·태그·종류·읍면 필터 / 장소 상세(운영시간·휴무·메뉴·이미지) / 현장 해설용 **근거 조각 검색(RAG)** | [docs/places/README.md](docs/places/README.md) |
| **planner** | 여행 일정의 단일 원천 | 일정 생성·조회·편집 / 타임라인 계산과 **결정론적 위반 검사**(운영시간·휴무·종료 시각·이동시간·긴 도보·사용자 규칙) / **구간별 이동 수단**(도보·차량, 두 수단 시간 비교) / **변경 제안(Proposal)** 미리보기·승인·거절(버전 충돌 감지) / 장소 추천 카드 / **안전 점검**(방문과 이동 경로의 위험 3단계)과 상황이 바뀌면 남기는 **안전 경고** | [docs/planner/README.md](docs/planner/README.md) |
| **agent** | 대화형 여행 계획 Agent | 세션 기반 채팅(SSE 스트리밍) / LLM 도구 호출로 장소 검색·일정 변경 / **계획 → 검토 → 수정** 루프(일정 규칙 + 안전) / 안전 점검·날씨 전망, 장소 사이 도보·차량 시간과 "지금 출발해도 안전한지" / 결과를 **변경 제안·장소 추천 카드**로 전달 / 로컬 LLM(Ollama)·OpenAI 선택 | [docs/agent/README.md](docs/agent/README.md) |
| **safety** | 구룡포 안전 정보 | 디지털 트윈(E-UM) 수위·방파제 실측과 기상청 예보·특보, 조위 예보를 "이 시각, 이 장소(또는 이 경로)가 위험한가"로 해석 / 위험 3단계(안전·주의·위험)와 참고 정보 / 방문·이동 경로 평가(`/safety/assess`), 날짜별 전망, 현재 상태, 상황 변화 감지(`/safety/situation`) / 키 없이 쓰는 mock 모드와 **가상 상황**(`/safety/sim`) | [docs/safety/README.md](docs/safety/README.md) |
| **routing** | 길찾기 (외부 지도 API 없이) | OSM 포항 데이터로 직접 띄운 Valhalla로 **도보·차량** 이동시간 계산(planner 일정에 사용) / 경로선(GeoJSON)과 **한국어 회전 안내** / safety의 침수 센서 실측을 받아 **위험 구역을 피하는 경로**와 원래 경로 비교 / 엔진이 없으면 직선거리 근사로 대체 | [docs/routing/README.md](docs/routing/README.md) |
| **account** | 로그인·토큰 | Google 로그인, ID/비밀번호 가입·로그인 / JWT access(1시간)·refresh(30일, rotation) 발급 / 다른 서비스는 공용 코드(`app.core.auth`)로 토큰만 검증 | [docs/account/README.md](docs/account/README.md) |
| **embedding** | 임베딩 모델 서버 | `intfloat/multilingual-e5-base` (CPU, TEI 호환 `/embed` API) | [services/embedding/app.py](services/embedding/app.py), [docs/places/README.md](docs/places/README.md#임베딩-로컬-embedding-컨테이너) |
| **valhalla** | 길찾기 엔진 | Valhalla 3.9.0. 첫 실행 때 대한민국 OSM을 받아 포항 영역만 잘라 그래프 빌드 (약 3분, 이후 재사용) | [services/valhalla/](services/valhalla/), [docs/routing/README.md §4](docs/routing/README.md) |

### 외부 데이터 명세
| 문서 | 내용 |
|---|---|
| [docs/dt/DT_API.md](docs/dt/DT_API.md) | 구룡포 디지털 트윈(E-UM 포항) 침수·방파제 API 명세와 실제 호출 결과 |
| [docs/external/WEATHER_WAVE_API.md](docs/external/WEATHER_WAVE_API.md) | 기상청 단기예보·기상특보, 조위 등 보조 외부 API |
| [docs/개방데이터_활용매뉴얼(국문)/](docs/개방데이터_활용매뉴얼(국문)/) | 한국관광공사 TourAPI 매뉴얼 원본 |

### 아직 없는 것
- **Flutter 앱**(`app/`): 지도·플래너·채팅 화면
- **선제 개입 (Guide Agent)**: planner가 남기는 안전 경고(`needs_action`)를 받아 여행 중 먼저 알리고 대안을 제안하는 부분
- **대중교통 길찾기**: 도보·차량만 지원
- **여행 중 동행**: 현재 위치·지연 추적, 도착 시 현장 해설(음성), 데모 시뮬레이터 — 설계: [docs/trip/README.md](docs/trip/README.md)
- **사용자 맞춤 가이드**: 메모리 문서, 외부 검색으로 장소별 가이드 노트 준비 — 설계: [docs/docent/README.md](docs/docent/README.md)

진행 상황과 우선순위는 [PROJECT.md](PROJECT.md)의 MVP 우선순위를 따른다.

---

## 빠른 시작

필요한 것: Docker Desktop(메모리 4GB 이상 권장), Git. Agent 채팅을 쓰려면 Ollama(로컬 LLM) 또는 OpenAI API 키가 필요하다.

```bash
git clone https://github.com/chansolpark7/GuryongMate.git
cd GuryongMate
cp backend/.env.example backend/.env   # 필요한 키만 채운다 (아래 표)
docker compose up -d --build
curl http://localhost:8000/health
```

- 첫 실행에는 임베딩 모델 다운로드(약 1.1GB), 장소 데이터 적재, 길찾기용 OSM 다운로드(약 280MB)·그래프 빌드 때문에 몇 분 걸린다. 장소 데이터는 저장소의 스냅샷(`data/places/snapshot/`)에서 채워지므로 **관광 API 키가 없어도** 검색이 동작한다.
- 개발 중에는 `backend/.env`에 `AUTH_REQUIRED=false`를 두면 로그인 토큰 대신 `X-User-Id` 헤더로 API를 호출할 수 있다.
- 특정 서비스만 띄우기: `docker compose up -d places` (의존하는 db·embedding도 같이 뜬다)
- 전체 테스트: `docker compose run --rm places pytest tests`

### 키와 기능

| 변수 (`backend/.env`) | 없으면 | 있으면 |
|---|---|---|
| (없음) | 장소 검색·의미 검색, planner, routing(길찾기), account(ID/비밀번호) 동작 | — |
| `SAFETY_SOURCE=mock` | — | 키 없이 safety를 고정 데이터로 테스트 (`SAFETY_MOCK_SCENARIO=calm\|storm`) |
| `DATA_GO_KR_SERVICE_KEY` | 관광지 재수집, safety의 기상·조위 예보 불가 | [data.go.kr](https://www.data.go.kr)에서 서비스별 활용신청 후 발급 |
| `DT_SERVICE_KEY` | safety의 디지털 트윈 실측 불가 | [E-UM 디지털트윈 포항](https://pohang-eum.kr)에서 발급 |
| `AGENT_PROVIDER`, `AGENT_LOCAL_*` / `LLM_API_KEY` | Agent 채팅 불가 | 로컬 Ollama 또는 OpenAI ([docs/agent/README.md §7](docs/agent/README.md)) |
| `ACCOUNT_GOOGLE_CLIENT_IDS` | Google 로그인 불가 | Google OAuth 클라이언트 ID |

전체 변수와 설명은 [backend/.env.example](backend/.env.example)에 있다.

---

## 저장소 구조

```
GuryongMate/
├─ backend/                FastAPI (서비스별 컨테이너로 실행)
│  ├─ app/
│  │  ├─ core/             공용: 설정, 인증(JWT 검증)
│  │  ├─ places/  planner/  agent/  safety/  routing/  account/   서비스별 코드
│  │  └─ main.py, db.py    모듈 자동 등록, DB 연결
│  ├─ tests/<서비스>/      서비스별 테스트
│  └─ requirements/        공용·서비스별 의존성
├─ services/embedding/     임베딩 모델 서버 (별도 이미지)
├─ services/valhalla/      길찾기 엔진 (별도 이미지, OSM 포항 데이터)
├─ infra/gateway/          게이트웨이(Caddy) 라우팅
├─ data/places/            관광지 데이터 스냅샷
├─ scripts/                planner·agent CLI, Agent 평가, 길찾기 비교 스크립트
├─ docs/                   서비스별 API 명세, 외부 데이터 명세, 협업 규칙
├─ app/                    Flutter 앱 (예정)
├─ docker-compose.yml
└─ PROJECT.md              기획·방향
```

## 브랜치

| 브랜치 | 용도 |
|---|---|
| `main` | 데모 가능한 안정 버전 |
| `dev` | 통합 브랜치. 기능 브랜치를 여기에 머지 |
| `feat/<기능>` | 기능 개발 (기능마다 git worktree를 따로 쓴다) |

자세한 규칙은 [docs/COLLABORATION.md](docs/COLLABORATION.md).
