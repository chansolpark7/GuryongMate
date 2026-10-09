# 29 Plan List Status — 02 개정: 여행 중 · 안전 배지 (P1)

Figma: 화면 `73:5305` · Spec `73:5429` · 기존 02 `21:1827`

- **02 에서 바뀌는 것**: 상단에 계정 버튼(→ 33) · "지금 여행 중" 섹션 · 계획 카드의 안전 배지/예보 전 안내 · 프로모 문구 "구룡메이트가 일정 초안을 만들어요".
- **여행 중 카드**: Green 테두리 #CDEFD9 r20 · nav 아이콘 박스 Green · 제목 + "● 여행 중" 칩 · "지금 {current} · 다음 {next.title}" · 남은 일정 위험(overall) + "HH:MM 종료" · [동행 화면 열기] Primary → 14.
  - 데이터: 시작일 ≤ 오늘 ≤ 종료일인 계획만 `GET /trip/plans/{id}/state` (`active`). 여러 개면 가장 최근 `started_at`.
  - 여행일인데 시작 전이면 같은 자리에 "오늘 여행" 카드 + [오늘 여행 시작] → 11.
- **다가오는 카드**:
  - 시작일이 오늘~3일 안: `GET /planner/plans/{id}/safety` → `overall_risk` 배지 + 주의·위험 수("주의 · 1곳"). null → 회색 "판단 불가".
  - 3일 넘게 남음: API 를 부르지 않고 회색 칩 "날씨는 MM/DD부터 확인"(시작일 − 3일).
  - new 경고(`GET …/safety/alerts?status=new`)가 있으면 배지 오른쪽 위 점.
  - AI 제안 수: `GET …/proposals?status=pending` (기존).
- **목록 갱신**: 화면 진입·당겨서 새로고침. 안전 호출은 카드가 보일 때만(지연 로딩).
