# app — Planit (Flutter)

Figma「Planit」 디자인(01~10)을 구현한 앱. 백엔드 게이트웨이(`:8000`)의 places · planner · agent API에 연결된다.

## 실행

```bash
docker compose up -d --build          # 저장소 루트에서 백엔드 (docs/COLLABORATION.md)
cd app
flutter pub get
flutter run -d windows                # Windows 데스크톱 (개발자 모드 필요: 플러그인 심볼릭 링크)
flutter run -d emulator-5554          # Android 에뮬레이터 (Android SDK 36 필요)
flutter test                          # 단위 + 화면 흐름 테스트 (가짜 게이트웨이, 서버 없이 돈다)

# 실제 백엔드 (docker compose up 상태, 게이트웨이 :8000)
flutter test test/live_backend_test.dart --dart-define=LIVE_API=http://127.0.0.1:8000   # API 계층
flutter test integration_test/app_flow_test.dart -d windows   # 실제 앱 화면을 눌러 보는 시나리오, 캡처: build/screens/
```

- WSL 의 Docker 로 백엔드를 띄웠다면 WSL 이 유휴 상태로 꺼지지 않게 Ubuntu 터미널을 하나 열어 두거나 `wsl -d Ubuntu -- sleep infinity` 를 띄워 둔다 (꺼지면 컨테이너도 내려간다).

- 로그인은 계정 서비스(account)가 생기기 전까지 **이메일 = 사용자 ID**다. 요청마다 `X-User-Id` 헤더로 보낸다.
- 서버 주소는 로그인 화면 → "이메일로 계속하기" → "서버 설정"에서 바꾼다. 기본값은 Android 에뮬레이터 `http://10.0.2.2:8000`, 그 외 `http://127.0.0.1:8000`.
- 웹 빌드는 백엔드에 CORS 설정이 없어 API 호출이 막힌다. 모바일·데스크톱으로 실행한다.
- 지도는 `flutter_map` + OpenStreetMap 타일이라 키가 필요 없다. 공용 OSM 타일 서버는 데모용으로만 쓴다(사용 정책). 지도 제공자가 정해지면 `map_screen.dart`의 `TileLayer`만 바꾸면 된다.

## 구조

```
lib/
├─ main.dart                     로그인 여부로 01 / 02 전환
├─ core/
│  ├─ api/api_client.dart        HTTP · 오류 메시지 · SSE 파서
│  ├─ api/guryong_api.dart       places · planner · agent 엔드포인트
│  ├─ models/                    백엔드 스키마(Pydantic)와 같은 모양의 모델
│  ├─ app_session.dart           사용자 ID · 서버 주소 (SharedPreferences)
│  ├─ theme/app_theme.dart       Figma Foundations 토큰 (색 · 타이포 · 그림자)
│  └─ widgets/common.dart        버튼 · 칩 · AI FAB · 카드
└─ features/
   ├─ auth/                      01 Login
   ├─ plans/                     02 Plan List · 03 Create Plan
   ├─ planner/                   04 Planner · 08 After Apply · 일정 편집 · 장소 검색/상세
   │  ├─ plan_controller.dart    계획 상태 (Planner · Map 공유: Day · 선택 항목 · AI 변경 표시)
   │  └─ restore_ops.dart        실행 취소용 ops 생성
   ├─ chat/                      05 AI 시트 · 06 변경 제안 카드 · 06B 장소 추천 카드
   │  └─ chat_controller.dart    agent 세션 · SSE 스트림 · 승인/거절
   ├─ map/                       07 Map · 09 After Apply · 10 Layers
   ├─ trip/  voice/              (예정) 여행 중 상태 · 음성
```

## 화면 ↔ API

| 화면 · 버튼 | 호출 |
|---|---|
| 02 목록 · D-day · "AI가 일정 N개를 제안했어요" | `GET /planner/plans`, `GET /planner/plans/{id}/proposals?status=pending` |
| 02 길게 누르기 → 복제 / 삭제 | `POST /planner/plans` + `POST …/ops` / `DELETE /planner/plans/{id}` |
| 03 여행 만들기 | `POST /planner/plans` (지역 · 여행 스타일은 `memo`) |
| 02 AI 프로모 → 03 "AI로 계획 만들기" | 계획 생성 후 AI 시트를 열고 조건 문장을 자동 전송 |
| 04 타임라인 · 위반 배너 · 칩 | `GET /planner/plans/{id}` (`days[].items`, `violations`) |
| 04 드래그 순서 변경 · ⋮ 시간 고정/체류/메모/날짜 이동/건너뛰기 | `PATCH /planner/plans/{id}/items/{item}` |
| 04 ⋮ 삭제 · 지도 카드 삭제 | `DELETE /planner/plans/{id}/items/{item}` |
| 04 ⋮ 꼭 유지하기 · ⋯ 여행 조건 | `POST/DELETE /planner/plans/{id}/constraints` |
| 04 ⋯ 여행 정보 수정 | `PATCH /planner/plans/{id}` |
| 04 "일정 추가" → 장소 검색 · 상세 · 오디오 가이드 이야기 | `GET /places?mode=hybrid`, `GET /places/{id}`, `GET /places/{id}/stories` → `POST …/items` |
| 05 AI 시트 열기 | `GET /agent/sessions?plan_id=` (없으면 `POST /agent/sessions`), `GET /agent/sessions/{sid}` |
| 05 빠른 실행 · 입력 · 후속 요청 칩 · 지도 "교체" | `POST /agent/sessions/{sid}/chat` (SSE: tool_call → 진행 단계, text → 답변, proposal → 카드) |
| 05 Stop | 스트림 연결을 끊는다 (서버가 턴을 정리) |
| 06 전체 적용 / 거절 | `POST /agent/sessions/{sid}/proposals/{pid}/accept` / `reject` |
| 06B 일정에 추가 / 취소 | 묶음 제안 `reject` 후 고른 장소만 `POST …/items` / `DELETE …/items/{id}` |
| 08 스낵바 "실행 취소" | 승인 전 계획과의 차이를 ops로 만들어 `POST /planner/plans/{id}/ops` |
| 08 배너 "이력" | `GET /planner/plans/{id}/proposals` |
| 10 관광지 레이어 | `GET /places?lat=&lng=&radius_m=` |

## 디자인과 다르게 한 것 (백엔드에 아직 없는 기능)

- **날씨 · 혼잡도 칩, 날씨 배너**: 데이터 API가 없다. 배너 자리에는 planner 검증 위반(운영시간, 종료 시각, 이동 시간 상한 등)을 보여 주고, "AI로 조정"을 누르면 그 내용으로 AI 요청이 채워진다. 지도 레이어의 혼잡도 · 날씨는 "준비 중"으로 비활성.
- **빠른 실행 "날씨에 맞게 조정"** → "일정 문제 해결"로 바꿨다 (agent에 날씨 도구가 없음).
- **06B 별점**: places 데이터에 평점이 없어 분류 · 이동 시간만 표시.
- **Google 로그인 · 음성 버튼**: account 서비스 · 음성 기능이 생기면 연결. 지금은 안내 문구만 띄운다.
- **실행 취소**: 백엔드에 undo가 없어 앱이 되돌리는 ops를 계산한다. 삭제됐던 항목은 새 id로 다시 만들어진다.
- 03에 "하루 일정 시간", "이동 수단"을 추가했다. planner 계획의 필수 정보(`day_start`/`day_end`/`travel_mode`)라 일정 계산에 바로 쓰인다.
