# 병렬 개발 규칙 (사람 · 에이전트 공통)

여러 명(또는 여러 AI 에이전트)이 동시에 기능을 개발할 때 충돌을 막기 위한 규칙.

## 1. 작업 공간: 기능마다 git worktree 하나

같은 폴더에서 여러 에이전트가 브랜치를 바꿔가며 작업하면 서로의 파일을 덮어쓴다.
**기능마다 별도 worktree(폴더)와 브랜치를 쓴다.**

```bash
# 예: planner 작업 시작
git -C C:/Users/chansol/Desktop/GuryongMate worktree add ../GuryongMate-wt/planner -b feat/planner dev
```

- 원본 폴더(`GuryongMate/`)는 `dev` 브랜치 상태로 두고, 통합(merge) 용도로만 쓴다.
- 작업이 끝나면 `dev`에 머지 후 `git worktree remove ../GuryongMate-wt/<이름>`.

## 2. 디렉터리 소유권

각 모듈은 **자기 디렉터리만** 수정한다.

| 모듈 | 브랜치 | 소유 경로 | DB 테이블 접두사 |
|---|---|---|---|
| 관광지 정보 (places) | `feat/tour-data` | `backend/app/places/`, `backend/requirements/places.txt`, `backend/tests/places/`, `scripts/places/`, `docs/places/` | `place*` |
| 플래너 (planner) | `feat/planner` | `backend/app/planner/`, `backend/requirements/planner.txt`, `backend/tests/planner/`, `scripts/planner/`, `docs/planner/` | `planner_*` |
| Planner Agent (agent) | `feat/planner` | `backend/app/agent/`, `backend/tests/agent/`, `docs/agent/` | `agent_*` |

### 공용 파일 (수정 시 반드시 별도 커밋, 가능하면 `dev`에 먼저 반영)
`backend/app/main.py`, `backend/app/db.py`, `backend/app/core/`, `backend/Dockerfile`,
`backend/requirements/base.txt`, `docker-compose.yml`, `backend/.env.example`, `PROJECT.md`, `.gitignore`

기능 개발 중에는 **공용 파일을 건드릴 필요가 없도록** 다음과 같이 구성해 두었다.

- **라우터 자동 등록**: `backend/app/<module>/router.py`에 `router = APIRouter(...)`만 만들면 `main.py`가 알아서 포함한다.
- **테이블 자동 생성**: `backend/app/<module>/models.py`에 `app.db.Base`를 상속한 모델을 두면 시작 시 `create_all` 된다. (해커톤 기간에는 Alembic을 쓰지 않는다. 스키마를 바꾸면 `docker compose down -v`로 DB를 초기화)
- **의존성 분리**: 모듈 전용 패키지는 `backend/requirements/<module>.txt`에 추가한다. Dockerfile이 `requirements/*.txt`를 모두 설치한다.

## 3. 모듈 간 연결 규칙

- 다른 모듈의 **내부 코드(models, service)를 import하지 않는다.** 필요한 것은 HTTP API 또는 그 모듈이 공개한 `schemas.py`로만 쓴다.
- **장소 식별자**: planner는 장소를 `place_id`(문자열, 예: `kto-126128`)로만 참조한다. 장소의 이름·좌표·운영시간 등은 `GET /places/{place_id}`로 조회한다.
- API 경로 prefix는 모듈 이름: `/places/...`, `/planner/...`

## 4. 머지 순서

1. 작업 전 `git merge dev`로 최신 공용 파일 반영
2. 기능 브랜치 → `dev` 머지 (공용 파일 충돌이 나면 양쪽 변경을 모두 살리는 방향으로 해결)
3. `docker compose up --build` 후 `/health`와 각 모듈 테스트 통과 확인

## 5. Docker 동시 실행

worktree마다 compose 프로젝트(컨테이너·DB 볼륨)가 따로 생긴다. 동시에 띄울 때는 worktree 루트에 `.env`(커밋 안 됨)를 만들어 포트를 바꾼다.

| worktree | `.env` |
|---|---|
| 원본 `GuryongMate/` | (기본값) `API_PORT=8000`, `DB_PORT=5432` |
| `GuryongMate-wt/tour-data` | `API_PORT=8001`, `DB_PORT=5433` |
| `GuryongMate-wt/planner` | `API_PORT=8002`, `DB_PORT=5434` |

API 키는 각 worktree의 `backend/.env`에 둔다 (`backend/.env.example` 참고).
