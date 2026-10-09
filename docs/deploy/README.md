# deploy — 공개 테스트·발표 서버 (GCP VM)

팀원·멘토가 직접 써 보는 **테스트 서버** 겸 **발표 데모 서버**. 개발과 서버를 나눈다.

| | 개발 | 서버 |
|---|---|---|
| 어디서 | 노트북 (worktree 마다) | GCP VM (공인 IP, ssh 만) |
| 브랜치 | `dev`, `feat/*` | **`main` 만** (PROJECT.md §8 — main 은 항상 데모 가능) |
| compose | `docker-compose.yml` (빌드·코드 마운트·`--reload`) | `docker-compose.prod.yml` (이미지 태그, 코드는 이미지 안, `restart: unless-stopped`) |
| 이미지 | 그 자리에서 빌드 | **노트북에서 빌드 → ssh 로 스트림 전송** (VM 에서 빌드·OSM 다운로드 없음) |
| 임베딩 | torch 서버 (`services/embedding/`) | ONNX 경량판 (`services/embedding-onnx/`) |
| DB | `pgvector/pgvector:pg16` | `postgres:16-alpine` + pgvector 0.8 (`services/db/`) |
| 로그인 | `AUTH_REQUIRED=false` 가능 | 로그인 필수, 데모 조작 API 는 관리 토큰, 사용자당 LLM 호출 수 제한 |

VM: Debian 13 x86_64, 메모리 15GB, CPU 4, 루트 디스크 9.7GB 중 여유 5.0GB(확장 불가), Docker 29.9 + compose 5.6,
**containerd 이미지 저장소**(`driver-type: io.containerd.snapshotter.v1`, 2026-10-10 ssh 로 확인).
containerd 저장소는 이미지를 **압축본(content) + 풀린 본(snapshot)** 두 벌로 둔다. 그래서 이미지 크기를 줄이는 것이 디스크 예산의 대부분이다.

```
노트북 (main 체크아웃)                                      GCP VM ~/guryongmate
deploy/update.sh                                             docker-compose.prod.yml, .env, backend.env
  ① main 확인  ② docker build (5개 이미지)                    ┌ gateway :8000  ← 방화벽은 이 포트만
  ③ docker save | gzip | ssh 'gunzip | docker load' ───────►  │ account places planner safety routing trip docent agent
  ④ compose up  ⑤ 헬스 체크  ⑥ 실패 시 이전 태그로             └ db, embedding(ONNX), valhalla, places-init   (포트 매핑 없음)
```

---

## 1. 디스크 예산 (목표 약 3GB, 실측 2.66GB)

### 1.1 실측 표

VM 과 같은 Docker 29.9.0 · compose 5.6.0 · containerd 저장소(`docker:29-dind`)에 **배포와 같은 파이프**(`docker save | gzip -1 | … | gunzip | docker load`)로
이미지를 넣고 운영 스택을 띄워 `du -smx /var/lib/docker`·`docker system df` 로 쟀다 (2026-10-10, 이미지 태그 `249f927091`).

| 항목 | 전송 (gzip) | VM 디스크 증가 | 비고 |
|---|---:|---:|---|
| `guryongmate/backend` | 98MB | 423MB | 서비스 8개 + places-init 이 같은 이미지 |
| `guryongmate/embedding-onnx` | 447MB | 1,047MB | 모델 포함 (임베딩 표 int8 + 나머지 fp32, 약 530MB). python 기반 레이어는 backend 와 공유 |
| `guryongmate/gateway` | 18MB | 67MB | caddy:2.8-alpine + 설정 |
| `guryongmate/db` | 115MB | 400MB | postgres:16-alpine + pgvector |
| `guryongmate/valhalla` | 162MB | 646MB | valhalla-scripted 3.9.0 (osmium 없음) |
| **이미지 합계** | **840MB** | **2,584MB** | `docker system df`: Images 2.706GB |
| 볼륨 `guryongmate-valhalla` | 8.7MB | 25MB | 타일 묶음·설정·행정구역만 (노트북 볼륨 173MB 중) |
| 볼륨 `pgdata` | — | 54MB | 장소 326곳 + 임베딩 432조각, 사용자 데이터가 쌓이면 는다 |
| **합계 (스택 실행 중)** | | **2,660MB** | `du -smx /var/lib/docker` |
| 로그 상한 | | ≤ 195MB | 컨테이너 13개 × 5MB × 3파일 (`json-file`, compose `x-logging`) |

- 여유 5.0GB 에서 첫 배포 뒤 약 2.2GB 가 남는다.
- **업데이트**: 백엔드 코드만 바뀌면 98MB 전송, VM 디스크는 약 2MB 만 는다 (같은 레이어 공유). 소스가 그대로인 이미지(임베딩·DB·길찾기·게이트웨이)는 보내지 않고 태그만 붙인다 (`deploy/transfer.sh`, 라벨 `guryong.src`).
  임베딩 이미지가 바뀌는 업데이트는 정리 전까지 약 1GB 가 더 필요하다 (여유 안).
- `update.sh` 는 성공하면 현재·이전 태그만 남기고 나머지 `guryongmate/*` 이미지와 태그 없는 이미지를 지운다. VM 에 tar 파일을 남기지 않는다.
- 개발 구성을 그대로 올렸다면 (같은 기준, 노트북 containerd 저장소의 `docker image ls`): torch 임베딩 이미지 1.87GB + 모델 볼륨 1.1GB + pgvector 631MB
  + valhalla(osmium 포함) 683MB + 길찾기 볼륨 173MB(OSM 원본·고도 원본 포함) + backend 417MB + caddy 70MB ≈ **4.9GB** — 여유 5.0GB 에 들어가지 않는다.

### 1.2 임베딩 경량화 (같은 모델, ONNX)

`services/embedding-onnx/`: 같은 모델 `intfloat/multilingual-e5-base`(리비전 고정)를 빌드 1단계에서 torch 로 ONNX 로 내보내고, 2단계 이미지는
onnxruntime + tokenizers + 모델 파일만 담는다 (torch 없음, 첫 실행 다운로드·모델 볼륨 없음). API 는 그대로 `POST /embed`·`/health`·`/info`.
계산도 sentence-transformers 와 같다: 512 토큰에서 자르기 → 평균 풀링(패딩 제외) → L2 정규화.

**양자화 방식** — 처음 계획은 모든 가중치 int8(동적 양자화, 279MB)이었는데, 검색 순위가 눈에 띄게 달라져 **단어 임베딩 표만 int8**(`emb-int8`, 기본)로 했다.
모델 파라미터의 2/3 가 단어 임베딩 표(25만 단어 × 768)라 이것만 int8 로 바꿔도 모델이 1.1GB → 530MB 가 되고, 트랜스포머 층은 fp32 라 결과가 사실상 같다.

벡터 비교 (장소 소개문 150개 + 질의 10개, torch 대비 코사인과 질의별 상위 10 겹침, `export.py` 와 같은 내보내기):

| 방식 | 모델 | 코사인 평균 / 최소 | 상위 10 겹침 |
|---|---:|---|---:|
| **임베딩 표만 int8 (`emb-int8`, 채택)** | 533MB | **1.0000 / 0.9999** | **99%** |
| 임베딩 표 + 어텐션 int8 | 448MB | 0.9942 / 0.9878 | 85% |
| 임베딩 표 + FFN int8 | 363MB | 0.9755 / 0.9556 | 82% |
| 모든 가중치 int8 (per-channel) | 279MB | 0.9704 / 0.9482 | 75% |
| 모든 가중치 int8 (per-tensor) | 278MB | 0.9536 / 0.9148 | 68% |

**places 검색 비교** (끝에서 끝: 각 임베딩으로 장소를 적재한 스택끼리 `GET /places?mode=semantic|hybrid&limit=10`, 질의 26개,
기준 = 개발 compose 의 torch 서버. 같은 torch 스택끼리는 100% 일치):

| 임베딩 | 모드 | 상위 10 겹침 | 상위 5 겹침 | 1위 같음 | 가장 다른 질의 |
|---|---|---:|---:|---:|---|
| **ONNX `emb-int8` (채택)** | semantic | **98.1%** | 99.2% | 25/26 | 해돋이 명소·등대·해산물 시장 90% |
| | hybrid | **98.1%** | 98.5% | 23/26 | 역사 박물관 80% |
| ONNX 전부 int8 | semantic | 72.7% | 65.4% | 17/26 | 포항 제철 산업 40%, 가족 숙소 50% |
| | hybrid | 76.5% | 74.6% | 21/26 | |

질의: 구룡포 과메기, 비 오는 날 갈 만한 실내, 일제강점기 근대 역사를 느낄 수 있는 곳, 아이랑 가기 좋은 바다, 해돋이 명소, 조용한 카페, 회 먹을 만한 곳,
걷기 좋은 해안 산책로, 역사 박물관, 사진 찍기 좋은 곳, 엄마랑 가기 좋은 곳, 드라마 촬영지, 등대, 해산물 시장, 전통 사찰, 캠핑할 수 있는 곳, 야경이 예쁜 곳,
포항 제철 산업, 해수욕장, 대게 맛집, 숲길 산책, 가족 숙소, 공원 벤치에서 쉬기, 일본인 가옥, 바다가 보이는 전망대, 어촌 마을 체험.
`emb-int8` 의 남은 차이는 점수가 거의 같은 장소끼리 순서가 바뀐 것이다 (e5 점수가 0.80~0.85 에 몰려 있다, docs/places/README.md).

| | torch 서버 | ONNX `emb-int8` |
|---|---|---|
| 이미지 + 모델 (디스크) | 1.87GB + 볼륨 1.1GB | 1.28GB (노트북) / 1.05GB (VM 증가분) |
| 메모리 | 약 0.7~1.0GB | 시작 0.66GB, 장소 적재 뒤 약 1.7GB (VM 15GB 라 문제없음) |
| 의미 검색 응답 (places 경유) | 약 60ms | 70~180ms |

빌드 옵션: `docker build --build-arg QUANTIZE=int8|fp32 services/embedding-onnx` (`export.py`). 개발 compose 는 torch 서버 그대로다.
DB 의 임베딩은 서버마다 그 서버의 임베딩 컨테이너로 적재되므로(`places-init`) 섞이지 않는다.

---

## 2. 첫 배포 (한 번)

노트북에 필요한 것: Git Bash(Windows)·macOS·Linux 셸, Docker, ssh 키로 VM 접속, python (비밀 생성·계정 만들기용).

1. **GCP 방화벽**: 게이트웨이 포트(기본 `tcp:8000`)만 연다 (§7). DB·내부 서비스는 포트 매핑이 없어 VM 밖으로 나가지 않는다.
2. **main 체크아웃** (원본 폴더 또는 main worktree): `git switch main && git pull`.
3. **설정 파일** (모두 커밋 안 함):
   ```bash
   cp deploy/deploy.conf.example deploy/deploy.conf        # VM=chansolpark7@34.64.209.209, API_PORT=8000
   cp deploy/backend.env.example deploy/backend.env        # AGENT_CLAUDE_API_KEY, DATA_GO_KR_SERVICE_KEY, DT_SERVICE_KEY 등
   cp deploy/users.txt.example deploy/users.txt            # 팀원·멘토·데모 계정 (아이디 비밀번호 이름)
   ```
4. **길찾기 타일**이 노트북에 있는지: 개발 compose 로 valhalla 를 한 번 띄웠으면 볼륨 `guryongmate-valhalla` 에 있다. 없으면 `docker compose up -d valhalla` (약 3분).
5. **실행**:
   ```bash
   deploy/first_deploy.sh
   ```
   하는 일: VM 확인(docker·compose·curl·디스크) → `~/guryongmate` 와 `.env`(JWT_SECRET·관리 토큰·DB 비밀번호를 새로 생성, 권한 600) →
   `backend.env` 올리기 → 이미지 5개 빌드·전송 → 길찾기 볼륨 전송(25MB) → `compose up --wait` → 헬스 체크 → `users.txt` 계정 만들기.
   다시 실행해도 된다 (`.env`·볼륨·계정은 있으면 그대로 둔다).
6. 앱에서 접속 확인 (§7).

VM 의 파일 (`~/guryongmate`):

| 파일 | 내용 | 누가 |
|---|---|---|
| `docker-compose.prod.yml` (+ `.prev`) | 운영 compose (이전 배포 것 보관) | update.sh 가 올림 |
| `.env` | `IMAGE_TAG`·`PREV_TAG`·`API_PORT`·`JWT_SECRET`·`GATEWAY_ADMIN_TOKEN`·`DB_PASSWORD`·`PLANNER_SAFETY_WATCH_SEC=10`·`TRIP_TICK_SEC=30` | first_deploy.sh 가 생성 |
| `backend.env` | 서비스 키·설정 (`deploy/backend.env.example`) | 노트북 `deploy/backend.env` 를 올림 |

`AUTH_REQUIRED=true`, `JWT_SECRET`, `ADMIN_TOKEN`, `DATABASE_URL`, 서비스 주소는 compose 가 넣어 `backend.env` 값보다 우선한다 (빠지면 compose 가 시작을 거부).

---

## 3. 공개 서버 보호

### 3.1 로그인과 계정
- 모든 사용자 API 는 로그인 필수(`AUTH_REQUIRED=true`). `X-User-Id` 헤더로는 401 이다.
- **가입은 막혀 있다** (`POST /auth/signup` 은 관리 토큰 필요, §3.2). 아무나 계정을 만들어 LLM 비용을 쓰지 못하게 한다.
- 계정 만들기 (노트북):
  ```bash
  deploy/create_user.sh mentor1 '비밀번호1234' 멘토1     # 아이디 비밀번호 [이름]. 규칙: docs/account/README.md §3.1
  ```
  VM 안에서 게이트웨이로 부르고 관리 토큰은 VM 의 `.env` 에서 읽는다. 여러 명은 `deploy/users.txt` 에 적고 `first_deploy.sh` 를 다시 실행해도 된다.
- 비밀번호는 각자 앱에서 바꿀 수 있다 (`PUT /users/me/password`). Google 로그인은 계정을 자동으로 만들어 이 제한을 우회하므로 서버에서는 켜지 않는다 (`ACCOUNT_GOOGLE_CLIENT_IDS` 비움).
- 앱에서 가입 화면을 열어 두려면: `infra/gateway/Caddyfile.prod` 의 `/auth/signup` 조건을 빼고 배포한다 (§3.3 의 호출 수 제한은 그대로 걸린다).

### 3.2 관리 토큰 (데모 조작 API)
게이트웨이(`infra/gateway/Caddyfile.prod`)가 아래 요청을 헤더 `X-Admin-Token` 이 `.env` 의 `GATEWAY_ADMIN_TOKEN` 과 같을 때만 넘긴다. 아니면 `403 {"detail": {"code": "admin_only"}}`.
토큰이 비어 있으면 항상 막는다. 경로는 대소문자·끝 `/`·`%` 인코딩과 상관없이 막힌다 (로컬에서 확인).

| 막는 것 | 왜 |
|---|---|
| `PUT /safety/sim` | 가상 상황·가상 시각·실제↔가상 전환은 **서버 전체**에 걸린다 (`GET` 은 앱 연결 확인용이라 연다) |
| `POST /planner/safety/scan` | 모든 사용자의 계획을 점검한다. 백엔드도 `AUTH_REQUIRED=true` 면 관리 토큰이 맞을 때만 연다 (`app.core.auth.admin_request`) |
| `POST /trip/plans/{id}/tick` | 재생 시각(`now`)을 받는 시연용 틱 |
| `POST /auth/signup` | §3.1 |

- 관리 토큰 보기: `deploy/admin_token.sh` (화면에만 출력).
- **발표 진행 스크립트**: `--admin-token` 또는 환경변수 `GURYONG_ADMIN_TOKEN` (CLI `scripts/planner/cli.py` 도 같은 환경변수). 모든 요청에 헤더로 붙는다.
  ```bash
  export GURYONG_ADMIN_TOKEN=$(deploy/admin_token.sh)
  python scripts/demo/run_demo.py --url http://34.64.209.209:8000
  ```
  토큰이 있으면 즉시 안전 점검을 부를 수 있어 장면 ④ 가 planner 점검 주기를 기다리지 않는다 (로컬 실측 s4_storm 14초).
- 앱의 데모 설정(가상 상황 칩·시각 바꾸기)은 토큰을 보내지 않으므로 공개 서버에서는 403 이다. 발표에서는 진행 스크립트가 바꾼다 (docs/demo/README.md).
- CORS 는 열지 않는다 (모바일 앱).

### 3.3 LLM 비용 보호 (사용자당 호출 수)
agent 가 사용자별로 센다 (`app/agent/limits.py`, 테이블 `agent_usage`, 실제 시각 기준). 값은 `backend.env` 의 `AGENT_LIMIT_*` (0 이면 제한 없음 — 개발 기본).

| 종류 | 무엇을 세나 | 서버 기본값 (시간당 / 하루) |
|---|---|---|
| `chat` | 대화 턴 (`POST /agent/sessions/{id}/chat`, 다시 제안) | 30 / 150 |
| `event` | Agent 선제 안내 (trip 이 보내는 지연·안전 선제 턴, 같은 이벤트 재시도는 안 셈) | 20 / 60 |
| `guide` | 가이드 준비 작업 (새 작업만, 장소 여러 곳이 한 번) | 3 / 10 |

- 넘으면 `429 rate_limited` + `message`(예: "1시간 대화 한도(30회)를 넘었습니다. 23분 뒤에 다시 시도하세요."), `retry_after`(초). 앱은 `message` 를 보여주면 된다.
  선제 턴이 막히면 trip 이 실패로 세고 다시 보낸다 (3번이면 `failed`).
- 내 사용량: `GET /agent/limits` (종류별 `used`·`limit`).
- 비용 감: Claude Haiku 5.5 로 데모 전체 1회 약 $0.04 (docs/demo/README.md §6). 한 사용자가 하루 한도를 모두 써도 대화 150턴(턴당 약 $0.003) + 가이드 준비 10번(5곳 약 $0.01~0.02) + 선제 안내 60번 ≈ 1달러 안쪽 (추정).
- 값 바꾸기: VM 의 `~/guryongmate/backend.env` 를 고치고 `docker compose -f docker-compose.prod.yml up -d agent` (또는 노트북 `deploy/backend.env` 를 고쳐 `first_deploy.sh`).
- **각본 LLM 으로 바로 바꾸기** (크레딧 부족·오류·비용을 아예 막을 때): `deploy/llm.sh scripted` (약 6~10초, LLM 호출 없음, 데모 문장만 답함). 되돌리기 `deploy/llm.sh claude`.

---

## 4. 업데이트 (dev → main → 배포)

```bash
# 1) dev 에서 확인 (docs/COLLABORATION.md §6: 전체 테스트·health)
git switch main && git pull && git merge --no-ff dev && git push      # main 은 항상 데모 가능
# 2) 배포 (main 체크아웃에서)
deploy/update.sh
```

`update.sh` 순서: ① main·깨끗한 작업 트리·`origin/main` 과 같은지 ② 이미지 5개 빌드 (태그 = 커밋 앞 10자리) ③ 바뀐 이미지만 스트림 전송
④ compose 파일 교체(이전 것 `.prev`)·`.env` 의 `IMAGE_TAG`/`PREV_TAG` 바꾸고 `compose up --wait` ⑤ 헬스 체크
(`/health`, 서비스마다 `/<서비스>/docs`, `/agent/info`, `/routing/status`) ⑥ 실패하면 실패한 서비스 로그를 보여주고 이전 태그·compose 로 되돌린다.
성공하면 현재·이전 태그 말고는 이미지를 지운다.

- DB 스키마: 새 테이블은 서비스 시작 때 `create_all` 로 생긴다. 컬럼 변경은 자동이 아니다 (해커톤 기간 Alembic 없음) — 그런 변경이면 배포 전에 따로 정한다.
- 장소 데이터: `places-init` 이 비어 있는 것만 채운다. 스냅샷을 바꿨으면 VM 에서 `docker compose -f docker-compose.prod.yml run --rm places python -m app.places.ingest load --embed`.
- 발표 직전에는 배포하지 않는다. 하더라도 `deploy/status.sh` 와 진행 스크립트 `prep` 으로 확인한다.

## 5. 되돌리기

```bash
deploy/rollback.sh          # 현재 <-> 이전 태그(와 compose 파일) 맞바꿈. 다시 실행하면 앞으로
```
VM 에는 현재·이전 두 태그만 남는다. 더 이전으로 가려면 main 을 되돌려(`git revert`) `update.sh`.

## 6. 상태·로그·디스크 확인

```bash
deploy/status.sh              # 태그, 서비스 상태, 헬스 체크, 디스크·메모리, 컨테이너별 메모리·CPU
deploy/status.sh logs agent   # 서비스 로그 최근 100줄 (gateway, trip, planner …)
deploy/status.sh disk         # docker system df -v, /var/lib/docker·containerd 크기
ssh chansolpark7@34.64.209.209 'cd ~/guryongmate && docker compose -f docker-compose.prod.yml logs -f trip'   # 실시간
```
- 로그는 컨테이너마다 최대 15MB (5MB × 3) 로 자동 정리된다.
- 디스크가 부족하면: `docker image prune -f`, 오래된 `guryongmate/*` 태그 삭제 (`update.sh` 가 성공할 때마다 한다).

## 7. 팀원·멘토 접속 안내

**GCP 방화벽** (콘솔 → VPC 네트워크 → 방화벽, 또는 gcloud):
```bash
gcloud compute firewall-rules create guryongmate-api --allow=tcp:8000 --target-tags=guryongmate --source-ranges=0.0.0.0/0
gcloud compute instances add-tags <VM 이름> --tags=guryongmate --zone=<zone>
```
- 게이트웨이 포트만 연다. ssh(22)는 GCP 기본 규칙. DB(5432)·내부 서비스는 compose 에 포트 매핑이 없어 열 필요도 없다.
- 확인: 노트북에서 `curl http://34.64.209.209:8000/health` → `{"status":"ok","service":"gateway"}`.

**앱 설정** (팀원·멘토에게 보낼 안내):
1. 앱 설치본(APK)을 받는다.
2. 설정 → 서버 주소: `http://34.64.209.209:8000` → [연결 확인] 에 `agent: claude…` 가 보이면 된다.
3. 받은 아이디·비밀번호로 로그인 (가입 화면은 서버에서 막혀 있다). 로그인 후 설정에서 비밀번호를 바꾼다.
4. 대화는 사용자마다 시간당 30턴·하루 150턴까지. 넘으면 몇 분 뒤 다시 하라는 안내가 나온다.
5. 다른 사람의 계획·대화는 보이지 않는다. 가상 상황(데모용 악천후)은 발표 진행자가 바꾸며 서버 전체에 보인다.

HTTP(평문)다. 실제 개인 정보·평소 쓰는 비밀번호를 넣지 않도록 안내한다.

## 8. 발표 후 정리

```bash
deploy/teardown.sh            # 컨테이너만 내림 (데이터·이미지 남김, 다시 up 가능)
deploy/teardown.sh --all      # 컨테이너·DB·길찾기 볼륨·이미지·~/guryongmate(.env·키)까지 삭제 ('delete' 입력 확인)
```
- 그다음 GCP 콘솔에서 방화벽 규칙 `guryongmate-api` 삭제, VM 반납.
- 쓴 키 정리: Anthropic Console 에서 서버용 API 키 폐기(또는 사용량 확인), 공공데이터포털·E-UM 키는 그대로 둬도 된다.
- 노트북의 `deploy/deploy.conf`·`deploy/backend.env`·`deploy/users.txt` 는 커밋되지 않는다. 필요 없으면 지운다.

---

## 9. 로컬에서 확인한 것 (2026-10-10, 실제 VM·Claude 호출 없음)

운영 compose 를 노트북에서 실제로 띄워 확인했다 (`-p guryongmate-prod`, 포트 8010, `AGENT_PROVIDER=scripted`, 실제 E-UM·기상청 키).
배포 스크립트는 `VM=local`(VM 쪽 명령을 노트북에서 실행)로 시험했다: `VM=local VM_DIR=guryongmate-localtest ALLOW_BRANCH=1 deploy/update.sh`
(그 폴더의 `.env` 에 `COMPOSE_PROJECT_NAME`·`VALHALLA_VOLUME` 을 따로 둬 개발 스택과 겹치지 않게).

| 확인 | 결과 |
|---|---|
| `first_deploy.sh` (VM=local) | 빌드·태그·compose up·헬스 체크 11개 200·계정 2개 생성, 2분 18초 |
| `update.sh` | 소스가 같은 이미지는 태그만, 헬스 체크 통과, 현재·이전 태그만 남기고 정리 |
| `update.sh` 자동 되돌리기 | 일부러 깨뜨린 커밋(`backend/app/main.py` 에서 예외) → 헬스 체크 실패 → 실패 로그 출력 → 이전 태그로 복귀, 모든 서비스 healthy |
| `rollback.sh` | 이전으로·다시 앞으로 둘 다 통과 |
| 전체 테스트 (운영 이미지, `pytest tests`) | **687 passed** |
| 진행 스크립트 `run_demo.py --auto --speed 0 --stay-speed 0` (각본 LLM, `AUTH_REQUIRED=true`, 관리 토큰) | **확인 20/20, 64초** (s4_storm 14초 — 즉시 점검 사용) |
| 보호 API | 토큰 없음·틀린 토큰 403, 맞는 토큰 200 (`PUT /safety/sim`, `POST /planner/safety/scan`). `/trip/…/tick`·`/auth/signup` 403. 로그인 없이·`X-User-Id` 만 `GET /planner/plans` 401. `GET /safety/sim` 200 |
| 호출 수 제한 | `AGENT_LIMIT_CHAT_HOUR=3` 으로 mentor1 대화 4번째 → `429 rate_limited` "1시간 대화 한도(3회)를 넘었습니다. 60분 뒤에…", `retry_after` 3579 |
| `llm.sh scripted` | agent 만 다시 만들기 약 10초 |
| 포트 | `gateway` 만 `0.0.0.0:8010->80` (db·valhalla 는 내부 포트만) |
| 개발 compose | 경로 분리(`routes.caddy`) 뒤에도 `docker compose up -d --build` 정상, 데모 조작 API 는 개발에서 그대로 열림 |
| 디스크·전송 (§1.1) | `docker:29-dind`(VM 과 같은 Docker 29.9.0 / containerd) 에 같은 파이프로 전송: 이미지 840MB(gzip) 전송, 디스크 2,660MB |
