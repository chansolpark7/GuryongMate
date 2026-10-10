# 28 Map Safety — 위험 구역 레이어 + 우회 경로 (P1)

Figma: 화면 `73:5140` · Spec `73:5276` · 레이어 패널 28-S1 `73:7999` · 흐름 F7

- **목적**: 지도에서 지금 피해야 할 구역을 보고, 원래 길과 피해 가는 길을 비교한다 ("우회하면 4분 더").
- **진입**: Map 탭(07), 23 [피해 가는 길 보기], 24 near_hazard 행, 15 우회 보조 줄.
- **레이아웃**: 07 틀 · 범례 pill "● 위험 ● 주의 · 지금 기준 HH:MM" · 구역 다각형(Red/Amber 18% + 점선) + 이름 라벨 · 원래 길 회색 점선, 피해 가는 길 Primary · 하단 우회 카드 358 r20: "위험 구역을 피해 가는 길" · "{출발} → {도착} · {수단}" · [원래 길 n분 · m m(취소선)] [피해 가는 길 n분 · m m (+k분)] · "{avoided 이름}을 지나지 않아요" · [원래 길][이 길로 안내].
- **레이어 패널(28-S1)**: 10 Layers 의 "날씨" 자리 → **"위험 구역"**(shield, 오른쪽 "지금 기준"), "혼잡도" → 비활성 50% + "데이터 없음". 일정·이동 경로·관광지는 기존.
- **데이터**:
  - 지금 피할 구역: `GET /safety/avoid?min_risk=caution` → `zones[{id, name, kind, risk, reason, polygon}]` (`polygon` 은 [lng, lat] 닫힌 고리). 침수 구역은 센서 주변 80m, 방파제는 선 + 60m.
  - (선택) 감시 구역 전체: `GET /safety/zones` + `GET /safety/status`(구역 `risk`, null 은 회색 "판단 불가") — 레이어를 길게 누르면 "모든 감시 구역" 보기.
  - 우회: `POST /routing/route {"mode","stops","hazards":"avoid","steps":true}` → `minutes`, `distance_m`, `geometry`, `hazards{checked, on_route, avoided, baseline{minutes, distance_m}, detour_minutes, note}`. 원래 길 선은 같은 요청을 `hazards:"report"` 로 한 번 더(지도에 두 선을 그리려면).
- **예외**: `checked:false` → "안전 정보를 확인하지 못했어요"(우회 없음, 안전하다는 뜻 아님) · `note`(경유지가 구역 안) → 그대로 + "목적지 자체가 위험하면 일정을 바꿔야 해요 [✨ AI에게 묻기]" · `avoided` 비어 있음 → 카드 대신 "피할 구역이 경로에 없어요" 토스트 · `source:"estimate"` → 직선 + "약".
- **갱신**: 지도가 보일 때 60초마다 avoid (situation `fingerprint` 가 같으면 생략 가능), near_hazard·safety 이벤트 때.
- **동작**: [이 길로 안내] → 15 를 이 경로로 · [원래 길] → 우회 끄기 · 구역 탭 → 작은 카드(이름·kind·risk·reason·"지금 기준").

- **2026-10-11 변경**: 위험 구역은 Map 이 따로 60초마다 `api.avoid` 하지 않고 Today 와 같은 `TripController.zones`(60초 + near_hazard·safety 이벤트 때 갱신)를 쓴다. 범례 "지금 기준"은 그 응답의 `now`.
  동행 중 [이 길로 안내] → Today 탭으로 돌아가 그 장소로 15 길 안내(`hazards:"avoid"` 경로, 안내 카드에 그 장소 이름). 동행 중이 아니면 지금처럼 지도에 표시 + 안내 토스트. 우회 카드는 Map 아래 시트의 접힘 내용.
