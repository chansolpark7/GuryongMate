# 32 Guide Prep — 가이드 준비 상태 (P1)

Figma: 화면 `73:5746` · Spec `73:5880` · 흐름 F10

- **목적**: 여행 전에 장소별 맞춤 해설을 미리 만들어 둔다("준비는 여행 전에, 전달은 여행 중에" — docent).
- **진입**: Planner ⋯ "현장 가이드 준비", 11 시트의 "아직 준비 안 된 n곳" 줄, (선택) 여행 전날 Planner 배너.
- **레이아웃**: App Bar "현장 가이드 준비 / {계획명}" · 요약 카드: "n곳 중 m곳 준비됐어요" 16 Bold + 진행 바(Purple on #E2DCFF) + ✨ 설명 12 + "준비 중 · 약 n분 남음" · 장소 행 358×64: 순서 원 32 · 이름 14 Bold · 상태 문구 12 · 상태 칩 · 안내 "출처를 찾지 못한 곳은 지어내지 않아요…" · 하단 버튼.

| status | 칩 | 문구 |
|---|---|---|
| pending | 회색 "대기" | 대기 |
| preparing | Purple "준비 중" (회전) | 자료를 찾는 중 |
| ready | Green "준비됨" | 준비됨 · 출처 n개 |
| failed | Red "다시 시도" | 자료를 찾지 못했어요 |
| stale | Amber "다시 준비" | 관심사가 바뀌었어요 |

- **데이터**: `GET /docent/plans/{plan_id}/notes` (일정에 있는 장소만 보여줌 — 빠진 장소 노트는 서버에 남지만 숨김). 진행: `POST /agent/plans/{plan_id}/guide/prepare` → 202 `{job_id}` → `GET /agent/plans/{plan_id}/guide/jobs/{job_id}` 3초. 진행률·남은 시간 필드는 미정([gaps](../gaps.md) b9) → 없으면 노트 목록을 3초마다 다시 읽어 ready 수로 바를 그리고 "약 n분"은 (남은 곳 × 20초)로 어림.
- **버튼**: [가이드 준비](Purple, 준비 안 된 곳이 있을 때) / [준비 중…](비활성) / 모두 ready 면 [다시 준비](Secondary, stale 이 있을 때만).
- **동작**: failed/stale 칩 → 같은 prepare · ready 행 → 17 미리보기.
