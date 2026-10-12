# facts — 발표에 쓰는 수치와 출처

> 발표 슬라이드·대본·Q&A 에 쓰는 숫자는 **이 표에 있는 것만** 쓴다. 모두 저장소 문서·코드에서 찾은 값이고, 출처 칸의 문서가 원본이다.
> 측정 날짜가 있는 값은 그 날짜를 같이 말한다 ("10/9 실측"). 원본 문서가 바뀌면 이 표도 고친다.
> 작성: 2026-10-10, 기준 브랜치 `dev` (c474bf7).

## 1. 데이터

| 무엇 | 값 | 출처 |
|---|---|---|
| 관광 데이터 (한국관광공사 TourAPI) | 포항시 관광지·음식점 등 **324곳**, 오디오 가이드 해설 **37편** 수집·DB화 | [README.md](../../README.md) "개발된 기능" places |
| 구룡포 반경 12km 안 관광공사 데이터 | **39건**뿐 → 포항시 전체를 수집하고 구룡포는 필터·반경 검색 | [docs/places/README.md](../places/README.md) (데이터 출처) |
| RAG 근거 조각 | 소개문·오디오 가이드 대본을 500자 단위로 나눈 **430조각** | [docs/places/README.md](../places/README.md) (근거 조각) |
| 직접 정의한 장소 | 구룡포 북방파제 (`poi-gr-breakwater-north`, 관광공사 데이터에 없음) | [docs/places/README.md](../places/README.md) "직접 정의한 장소" |
| 임베딩 모델 | `intfloat/multilingual-e5-base` (CPU, 로컬 컨테이너, 첫 다운로드 약 1.1GB) | [README.md](../../README.md) |

## 2. 디지털 트윈 (E-UM 포항) 과 보조 데이터

| 무엇 | 값 | 출처 |
|---|---|---|
| E-UM API | 침수·방파제 **12개 전부** 활용신청·호출 확인 (2026-10-04). 서비스에서 쓰는 실시간 값은 3개: 수위 `INT_S05_006`, 자외선 `INT_S06_005`, CCTV 이상행동 `INT_S06_006` | [docs/dt/DT_API.md](../dt/DT_API.md) §0 |
| 침수 센서 | **10대** (지표면 5 · 맨홀 3 · 하천 1 · 강우 1) | DT_API.md §1 (INT_S05_001 실제 응답) |
| 방파제 장비 | AI CCTV **15대**, 스피커 **8대**, 자외선 **1대** (남·북방파제) | DT_API.md §3 (INT_S06_001 실제 응답) |
| 감시 구역 | 북·남방파제 + 침수 감시 구역 **9곳** | [docs/safety/README.md](../safety/README.md) §1 `/safety/status` |
| E-UM 이 주지 않는 것 | 침수 **예측값 API 없음**, 방파제 **예측·경보 API 없음**, 수위 응답에 측정 시각 없음, 등급 기준 미공개 | DT_API.md §1·§4 |
| CCTV 집계 지연 | 시간 단위 집계, 최신 값이 **약 하루** 늦음 (2026-10-04 확인) | DT_API.md §3 |
| 평상시 실측 | 수위 센서 전부 `level 1` (2026-10-04) | DT_API.md §2 |
| 보조 데이터 | 기상청 단기예보(약 3일, 파고 포함, 하루 8회)·특보 발효 현황, 해양조사원 조위(양포항, 구룡포에서 12km) | safety/README.md §4 |
| 캐시 | 침수 센서 2분 · CCTV 10분 · 자외선 10분 · 단기예보 30분 · 특보 5분 · 조위 1일. 실패도 30초 기억 | safety/README.md §4 |
| 호출량 | 캐시 기준 최대 시간당 E-UM **약 42회**, 공공데이터포털 **약 14회** + 조회 날짜 수 → 개발계정 하루 10,000건 안 | safety/README.md §4 |

## 3. 안전 판단 기준 (결정론적, `assumptions` 로 응답에도 포함)

| 항목 | 참고 | 주의 | 위험 | 근거 |
|---|---|---|---|---|
| 시간당 강수 | 3mm | 10mm | 30mm | 기상청 '매우 강한 비' 30mm/h |
| 풍속 (방파제) | — | 9m/s | 14m/s | 육상 강풍주의보 14m/s |
| 파고 (방파제) | — | 1.5m | 3m | 풍랑주의보 3m |
| E-UM 수위 등급 | 지표면 값 > 0 | 2 | 3 이상 | **가정** (E-UM 이 기준을 공개하지 않음) |
| 방파제 CCTV | 접근·침입 20건/시 | 실족 감지 1건 이상 | — | 가정. 최근 3시간 집계만 |

출처: [docs/safety/README.md](../safety/README.md) §3.

| 무엇 | 값 | 출처 |
|---|---|---|
| 위험 단계 | 3단계 `safe`·`caution`·`danger` + 판단하지 않음 `risk=null` (단계가 아님) | safety/README.md §3 |
| 장소 성질 반경 | 방파제 선 **60m**, 침수 감시 구역 센서 **200m** (판단용) | safety/README.md §3 |
| 길찾기에서 막는 반경 | 실측이 오른 센서 주변 **80m** (중심가 도로가 다 막히지 않게) | safety/README.md §1 `/safety/avoid` |
| 날씨 판단 범위 | `now` 3시간 안 · `short_range` 단기예보 범위(약 3일) 까지만. 그 뒤는 `risk=null (beyond_forecast)` + 방문 3일 전 다시 확인 | safety/README.md §2 |
| 발효 중 특보 | 방문 6시간 안 그대로 · 6~24시간 참고 · 24시간 넘게 쓰지 않음 | safety/README.md §2 |
| 가상 상황 시나리오 | 5개: `calm`·`rain`·`wind_tomorrow`·`flood`·`storm` (`SAFETY_SOURCE=mock` 에서만) | safety/README.md §1 `/safety/sim` |

## 4. 여행 중 판정 (trip · planner)

| 무엇 | 값 | 출처 |
|---|---|---|
| 도착 판정 | 장소 반경 60m + 위치 정확도(최대 150m) 안에 **90초** 머묾 | [docs/trip/README.md](../trip/README.md) §3.2·§9 |
| 출발 판정 | 반경 밖이 **120초** 이어짐 | trip/README.md §3.2 |
| 위험 구역 접근 경고 | 구역 안 또는 **50m** 안, LLM 없이 즉시 | trip/README.md §3.2 |
| 지연 정의 | "N분 늦음"이 아니라 현재 기준 재계산에서 **새 위반이 생김** (가벼운 지연은 알리지 않음) | trip/README.md §3.2, planner/README.md §8.2 |
| 개입 단계 | 0 상태만 · 1 고정 문구 · 2 카드 · **3 Agent 선제 턴** (LLM 은 3단계에서만) | trip/README.md §3.1 |
| 선제 턴 쿨다운 | **10분** (안전은 무시), 한 번에 하나, 실패 시 30초×2ⁿ 재시도 최대 3번 | trip/README.md §4.1·§9 |
| 백그라운드 틱 | **30초** (compose 기본) | trip/README.md §4.3 |
| planner 안전 주기 점검 | **60초**마다 상황 지문(`fingerprint`) 비교, 바뀌었거나 10분 지나면 점검 | [docs/planner/README.md](../planner/README.md) §7.4 |
| 자동 종료 | 마지막 일정 + **120분** | trip/README.md §9 |
| 앱 위치 전송 | 화면이 켜진 동안 30m 또는 30~60초마다 · 이벤트 폴링 5~10초 | trip/README.md §2.2, app/README.md §3.3 |
| 저장하는 위치 | **마지막 위치와 이벤트만**, 전체 이동 기록 저장 안 함 | trip/README.md §2.1·§7 |

## 5. Agent (LLM)

| 무엇 | 값 | 출처 |
|---|---|---|
| 운영 모델 | Claude **Haiku 5.5**, effort `medium` (2026-10-09 결정) | [docs/agent/README.md](../agent/README.md) §7 |
| 모델 비교 (2026-10-09, 시나리오 4개 × 2회 = 18턴, 검토 루프 있음) | Haiku 5.5: 제안 **8/8**, 남은 문제 must/should **0/0**, 턴 평균 **15.9초**, 턴당 약 **$0.003** · Sonnet 5.5: 8/8, 0/0, 16.3초, 약 $0.050 | agent/README.md §7.1 "모델 비교" |
| 캐시 비율 | 입력의 약 **86%** 를 프롬프트 캐시에서 읽음 (Haiku·Sonnet 모두), 턴당 LLM 호출 평균 4.5회 | agent/README.md §7.1 |
| Haiku 턴당 토큰 | 입력 74,471 (캐시 읽음 63,582 / 캐시 씀 9,705), 출력 1,936 | agent/README.md §7.1 |
| 비용 비교 | Haiku 는 Sonnet 의 약 1/17, gpt-5.4-mini(턴당 약 $0.011)의 약 1/4 | agent/README.md §7 표 `AGENT_CLAUDE_MODEL`, §7.1 |
| 검토 루프 효과 (2026-10-04, gpt-5.4-mini, 시나리오 3개 × 2회) | 없음: 남은 should 0.8, 13초 → 있음: **0/0**, 18초 | agent/README.md §7 "모델·구조 비교" |
| 로컬 LLM (qwen3.6:35b) | 제안 4/6, 턴 3~10분 → 운영에 쓰지 않음 | agent/README.md §7, §8 |
| gpt-5.4-mini 비용 (2026-10-04) | 턴당 평균 약 $0.011 (약 16원), 최대 $0.025, 입력의 83% 캐시 | agent/README.md §7 "비용" |
| 동행 모드 해설 턴 (2026-10-09, Haiku 5.5) | 턴당 $0.0014~$0.0052, 4턴 합계 약 $0.013 | agent/README.md §11.3.2 |
| 선제 턴 (같은 실측) | 약 $0.0084 (LLM 9회, 검토 관문 2번) | agent/README.md §11.3.2 |
| 가이드 노트 준비 (2026-10-09, Haiku 5.5, 5곳) | 장소당 14~27초(평균 약 20초), 장소당 약 $0.002~0.004 (웹 검색 1번이면 +$0.01), 5곳 약 $0.025 | [docs/docent/README.md](../docent/README.md) §2.1·§3.3 |
| 노트 길이 | short 108~167자(30초 안), long 560~580자 | docent/README.md §3.3 |
| 검토 관문 | 최대 **2번** 되돌림 (`AGENT_REVIEW_ROUNDS`), 한 턴 LLM 호출 최대 20번 | agent/README.md §7 |

## 6. 길찾기

| 무엇 | 값 | 출처 |
|---|---|---|
| 엔진 | Valhalla 3.9.0, 대한민국 OSM 약 280MB 에서 포항만 잘라 약 6MB, 첫 빌드 약 3분, 메모리 약 60MB | [docs/routing/README.md](../routing/README.md) §4 |
| 걷는 속도 | 기본 4.2km/h (70m/분, 어르신 동행 기준), `easy_walk` 3.6km/h | routing/README.md §1 |
| 근사 vs 실제 길 (2026-10-04) | 근대문화역사관 → 과메기문화관: 직선 근사 5분 → 실제 길 **9분** | routing/README.md §5 |
| 우회 예 (storm 가상 상황) | 구룡포항 → 구룡포교 방향 도보: 원래 8분 · 549m → 우회 12분 · 791m (**+4분**) | routing/README.md §1 `/routing/route` |
| 실패하지 않음 | 엔진이 없으면 직선 × 1.3 근사, `source: "estimate"` 로 표시 | routing/README.md §1 |

## 7. 구조·운영·품질

| 무엇 | 값 | 출처 |
|---|---|---|
| 백엔드 서비스 | **8개** (places·planner·agent·safety·routing·trip·docent·account) + embedding·valhalla·places-init·db·gateway | [docs/COLLABORATION.md](../COLLABORATION.md) §1 |
| 상주 메모리 | 전체 약 **1.8GB** (embedding 약 1.0GB, 서비스 각 약 100MB) | COLLABORATION.md §5 |
| 테스트 | 테스트 함수 **506개** (agent 130 · planner 119 · safety 69 · trip 66 · docent 43 · places 28 · routing 26 · account 19 · core 6). `backend/tests` 의 `def test_` 정적 집계 (parametrize 펼치기 전), 2026-10-10 | 코드 `backend/tests/` |
| 데모 장면 재생 확인 (2026-10-09, 각본 LLM, `--speed 0`) | 장면 ② 기대 3개 · ③ 4개 · ④ 6개 **모두 통과** | trip/README.md §8 |
| 실제 LLM 으로 장면 재생 | 2026-10-09 Claude Haiku 5.5 로 장면 ②③④ 기대 모두 통과 (그때는 북방파제 대신 구룡포해수욕장 계획) | trip/README.md §8 |
| 앱 fixture | 각본 LLM 스택에서 녹화한 응답 파일 (흐름 F1~F11 P0, 오류 예시, 장면 ②③④ 재생) | [docs/app/fixtures.md](../app/fixtures.md) |
| 앱 화면 명세 | 화면 01~36 (P0 = 데모 4장면 필수) | [docs/app/README.md](../app/README.md) §5 |
| 개발 기간 | 2026-09-21 ~ 10-12, 본선 2026-10-13 | [PROJECT.md](../../PROJECT.md) §2 |

## 8. 데모 데이터 (모든 화면·대본 공통)

[docs/app/README.md](../app/README.md) §6.1, 재생 파일 `scripts/trip/scenes/`, 녹화 `app/test/fixtures/`.

| 장면 | 값 |
|---|---|
| 계획 | "엄마랑 구룡포" 10/10(토) 12:30~18:00, 규칙 `end_before 18:00 (hard)` |
| 일정 | ① 일본인 가옥거리 12:30 (60분) → ② 구룡포항(점심) 13:40 (50분) → ③ 과메기문화관 14:47 (60분) → ④ 북방파제 16:00 (40분) → ⑤ 카페파도 16:53 (차량 13분, 45분) → **17:38 종료** |
| ① 계획 | 검토 관문이 문제 3건(북방파제→카페 도보 45분 `long_walk`, 18:10 종료 `day_overflow`·`end_before`)으로 되돌림 → 카페 구간 차량으로 → 문제 없음 → 제안 (17:38 종료) |
| ② 도착 | 12:30 가옥거리, 90초 머물면 도착 → 해설 카드 → "언제 지어진 집들이에요?" → "더 짧게" → 기억하기 `해설은 핵심만 짧게` |
| ③ 지연 | 가옥거리에 예정보다 오래 → 13:45 `delay`(`behind_schedule`: 구룡포항 13:40 → 13:55, 15분 늦음, `TRIP_BEHIND_MIN` 15) → 제안 "가옥거리 60 → 95분 + 구룡포항 50 → 30분, 17:52 종료" (다른 방법: 과메기문화관 빼기) → "과메기문화관은 유지해줘" → 앞의 안 + `must_include` 새 제안이 이전 제안 대체 → 13:53(18:00 초과) 전에 승인 → 이동 중 지연 알림 없음 (승인한 일정 기준으로 새로 셈) |
| ④ 안전 | 15:20 과메기문화관 관람 중 가상 `storm` → 북방파제 안전 → **위험** (풍속 16m/s·파고 3.5m 예보, 강풍·풍랑주의보, 방파제 CCTV 실족 감지) + 가는 길·카페 차량 구간(구룡포수협 맨홀 등급 3) 위험 → 제안 "북방파제 → 근대문화역사관(실내)" (종료 17:30 → 17:22, 위험 방문 1 → 0) → 15:25 승인 → 15:52 역사관 도착 → 16:33~ 침수 감시 구역 접근 경고 3번 (수협 맨홀·파출소 지표면·구룡포교 하천) → 16:39 카페 도착 |

> 장면 ④의 풍속·파고·특보·CCTV·수위 등급 3 은 **가상 상황(mock `storm`) 값**이다. 실제 측정값이라고 말하지 않는다.

## 9. 쓰지 않는 숫자 (문서에 없음)

- 사용자 수·만족도·정확도(%) 같은 효과 수치 — 측정한 적 없음.
- 실제 악천후에서 E-UM 등급 2·3 이 뜬 기록 — 아직 관측하지 못함 (DT_API.md §2, safety/README.md §8).
- 발표 시간 배정 — 문서에 없음 ([outline.md](outline.md) 는 10분 발표를 가정).
- 로컬테마상권 40억 사업 "연계 성과" — PROJECT.md §1 에 연계 방향만 있고 구현·협의 기록은 없음.
