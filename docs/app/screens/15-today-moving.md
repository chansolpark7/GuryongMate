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

## 2026-10-11 변경 (길 안내 자동 + 작은 토글 — [today-map-redesign.md](../today-map-redesign.md) §2.1)
- **자동 켜짐**: `current.state=moving` 이거나, 첫 장소 전(`idle`, 아직 아무 데도 안 감)에 위치가 있으면 다음 장소로 안내 카드 + 안내선.
- **자동 꺼짐**: 도착(`visiting`)하면 끈다. 다음 이동이 시작되면 다시 켠다.
- 안내 카드 오른쪽 작은 [×](`guide-close`) → **이번 구간 동안만** 숨김 (구간 = 지금 상태 · 지금 항목 · 다음 항목). 다음 구간이면 다시 자동.
- 수동 켜기: 다음 일정 🧭, 지도 작은 창 [길 안내], Map 우회 카드 [이 길로 안내](28). 고른 장소 이름이 안내 카드에 나온다. 도착·출발로 상태가 바뀌면 수동 대상은 지운다.
- 위치가 없으면 카드에 "위치를 켜면 길을 안내해요" (예전: "{다음} 방향").
- 패널의 [경로 전체] 는 없앴다 → 남은 일정 머리의 작은 [전체 지도]. 토스트 [경로 보기] 는 그대로 Map 탭 (Map 에도 동행 레이어가 있다).
