# 15 Today Moving — 출발 → 다음 장소 안내 (P1)

Figma: 화면 `73:2240` · Spec `72:1061` · 흐름 F7 1단계, F8

## 목적
장소를 떠나면 다음 장소를 알려주고, 도보는 "경로선 + 다음 안내 한 줄"로 길을 안내한다 (PROJECT §5 M2). 정밀 차량 길안내는 Kakao Navi 로 넘긴다(P2).

## 진입
`departed` 이벤트(level 1) 또는 `TripState.current.state = moving`, 14 [길 안내].

## 레이아웃
14 와 같은 틀 + 상단 **길 안내 카드**(16, 112) 358×64 r16: 회전 아이콘 박스 40 Primary · 안내 15 Bold(`steps[i].instruction`) · "{장소}까지 {수단} n분 · HH:MM 도착 예상" 13.
departed **토스트**(Dark, 패널 위 8): walk 아이콘 + 서버 `message`("다음은 구룡포항, 도보 8분") + [경로 보기]. 4초.
패널: caption "이동 중 · HH:MM 출발"(이전 항목 `done_at`) · "{다음 장소}으로 가는 중" 17 · "{planned_start} 예정 → {eta} 도착 예상" · ETA 칩 · [도착했어요](P2) [경로 전체] · 남은 일정.

## 데이터
- 경로: `POST /routing/route {"mode": 다음 항목 leg_mode, "stops":[현재 위치, 다음 장소], "hazards":"avoid", "steps":true}`
- 현재 안내: 현재 위치에서 가장 가까운 `geometry.coordinates` 인덱스 k → `begin_index ≤ k` 인 마지막 step 의 **다음** step 을 보여준다. 위치가 바뀔 때마다 다시 계산(경로 요청은 다음 장소가 바뀌거나 경로에서 50m 벗어날 때만 다시)
- 우회가 있으면(`hazards.avoided` 비지 않음) 안내 카드 아래 보조 줄 Amber "위험 구역을 피해 n분 더 걸려요" (n = `detour_minutes`)
- ETA: `TripState.next.eta`, `delay_minutes`

## 동작
| 동작 | 결과 |
|---|---|
| 토스트 [경로 보기] / [경로 전체] | 지도 탭(07)에서 이 경로 강조 |
| [도착했어요] (P2) | `POST /planner/plans/{id}/items/{item}/progress {"status":"visiting"}` — 서버 판정이 늦을 때 보정. 데모 빌드에서는 숨긴다 |
| 차량 구간 | 안내 카드 대신 "차로 n분 · [카카오맵으로 열기]" (P2) |

## 엣지
- `source:"estimate"`(엔진 없음): 경로 직선, `steps` 없음 → 안내 카드에 "{장소} 방향 · 약 n분" 만.
- 다음 항목이 없으면(마지막 장소를 떠남) 패널 "오늘 일정을 모두 마쳤어요".
