# console — 데모 관제 대시보드 (웹)

발표자·개발자가 **브라우저에서** 서버의 가상 세계(사용자 위치·가상 시각·안전 상황·여행 상태)를 보고 조작하는 화면.
지금은 CLI(`scripts/planner/cli.py`의 `sim`·`trip` 명령)와 진행 스크립트(`scripts/demo/run_demo.py`)로만 조작할 수 있어 상태가 눈에 보이지 않는다.

- **지도 중심**: 구룡포 지도 위에 사용자 위치·오늘 일정·안전 구역·센서·피할 구역을 그리고, **지도에서 위치를 직접 옮긴다.**
- **발표 화면에도 쓴다**: 왼쪽 앱 에뮬레이터, 오른쪽 이 대시보드. "DT 데이터 변화 → 결정론적 판정 → 이벤트 → (레벨 3만) Agent" 흐름이 이벤트 타임라인으로 보인다.
- **진행 스크립트를 대체하지 않는다.** 장면 순서·대화·제안 승인은 `run_demo.py`가 맡는다. 대시보드는 상태 표시 + 수동 조작이다 (둘을 같이 써도 된다. 대시보드는 읽기 위주).

---

## 1. 구성 (백엔드 코드는 바꾸지 않는다)

| | |
|---|---|
| 형태 | **빌드 없는 정적 페이지** (HTML + CSS + 바닐라 JS ES 모듈). 프레임워크·번들러·npm 없음 |
| 위치 | `infra/gateway/console/` (`index.html`, `app.js`, 모듈 몇 개, `style.css`, `vendor/leaflet/`) |
| 서빙 | **게이트웨이(Caddy)가 `/console/`에서** 직접 서빙. 같은 출처라 CORS 불필요, 앱과 같은 주소 하나 |
| 지도 | Leaflet 1.9.4 (**`vendor/`에 파일로 포함** — 발표장에서 CDN 장애 영향 없게) + OSM 타일 (타일은 인터넷 필요, 앱과 같음) |
| 언어 | 화면 문구는 한국어 |

### 1.1 게이트웨이 변경 (공용 파일 — 별도 커밋)
- `infra/gateway/routes.caddy`: 맨 끝 404 `handle` **앞에** 정적 경로 추가. `/console` → `/console/` 리다이렉트, `handle_path /console/*` + `root * /srv/console` + `file_server`.
  개발 `Caddyfile`·운영 `Caddyfile.prod` 모두 이 파일을 import 하므로 한 곳만 고친다.
- 개발 `docker-compose.yml` gateway: `./infra/gateway/console:/srv/console:ro` 마운트 (파일 고치고 새로고침만 하면 반영).
- 운영 `infra/gateway/Dockerfile`: `COPY console /srv/console` (운영은 마운트 없이 이미지에 넣는다, docs/deploy). 이미지 증가는 수백 KB.
- 페이지 자체는 공개 정적 파일이다. **비밀(토큰·키)을 파일에 넣지 않는다.** 조작은 아래 인증으로만 된다.

### 1.2 인증
- **사용자 로그인**: 대시보드가 조작하는 여행·위치·이벤트는 사용자별이다. 앱과 **같은 데모 사용자**(`seoyun-demo`)로 로그인한다.
  `POST /auth/login {login_id, password}` → access·refresh 토큰. 이후 `Authorization: Bearer …`. 만료(401)면 `POST /auth/refresh` 한 번 후 재시도.
  개발 스택이 `AUTH_REQUIRED=false`면 로그인 없이 `X-User-Id` 헤더로도 된다 (로그인 기능은 그대로 둔다 — 운영은 로그인 필수).
- **관리 토큰** (운영 서버만): 아래 표의 🔒 API 는 게이트웨이가 `X-Admin-Token` 헤더를 요구한다 (`infra/gateway/Caddyfile.prod`, 없으면 403 `admin_only`).
  설정 칸에 한 번 입력 → 이 브라우저의 `localStorage`에만 저장, 모든 요청에 헤더로 붙인다. 개발 스택은 토큰 없이 열려 있다.
  토큰 값: 노트북에서 `deploy/admin_token.sh`.
- 토큰·비밀번호를 URL·콘솔 로그에 남기지 않는다.

---

## 2. 화면

```
┌ 상단 바 ──────────────────────────────────────────────────────────────────────┐
│ 구룡메이트 관제 · 서버 http://… ● 연결  LLM claude:haiku / scripted  안전: 실제 데이터 | 가상(storm) │
│ 사용자 seoyun-demo [로그아웃]   계획 [엄마랑 구룡포 ▾]   ● 동행 중 1일차   가상 시각 10/10 15:21:07 │
├──────────────────────────────────────────────┬────────────────────────────────┤
│                                              │ ① 시각                          │
│                 지 도                         │ ② 안전 상황                     │
│  - 오늘 일정 (번호 마커 + 구간 경로선)           │ ③ 위치 · 여행                   │
│  - 현재 위치 (끌어서 옮김)                      │ ④ 이벤트 타임라인                │
│  - 안전 구역·센서·피할 구역 (레이어 켜고 끄기)     │                                │
│  - 지도 클릭 → [여기로 순간이동] [여기로 걸어가기]  │                                │
└──────────────────────────────────────────────┴────────────────────────────────┘
```
노트북 화면 절반(약 960px 폭)에서도 쓸 수 있게 한다 (발표 때 에뮬레이터와 나란히). 오른쪽 패널은 접을 수 있게.

### 2.1 지도 (구룡포 중심 35.990, 129.556, 확대 15)
| 레이어 | 데이터 | 표시 |
|---|---|---|
| 오늘 일정 | `GET /planner/plans/{id}` → 여행일(`trip state.day`, 여행 전이면 1일차) `items[].place.lat/lng` | 순서 번호 마커, 상태별 색(예정·머무는 중·완료·건너뜀). 마커 클릭: 이름, 예정 시각, [여기로 순간이동] [여기로 걸어가기] [여기서 N분 머물기] |
| 구간 경로선 | `POST /routing/route {mode: item.leg_mode, stops: [이전, 이번]}` → `geometry` | 얇은 선. 계획이 바뀔 때만 다시 계산 (캐시) |
| 현재 위치 | `GET /trip/plans/{id}/state` → `location` | 파란 점 + 정확도 원. **끌어서 놓으면 그 자리로 위치 전송** (순간이동) |
| 안전 구역 | `GET /safety/zones` (모양: `path` 점 하나 = 원 중심, 여러 개 = 선, `radius_m`) + `GET /safety/status` `zones[].risk` | 위험 단계 색(safe 초록 · caution 주황 · danger 빨강 · null 회색). 툴팁: 이름, `reasons` |
| 센서 | `GET /safety/status` `sensors[]` (lat, lng, type, value, level, risk) | 작은 원 마커, 등급 색. 툴팁: 이름, 종류, 값, 등급 |
| 방파제 CCTV | `status.breakwater_activity` | 북·남방파제 구역 툴팁에 최근 1시간 집계 |
| 피할 구역 | `GET /safety/avoid` `zones[].polygon` (**[lng, lat] 순서** 주의) | 빗금·반투명 빨강. 비면 없음 |

### 2.2 ① 시각
- 지금 가상 시각을 초 단위로 표시 (`GET /safety/sim`의 `now` + 받은 뒤 흐른 시간). 실제 시각과 다르면 "가상" 배지 + 차이.
- 버튼 `+5분` `+10분` `+30분` `+1시간`, 날짜·시각 직접 입력 [적용], [실제 시각으로] (`reset_clock: true`).
- 바꾸기: 🔒 `PUT /safety/sim {"now": ISO8601(+09:00)}`.
- **뒤로 돌릴 때 경고**: 마지막 위치 `recorded_at`보다 이른 시각으로 옮기면 그 뒤 위치가 버려진다(§4). 확인창으로 알린다.

### 2.3 ② 안전 상황
- 모드: [실제 데이터] / [가상 상황] 전환 (🔒 `PUT /safety/sim {"source": "live"|"mock"}`). `switchable=false`면 비활성 + 이유 표시.
- 시나리오 칩 5개: 이름과 설명은 `GET /safety/sim`의 `scenarios`에서 (calm 평상시, rain 비, wind_tomorrow 내일 강풍, flood 침수 센서, storm 폭풍). 누르면 🔒 `PUT /safety/sim {"scenario": …}`.
  시나리오는 "지금 기준 상태"다 (특정 시각 사건이 아님, docs/safety §1). 칩 아래 설명 한 줄로 보여준다.
- 요약: `overall_risk`, 특보(`warnings`), 앞으로 12시간 위험 신호(`next_hours`), `data_status` 중 실패한 소스.
- 버튼 [안전 점검 지금] 🔒 `POST /planner/safety/scan` → 이어서 [틱] 🔒 `POST /trip/plans/{id}/tick?now=<가상 시각>`.
  자동 주기(planner 점검 개발 60초·운영 10초, trip 틱 30초)를 기다리지 않고 바로 이벤트가 나오게 한다. 시나리오를 바꾼 뒤 "바로 반영" 체크가 켜져 있으면 자동으로 둘 다 부른다.

### 2.4 ③ 위치 · 여행
- 여행 상태: `GET /trip/plans/{id}/state` — 동행 중 여부·일차, 지금(머무는 중/이동 중 + 항목), 다음 일정·예정·예상 도착·지연 분.
- [여행 시작] `POST /trip/plans/{id}/start` / [여행 종료] `POST /trip/plans/{id}/end`. 여행 중이 아니면 위치 조작 버튼은 비활성 ("여행을 먼저 시작하세요" — 서버가 409 `trip_not_active`).
  시작이 409 `not_trip_day` 등으로 실패하면 서버 메시지를 그대로 보여주고, 가상 시각을 계획 날짜로 옮기라고 안내한다.
- 위치 조작 (모두 `POST /trip/plans/{id}/location`, `source: "sim"`, `accuracy: 10`):
  | 동작 | 방법 |
  |---|---|
  | **순간이동** | 지도 클릭 → [여기로 순간이동], 또는 위치 마커 끌어 놓기. 점 하나, `recorded_at` = 지금 가상 시각 |
  | **걸어가기** | 지도 클릭·일정 마커 → [여기로 걸어가기]. `POST /routing/route {mode: "walk", stops: [현재, 목적지]}`의 `geometry`를 따라 약 25m 간격 점. 도보 70m/분으로 점마다 가상 시각을 계산하고, 점마다 🔒 `PUT /safety/sim {now}` → 위치 전송. 배속(기본 20배: 실제 1초 = 가상 20초) 만큼 기다린다. [정지] 가능. 차량은 [차로 가기] (`mode: "car"`, 30km/h 가정) |
  | **머물기** | [여기서 N분 머물기] (기본 3분). 같은 좌표로 30초(가상) 간격 점을 N분 동안, 가상 시각을 함께 진행. 도착 판정은 반경 안 **90초 이상**이라 최소 2점이 90초 넘게 떨어져야 한다 |
  | 일정 바로가기 | 일정 목록에 항목마다 [이동] [걸어가기] [머물기] |
- 전송 응답의 `events`(이번에 생긴 이벤트)는 지도에 잠깐 말풍선으로 띄우고 타임라인에도 들어간다. `ignored > 0`이면 "버려진 점 n개 (시각이 이전 위치보다 이름)" 표시.

### 2.5 ④ 이벤트 타임라인
- `GET /trip/plans/{id}/events` 를 2초마다 (여행 중일 때). 최신이 위.
- 한 줄: 시각(`now`) · 종류 한국어(도착·출발·지연·안전 상승·안전 완화·위험 구역 접근·여행 시작·종료) · **개입 레벨 0~3 배지** · 심각도 색 · `message` · 상태(`queued`·`delivered`·`suppressed`·`failed`).
  레벨 3은 강조하고 `agent.proposal_id`가 있으면 "Agent 제안 #id 보냄" 표시. `failed`면 `payload`/오류 사유 툴팁.
- **읽음 처리(`/seen`)는 하지 않는다.** 그건 앱이 한다 (대시보드가 하면 앱 알림이 사라질 수 있다).
- 보조: planner 안전 경고 `GET /planner/plans/{id}/safety/alerts` 최근 몇 개 (접힌 영역).

### 2.6 연결·설정
- 서버 주소 (기본: 페이지가 열린 출처 — 같은 게이트웨이), 사용자 로그인, 관리 토큰, 배속.
- 연결 확인: `GET /health`, `GET /agent/info`(LLM 공급자), `GET /safety/sim`(404 `sim_disabled`면 "가상 상황 꺼짐 — `SAFETY_SIM_ENABLED=true` 필요" 안내, 시각·안전 조작 비활성), `GET /routing/status`.
- 계획 선택: `GET /planner/plans`. 하나면 자동 선택. 선택은 `localStorage`에 기억.

---

## 3. API 정리 (모두 게이트웨이 경유, 🔒 = 운영에서 `X-Admin-Token` 필요)

| 용도 | 메서드·경로 |
|---|---|
| 로그인 | `POST /auth/login`, `POST /auth/refresh`, `GET /users/me` |
| 연결 | `GET /health`, `GET /agent/info`, `GET /routing/status` |
| 시각·안전 | `GET /safety/sim`, 🔒`PUT /safety/sim`, `GET /safety/status`, `GET /safety/zones`, `GET /safety/avoid` |
| 계획 | `GET /planner/plans`, `GET /planner/plans/{id}`, `GET /planner/plans/{id}/safety/alerts`, 🔒`POST /planner/safety/scan` |
| 여행 | `POST /trip/plans/{id}/start`, `POST /trip/plans/{id}/end`, `GET /trip/plans/{id}/state`, `POST /trip/plans/{id}/location`, `GET /trip/plans/{id}/events`, 🔒`POST /trip/plans/{id}/tick` |
| 길찾기 | `POST /routing/route` |

응답 형태는 각 서비스 Swagger(`/<서비스>/docs`)와 `backend/app/<서비스>/schemas.py`가 기준이다.

---

## 4. 꼭 지킬 규칙 (서버 동작에서 오는 함정)

- **시계는 safety 하나**. trip·planner는 `GET /safety/sim`을 **5초 캐시**한다. 시각을 크게 옮긴 직후 틱을 부르면 이전 시각으로 판정될 수 있으므로,
  틱 전에는 `GET /trip/plans/{id}/state`의 `now`가 목표 시각에 닿을 때까지(최대 약 6초) 기다리거나 틱에 `?now=`를 넘긴다 (둘 다 하는 게 안전). 참고 구현: `scripts/trip/trip_cli.py`의 `Player`, `_tick`, `_wait_clock`.
- **위치 `recorded_at`은 단조 증가**여야 한다. 이전 점보다 이르면 서버가 버린다. 위치 판정은 `recorded_at` 기준이라 캐시와 무관하다.
- **도착**: 장소 반경(60m + 정확도, 최대 150m) 안에 90초 이상. **출발**: 반경 밖 2분 이상. 순간이동 한 번으로는 도착이 안 난다 → [머물기]를 쓴다.
- **중복 억제**: 같은 원인으로는 한 번만 알린다 (docs/trip §4.1). 같은 장면을 다시 보려면 초기화가 필요하다 — 대시보드에서 하지 말고 `run_demo.py`의 `prep`(또는 `--fast-to <단계>`)을 쓰라고 화면 도움말에 적는다.
- 가상 상황이 꺼져 있으면(`sim_disabled`) 시각·안전 조작은 못 하지만 지도·상태·위치는 된다.
- 대시보드가 여러 개 열려도 망가지지 않게: 상태는 항상 서버에서 다시 읽고, 대시보드에만 있는 상태는 진행 중인 '걸어가기' 하나뿐이다.

---

## 5. 범위

**P0** (발표에 필요)
- 정적 서빙(개발 마운트·운영 이미지), 로그인·관리 토큰, 계획 선택
- 지도: 일정·현재 위치(끌기)·안전 구역·센서·피할 구역
- 시각 조작, 실제 ↔ 가상 전환, 시나리오 칩, [안전 점검 지금 + 틱]
- 여행 시작·종료, 순간이동·걸어가기·머물기, 여행 상태
- 이벤트 타임라인 (2초 폴링)

**P1**
- 경로선(구간), 일정 항목 바로가기 버튼, planner 안전 경고 목록, 다크 모드, 패널 접기

**하지 않음**
- 대화·제안 승인 (앱과 `run_demo.py`의 몫), 계획 편집, 데이터 초기화(`prep`), 백엔드 API 추가·변경
- 백엔드에 꼭 필요한 것이 생기면 구현하지 말고 이 문서 §7에 적고 보고한다.

---

## 6. 확인 방법

1. 개발 스택: `backend/.env`에 `SAFETY_SIM_ENABLED=true`, `AGENT_PROVIDER=scripted`(비용 0) → `docker compose up -d --build`.
2. `python scripts/demo/run_demo.py --url http://127.0.0.1:8000 --fast-to s2_start` 로 데모 계획·사용자를 만들고 스크립트는 끝낸다(`q`).
3. `http://127.0.0.1:8000/console/` 에서 `seoyun-demo`로 로그인 (비밀번호는 `run_demo.py`의 기본값/환경변수 `GURYONG_DEMO_PASSWORD`). 확인할 것:
   - 여행 시작 → 가옥거리로 걸어가기 → 머물기 3분 → 타임라인에 `arrived`(레벨 2) / 앱 동행 화면에 해설 카드
   - 시각 15:20 → 가상 상황 storm → [안전 점검 지금] → 지도 구역·센서가 빨강으로, 피할 구역 표시 → 타임라인 `safety_raised` 레벨 3 → 앱에 Agent 선제 메시지
   - 수협 앞(35.9912, 129.5572)으로 순간이동 후 머물기 → `near_hazard`
   - 실제 데이터로 전환 → 실제 센서 10대·방파제 집계가 지도에 보임 (키가 있을 때)
4. 운영 설정 확인: `docker-compose.prod.yml` 방식(docs/deploy §9의 로컬 운영 확인 방식)으로 띄워 관리 토큰 없이 🔒 버튼이 403 안내를 보이는지, 토큰을 넣으면 되는지.
5. 브라우저 화면 캡처(960px·1440px 폭) 몇 장을 `docs/console/review/`에 남긴다.
6. 끝나면 `docker compose down` (worktree 스택을 켜 둔 채 끝내지 않는다).

## 7. 확인 결과와 열린 문제

### 7.1 구현 (2026-10-10, `feat/console`)
`infra/gateway/console/`: `index.html`, `style.css`, `app.js`(시작·로그인·설정·폴링), `api.js`(토큰·refresh·관리 토큰), `data.js`(읽기), `actions.js`(시각·안전·여행·위치 조작),
`map.js`(Leaflet 레이어), `panels.js`(①~④), `store.js`, `ui.js`, `util.js`, `vendor/leaflet/`(1.9.4 + LICENSE).
P0 전부와 P1(경로선, 일정 바로가기, planner 안전 경고, 다크 모드, 패널 접기)을 넣었다. 백엔드는 바꾸지 않았다.

- 폴링: 시각 `/safety/sim` 5초(그 사이는 받은 뒤 흐른 시간으로 초 단위 표시), 안전 10초, 계획 목록 5초(`version`·`progress_version` 이 바뀔 때만 상세), 여행 상태 2초,
  이벤트 2초(여행 중이 아니면 10초, `queued` 가 있으면 그 앞부터 다시 읽어 `delivered`·`failed`·`agent` 반영), planner 경고 15초.
- 걸어가기: 경로선 25m 간격, 70m/분. 차: 60m 간격, 30km/h. 머물기: 30초 간격. 점마다 🔒 `PUT /safety/sim {now}` → 위치 전송 → 배속만큼 대기 (기본 20배, `즉시` 는 100점씩 묶어 보내고 시각은 끝에 한 번).
  출발 시각은 지금 가상 시각, 단 마지막 위치 `recorded_at` 보다 이르면 그 1초 뒤 (서버가 버리지 않게).
- [안전 점검 지금 + 틱]·[여행 시작] 전에 trip 의 지금이 가상 시각(±60초)을 따라올 때까지 최대 8초 기다리고, 틱에는 `?now=` 를 넘긴다 (§4).
- [여행 시작]이 409 `not_trip_day` 면 서버 문구 + [가상 시각을 계획 날짜로] 버튼. ① 에도 "계획 날짜로: 첫 일정 10분 전" 바로가기가 있다.
- 지도 클릭 팝업은 `[순간이동] [걸어가기] [차로 가기] [N분 머물기]`, 일정 마커 팝업도 같다 (이름·예정 시각·상태 포함). 파란 점을 끌어 놓으면 순간이동.
- 관리 토큰은 설정 대화상자에서 입력 → `localStorage` 에만, 모든 요청에 `X-Admin-Token`. 403 `admin_only` 는 "설정에서 관리 토큰을 입력하세요" 알림.
- 로그인 토큰(access·refresh)도 `localStorage` 에 둔다 (새로고침해도 유지). 401 이면 refresh 한 번 후 재시도, 실패하면 로그인 창.
  개발 스택용 "로그인 없이 (X-User-Id)" 버튼이 있다 (`AUTH_REQUIRED=true` 면 401 안내).

### 7.2 확인 (§6, worktree 스택 `-p guryong-console`, `API_PORT=8030`, `AGENT_PROVIDER=scripted`, `SAFETY_SIM_ENABLED=true`, 실제 E-UM·기상청 키)
`run_demo.py --fast-to s2_start` (각본 LLM, 확인 2/2) 뒤 `q` → `/console/` 에서 `seoyun-demo` 로그인.

| 확인 | 결과 |
|---|---|
| `/console` → `/console/` 308, 정적 파일·Leaflet 200 (개발 마운트) | 통과 |
| ① [계획 날짜로] 10/10 12:20 → [여행 시작] (시계 캐시 대기 포함) | 통과 — `day_started` |
| 구룡포항으로 순간이동 → 가옥거리 걸어가기(626m, 약 9분, 60배) → 머물기 3분 | 통과 — 12:28 `arrived` L2, 카드 '해설', 일정 1번 '머무는 중' |
| 15:20 → 폭풍 칩 (바로 반영: 점검 + 틱) | 통과 — 구역 위험 3·주의 8, 센서 빨강, 피할 구역 5곳 빗금, `safety_raised` L3 "Agent 제안 #2 보냄" (함께 `delay` L3 가 `queued` — 쿨다운) |
| 수협 앞(35.9912, 129.5572) 순간이동 + 머물기 3분 | 통과 — `near_hazard` L1 (구룡포수협_스마트맨홀 주변), 가옥거리 `departed` L1 |
| [실제 데이터] 전환 | 통과 — `mode: live`, 센서 10대, 북·남방파제 CCTV 1시간 집계(모두 0), 데이터 5종 수신, "실제 데이터 (가상 시각에 맞춰 시각을 옮김)" |
| 운영 게이트웨이: `infra/gateway/Dockerfile` 로 이미지를 만들어 `Caddyfile.prod` + 임시 관리 토큰으로 같은 스택에 붙임(:8031) | 통과 — 이미지 안 `/console/` 200, 토큰 없이 [+5분] → 403 "관리 토큰이 필요한 API 입니다 — 설정(⚙)에서…" 알림, 토큰 입력 후 [+5분]·[안전 점검 지금 + 틱] 200 |
| [여행 종료] | 통과 — `day_ended`, 위치 조작 버튼 비활성 |
| 토큰이 URL·콘솔 로그에 남는지 | 남지 않음 (콘솔에는 브라우저가 찍는 403 한 줄만) |
| 화면 캡처 | `review/1440_live.jpg`, `review/1440_storm.jpg`, `review/960_storm.jpg`, `review/960_timeline.jpg`, `review/prod_403.jpg` |

### 7.3 열린 문제·참고
- **운영 compose 전체(`docker-compose.prod.yml`)로는 띄우지 않았다.** 운영 이미지 5개 빌드·Valhalla 볼륨이 필요해서, 게이트웨이 이미지만 운영 설정으로 만들어 개발 스택에 붙여 확인했다 (`AUTH_REQUIRED` 는 개발값 false — 로그인 자체는 `/auth/login` 으로 확인).
  배포 전 `deploy/build.sh` 로컬 확인(docs/deploy §9) 때 `/console/` 을 한 번 열어 볼 것.
- 캡처는 브라우저 도구가 폭 800px 로 줄여 저장했다 (레이아웃은 960·1440 뷰포트에서 그린 것).
- 실제 ↔ 가상을 오가면 planner 가 그때마다 새 경고를 만들어 `safety_eased`(L1) → `safety_raised`(L3, 새 Agent 제안)가 다시 나온다 (원인 키 = 경고 id). 발표 중에는 전환을 한 번만 할 것.
  "바꾸면 바로 반영" 이 켜져 있으면 전환할 때마다 점검·틱을 부른다 — 끄면 planner 주기(개발 60초·운영 10초)를 기다린다.
- 가상 시각을 크게 앞으로 옮기면 그동안 위치가 없어 `delay` L3 가 함께 나올 수 있다 (실제로 늦은 것, docs/trip §3.2). 장면 순서는 `run_demo.py` 로.
- 대시보드 걸어가기와 `run_demo.py` 재생을 동시에 돌리면 두 쪽이 가상 시각을 서로 덮어쓴다 (위치 시각이 뒤섞여 버려지는 점이 생김). 하나만 돌릴 것.
- 백엔드에 꼭 필요한 변경은 없었다. 아래는 있으면 좋은 것 (구현하지 않음).

### 7.4 백엔드에 필요한 것: 가상 상황의 끝 시각 (구현하지 않음, 2026-10-11)
**문제**: 가상 상황 시나리오는 "지금(가상 시각) 기준 상태"라 시각을 옮기면 같이 밀린다 (`backend/app/safety/sources.py` `MockSources`).
- 센서·특보(storm·flood)는 시각과 상관없이 고정, 방파제 CCTV 는 항상 직전 1시간.
- 예보: storm 은 최근 단기예보 발표 시각(02·05·…·23시) + 1시간부터 30시간 악천후 (발표 시각이 바뀔 때 3시간씩 밀림),
  rain·flood 는 지금부터 몇 시간 뒤, wind_tomorrow 는 발표 날짜의 다음 날 12~18시.
- 그래서 storm 을 켠 채 뒤 일정으로 가면 계속 날씨가 나빠 남은 야외 일정마다 경고·계획 수정 제안이 나온다. "폭풍이 지나가고 뒤 일정은 그대로"를 보여줄 수 없다
  (지금은 시나리오를 [평상시]로 바꾸는 수밖에 없다 — 그러면 `safety_eased` 가 나온다).

**제안** (기존 동작은 그대로 두는 확장):
- `PUT /safety/sim` 에 선택 필드 `until` (ISO 시각). 예: `{"scenario": "storm", "until": "2026-10-10T17:00:00+09:00"}`. `GET /safety/sim` 응답에도 `until`.
- `until` 이 있으면 그 시각 전까지만 시나리오를 쓴다. 예보는 시각별로 `until` 전만 나쁨 (경계에서 정확히 끊겨 16:00 북방파제는 위험, 17:30 카페 가는 길은 안전),
  센서·특보·CCTV 는 지금이 `until` 을 지나면 평상시. 생략하면 지금처럼 "지금 기준" (run_demo.py·기존 테스트 영향 없음).
- 대시보드: 시나리오 칩 아래 "끝나는 시각" 입력 (비우면 계속).
- 데모 타임라인 참고: `run_demo.py` 의 `s4_move` 는 16:33 쯤 수협 앞 `near_hazard` 를 기대하므로, 데모에 쓴다면 `until` 은 17:00 정도. (run_demo.py 는 그대로 두기로 함)


### 7.5 결정 — 가상 날씨 시간표 (2026-10-11, 구현: `feat/sim-timeline`)
§7.4 의 "끝 시각만 추가" 대신 **시간대 목록(시간표)**으로 간다. "시뮬레이션은 실제 여행을 빠르게 보는 것"이므로 날씨도 정해진 시각에 오고 그친다.

- **구간** `{kind, start, end}` (가상 시각 기준 절대 시각, `start` 포함·`end` 제외). 여러 개 가능. `kind`: `storm` 폭풍 · `rain` 비 · `flood` 침수 센서 · `wind` 강풍·높은 파도(특보 없음).
  겹치면 그 시각에는 더 심한 것 하나 (storm > flood > wind > rain).
- **구간 밖은 평상시(calm)**. 시간표는 가상 상황 모드(`mode: sim`)에서만 쓰고, 실제 데이터 모드는 그대로 실제 데이터다 (시간표는 기억해 두었다가 sim 으로 바꾸면 씀).
- **예보는 지정 즉시 반영**: 예보 범위(약 3일) 안의 구간은 바로 시각별 예보에 나타난다 → planner 가 미리 경고한다. 그래서 데모 장면 ④는 지금처럼 **그 순간에 구간을 추가**한다.
- **지금 관측값**(센서·특보·방파제 CCTV)은 가상 시각이 구간 안일 때만 그 종류 값, 구간을 지나면 평상시 → planner 경고 완화 → trip `safety_eased` 가 자연스럽게 나온다.
- **기존 시나리오는 프리셋**으로 유지 (앱 칩·재생 파일·`run_demo.py` 호환): `scenario` 를 주면 시간표를 그 프리셋으로 **바꾼다**. 설정한 순간 기준 절대 구간이 되고 더는 밀리지 않는다.
  `calm` = 비움, `storm` = 지금~30시간, `flood` = 지금~9시간, `rain` = 2~9시간 뒤, `wind_tomorrow` = 다음 날 12~18시 `wind`. `until` 을 함께 주면 그 시각에 끝난다.
- 구간 안 시각의 판정 결과는 기존 시나리오 표(docs/safety §1)의 기대 결과와 같다 (storm: 방파제·침수 구역 위험, 야외 주의, 실내 안전 …).
