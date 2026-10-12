# 24 Event Inbox — 알림 목록 (P1)

Figma: 화면 `73:3974` · Spec `73:4113`

- **목적**: 지나간 알림(토스트·카드·경고)을 다시 보고, 처리 결과를 확인한다.
- **진입**: 14 bell 버튼(배지 = seen 안 된 level ≥ 1 이벤트 수), 앱 상단 미리보기 배너.
- **레이아웃**: App Bar "알림 / 오늘 · {계획명}" · 그룹 "새 알림"(seen 전) / "오늘" · 행 358×76 r16: 아이콘 박스 40(타입 색) · 제목 14 Bold(`message`) · "HH:MM · 상태" 12 · 오른쪽 동작 칩 · 새 알림 점 6 Primary.
- **데이터**: `GET /trip/plans/{id}/events?after=0&limit=200` 를 가득 차면 이어 읽어 전부, 최신순. 오늘 여행일(이벤트 `day` = state `day`) 것만. level 0(`day_started`·`day_ended`), `suppressed` 는 숨김 (audit-2026-10-12 F6·F7).

| type | 아이콘·색 | 상태 문구 / 동작 칩 |
|---|---|---|
| safety_raised | shield · Red | 제안 상태: pending [제안 보기](Purple) / accepted "승인함" / rejected "거절함" / superseded "새 제안으로 바뀜" (`GET /planner/plans/{id}/proposals/{agent.proposal_id}`) |
| delay | clock · Amber | 같음 |
| near_hazard | alert · Red | [지도] |
| arrived | pin · Blue | 해설을 열었으면 "해설 들음 · 짧게"(로컬), 아니면 [해설 듣기] |
| departed | walk · 회색 | — |
| safety_eased | shield-check · Green | — |

- **동작**: level 3 행 → AI 시트 + `agent.message_id` 로 스크롤 · arrived → 17 · near_hazard → 28 · 화면에 보인 행은 `POST /trip/plans/{id}/events/{event_id}/seen`.
- **빈 상태**: "아직 알림이 없어요. 도착하거나 일정에 변화가 생기면 여기에 모아요."
