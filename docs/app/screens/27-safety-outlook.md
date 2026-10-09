# 27 Safety Outlook — 날짜 전망 (P1)

Figma: 화면 `73:4751` · Spec `73:5116`

- **목적**: "그날 구룡포 날씨·바다 어때?"를 장소 종류별 시간 띠로. 계획을 짤 때 방파제·해안을 언제 넣을지 정하게 한다.
- **진입**: 25 "안전 점검 ›", 29 계획 카드 배지. Bottom Sheet(top 220).
- **레이아웃**: 제목 "MM/DD (요일) 구룡포 전망" · 요약 한 줄 · 시간 띠 4행 × 09–18시(셀 24 r4: 안전 #CDEFD9 / 참고 #E5E7EB / 주의 #FCD9A3 / 위험 #F6A6A9): 방파제 / 해안 저지대 / 그 밖의 야외 / 실내 · 범례 · "야외에 괜찮은 시간" Green 칩 · 만조 행 · 특보 행 · 출처·발표 시각 12.
- **데이터**:
  - `GET /safety/outlook?date=YYYY-MM-DD` — 요약·위험 시간대·만조·특보, `safe_windows{breakwater, flood, outdoor}`(각 `has_info`). 응답의 정확한 필드 이름은 `/safety/docs` 로 확인 → [gaps](../gaps.md) b10.
  - 시간 띠: `GET /safety/situation` 의 `hours[]` 중 그날: `levels{breakwater, flood, outdoor, indoor}` + `info[]`(safe 지만 참고가 있는 종류 → 회색 셀).
  - 행 이름: breakwater "방파제", flood "해안 저지대(침수 감시 구역)", outdoor "그 밖의 야외", indoor "실내".
- **먼 날짜**(hours 없음, safe_windows 비어 있음): 시간 띠 대신 회색 박스 "아직 예보 전이에요 · MM/DD부터 확인할 수 있어요" + 만조 시각 + 계절 경향. "괜찮아요"라고 말하지 않는다.
- **동작**: 괜찮은 시간 칩 → AI 시트 "{종류}는 {시간대}에 가도록 조정해줘". 닫기.
