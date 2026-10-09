# gaps — 화면과 백엔드 사이의 빈 곳

> 기준: `dev` (da2b60e, 2026-10-09). §0·b1·b2·b17 은 trip·docent·agent 동행 모드 머지 후(2bcea49) 갱신. 백엔드 문서에 없는 API·필드는 앱 명세에서 지어내지 않고 여기에 적는다.
> 상태 표시: **필수** = 데모 4장면이 막힘 / 권장 / 나중.

## 0. 동행 모드 서버 상태 (2026-10-09 갱신)

| 서비스 | 문서 | `dev` 코드 | 영향받는 화면 |
|---|---|---|---|
| trip (여행일·위치·이벤트) | `docs/trip/README.md` | **구현됨** | 11~16, 19, 22~24, 29(여행 중 카드), 30 — 동행 모드 전부 |
| docent (메모리·가이드 노트 저장, 검색 대행) | `docs/docent/README.md` | **구현됨** | 16, 17, 18(메모리 칩), 31, 32 |
| agent 선제 턴 `POST /agent/sessions/{sid}/events`, 동행 모드(현재 상황 블록) | trip §4.2, agent §11 | **구현됨** | 19, 22 |
| agent 가이드 준비 `POST /agent/plans/{id}/guide/prepare`·`…/jobs/{job_id}` | docent §3.3 | 없음 (작업 E 예정) | 32 |
| agent docent 도구 (`get_guide_note`, `remember` …, 외부 검색은 OpenAI 내장 `web_search`) | docent §2.1·§4 | 없음 (작업 E 예정) | 18 |
| planner 진행 상태·재계산 | planner §8 | **구현됨** | (trip 이 씀) |

앱은 이 명세대로 화면을 먼저 만들고, **가짜 게이트웨이**로 화면 흐름을 확인한다. 응답은 각본 LLM 으로 띄운 실제 스택에서 녹화한 `app/test/fixtures/`(F1~F11 P0, 오류 예시, 여행 재생)를 쓴다 → [fixtures.md](fixtures.md).
(2026-10-09 갱신: 위 표의 가이드 준비·docent 도구는 agent 에 구현됨 — docs/agent/README.md §5·§12)

---

## (a) 백엔드에 있는데 화면이 없던 것 → 이번 명세에서 화면을 정함

| 기능 | API | 기존 앱 | 이번 명세 |
|---|---|---|---|
| account 로그인 (ID/비밀번호, Google, refresh) | `/auth/*`, `/users/me` | 이메일 = `X-User-Id` | 01·34·35·33 |
| 안전 점검 (방문·이동 위험 3단계, 근거) | `GET /planner/plans/{id}/safety`, `POST …/safety/preview` | 없음 (날씨 칩 자리 비움) | 25·26·21 |
| 안전 경고 (상황이 바뀌면) | `GET …/safety/alerts?status=`, `POST …/seen` | 없음 | 25 배너 · 29 배지 · 22(동행) |
| 날짜 전망 | `GET /safety/outlook?date=`, `GET /safety/situation` | 없음 | 27 |
| 지금 위험 구역 | `GET /safety/avoid`, `/safety/zones`, `/safety/status` | 없음 (레이어 "날씨" 준비 중) | 14·23·28·28-S1 |
| 가상 상황·시각 (발표용) | `GET·PUT /safety/sim`, `POST /planner/safety/scan` | 없음 | 33 |
| 경로선·회전 안내·우회 | `POST /routing/route` (`geometry`, `steps`, `hazards`) | 핀을 직선으로 이음 | 14·15·21·28 |
| 구간 이동 수단·두 수단 비교 | ItemView `travel_mode`·`leg_mode`·`travel_options`·`travel_source` | 표시 안 함 | 25 Travel Segment |
| 변경 진행 단계 | SSE `status`·`step`·`steps_done` | 옛 이벤트(`tool_call`) | 05·18·20 |
| 장소 추천 카드 | SSE `recommendation`, `…/recommendations/{rid}/candidates/{cid}/add·remove` | 묶음 제안을 거절하고 앱이 직접 추가 | 06B (기존 화면 유지, API 교체) |
| 제안 이후 일정이 바뀜 | 409 `base_changed`, `mode:"overwrite"`, `repropose` | 처리 없음 | 36 |
| Agent 정보 | `GET /agent/info` | 없음 | 33 (연결 확인) |
| 진행 상태 수동 보정 | `POST /planner/plans/{id}/items/{item}/progress` | 없음 | 15 [도착했어요] (P2, 데모에서는 숨김 가능) |
| 비밀번호 변경·이름 변경 | `PUT /users/me/password`, `PATCH /users/me` | 없음 | 33 에 줄만 (화면 설계는 P2) |

## (b) 화면에 필요한데 백엔드에 없는 것

| # | 필요한 것 | 어디서 | 지금 가능한 대안 | 우선 |
|---|---|---|---|---|
| b1 | ~~trip·docent 서비스, agent 선제 턴~~ (구현됨) / agent 가이드 준비·docent 도구 (§0) | 16·17·18·32 | 가짜 게이트웨이 | **필수** |
| b2 | ~~`near_hazard` 의 `payload` 필드~~ **구현됨**: `payload.zones[]` = `{id, name, kind, risk, reason, distance_m}` | 23 패널의 구역명·근거 줄, 지도 강조 | — | 해결 |
| b3 | 선제 메시지 구분 필드 | 19·22 "먼저 알려드려요" | 이벤트 `agent.message_id` 와 메시지 id 매칭 (events.md §3.1). 세션 메시지에 `origin:"event"` 같은 필드가 있으면 더 단순 | 권장 |
| b4 | ~~메모리 갱신을 알리는 SSE 단계~~ **구현됨**: `remember` → Step `kind:"memory"` title "기억하기", `detail` = 저장한 문장 (항목마다 따로, 합치지 않음). `forget` → `forget` "기억 지우기" (agent §3.2) | 18 "기억했어요" 칩 | `memory` 단계가 `done` 이면 칩 "기억했어요 · {detail}" | 해결 |
| b5 | ~~docent 도구들의 Step `kind`·title~~ **구현됨**: `get_guide_note` → `guide` "가이드 노트 확인", `search_passages`·`web_search`·`read_page`·내장 웹 검색 → `research` "자료 찾기", `view_memory` → `recall` "기억 확인" (agent §3.2) | 18 진행 단계 문구 | 서버 title 을 그대로 보여줌 (앱 변경 없음) | 해결 |
| b6 | 답변 출처 (근거 조각·웹 출처) | 18 출처 칩 | 숨김 | 나중 |
| b7 | 한 번에 여러 대안 카드 (장면 ③ "대안 2개") | 19 | 카드 1개 + 텍스트 대안 + 후속 칩 | 나중 |
| b8 | 하루 요약 API | 30 | 앱이 PlanDetail·이벤트·제안·메모리로 계산 | 나중 |
| b9 | ~~가이드 준비 작업 응답 형식~~ **구현됨**: `GuideJobView` = `total`·`finished`·`ready`·`failed`·`current`·`eta_sec`·`places[{place_id, title, status, error, sources, …}]` (agent §12) | 32 진행 바 | 작업 조회 3초마다. 작업이 사라졌으면(404 `job_not_found`, 서버 재시작) 노트 목록으로 | 해결 |
| b10 | safety outlook 응답 필드 이름 (요약·위험 시간대·만조·특보) — 문서에 `safe_windows` 외 필드명이 없음 | 27 | `/safety/docs` 로 확인 후 명세 보완. 시간 띠는 `GET /safety/situation` `hours[].levels` | 권장 |
| b11 | 메모리 일괄 삭제 | 31 모두 지우기 | 항목마다 `DELETE /docent/memory/{id}` | 나중 |
| b12 | 앱 백그라운드 알림 (OS 푸시·백그라운드 위치) | 화면이 꺼진 동안의 지연·안전 | 앱을 켜면 쌓인 이벤트를 정리해 보여줌 (events.md §2) | 나중 |
| b13 | 앱에서 "장소로 즉시 이동"(도착 판정용 머무름 90초 포함) | 33 데모 조작 | 발표는 CLI `trip goto` / `trip replay` 로 위치를 넣고 앱은 데모 위치 모드로 그리기 | 권장 |
| b14 | 혼잡도 데이터 | 기존 디자인의 "혼잡 낮음" 칩, 지도 레이어 | **없음.** 칩은 지우고 레이어는 "데이터 없음"으로 비활성 (28-S1) | — |
| b15 | 날씨 칩(기온) | 기존 디자인의 "18°" | 안전 배지로 대체 (위험 단계 + 근거). 기온은 safety 가 판단에 쓰지 않음 (폭염·한파 미반영, safety §7) | — |
| b16 | 실행 취소 API | 08 스낵바 | 앱이 역 ops 계산 (아래 c9 한계) | 나중 |
| b17 | ~~agent 날짜 표시 시계~~ **구현됨**: agent 도 safety·trip 시계를 따른다 (agent §11) | 데모 가상 시각 | — | 해결 |
| b19 | ~~해설 길이 선호를 구조화한 값~~ **구현됨**: `GET /docent/memory`·`/summary` 의 `style_length: short\|long\|null` (docent §5.1) | 16 [짧게]/[자세히] 강조 | `style_length` 를 그대로 쓴다 (null 이면 선호 없음) | 해결 |
| b18 | 웹 빌드 CORS | — | 모바일·데스크톱으로만 실행 (기존과 같음) | 나중 |

## (c) 기존 앱 코드(`origin/feat/app` 의 `app/`)에서 고칠 것

| # | 위치 | 지금 | 바꿀 것 | 명세 |
|---|---|---|---|---|
| c1 | `main.dart` `title: 'Planit'`, `features/auth/login_screen.dart`, `core/widgets/common.dart`·`features/chat/ai_sheet.dart`·`chat_controller.dart` 의 "AI Planner", app/README 제목 | Planit / AI Planner | **구룡메이트** (시트 헤더·오류 문구 "구룡메이트에 연결하지 못했어요"), 패키지·클래스는 GuryongMate | README §1 |
| c2 | `core/app_session.dart`, `api_client.dart` | 이메일 = `X-User-Id`, SharedPreferences | account 토큰(access·refresh) `flutter_secure_storage`, `Authorization: Bearer`, 401 → refresh 1회 → 01. 서버 주소 설정은 33 으로 | 01·34·33 |
| c3 | `guryong_api.dart` `chat()` · `chat_controller.dart` | SSE `tool_call`·`tool_result` | `status`·`step`(같은 id 갱신)·`steps_done`·`recommendation`·`done{usage}` (agent §3.1) | 05 |
| c4 | `chat_controller.dart` 장소 추천 (06B) | 묶음 제안 reject 후 앱이 `POST …/items` | `recommendation` 카드 + `…/candidates/{cid}/add·remove` (후보 `state` 로 버튼) | 06B |
| c5 | `acceptProposal` | 본문 없음, 409 처리 없음 | `{"mode":"auto"}` → 409 `base_changed` → 36, `repropose`, `overwrite` · 409 `agent_busy`·`proposal_not_pending`·503 `upstream_unavailable` 처리 | 36, README §6.3 |
| c6 | `planner_screen.dart` 칩·배너 | 날씨·혼잡 자리 없음, 배너 = 검증 위반 | 위험 배지 + 참고 칩, 배너 = 안전 경고 > 위험 항목 > 위반 | 25 |
| c7 | `planner_screen.dart` 이동 구간 | 도보/차량 분만 | `leg_mode` 아이콘 + 다른 수단 시간(`travel_options`), `estimate` 면 "약" | 25 |
| c8 | `map_screen.dart` 경로 | 핀 좌표를 직선 `Polyline` | `POST /routing/route` 의 `geometry`([lng,lat] 주의). 레이어 "날씨" → "위험 구역", "혼잡도" → 비활성 "데이터 없음" | 07·28 |
| c9 | `restore_ops.dart` 실행 취소 | 승인 전 일정과의 차이를 ops 로 | 유지하되 한계 명시: 삭제됐던 항목은 새 id(진행 상태 `arrived_at`·`done_at`, 가이드 노트 연결은 place_id 라 유지), 그 사이 다른 변경이 있으면 함께 되돌아감 → 실행 취소는 **승인 직후 스낵바(4초)에서만**, 동행 중에는 done 항목이 바뀌는 되돌리기는 막기 | 08 |
| c10 | `ai_sheet.dart` 빠른 실행 | "일정 문제 해결" (날씨 도구 없음 가정) | agent 에 `check_safety`·`safety_outlook` 이 생겼으므로 "날씨·안전 확인"("Day n 날씨와 안전 확인해줘") 추가. 동행 중에는 "지금 근처 쉴 곳", "남은 일정 줄여줘" | 05 |
| c11 | `planner_shell.dart` 하단 탭 | Planner · Map | 여행 중이면 Today · Planner · Map (Today 기본), FAB Has Suggestion | README §3.3 |
| c12 | `plan_controller.dart` 갱신 | version 기준 | `progress_version` 도 비교 (진행 상태는 version 을 안 올림) | 14 |
| c13 | `features/trip/`, `features/voice/` | 비어 있음 (.gitkeep) | trip: 위치 전송기(30m/30~60초, 끊기면 모아서), 이벤트 폴러, TripState 컨트롤러, 데모 위치 모드. voice 는 이번 범위 밖 | 14, events.md |
| c14 | 03 여행 만들기 "여행 스타일" | `memo` 에 저장 | 유지. 대화에서 확인한 취향은 docent 메모리로 가므로 31 에서 본다 (메모는 Agent 가 일정 컨텍스트로만 봄) | — |
| c15 | 01 디자인의 "Google로 계속하기" | 안내 문구만 | `google_sign_in` → `POST /auth/google` (서버 `ACCOUNT_GOOGLE_CLIENT_IDS` 필요) | 01 |
