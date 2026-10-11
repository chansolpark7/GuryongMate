# 36 Base Changed — 제안 이후 일정이 바뀜 (P1)

Figma: 화면 `73:6261` · Spec `73:6337` · 흐름 F2

- **언제**: 카드 [전체 적용] → `POST /agent/sessions/{sid}/proposals/{pid}/accept {"mode":"auto"}` → 409 `base_changed {base_version, current_version, since_proposal[], overwrite_effect[]}` (agent §4.1, planner §5.4). 동행 중 도착·완료만으로는 생기지 않는다(`progress_version`).
- **레이아웃**: 시트 위 Scrim + Dialog 334 r20 · "이 제안 이후 일정이 바뀌었어요" 17 Bold · "카드에서 본 일정 그대로 반영하면 아래 변경이 사라져요." 13 · "사라지는 변경" 박스(#F5F6F8): `since_proposal` 의 Change 마다 kind 배지 + `message`(접두사 뗀 것) · 버튼 세로: [✨ 현재 일정에 맞춰 다시 제안] Purple(AI 작업) / [카드에서 본 일정 그대로 반영] Secondary / [취소] 텍스트.
- **동작**:

| 버튼 | API | 결과 |
|---|---|---|
| 다시 제안 | `POST …/proposals/{pid}/repropose` (SSE, 05 와 같은 이벤트) | 대화에 "이전 제안을 현재 일정에 맞춰 다시 만들어줘." + 새 카드, 이전 카드 superseded. 409 `proposal_not_pending` 이면 카드 다시 그림 |
| 그대로 반영 | 같은 accept `{"mode":"overwrite"}` | 200 → Applied. 진행 상태(도착·완료)는 서버가 유지 |
| 취소 | — | 카드 pending 유지 |

- `overwrite_effect` 는 "자세히"(접힘)로 "그대로 반영하면 지금 일정에서 바뀌는 것"을 보여준다 (P2).
- `since_proposal` 이 비면 "다른 곳에서 일정이 바뀌었어요" 한 줄.
