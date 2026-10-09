# 병렬 개발 규칙 (사람 · 에이전트 공통)

여러 명(또는 여러 AI 에이전트)이 동시에 기능을 개발할 때 충돌을 막기 위한 규칙.
기능은 **서비스(컨테이너) 단위**로 나뉘고, 담당자는 자기 서비스만 수정·실행·테스트한다.

## 1. 서비스 구성

```
Flutter 앱 ──► gateway (:API_PORT, 기본 8000)   Caddy. URL 앞부분으로 서비스에 나눠 보낸다
                ├─ /places*   ──► places      관광지 검색·상세·근거 검색
                ├─ /planner*  ──► planner     일정·검증·변경 제안
                ├─ /agent*    ──► agent       LLM 채팅 (SSE) · 도구 실행
                ├─ /safety*   ──► safety      구룡포 안전 정보 (디지털트윈 실측 + 기상 예보·특보)
                ├─ /routing*  ──► routing     길찾기 (이동시간·경로선·한국어 안내·위험 구역 우회)
                ├─ /trip*     ──► trip        여행 중 상태·이벤트 (위치 → 도착·지연·위험 판정, Agent 선제 턴 큐)
                ├─ /docent*   ──► docent      사용자 메모리·가이드 노트 저장, 외부 검색·페이지 읽기 대행
                └─ /auth* /users* ──► account   로그인 (Google·ID/비밀번호)·사용자 정보
               embedding   임베딩 모델 서버 (내부 전용, places 가 호출)
               valhalla    길찾기 엔진, OSM 포항 데이터 (내부 전용, routing 이 호출)
               places-init 시작 시 장소 데이터·임베딩이 비어 있으면 채우고 종료
               db          Postgres + pgvector (모든 서비스 공용, 테이블 접두사로 구분)
LLM: Claude API(운영 기본, Haiku 5.5) · OpenAI · 호스트 Ollama(host.docker.internal:11434) — Docker 밖.
     개발·테스트·앱 fixture 는 각본 LLM(AGENT_PROVIDER=scripted, 호출 없음)
```

| 서비스 | 담당 | 코드 | 소유 경로 | DB 테이블 | 문서 |
|---|---|---|---|---|---|
| places | 관광지 담당 | `backend/app/places/` | `backend/app/places/`, `backend/requirements/places.txt`, `backend/tests/places/`, `scripts/places/`, `data/places/`, `docs/places/` | `place*` | `/places/docs` |
| embedding | 관광지 담당 | `services/embedding/` | `services/embedding/` | 없음 | 내부 `http://embedding/info` |
| planner | 플래너 담당 | `backend/app/planner/` | `backend/app/planner/`, `backend/requirements/planner.txt`, `backend/tests/planner/`, `scripts/planner/`, `docs/planner/` | `planner_*` | `/planner/docs` |
| agent | Agent 담당 | `backend/app/agent/` | `backend/app/agent/`, `backend/tests/agent/`, `docs/agent/` | `agent_*` | `/agent/docs` |
| safety | 안전 정보 담당 | `backend/app/safety/` | `backend/app/safety/`, `backend/tests/safety/`, `docs/safety/`, `docs/dt/`, `docs/external/` | 없음 | `/safety/docs` |
| routing | 길찾기 담당 | `backend/app/routing/` | `backend/app/routing/`, `backend/tests/routing/`, `scripts/routing/`, `docs/routing/` | 없음 | `/routing/docs` |
| valhalla | 길찾기 담당 | `services/valhalla/` | `services/valhalla/` | 없음 (볼륨 `guryongmate-valhalla`) | 내부 `http://valhalla:8002/status` |
| trip | 동행 담당 | `backend/app/trip/` | `backend/app/trip/`, `backend/tests/trip/`, `scripts/trip/`, `docs/trip/` | `trip_*` | `/trip/docs` |
| docent | 가이드 담당 | `backend/app/docent/` | `backend/app/docent/`, `backend/tests/docent/`, `docs/docent/` | `docent_*` | `/docent/docs` |
| account | 계정 담당 | `backend/app/account/` | `backend/app/account/`, `backend/requirements/account.txt`, `backend/tests/account/`, `docs/account/` | `account_*` | `/account/docs` |
| gateway·db·공용 | 공용 | — | 아래 "공용 파일" | — | — |

### 백엔드 서비스는 이미지 하나, 컨테이너 여러 개
- `backend/`는 하나의 Python 프로젝트·이미지다. compose가 같은 이미지를 `SERVICE=places`, `SERVICE=planner`처럼 **환경변수만 다르게** 여러 번 띄운다.
- `SERVICE=places`인 컨테이너는 `app/places/`의 **테이블과 API만** 연다. 서비스 문서도 `/places/docs`에만 있다.
- `SERVICE=all`(기본값)은 모든 모듈을 한 프로세스로 띄운다. 테스트(`backend/tests/conftest.py`)가 이 모드를 쓴다.
- **새 서비스 추가**: `backend/app/<이름>/`에 `router.py`·`models.py`를 만들고, compose에 `SERVICE: <이름>` 서비스와 `infra/gateway/Caddyfile`에 경로 한 줄을 추가한다(공용 파일 변경 → 별도 커밋).
- 무거운 런타임(모델 서버 등)은 `services/<이름>/`에 별도 Dockerfile로 둔다. 백엔드 이미지를 가볍게 유지하기 위해서다.

### 공용 파일 (수정 시 반드시 별도 커밋, 가능하면 `dev`에 먼저 반영)
`docker-compose.yml`, `infra/`, `backend/app/main.py`, `backend/app/db.py`, `backend/app/core/`, `backend/Dockerfile`,
`backend/requirements/base.txt`, `backend/tests/conftest.py`, `backend/.env.example`, `PROJECT.md`, `.gitignore`

기능 개발 중에는 공용 파일을 건드릴 필요가 없도록 다음과 같이 구성해 두었다.

- **라우터 자동 등록**: `backend/app/<module>/router.py`에 `router = APIRouter(prefix="/<module>")`만 만들면 된다.
- **테이블 자동 생성**: `backend/app/<module>/models.py`에 `app.db.Base`를 상속한 모델을 두면 해당 서비스가 시작할 때 `create_all` 된다. 해커톤 기간에는 Alembic을 쓰지 않는다. 테이블 이름은 반드시 모듈 접두사로 시작한다.
- **의존성 분리**: 모듈 전용 패키지는 `backend/requirements/<module>.txt`에 추가한다. Dockerfile이 `requirements/*.txt`를 모두 설치한다.
- **백그라운드 작업**: 서버가 떠 있는 동안 돌아야 하는 작업(주기 점검 등)은 `backend/app/<module>/background.py`에
  `running()`(contextmanager)으로 둔다. 그 모듈을 맡은 서비스에서만 열리고, 테스트(`tests/conftest.py`)에서는 환경변수로 끈다.
  예: planner 안전 주기 점검 (`PLANNER_SAFETY_WATCH_SEC`, `docs/planner/README.md` §7.4).

## 2. 서비스 간 연결 규칙

- 다른 모듈의 **내부 코드(models, service)를 import하지 않는다.** 다른 서비스는 **HTTP API로만** 호출한다.
- 서비스끼리도 **게이트웨이 주소(`http://gateway`)로 호출**한다. compose가 `PLANNER_PLACES_BASE_URL`, `AGENT_API_BASE_URL` 등을 이 주소로 넣어 준다. 서비스 위치가 바뀌어도 호출 코드는 그대로다.
- **장소 식별자**: 장소는 `place_id`(문자열, 예: `kto-126128`)로만 참조한다. 상세는 `GET /places/{place_id}`.
- **다른 서비스의 테이블을 직접 읽거나 쓰지 않는다.** DB는 하나지만 테이블 접두사가 소유 경계다.
- **API 를 추가·변경하면 같은 브랜치에서 CLI 명령과 `docs/planner/README.md` §11 CLI 목록도 갱신한다.** (`scripts/planner/`, trip 은 `scripts/trip/`)
- **앱이 받는 응답(API·SSE·trip 이벤트·데모 데이터·각본)이 바뀌면 같은 브랜치에서 앱 fixture 를 다시 녹화한다**: 각본 LLM 스택(`AGENT_PROVIDER=scripted`, `SAFETY_SOURCE=mock`)에서
  `python scripts/app/record_fixtures.py --url http://127.0.0.1:$API_PORT` → `app/test/fixtures/` 커밋 (형식: `docs/app/fixtures.md`).

## 3. 인증

- account가 로그인 시 JWT(HS256, claim `sub` = user_id)를 발급한다. 앱은 요청마다 `Authorization: Bearer <토큰>`을 붙인다.
- 다른 서비스는 account를 호출하지 않고 **공용 코드로 토큰을 직접 검증**한다.

  ```python
  from app.core.auth import CurrentUser, OptionalUser
  @router.get("/plans")
  def list_plans(user_id: CurrentUser): ...
  ```

- 서명 키는 `JWT_SECRET`, 로그인 강제 여부는 `AUTH_REQUIRED`(기본 `false`, 토큰이 없으면 `anonymous`)로 정한다. 토큰 발급은 `app.core.auth.issue_token`.
- `AUTH_REQUIRED=false`(개발·테스트)에서는 토큰이 없으면 `X-User-Id` 헤더를 사용자로 본다. 앱·데모는 `AUTH_REQUIRED=true`로 띄운다.
- 다른 서비스를 사용자 대신 부를 때는 `ForwardAuth`로 받은 인증을 그대로 전달한다 (예: agent → planner).
- 사용자 데이터를 가진 서비스는 탈퇴용 `DELETE /<서비스>/me`를 제공한다. account가 탈퇴 시 부른다 (`docs/account/README.md` §4).

## 4. 작업 공간: 기능마다 git worktree 하나

같은 폴더에서 여러 에이전트가 브랜치를 바꿔가며 작업하면 서로의 파일을 덮어쓴다.
**기능마다 별도 worktree(폴더)와 브랜치를 쓴다.**

```bash
git -C C:/Users/chansol/Desktop/GuryongMate worktree add ../GuryongMate-wt/<이름> -b feat/<이름> dev
```

- 원본 폴더(`GuryongMate/`)는 `dev` 브랜치 상태로 두고, 통합(merge) 용도로만 쓴다.
- 작업이 끝나면 `dev`에 머지한 뒤 `git worktree remove ../GuryongMate-wt/<이름>`.

## 5. 실행

```bash
cp backend/.env.example backend/.env           # 키 입력 (load 만 쓸 거면 비워도 됨)
docker compose up -d --build                   # 전체. 첫 실행 시 임베딩 모델 다운로드·장소 적재로 몇 분 걸린다
docker compose up -d places                    # places 와 의존 서비스(db, embedding, places-init)만
docker compose up -d gateway                   # 게이트웨이 + 모든 백엔드 서비스
docker compose logs -f planner                 # 한 서비스 로그
docker compose run --rm planner pytest tests/planner   # 테스트 (어느 백엔드 서비스에서 돌려도 됨)
```

- 백엔드 코드는 볼륨으로 마운트되고 `--reload`로 실행된다. 저장하면 각 서비스가 재시작된다. 임베딩 모델은 별도 컨테이너라 영향이 없다.
- `places-init`은 비어 있는 것만 채운다. 장소 데이터를 다시 적재하려면 `docker compose run --rm places python -m app.places.ingest load --embed`를 실행한다.
- DB를 완전히 초기화하려면 `docker compose down -v` 후 `up`.
- **게이트웨이 설정(`infra/gateway/Caddyfile`)은 자동으로 다시 읽히지 않는다.** 바뀌면 `docker compose restart gateway`.
  `up -d --build`만으로는 게이트웨이 컨테이너가 재시작되지 않아, 새 경로(예: `/safety`)가 `{"detail":"Not Found"}`로 나온다.

### 메모리 (Docker Desktop 한도 약 3.8GB)

| 컨테이너 | 상주 메모리 |
|---|---|
| embedding | 약 1.0GB |
| places · planner · agent · safety · routing · account · trip · docent | 각 약 100MB |
| valhalla | 약 60~100MB (포항 영역 타일 18MB) |
| db | 약 50MB |
| gateway | 약 15MB |
| **합계** | **약 1.8GB** |

여러 worktree에서 전체 스택을 동시에 띄우면 그만큼 늘어난다. 필요한 서비스만 띄우는 것을 권장한다.

### worktree 동시 실행

worktree마다 compose 프로젝트(컨테이너·DB 볼륨)가 따로 생긴다. 임베딩 모델 파일(`guryongmate-hf-models`)과 길찾기 OSM·타일(`guryongmate-valhalla`)만 이름이 고정된 볼륨으로 공유해서 한 번만 내려받는다.
길찾기 타일이 아직 없을 때(처음 한 번) 여러 worktree에서 `valhalla`를 동시에 띄우지 않는다. 같은 볼륨에 동시에 빌드한다.
동시에 띄울 때는 worktree 루트에 `.env`(커밋 안 됨)를 만들어 포트를 바꾼다.

| worktree | `.env` |
|---|---|
| 원본 `GuryongMate/` | (기본값) `API_PORT=8000`, `DB_PORT=5432` |
| `GuryongMate-wt/tour-data` | `API_PORT=8001`, `DB_PORT=5433` |
| `GuryongMate-wt/planner` | `API_PORT=8002`, `DB_PORT=5434` |
| `GuryongMate-wt/service-split` | `API_PORT=8003`, `DB_PORT=5435` |
| `GuryongMate-wt/safety` | `API_PORT=8004`, `DB_PORT=5436` |
| `GuryongMate-wt/account` | `API_PORT=8005`, `DB_PORT=5437` |
| `GuryongMate-wt/routing` | `API_PORT=8006`, `DB_PORT=5438` |
| `GuryongMate-wt/trip` | `API_PORT=8007`, `DB_PORT=5439` |
| `GuryongMate-wt/docent` | `API_PORT=8009`, `DB_PORT=5441` |

API 키는 각 worktree의 `backend/.env`에 둔다 (`backend/.env.example` 참고).

## 6. 머지 순서

1. 작업 전 `git merge dev`로 최신 공용 파일을 반영한다.
2. 기능 브랜치를 `dev`에 머지한다. 공용 파일 충돌이 나면 양쪽 변경을 모두 살리는 방향으로 해결한다.
3. `docker compose up -d --build` 후 `curl :8000/health`와 `docker compose run --rm places pytest tests`(전체 테스트) 통과를 확인한다.
   - 머지로 `Caddyfile`이 바뀌었으면 `docker compose restart gateway` 후 새 경로가 열리는지(`curl :8000/<서비스>/docs`)도 확인한다.
   - `backend/.env.example`에 새 항목이 생겼으면 자기 `backend/.env`에도 추가한다 (예: safety 의 `DT_SERVICE_KEY`).
