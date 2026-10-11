# 26 Safety Detail — 위험 근거 시트 (P1)

Figma: 화면 `73:4400` · Spec `73:4727`

- **목적**: 배지 하나 뒤의 이유(근거·시간대·이동 구간·우회)를 보여준다. "DT 데이터를 해석해서 행동으로" 를 화면에서 증명하는 곳.
- **진입**: 25·14·21 의 위험 배지 탭. Bottom Sheet(top 260).
- **레이아웃**: 장소 20 Bold + 시간 · 배지 줄(단계 h28 · 장소 성질 `sensitivity`("방파제"/"침수 감시 구역"/"실내") · 신뢰도) · "근거" 행 h52(아이콘 박스 36 + reason 14 + source 12 + 단계 12 Bold: 위험 Red / 주의 Amber / 참고 회색) · "괜찮은 시간대" Green 칩(`better_times`, "MM/DD HH:MM–HH:MM", `has_info` 면 칩 끝에 "· 참고 있음") · "가는 길 · {출발} → {도착} {수단} n분" + 그 travel 항목 배지·요약, `detour` 있으면 "우회하면 n분 더" · "예보는 바뀔 수 있어요 · HH:MM 확인" · [✨ AI에게 대안 묻기].
- **데이터**: `GET /planner/plans/{id}/safety` 의 그 `item_id` 의 visit + travel 항목: `risk, unassessed_reason, hazards[{type, level, reason, source}], sensitivity, horizon, better_times, notes, detour{on_route, avoided, detour_minutes, note}`, 응답 `checked_at`.
- **신뢰도 문구**: `horizon` now → "실측·24시간 안 예보" / short_range(24h 안) → "신뢰도 높음" / short_range(그 뒤) → "신뢰도 보통". `data_missing:true` 면 "일부 데이터가 빠져 실제로는 더 높을 수 있어요" 줄 추가.
- **notes**(만조·계절 경향)는 "참고" 행으로.
- **null 일 때**: beyond_forecast "아직 예보 전이라 판단하지 않았어요. MM/DD(`recheck_from`)부터 확인할 수 있어요." + notes / data_missing "데이터를 받지 못해 판단하지 못했어요. 안전하다는 뜻이 아니에요." — AI 버튼 숨김.
- **동작**: [✨ AI에게 대안 묻기] → AI 시트 + "{장소} {시각} 일정을 안전하게 바꿔줘" 전송. 괜찮은 시간대 칩 탭 → 같은 문구에 "{시간대}로" 추가.
- **카피**: `advice` 는 LLM 지시문("…제시하라")이라 보이지 않는다.
