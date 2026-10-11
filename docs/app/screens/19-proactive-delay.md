# 19 Proactive Delay — 지연 → AI 선제 메시지 + 제안 (P0, 장면 ③)

Figma: 화면 `73:2877` · Spec `73:3086` · 대화 밖 알림 14-S3 `73:7529` · 흐름 F5 `73:8445`

## 목적
사용자가 묻지 않았는데 구룡메이트가 먼저 말을 건다. 지연 때문에 **실제로 문제가 생겼을 때만**(새 위반) 영향을 설명하고 대안과 제안 카드를 낸다.

## 진입
`delay` 이벤트 (level 3, `agent{session_id, message_id, proposal_id}` 가 채워진 뒤).
- 시트가 열려 있으면 바로 그 메시지로 스크롤.
- 닫혀 있으면 14-S3: FAB Has Suggestion + 미리보기 말풍선 → 탭 → 시트 Full.

## 레이아웃 (AI 시트 Full)
| 구역 | 내용 |
|---|---|
| 헤더 | 구룡메이트 · "{계획명} · 동행 중" |
| 선제 표시 줄 h28 | ✨ "구룡메이트가 먼저 알려드려요" 12 Bold Purple · "· HH:MM"(이벤트 `now`) · 오른쪽 이벤트 칩 Amber clock "일정 지연" |
| 답변 | 영향 설명(어느 장소가 몇 시에 끝나 어떤 규칙을 넘는지) + 대안 ①② 텍스트 — LLM 답변 그대로 |
| 제안 카드 | 06 AI Change Card 확장: 헤더 "✨ 일정 변경 제안 · 변경 n건" · Change Item(타입 배지 + 장소 + 이유) · **종료 시각 행**(`changes` 중 kind `day_end`: 이전 취소선 → 이후 Purple Bold) · 영향 박스(이동 증감, "18:00 전 종료 ✓") · "적용 전까지 일정은 바뀌지 않아요 · 비교 보기 ›" · [거절 96][✓ 전체 적용] |
| 후속 칩 | 대안 선택·제약 표현 ("과메기문화관은 유지해줘", "②번으로 해줘", "그대로 둘게요") — 서버가 주지 않으므로 앱 고정 문구가 아니라 **답변에 나온 대안 개수에 맞춘 기본 칩 + "그대로 둘게요"** (기본 칩: 카드에서 삭제되는 장소가 있으면 "{장소}은 유지해줘") |
| Input | |

## 데이터
| 무엇 | 출처 |
|---|---|
| 이벤트 | `GET /trip/plans/{id}/events?after=` → `{type:"delay", level:3, severity, now, item_ids, message, payload:{new_violations, delay_minutes}, agent}` |
| 메시지·카드 | `GET /agent/sessions/{agent.session_id}` → `messages[]` 중 id = `agent.message_id` (선제 표시 줄), 그 메시지 `cards[]` 의 proposal |
| 카드 | ProposalDetail `changes[{kind, message, item_id, day}]`, `before`/`after`, `status`, `base_changed` |
| 영향 박스 | before/after 의 이동 합계(`travel_minutes` 합) 차이, 그날 `end_at`, after 의 `violations` 에 error 가 없으면 "규칙 지킴" |

## 동작
| 동작 | API | 결과 |
|---|---|---|
| 전체 적용 | `POST /agent/sessions/{sid}/proposals/{pid}/accept {"mode":"auto"}` | 200 → 카드 Applied("✓ n건 적용됨 · 실행 취소") + 08 스낵바, Today·Planner·지도 갱신 · 409 `base_changed` → 36 |
| 거절 | `…/reject` | 카드 Rejected (70% 투명 + "다시 보기") · 같은 원인으로 다시 오지 않음 |
| 비교 보기 | — | 21 (21-S1 지연 버전) |
| 후속 칩 / 입력 | `POST …/chat` | 20 |
| 미리보기 무시 | — | 점은 제안이 pending 인 동안 유지 |

## 엣지
- `agent` 가 아직 null(`queued`): 미리보기 문구는 이벤트 `message`, 탭하면 시트에 "구룡메이트가 대안을 준비하고 있어요" 진행 줄 → 2초 폴링으로 채워지면 교체.
- 선제 턴이 제안 없이 끝날 수 있다(대안이 없거나 설명만): 카드 없이 답변만.
- 같은 세션에서 사용자가 응답 중이면 서버가 큐에 넣는다 → 사용자 턴이 끝난 뒤 온다.
