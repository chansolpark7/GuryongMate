# 22 Proactive Safety — 안전 상승 → AI 선제 제안 (P0, 장면 ④)

Figma: 화면 `73:3564` · Spec `73:3781` · 흐름 F6 `73:8546`

## 목적
DT·예보 위험 신호를 **위치 + 남은 일정 + 관심사**와 함께 해석해 대안을 제안한다. 근거를 사람 말로 보여주는 것이 핵심("DT 데이터를 행동으로 연결").

## 진입
`safety_raised` 이벤트 (level 3, severity warning/danger). 쿨다운을 무시하고 바로 온다. 시트가 닫혀 있으면 FAB 미리보기(Red 테두리, 사용자가 닫을 때까지 남음).

## 레이아웃 (AI 시트 Full)
| 구역 | 내용 |
|---|---|
| 선제 표시 줄 | ✨ "구룡메이트가 먼저 알려드려요 · HH:MM" + Red shield 칩 "안전 · 위험으로 바뀜"(주의면 Amber "안전 · 주의로 바뀜") |
| 답변 | LLM 설명 |
| **근거 박스** | #FFF8F8, 1px #F8C9CB, r14 · "근거 · HH:MM 발표 예보"(가장 최근 `at`) · hazards 행: 아이콘(type: wind/waves(wave)/rain/drop(flood)/alert(warning)) 16 + reason 13 + 단계 배지 · 출처 줄(hazards[].source 를 중복 없이 " · "로) |
| 답변(대안 설명) | |
| 제안 카드 | 교체 배지 Purple: "16:15 북방파제" · 이전(취소선) → 새 장소(Purple Bold) · 이유 · 영향 박스 "위험한 곳 1 → 0 · HH:MM 종료" · 비교 보기 · [거절][전체 적용] |
| 후속 칩 | "방파제는 내일 아침에 갈게"(better_times 가 있을 때), "실내 말고 다른 곳은?" |

## 데이터
| 무엇 | 출처 |
|---|---|
| 이벤트 | `{type:"safety_raised", level:3, severity, item_ids, message, payload, agent}` — `payload` 는 planner 경고(`SafetyAlertView`)의 내용(changes[{kind, from_risk, to_risk, item: SafetyItem}]). **정확한 payload 필드는 trip 구현에서 확정** → [gaps](../gaps.md) b2/b3 와 같이 확인 |
| 근거 행 | 우선 `payload` 의 SafetyItem.hazards, 없으면 `GET /planner/plans/{id}/safety` 에서 `item_ids` 의 visit hazards (level caution·danger 만, info 는 "참고 n개" 접기) |
| 카드 | 세션 메시지의 proposal |

## 동작
19 와 같음 (승인·거절·비교·칩). 승인 뒤 Today·Planner 배지와 지도 위험 구역 갱신. 같은 경고가 낮아지면 `safety_eased` 토스트.

## 카피 규칙
- "E-UM INT_S05_006 등급 3" 이 아니라 "수위 센서 실측 · 위험". 예보는 "~예보", 실측은 "(실측)".
- 위험을 과장하지 않는다. `risk` 단계 이름(주의·위험)은 배지로만, 문장은 LLM 답변.
