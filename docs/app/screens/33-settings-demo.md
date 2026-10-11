# 33 Settings · Demo — 설정 · 발표용 데모 조작 (P0)

Figma: 화면 `73:5904` · Spec `73:6045`

## 목적
계정·나에 대해·서버 주소를 모으고, **발표장(구룡포가 아님)에서 4장면을 재현**하는 조작(데모 위치 모드, 가상 상황·시각, 안전 점검 실행)을 한 화면에 둔다.

## 진입
29/02 상단 계정 버튼. 로그인 전에는 01 하단 "서버 설정"으로 서버 주소 줄만 있는 축약판.

## 레이아웃 (회색 배경, 그룹 카드 r16)
| 그룹 | 행 |
|---|---|
| 계정 | 아바타 40(이름 첫 글자, Primary) · `display_name` 14 Bold · "{login_id} · 아이디 로그인" / "{email} · Google" · [로그아웃] · (맨 아래 별도) 계정 삭제 |
| 구룡메이트 | 나에 대해 → 31 (항목 수) |
| **데모 · 개발** (검정 "발표용" 칩) | 아래 표 |
| 하단 | "구룡메이트 {버전} (demo)" |

### 데모 · 개발 그룹 (`--dart-define=DEMO=true` 빌드에서만)
| 행 | 표시 | 동작·API |
|---|---|---|
| 데모 위치 모드 (스위치) | "GPS 대신 서버에 들어온 위치(CLI·재생기)를 지도에 그려요" | ON: GPS 전송 중단, 지도 현재 위치 = `TripState.location`(source cli/replay/sim), 13 생략, Today 에 "데모 위치" 칩 (trip §2.2) |
| 서버 주소 | 입력 #F3F4F6 + "✓ 연결됨" · 아래 "agent: {provider} · safety: 가상 상황/실제 데이터" | [연결 확인] → `GET /health`, `GET /agent/info`, `GET /safety/sim`(200 이면 "가상 상황", 404 `sim_disabled` 면 "실제 데이터") |
| 가상 상황 | 칩: 평상시(calm) · 비(rain) · 내일 강풍(wind_tomorrow) · 침수 센서(flood) · 폭풍(storm). 현재값 Primary 채움 | `PUT /safety/sim {"scenario"}` (모르는 값 422 `unknown_scenario`) |
| 가상 시각 | "MM/DD (요일) HH:MM · 흐르는 중" + 칩 [시각 바꾸기][+10분][+30분][실제 시각] | `PUT /safety/sim {"now": ISO}` · +n분 = 현재 sim now + n · 실제 시각 `{"reset_clock": true}`. trip·planner 가 이 시계를 따른다 (trip §5) |
| 장소로 이동 (가상 위치) | 오늘 장소 목록 시트 | `POST /trip/plans/{id}/location {lat, lng, accuracy:10, source:"sim"}`. 도착 판정에는 90초 머무름이 필요 → 발표는 CLI `trip goto`/`trip replay` 권장 ([gaps](../gaps.md) b13) |
| 안전 점검 지금 실행 | | `POST /planner/safety/scan?force=true` (`AUTH_REQUIRED=false` 에서만. 404 `not_available` 이면 행 숨김) |

- 가상 상황 그룹 전체는 `GET /safety/sim` 이 404 면 비활성("실제 데이터 모드").
- 바꾼 뒤 토스트 "가상 상황: 내일 강풍" 한 줄. 앱 화면 갱신은 다음 폴링/점검에서 자연스럽게 일어난다.

## 계정 동작
| 동작 | API |
|---|---|
| 로그아웃 | `POST /auth/logout {refresh_token}` → 저장 토큰 삭제 → 01 |
| 계정 삭제 | 다이얼로그 2단계 → `DELETE /users/me` (account 가 planner·agent 데이터를 지운다). 503 `upstream_unavailable` → "잠시 후 다시 시도해 주세요" |
| (P2) 이름 변경 · 비밀번호 변경 | `PATCH /users/me`, `PUT /users/me/password` (새 토큰 쌍 저장) |

## 발표 순서 예 (참고)
1. 서버 주소 확인 → 가상 상황 "평상시", 시각 10/10 12:20 → 데모 위치 모드 ON
2. CLI 로 위치 재생(가옥거리 도착) → 장면 ②
3. 시각 +90분 → 지연(장면 ③)
4. 가상 상황 "내일 강풍"(+ 시각을 하루 전으로 맞춘 리허설 값) → 안전 점검 지금 실행 → 장면 ④

## 2026-10-11 변경 (이용약관 · 개인정보처리방침)
- 계정 삭제·버전 줄 위에 그룹 **"앱 정보"**: [이용약관] [개인정보처리방침] 두 줄(›) → 문서 화면 (`settings-terms`·`settings-privacy`). 로그인 전 축약판(서버 설정)에는 없음.
- 문서 화면: 앱 바 제목 + 본문은 `assets/legal/*.md`(원본 `docs/legal/`, 오프라인에서도 보임)를 가벼운 마크다운 **문서 모드**로 — 제목 #·##, 표는 행마다 작은 카드(첫 칸 굵게 + "머리 · 값"), 인용은 회색 상자. 문서 사이 링크(`[개인정보처리방침](privacy.md)`)는 앱 안 화면으로.
- 01 로그인 첫 화면 문구 "계속하면 **이용약관** 및 **개인정보처리방침**에 동의하게 됩니다" 의 두 낱말이 밑줄 링크 → 같은 문서 화면.
