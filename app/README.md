# app — 구룡메이트 (Flutter)

여행을 함께 수행하는 AI **구룡메이트**의 앱. 명세는 [docs/app/](../docs/app/README.md) 와 Figma 「GuryongMate」. 게이트웨이 하나(`설정 > 서버 주소`)로
account · places · planner · agent · safety · routing · docent · trip 을 부른다.

- **L1 (지금)**: 기반과 계획 모드 — account 로그인, 새 SSE 진행 단계, 카드 동작, 안전 화면, 설정·메모리·가이드 준비 (화면 01~10, 21, 25~29, 31~36).
- **L2 (다음)**: 동행 모드 — Today·도착 해설·선제 메시지·위험 구역 경고·하루 요약 (화면 11~24, 30), 위치 전송기·이벤트 폴러 (gaps c13).
- Figma 대조 보고서: [docs/app/review/l1.md](../docs/app/review/l1.md).

## 실행

```bash
docker compose up -d --build          # 저장소 루트에서 백엔드 (docs/COLLABORATION.md). 포트는 .env 의 API_PORT
cd app
flutter pub get
flutter run -d windows --dart-define=DEMO=true      # Windows 데스크톱 (개발자 모드 필요: 플러그인 심볼릭 링크)
flutter run -d emulator-5554                        # Android 에뮬레이터
flutter test                                        # 단위 + 위젯 + SSE 재생 테스트 (녹화 fixture, 서버·LLM 없이)

# 실제 백엔드
flutter test test/live_backend_test.dart --dart-define=LIVE_API=http://127.0.0.1:8000     # API 계층 (테스트 계정을 만들고 끝에 탈퇴)
flutter test integration_test/app_flow_test.dart -d windows \
  --dart-define=LIVE_API=http://127.0.0.1:8018 --dart-define=DEMO=true                   # 각본 LLM 스택에서 F1·F2 를 눌러 봄, 캡처: build/screens/
flutter test integration_test/claude_check_test.dart -d windows --dart-define=LIVE_API=http://127.0.0.1:8018  # AGENT_PROVIDER=claude 로 계획 모드 3턴 (비용 발생, 손으로만)
flutter test test/capture_test.dart --dart-define=CAPTURE=true    # Figma 대조용 fixture 캡처 → docs/app/review/l1/app/fx_*.png (Windows 맑은 고딕)
```

- **로그인은 필수**다 (account §2). 토큰(access·refresh)은 `flutter_secure_storage`, 모든 요청에 `Authorization: Bearer`. 401 이면 `POST /auth/refresh` 를 **한 번만**
  보내고(동시에 여러 요청이 401 이어도 한 번) 원래 요청을 다시 보낸다. refresh 도 실패하면 01 로 (refresh_token_reused 면 안내 문구).
- **Google 로그인**: `google_sign_in` → `POST /auth/google`. 빌드에 `--dart-define=GOOGLE_SERVER_CLIENT_ID=<Web 클라이언트 ID>` 와 서버 `ACCOUNT_GOOGLE_CLIENT_IDS` 가 필요하다.
  Windows 데스크톱은 Google Sign-In 을 지원하지 않아 안내만 띄운다.
- 서버 주소는 로그인 전 01 하단 "서버 설정", 로그인 뒤 33 설정에서 바꾼다. 기본값은 Android 에뮬레이터 `http://10.0.2.2:8000`, 그 외 `http://127.0.0.1:8000`.
- **데모·개발 그룹**(33: 데모 위치 모드 스위치, 가상 상황·시각, 안전 점검 지금 실행)은 `--dart-define=DEMO=true` 빌드에서만 보인다.
- 웹 빌드는 백엔드에 CORS 설정이 없어 API 호출이 막힌다. 모바일·데스크톱으로 실행한다.
- 지도는 `flutter_map` + OpenStreetMap 타일(데모용). 경로선은 `POST /routing/route` 의 geometry (구간마다 `leg_mode`, 차량 구간은 점선, 실패·estimate 면 직선).
- WSL 의 Docker 로 백엔드를 띄웠다면 WSL 이 유휴 상태로 꺼지지 않게 Ubuntu 터미널을 하나 열어 둔다.

## 구조

```
lib/
├─ main.dart                       로그인 상태(restoring / signedOut / signedIn)로 01 / 02 전환, 바뀌면 쌓인 화면을 걷어 냄
├─ core/
│  ├─ api/api_client.dart          HTTP · 오류 code(ApiException.code/detail) · 401 → refresh 1회 · SSE 파서
│  ├─ api/guryong_api.dart         모든 서비스 엔드포인트
│  ├─ models/                      plan(ItemView·Proposal) · agent(Step·카드·추천·가이드 작업) · safety(점검·전망·구역·경로) · account(토큰·메모리·노트·TripState)
│  ├─ app_session.dart             토큰 · 사용자 · 서버 주소 · 데모 위치 모드, AppConfig(DEMO)
│  ├─ token_store.dart             flutter_secure_storage (테스트는 MemoryTokenStore)
│  ├─ theme/app_theme.dart         Figma Foundations 토큰
│  └─ widgets/                     common(버튼·칩·FAB·오류 문구) · safety_widgets(위험 배지·근거 짧은 값)
└─ features/
   ├─ auth/                        01 Login · 34 Login ID(429 잠김) · 35 Signup
   ├─ plans/                       02→29 Plan List(여행 중·안전 배지) · 03 Create Plan
   ├─ planner/                     04→25 Planner(위험 배지·배너·구간 수단) · 08 · 일정 편집 · 장소 검색
   │  └─ plan_controller.dart      계획·안전 점검 상태 (version·progress_version 이 바뀌면 다시 점검), 구간 경로 캐시, 실행 취소
   ├─ chat/                        05 AI 시트 · 06/06B 카드 · 21 Compare · 36 확인창(proposal_actions.dart)
   │  └─ chat_controller.dart      세션 · SSE(status/step/steps_done/text/proposal/recommendation/done/error) · 카드 동작
   ├─ safety/                      26 위험 근거 시트 · 27 날짜 전망 시트
   ├─ map/                         07/09 Map · 28 위험 구역·우회 · 28-S1 레이어
   ├─ guide/                       32 가이드 준비 (작업 3초 폴링)
   ├─ settings/                    33 설정·데모 · 31 나에 대해
   └─ trip/  voice/                (L2) 동행 모드 · 음성
```

## 화면 ↔ API

| 화면 · 버튼 | 호출 |
|---|---|
| 01 Google로 계속하기 / 34 로그인 / 35 가입 | `POST /auth/google` · `/auth/login` · `/auth/signup` → TokenResponse 저장 |
| 앱 시작 · 401 | `POST /auth/refresh` (한 번) |
| 33 로그아웃 / 계정 삭제 | `POST /auth/logout` / `DELETE /users/me` (확인 2단계) |
| 29 목록 · 여행 중 · 안전 배지 · AI 제안 수 | `GET /planner/plans`, `GET /trip/plans/{id}/state`(여행일만), `GET …/safety` + `…/safety/alerts?status=new`(오늘~3일), `GET …/proposals?status=pending` |
| 25 타임라인 · 구간 수단 · 위험 배지 · 배너 | `GET /planner/plans/{id}`, `GET …/safety`, `GET …/safety/alerts?status=new` (+ 보이면 `POST …/alerts/{id}/seen`) |
| 25 구간 탭 → 수단 바꾸기 | `PATCH /planner/plans/{id}/items/{item} {"travel_mode"}` |
| 26 위험 근거 · 27 전망 | (25 의 점검 결과) · `GET /safety/outlook?date=` + `GET /safety/situation` |
| 05 AI 시트 열기 | `GET /agent/sessions?plan_id=` (없으면 `POST`), 다시 열면 `GET /agent/sessions/{sid}` (카드를 서버 상태로) |
| 05 입력 · 빠른 실행(날씨·안전 확인 포함) · 후속 칩 · 배너 "AI로 조정" | `POST /agent/sessions/{sid}/chat` (SSE) · Stop = 연결 끊기 |
| 06 전체 적용 / 거절 | `POST …/proposals/{pid}/accept {"mode":"auto"}` / `reject` — 409 base_changed → 36, progress_changed → 다시 제안만, agent_busy → 스낵바, proposal_not_pending → 세션 다시 읽기 |
| 36 다시 제안 / 그대로 반영 | `POST …/proposals/{pid}/repropose` (SSE) / accept `{"mode":"overwrite"}` |
| 21 비교 보기 | `GET …/safety` (기존안) · `POST …/safety/preview {"ops"}` (수정안) · `GET /safety/avoid` · `POST /routing/route` |
| 06B 일정에 추가 / 취소 | `POST …/recommendations/{rid}/candidates/{cid}/add` / `remove` (409 already_in_plan·not_in_plan → 카드만 갱신) |
| 08 스낵바 "실행 취소" (4초) | 승인 전 일정과의 차이를 ops 로 `POST /planner/plans/{id}/ops` (다녀온 항목이 바뀌면 막음) |
| 07 경로선 · 28 위험 구역 · 우회 카드 | `POST /routing/route` (구간마다) · `GET /safety/avoid?min_risk=caution` (60초) · `POST /routing/route {"hazards":"avoid"}` |
| 32 가이드 준비 | `GET /docent/plans/{id}/notes`, `POST /agent/plans/{id}/guide/prepare`, `GET …/guide/jobs/{job}` (3초), 준비된 행 → `GET /docent/plans/{id}/notes/{place}` |
| 31 나에 대해 | `GET /docent/memory`, `DELETE /docent/memory/{id}` (+되돌리기 `POST`), `PATCH` |
| 33 서버 연결 확인 · 가상 상황·시각 · 점검 실행 | `GET /health`·`/agent/info`·`/safety/sim`, `PUT /safety/sim`, `POST /planner/safety/scan?force=true` (404 면 숨김) |

## 테스트 (LLM 호출 없음)

`test/support/fixture_gateway.dart` 가 `test/fixtures/` 의 녹화 응답을 `"METHOD route"` 별 순서대로 돌려주는 가짜 게이트웨이다 (docs/app/fixtures.md §4).
SSE 는 줄 중간에서 끊어 보내고, `sseRealtime: true` 면 녹화 시각(t_ms) 간격대로 흘린다. 녹화에 없는 응답(전망·시뮬레이션 등)은 `test/support/extra.dart`.

| 파일 | 내용 |
|---|---|
| `chat_sse_test.dart` | F1 SSE: status 교체·같은 id 단계 갱신·steps_done 접힘·카드, 추천 카드, error 이벤트·다시 시도, 409 agent_busy, repropose → superseded, 기록 → 턴 |
| `ai_sheet_test.dart` | 05 실시간 재생(단계가 순서대로 바뀌는 화면) → 승인 → 실행 취소, 06B 추가·빼기·409, F2 36 확인창 → 다시 제안 → 거절 → proposal_not_pending → overwrite → agent_busy |
| `safety_screens_test.dart` | 25 배지·참고 칩·다녀옴·구간·배너, risk=null 회색, 503, 새 경고 배너, 26, 27(먼 날짜 포함), 28·28-S1, 21 |
| `account_screens_test.dart` | F11 가입 409·422 / 로그인 401·200 / 429 잠김 / refresh_token_reused, 401 → refresh 한 번, 29, 31, 32, 33, 01 서버 설정 |
| `plan_list_test.dart` · `unit_test.dart` | 목록 복제·삭제, SSE 파서·실행 취소 ops·변경 요약·안전 문구 |
| `capture_test.dart` | (CAPTURE=true 일 때만) Figma 와 같은 장면을 그려 PNG 로 저장 |

fixture 를 다시 녹화하는 방법은 docs/app/fixtures.md §1 (응답 형식이 바뀌면 같은 브랜치에서 다시 녹화).

## 디자인과 다르게 한 것

- **혼잡도**: 데이터가 없어 칩을 지우고 지도 레이어는 비활성 "데이터 없음" (gaps b14). 날씨 칩(기온)은 위험 배지로 대체 (b15).
- **06B 별점·이동 시간**: 별점 데이터가 없고, 아직 일정에 없는 후보는 이동 시간을 모른다 (b22). 분류·추천 이유·시간 힌트를 보여 준다.
- **실행 취소**: 백엔드에 undo 가 없어 앱이 되돌리는 ops 를 계산한다. 승인 직후 4초 스낵바에서만 (c9).
- **29 "오늘 여행 시작"·[동행 화면 열기]**: 동행 모드는 L2. 지금은 계획 화면을 연다.
- 차이 목록과 이유는 [docs/app/review/l1.md](../docs/app/review/l1.md).
