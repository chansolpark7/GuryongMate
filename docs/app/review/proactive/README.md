# 선제 메시지 카드 확인 (2026-10-12, `feat/proactive-bubble`)

Agent 가 먼저 말을 건 메시지(선제 턴)가 앞 답변에 이어붙은 것처럼 보이던 문제 → 서버가 메시지에 `trigger` 를 남기고(docs/agent/README.md SessionDetail),
앱은 그 메시지를 별도 카드로 그린다 (docs/app/events.md §3.1).

확인 환경: worktree 스택(`API_PORT=8010`, `AGENT_PROVIDER=scripted` — LLM 비용 0, `SAFETY_SIM_ENABLED=true`),
`run_demo.py --plan prebuilt --fast-to s3_delay` 로 여행 시작·가옥거리 도착·대화 3턴까지 만든 뒤 관제 대시보드(`/console/`)로 조작, 웹 앱(`deploy/build_web.sh`) 420×860.

| 파일 | 내용 |
|---|---|
| `01-boundary.jpg` | 대시보드 [+30분]×3 → 14:05 `delay`(violations) 선제 턴. 앞 턴(기억 칩) 아래로 넉넉히 떨어진 카드, "구룡메이트가 먼저 알려드려요 · 14:05" + "일정 지연" 칩 |
| `02-card-with-proposal.jpg` | 같은 카드 안에 답변·제안 카드 |
| `03-after-reload.jpg` | 새로고침 뒤 다시 열어도 카드 (서버 trigger) |
| `04-safety-card.jpg` | 대시보드 폭풍(계속) → 14:08 `safety_raised` 선제 턴. "안전 · 위험으로 바뀜"(danger), 근거 박스가 카드 안 |
| `05-safety-card-proposal-chips.jpg` | 제안 카드·후속 칩도 카드 안 |
| `06-two-cards-after-reload.jpg` | 새로고침 뒤: 연이은 선제 메시지 두 개가 각각 카드 (앞 제안은 "새 제안으로 대체됨") |

API 로 본 메시지: `{"kind": "event", "type": "delay", "event_id": 201, "reason": "violations", "at": "2026-10-10T14:05:17…+09:00"}`, 일반 대화 메시지는 `trigger: null`.
