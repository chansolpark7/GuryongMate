# 20 Proposal Revised — 제약 반영 다시 제안 (P0, 장면 ③)

Figma: 화면 `73:3115` · Spec `73:3336` · 흐름 F5

## 목적
사용자 제약("과메기문화관은 유지")을 반영한 새 안이 이전 안을 **대체**했다는 것을 분명히 보여준다.

## 진입
19 의 후속 칩·입력 → 새 턴.

## 레이아웃
| 구역 | 내용 |
|---|---|
| 이전 카드 | **Superseded** 접힘: 흰 바탕 1px 점선 #D9DDE3 r16 한 줄 — ✨(회색) "이전 제안 · {요약}" + 회색 칩 "새 제안으로 대체됨" (탭 → 펼쳐서 읽기 전용) |
| 사용자 말풍선 | "과메기문화관은 유지해줘" |
| 단계 요약 | "3단계 완료 : 조건 반영, 일정 조정, 변경안 만들기" (서버 `steps_done.summary`) |
| 답변 | |
| 새 카드 | Change Item 타입 배지: **규칙**(Purple, `rule_added`) "구룡포과메기문화관 꼭 포함" · **삭제**(Red) 바다뷰 카페 · **변경**(Blue, `updated`) 구룡포항 머무는 시간 60분 → 45분 · 종료 행 18:07 → 16:55 · "18:00 전 종료 ✓" |

## 타입 배지 규칙 (`Change.kind` → 배지)
| kind | 배지 | 색 |
|---|---|---|
| added | 추가 | Green |
| removed | 삭제 | Red |
| moved | 이동 | Blue |
| updated | 변경 (같은 장소가 다른 장소로 바뀐 것처럼 보이면 교체 — 같은 order 에서 removed+added 쌍. 같은 `place_id` 의 removed+added 는 자리와 상관없이 "이동" 한 줄: 이전 시각 → 새 시각, audit-2026-10-12 F10) | Blue / Purple(교체) |
| rule_added · rule_removed | 규칙 | Purple |
| plan | 설정 | Blue |
| day_end | 카드 아래 "종료" 행으로 따로 | — |
문구는 `Change.message` 에서 접두사("추가: ")를 뗀 장소·값을 쓰고, 이전·이후 값은 before/after 에서 찾는다.

## API
- 새 턴: `POST /agent/sessions/{sid}/chat` → SSE `proposal`(새 ProposalDetail). 이전 제안은 서버가 `superseded` 로 바꾼다 → 세션 다시 조회(`GET /agent/sessions/{sid}`) 때 카드 상태로 확인.
- 승인·거절·비교 = 19 와 같다.
