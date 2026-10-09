# 기존 화면 (00~10) — 바뀌는 부분만

기존 Figma 프레임은 그대로 두고, 바뀐 디자인은 새 화면(25·28-S1·29·34)으로 그렸다. 코드 쪽 할 일은 [gaps.md](../gaps.md) (c).

## 01 Login
Figma `21:1964` · Spec `21:2331` (Figma 에서 이름·버튼 문구를 고침)
- 앱 이름 "Planit" → **구룡메이트**, 태그라인 "여행을 함께 걷는 AI", 부제 "구룡포 여행 계획부터 현장 안내까지".
- [이메일로 계속하기] → **[아이디로 계속하기]** → 34. 가입은 34 의 링크 → 35.
- [Google로 계속하기] → `google_sign_in` ID 토큰 → `POST /auth/google {id_token}` → `TokenResponse` (`created:true` 면 첫 로그인).
- 서버 주소 설정은 하단 작은 링크 "서버 설정"(33 의 서버 주소 줄만) — 로그인 전에도 바꿀 수 있어야 해서.
- 앱 시작: 저장된 refresh 토큰이 있으면 01 을 건너뛰고 `POST /auth/refresh` → 02 (실패하면 01).

## 02 Plan List
Figma `21:1827` → 개정 **29** (`73:5305`). 여행 중 카드, 안전 배지, 계정 버튼.

## 04 Planner
Figma `29:4924` → 개정 **25** (`73:4137`). 날씨·혼잡 칩 → 위험 배지, 날씨 배너 → 안전 경고 배너, 구간 수단, 동행 중 진행 표시.
- ⋯ 메뉴에 추가: "현장 가이드 준비"(→ 32), "날씨·바다 전망"(→ 27), 동행 중이면 "여행 종료", 여행일이면 "여행 시작"(→ 11).

## 05 AI Bottom Sheet
Figma `29:4550` · Spec `29:5405` (헤더 문구를 "구룡메이트"로 고침)
- 헤더 "AI Planner" → **구룡메이트**. 컨텍스트: 계획 모드 "{계획명} · Day n" / 동행 모드 "{계획명} · 동행 중" / 장소에 있으면 "{장소} · 현장 해설".
- SSE 처리 교체 (agent §3.1): `status` → 진행 카드 제목 한 줄(새로 오면 교체) · `step` → 같은 `id` 는 그 줄 갱신, 새 `id` 는 추가 (`running` 스피너 Purple / `done` Green ✓ + `detail` / `error` Red + `detail`) · `steps_done` → 진행 카드를 "n단계 완료 : …" 한 줄로 접기 · `text` → 답변 · `proposal`·`recommendation` → 카드 · `done` → 종료 · `error` → 오류 문구.
- 빠른 실행: "날씨·안전 확인"(새) 추가. 동행 중 빠른 실행: "남은 일정 줄여줘", "근처 쉴 곳", "지금 출발해도 괜찮아?"(Agent `travel_time` check_hazards).
- 선제 메시지 표시 줄 (events.md §3.1), 도착 카드 삽입(16), 메모리 칩(18).
- 응답 중 409 `agent_busy` 스낵바, 503 `upstream_unavailable` → 답변 자리 오류 + 다시 시도.

## 06 AI Change Preview
Figma `29:4144` · Spec `29:5372`
- 카드는 그대로. 추가: 종료 시각 행(`day_end`), 규칙 배지(`rule_added`), "자세히 보기 ›" → **"비교 보기 ›"**(21). 상태 Superseded(20 의 접힘 카드).
- [전체 적용]은 `{"mode":"auto"}` 를 보내고 409 `base_changed` 를 36 으로.
- 안내 문구 "적용 전까지 Planner는 바뀌지 않아요" → "적용 전까지 일정은 바뀌지 않아요" (탭 이름이 아니라 일정).

## 06B 장소 추천
Figma `29:3719` · Spec `29:5344`
- 디자인은 유지. 데이터를 agent 추천 카드로: SSE `recommendation` (RecommendationView) → 후보마다 `state` 로 버튼: `not_in_plan` [일정에 추가] / `in_plan` [✓ 추가됨 · 취소] / `day_out_of_range` 버튼 없음.
- [일정에 추가] `POST /agent/sessions/{sid}/recommendations/{rid}/candidates/{cid}/add` · [취소] `…/remove` → 응답의 `recommendation` 으로 카드를 다시 그림 + `plan` 으로 Planner 갱신. 409 `already_in_plan`/`not_in_plan` 은 카드 갱신만.
- 별점은 데이터가 없어 계속 빼고, `reason` 과 이동 시간을 보여준다.

## 07-09-10 Map
Figma `21:569` · `21:149` · `21:26`
- 경로선: 핀 직선 → `POST /routing/route` 의 `geometry` (GeoJSON [lng, lat]). 차량 구간 점선. `estimate` 면 직선 + 요약에 "약".
- 장소 카드 칩: 18°·혼잡 → 위험 배지 + "다음 장소 {수단} n분".
- 10 레이어: "날씨" → **위험 구역**, "혼잡도" → 비활성 "데이터 없음" (28-S1 `73:7999`). 위험 구역 레이어 = 28.
- 동행 중이면 현재 위치 점 + 다녀온 구간 회색 (14 와 같은 규칙).
