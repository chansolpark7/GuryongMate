# 30 Trip End — 여행 종료 · 하루 요약 (P1)

Figma: 화면 `73:5458` · Spec `73:5579` · 확인 다이얼로그 States_Feedback `73:7102` · 흐름 F9

- **진입**: 14 패널 "여행 종료" / Planner ⋯ → 다이얼로그 "여행을 마칠까요? 마쳐도 일정과 기록은 그대로 남아요." [계속 여행][마치기] → `POST /trip/plans/{id}/end` → 이 화면(풀스크린 모달). 자동 종료(`ended_reason:"auto"`) 뒤 처음 앱을 열 때 한 번.
- **레이아웃**: 닫기 · "오늘 여행을 마쳤어요" 24 · "{계획명} · MM/DD (요일) {첫 arrived_at} – {마지막 done_at}" · 지표 2×2(171×72 r16): 다녀온 곳 / 이동(수단) / 들은 해설 / 함께 바꾼 일정 · "실제 동선"(✓ + 실제 시각 + 장소 + 메모: 예정보다 길면 "+n분", 제안으로 들어온 곳 "바꿈") · "구룡메이트가 도운 순간"(level ≥ 1 이벤트: 지연·안전·위험 구역, 결과 한 줄) · 기억한 것 칩 · [대화 이어가기][계획 목록으로].
- **데이터 (요약 API 없음 → 앱 계산, [gaps](../gaps.md) b8)**:
  - 실제 동선·다녀온 곳: PlanDetail 그날 항목 `arrived_at`·`done_at` (`status` done/visiting)
  - 이동: done 항목들의 `travel_minutes` 합 (`leg_mode` 별)
  - 들은 해설: 앱 로컬 기록 (17 을 연 장소 수)
  - 함께 바꾼 일정: `GET /planner/plans/{id}/proposals?status=accepted` 중 `decided_at` 이 이번 여행일을 시작한 뒤 (계획을 만든 제안은 빼고, audit-2026-10-12 F3).
    `decided_at` 은 실제 시각이고 `started_at` 은 서버 시계(가상 시각일 수 있음)라, 시작 시각은 그날 `day_started` 이벤트의 `created_at`(실제 시각) → 실제 시계(`clock: system`)면 `now − started_at` → 모르면 18시간 전 순으로 정한다.
  - 도운 순간: `GET /trip/plans/{id}/events` (끝까지 넘겨 읽음) 중 이번 여행일(`day`) 의 delay·safety_raised·near_hazard (+ 제안 결과)
  - 기억한 것: `GET /docent/memory?plan_id=` 중 `created_at` 오늘
- **실수로 시작**(다녀온 곳 0): 지표 없이 "기록된 방문이 없어요. 일정은 그대로예요." + [계획 목록으로].
