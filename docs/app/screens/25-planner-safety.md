# 25 Planner Safety — 04 Planner 개정: 위험 단계·근거·구간 수단 (P0)

Figma: 화면 `73:4137` · Spec `73:4366` · 기존 04 `29:4924` · 컴포넌트 Risk Badge / Context Banner · Safety / Timeline Event Card · Safety / Travel Segment

## 목적
기존 디자인의 날씨·혼잡 칩과 "15시 비 60%" 배너는 데이터가 없어 빠져 있었다. 이 자리를 **safety 판단(위험 3단계 + 근거)** 으로 채운다. 계획 모드·동행 모드 공통 화면.

## 04 에서 바뀌는 것
| 04 구역 | 25 |
|---|---|
| Day Selector 오른쪽 빈 공간 | "🛡 안전 점검 · 예보 기준 HH:MM ›" (`checked_at`) → 27 |
| Context Banner (날씨 경고) | **안전 경고 배너** (아래 우선순위) |
| 카드 칩: 18° · 혼잡 낮음 · 15시 비 60% | **위험 배지 1개** + (있으면) 회색 참고 칩 1개. 혼잡 칩 삭제 |
| Warning 카드 변형 | caution = Amber 테두리 #FBE1B5, danger = Red 테두리 #F8C9CB, 노드 원 색도 같이 |
| Travel Segment "도보 8분" | "🚶 도보 8분 · 차량 6분"(다른 수단은 회색), 경로 주의면 Amber |
| (동행 중) | 다녀온 카드: 제목 회색 + Green "다녀옴 HH:MM–HH:MM", 노드 Green 채움 · 지금 카드: Primary 2px 테두리 + "지금 여기" |

## 위험 배지 (Risk Badge)
| 값 | 배지 | 색 |
|---|---|---|
| `risk:"safe"` | "안전" (실내면 "안전 · 실내" — `sensitivity` 에 "실내") | Green #12A150 / #E7F6EE, shield-check |
| `risk:"caution"` | "주의 · {근거}" | Amber #D97706 / #FFF4E0, alert |
| `risk:"danger"` | "위험 · {근거}" | Red #E5484D / #FDECEC, alert |
| `risk:null`, `unassessed_reason:"beyond_forecast"` | "예보 전 · MM/DD부터" (`recheck_from`) | 회색 #4B5563 / #F3F4F6, shield-question |
| `risk:null`, `"data_missing"` | "판단 불가 · 데이터 없음" | 회색 |
| `risk:null`, `"past"` | 배지 숨김 | — |
| safe + hazards 에 info | 배지 옆 회색 칩 "참고 · {짧은 reason}" (rain 아이콘) | 회색 |

근거 = caution·danger hazards 중 가장 높은 것의 짧은 값: wind "풍속 15m/s", wave "파고 3.2m", rain "시간당 10mm", flood "수위 등급 2", warning "풍랑주의보". 서버 reason 에서 숫자+단위를 뽑고, 못 뽑으면 type 이름("바람"·"파도"·"비"·"침수"·"특보").
**`risk=null` 은 절대 Green 이 아니다.**

## Context Banner 우선순위
1. 안전 경고 new: `GET /planner/plans/{id}/safety/alerts?status=new` 중 `needs_action` — "{장소} {HH:MM} {단계} · {근거}"
2. 오늘~예보 범위의 caution/danger 항목 중 가장 이른 것
3. 검증 위반(error) — 기존 04 동작 ("바다뷰 카페가 18:00 이후에 끝나요")
4. 없음 → 배너 숨김
- 오른쪽 "✨ AI로 조정" → AI 시트 + 자동 전송 "{장소} 일정을 안전하게 조정해줘" (위반이면 기존 "일정에 있는 경고를 해결해줘")
- 해제(`lowered`/`cleared` 경고)는 Green Eased 배너 4초 후 사라짐. 배너를 보이면 경고 `POST …/alerts/{id}/seen`.
- 색: danger #FDECEC / caution·위반 #FFF4E0 / eased #E7F6EE.

## 데이터
| 무엇 | API |
|---|---|
| 일정 | `GET /planner/plans/{id}` — ItemView `leg_mode`, `travel_minutes`, `travel_source`, `travel_options{walk,car}`, `status`, `arrived_at`, `done_at` |
| 안전 | `GET /planner/plans/{id}/safety` → `items[]` 를 `(item_id, kind)` 로 매칭: `visit` → 카드 배지, `travel` → 그 카드 위 Travel Segment |
| 경고 | `GET /planner/plans/{id}/safety/alerts?status=new` |
| 갱신 | 화면 진입, `version` 변경, (동행) `safety_*` 이벤트. `checked_at` 이 10분 넘으면 다시 |

## 상태
- 안전 로딩: 배지 자리 회색 스켈레톤 칩.
- 503 `safety_unavailable`: 배지 숨김 + 헤더 줄 "안전 정보를 불러오지 못했어요". `data_ok` 에 false 가 있으면 헤더 줄에 "일부 데이터 없음".
- 먼 여행(모든 항목 beyond_forecast): 헤더 줄 "날씨·바다 판단은 MM/DD부터 해요".

## 동작
| 동작 | 결과 |
|---|---|
| 배지 탭 | 26 근거 시트 |
| Travel Segment 탭 | 수단 바꾸기 시트: 도보 n분 / 차량 m분 → `PATCH /planner/plans/{id}/items/{item} {"travel_mode"}` (직접 편집, 즉시) |
| "안전 점검 ›" | 27 |
| 나머지 | 04 와 같음 (드래그 순서, ⋮ 메뉴, 일정 추가, FAB) |
