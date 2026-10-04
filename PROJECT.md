# GuryongMate — 프로젝트 방향

> 2026 디지털 트윈 AI Agent 해커톤 (포항 구룡포) 본선 개발용 정리 문서.
> 원본: Notion「2026 디지털 트윈 AI Agent 해커톤」(아이디어 기획서 · 세부 기획 · 자료조사)
> 작성일: 2026-10-03

---

## 1. 한 줄 요약

**여행 전 계획부터 여행 중 안내·해설·상황 변화 대응까지 함께하는 구룡포 맞춤형 AI 여행 동행 Agent.**

- 대상: 구룡포를 방문하는 관광객 전반
- 핵심 차별점: 구룡포 **디지털 트윈 안전 데이터(침수예측, 방파제 보행자 안전)** 를 여행자의 **현재 위치·진행 상태·남은 일정**과 결합하고,
  영향이 있을 때만 Agent가 **먼저 개입해 일정 변경안을 제안**, **사용자 승인 후에만** Planner·지도에 반영
- "여행을 계획하는 AI"가 아니라 **"여행을 함께 수행하는 AI"**

### 주의할 포지셔닝 (자료조사 결론)
`AI + 플래너 + 지도 + 맛집 추천`은 Wanderlog, Google Maps(Ask Maps), Gemini가 이미 제공 → 이것만으로는 차별점이 아님.
강조할 것:
1. 구룡포 전용 DT 예측 데이터 활용
2. "현재 여행 실행 상태"를 장시간 유지
3. Event / Intervention Policy — 필요한 순간에만 선제 개입
4. 장소별 curated 해설 자료(RAG) 기반 음성 동행
5. 모든 일정 변경은 사용자 승인
6. 지역 음식·상권 연결 (2026 구룡포 로컬테마상권 40억 사업과 연계)

---

## 2. 일정 & 평가 기준

| 항목 | 날짜 |
|---|---|
| 개발·멘토링 | 2026-09-21 ~ 10-12 |
| **본선 발표** | **2026-10-13** |

오늘(10-03) 기준 **남은 개발 기간 약 9~10일** → 기능별 병렬 개발 후 빠른 통합이 필수.

### 본선 평가 (2025 기준표)
| 항목 | 배점 | 개발 시 의미 |
|---|---|---|
| 기술 구현 완성도 | 30 | 핵심 흐름이 끊김 없이 동작, 버그·지연 최소화, 확장 가능한 구조 |
| DT 서비스·데이터 활용도 | 25 | DT 데이터를 **가공·해석해서 행동으로 연결**(단순 표시 X) |
| 실현 가능성·효율성 | 20 | 포항 DT 서비스와 연계 가능, 유지보수 가능한 구조 |
| 혁신과 창의성 | 15 | 생성형 AI의 적절한 사용, 참신한 문제 해결 |
| 발표·협업 | 10 | 실제 UI 데모 중심 발표 (기능 → 조작 → 결과 화면 흐름) |

---

## 3. 핵심 데모 시나리오 (개발 우선순위의 기준)

페르소나: 서윤(29) + 어머니, 구룡포 첫 방문, 12:30~18:00, 역사·사진 / 음식·바다, 오래 걷기 부담.

1. **여행 전 계획** — 음성/채팅으로 조건 전달 → Agent가 추가 질문 → 일정 초안 + 추천 이유 → 대화·직접 수정 반복 → 승인 시 Planner 반영
2. **현장 해설** — 지도에 오늘 일정·현재 위치 → 일본인가옥거리 도착 → 관심사 반영 음성 해설 → 버튼 눌러 추가 질문 ("핵심만 짧게" 같은 선호도 기억)
3. **지연 대응** — 30분 지연 감지 → Agent가 영향 설명 + 대안 2개 제시 → 사용자 제약 반영("과메기문화관은 유지") → 승인 시 Planner·지도 갱신
4. **DT 안전 대응** — 예정 동선(방파제) 관련 위험 신호 → `DT 위험 + 위치 + 남은 일정 + 관심사`를 함께 해석 → 대체안 제안 → 기존안/수정안 비교 → 승인

> 모든 기능은 이 4개 장면이 하나의 흐름으로 이어지는 것을 최우선으로 한다.
> 세부 기능은 남은 기간에 따라 조정.

---

## 4. 시스템 구조

```
[Flutter App]  지도 · 플래너 · 채팅/음성 UI · GPS
      │  REST / WebSocket
[FastAPI Backend]
  ├─ Agent (LLM + Tool Calling) ── tools ──┐
  ├─ Planner Service (조작 / 추천 / 검증)   │
  ├─ Travel State (위치·시간·진행 상태)      │
  ├─ Event / Intervention Policy           │
  ├─ Docent RAG (관광 해설 자료)            │
  └─ Data Adapters ◄───────────────────────┘
        ├─ Digital Twin (침수예측, 방파제 보행자 안전)
        ├─ 한국관광공사 TourAPI (KorService2)
        ├─ Kakao Map (지도 화면, 장소 검색)
        ├─ 길찾기: 직접 띄운 Valhalla + OSM 포항 (도보·차량 이동시간, 경로선, 위험 구역 우회)
        └─ 기상(포항시), 파고(해수부)
[PostgreSQL]  사용자 조건 · 일정 · 방문 상태 · 대화 기록
```

### 동작 흐름
```
사용자 음성·채팅·직접 수정 → Agent → Travel State / Planner 확인
→ 관광정보·위치·DT·외부 데이터 조회 → 안내 또는 변경안(Proposal) 생성
→ 사용자 승인 → Planner·지도 반영 (거절 시 기존 일정 유지)
```

### 설계 원칙
- **LLM이 모든 센서/API 변화를 직접 보지 않는다.** Event Layer가 안전성·일정 영향·지속성·중복을 먼저 평가하고 의미 있는 이벤트만 Agent에 전달.
- **LLM은 일정을 직접 바꾸지 않는다.** 변경은 항상 `Proposal(diff)` → 사용자 승인 → Planner 적용.
- **검증은 결정론적으로.** 운영시간·이동시간·시간 초과 등 위반은 LLM이 아닌 알고리즘으로 검사.
- **DT가 핵심, 외부 데이터는 보조.** 외부 날씨/파고 API가 DT를 대체하는 구조로 보이지 않게.
- **기획서에서 DT API 필드명·임계값을 임의로 만들지 않는다.** 실제 명세 확보 전까지는 Mock 어댑터로 개발.

---

## 5. 기능 모듈 (독립 개발 단위)

각 모듈은 **공통 데이터 계약(§6)** 만 지키면 따로 개발·테스트한 뒤 합칠 수 있도록 나눈다.

### M1. Planner (핵심 도메인)
여행 일정의 단일 진실 원천(Source of Truth). Agent와 UI 모두 이 모듈의 API로만 일정을 조작.

- **조작 기능**
  - 장소 검색
  - 일정 추가 / 삭제
  - 일정 순서 변경
  - 날짜 이동
  - 시작 시간, 체류 시간 설정
  - 장소 상세 조회
  - 전체 일정 조회
- **추천 기능**
  - 장소 임베딩 기반 추천 (사용자 관심사 ↔ 장소 설명 유사도)
  - 추천 이유 생성
- **검증 기능**
  - Rule 세팅: LLM 또는 사람이 규칙 정의 (예: "과메기문화관 유지", "연속 도보 20분 이하", "18:00 전 종료")
  - Deterministic algorithm으로 violation 검사 (운영시간·휴관일, 이동시간, 총 시간 초과, 사용자 제약)
- **Proposal 적용**: 변경안(diff) 생성 → 검증 → 승인 시 적용

### M2. Map (지도)
- 현재 위치, 오늘 일정 장소 마커, 이동 경로(Polyline) 표시
- 마커 탭 → 장소 텍스트 설명
- 경로·이동시간 계산은 **routing 서비스** (OSM 포항 데이터로 직접 띄운 Valhalla, `docs/routing/README.md`)
  - 도보·차량. 경로선(GeoJSON)·한국어 회전 안내, 걷기 편한 길(계단·언덕 회피)
  - DT 위험 구역(침수 센서) 우회 경로와 원래 경로 비교 ("우회하면 4분 더")
  - 외부 지도 API는 지도 화면·장소 검색에만 쓴다 (Kakao·Naver 공개 API에는 도보 길찾기가 없는 것으로 파악)
- 정밀 turn-by-turn 차량 길안내는 **Kakao Navi 앱으로 위임** (도보는 경로선 + 다음 안내 한 줄)
- 지도 제공자(Kakao/Google/Naver) 최종 결정 필요

### M3. Chat / Voice (대화 인터페이스)
- 채팅 UI (접기/펼치기 가능, 플래너와 한 화면)
- **버튼 기반 음성 대화** (always-listening / wake word 아님)
- 음성 방식 A/B: `Realtime Mini` vs `STT → Text Agent → TTS` (후자가 디버깅·기록·Tool orchestration에 유리)
- Proposal 카드 UI: 기존안 vs 수정안 비교 + 승인/거절

### M4. Agent (LLM 오케스트레이션)
- LLM API + Tool Calling
- Tools: Planner 조작/조회, 장소 검색, 경로 조회, Travel State 조회, DT/날씨 조회, Docent 검색, Proposal 생성
- 대화 맥락·사용자 선호 유지 (여행 전 → 여행 중 이어짐, "설명은 짧게" 같은 선호 기억)

### M5. Travel State & Event Policy (현장 동행)
- `여행 시작` 버튼으로 세션 시작 → 위치 추적 활성화 (When-in-Use 우선)
- 도착 감지(지오펜스), 지연 감지, 외부 상황 변화 감지
- Event Layer가 개입 여부 판단 (안전 > 일정 영향 > 중복 억제) → Agent에 전달
- **데모용 시뮬레이터 필수**: GPS 위치·시간·DT 이벤트를 수동 주입할 수 있어야 발표에서 시나리오 재현 가능

### M6. Docent RAG (현장 해설)
- 포항시·한국관광공사 공식 자료 기반 장소 해설 corpus
- 사용자 관심사·선호 길이에 맞춰 생성
- 자주 쓰는 기본 해설은 사전 생성/캐싱
- 공공누리/저작권 조건 확인 후 corpus 구성

### M7. Data Adapters (외부 데이터)
- **Digital Twin**: 침수예측(강우+월파 통합 예측), 방파제 보행자 이동 예측·안전경보 → 실제 명세 확보 전 Mock
- TourAPI(KorService2), 오디오 가이드/연관 관광지 API
- Kakao Map REST (장소·카테고리 검색)
- OpenStreetMap (Geofabrik 대한민국 추출본 → 포항 영역) — 길찾기 그래프
- 포항시 지역별 기상, 해수부 파고 예측
- 모든 어댑터는 동일 인터페이스 + Mock 구현 제공 → 키 없이도 다른 모듈 개발 가능

---

## 6. 공통 데이터 계약 (모듈 간 인터페이스)

병렬 개발 전에 **가장 먼저 합의하고 고정**할 것. 초안:

```text
Place        { id, name, category, lat, lng, description, open_hours, closed_days, tags[], source }
ItineraryItem{ id, place_id, day, order, start_time, stay_minutes, status(planned|visiting|done|skipped) }
Itinerary    { id, user_id, date_range, items[], constraints[] }
Constraint   { id, type, params, source(user|llm), hard|soft }
Violation    { constraint_id | rule, item_id, message, severity }
TravelState  { itinerary_id, now, location, current_item_id, delay_minutes, preferences }
Event        { id, type(arrival|delay|weather|dt_flood|dt_breakwater|closure), severity, affected_item_ids[], payload, created_at }
Proposal     { id, reason, diff[ add|remove|move|update ], violations_after[], status(pending|approved|rejected) }
```

- 백엔드: Pydantic 모델 → OpenAPI 스키마 자동 생성 → Flutter 쪽 모델 생성/동기화
- 스키마 변경은 `contracts` 관련 PR로만, 다른 모듈 담당자 리뷰 필수

---

## 7. 저장소 구조

기능별 서비스(컨테이너)로 나뉘고, 게이트웨이 하나로 접속한다. 자세한 규칙·담당·실행 방법은 `docs/COLLABORATION.md`에 있다.

```
GuryongMate/
├─ PROJECT.md              # 이 문서
├─ docker-compose.yml      # gateway, db, embedding, places-init, places, planner, agent
├─ infra/gateway/          # Caddy 설정 (URL 경로 -> 서비스)
├─ services/embedding/     # 임베딩 모델 서버 (별도 이미지)
├─ backend/                # FastAPI. 이미지 하나를 SERVICE 환경변수로 서비스별 컨테이너로 띄움
│  └─ app/
│     ├─ core/             # 공용: 설정, 인증(JWT 검증)
│     ├─ places/           # 관광지 정보·검색 (M7 데이터 + M6 근거 검색)
│     ├─ planner/          # M1 Planner
│     ├─ agent/            # M4 Agent (채팅·도구 실행)
│     └─ (account/)        # 예정: 로그인·사용자 프로필
├─ app/                    # Flutter (예정)
├─ data/places/            # 관광지 스냅샷
├─ docs/                   # 설계·API 문서, 서비스별 README
└─ scripts/                # CLI 등
```

## 8. Git 운영 방식

- 기본 브랜치: `main` (항상 데모 가능한 상태 유지)
- 통합 브랜치: `dev` — 기능 브랜치는 여기로 머지, 안정화되면 `main`으로
- 기능 브랜치: `feat/<모듈>-<기능>` 예) `feat/planner-validation`, `feat/map-markers`, `feat/agent-tools`
- 기타: `fix/…`, `docs/…`, `chore/…`
- 검증용 실험(PoC): `spike/<주제>` 예) `spike/voice-realtime`, `spike/tourapi-coverage`
  - API가 실제로 되는지, 방식 A/B 중 뭐가 나은지 확인하는 **버리는 코드**
  - `dev`에 머지하지 않는다. 결과(결론·수치·주의점)만 `docs/spikes/<주제>.md`로 정리해 `docs/` 브랜치로 머지
  - 채택된 방식은 `feat/` 브랜치에서 테스트와 함께 다시 제대로 구현
  - 반대로 핵심 로직을 **제대로 구현하면서 테스트로 검증**하는 것은 그냥 `feat/` (테스트는 기능과 같은 PR에)
- 커밋 메시지: `type(scope): 요약` 예) `feat(planner): 일정 순서 변경 API 추가`
- PR 단위는 작게, 머지 전 최소 1인 리뷰
- API 키·`.env`는 절대 커밋 금지 (`.env.example`만 커밋)

### 통합 순서
1. **Day 0**: 공통 계약(§6) + 디렉터리 골격 + Mock 어댑터 → `dev`
2. 각 모듈 병렬 개발 (Mock 기반으로 독립 테스트)
3. **Planner ↔ Agent 연결** (시나리오 1)
4. **Map ↔ Planner ↔ Travel State** (시나리오 2·3)
5. **DT Adapter ↔ Event Policy ↔ Agent Proposal** (시나리오 4)
6. 데모 시뮬레이터로 4개 장면 end-to-end 리허설 → `main`

---

## 9. MVP 우선순위

| 우선순위 | 범위 |
|---|---|
| **P0 (반드시)** | Planner 조작·검증, Agent tool calling, 채팅, 지도 마커/경로, Proposal 승인 흐름, DT 이벤트(Mock 포함) 기반 재계획, 데모 시뮬레이터 |
| **P1** | 음성 대화(버튼식), Docent RAG 해설, 지연 감지, 임베딩 추천 |
| **P2** | 실시간 GPS 백그라운드, 날씨·파고 연동, Kakao Navi 연동, 해설 캐싱 |

---

## 10. 미결 사항 / 확인 필요

- [ ] **DT API 실제 명세** (endpoint, 인증, 필드, 갱신주기, 좌표체계, 호출 제한)
- [ ] 구룡포 POI가 TourAPI / 오디오 가이드 API에 실제로 얼마나 있는지 샘플 호출
- [ ] 음식점 데이터 소스 (Kakao 장소검색 단독 vs 공식 데이터 병행)
- [ ] 지도 UI 제공자 확정 및 혼합 사용 약관 검토
- [ ] 음성 아키텍처 A/B (Realtime Mini vs STT→Agent→TTS)
- [ ] LLM 제공자/모델 확정
- [ ] 해설 콘텐츠 저작권(공공누리) 확인
- [ ] 팀원별 모듈 담당 배정

---

## 11. 참고 링크

- 대회: https://dtaiagent.imweb.me/
- E-UM 디지털트윈 포항: https://pohang-eum.kr/
- TourAPI(KorService2): https://www.data.go.kr/tcs/dss/selectApiDataDetailView.do?publicDataPk=15101578
- Kakao Map REST API: https://developers.kakao.com/docs/ko/kakaomap/rest-api
- Kakao Navi Flutter: https://developers.kakao.com/docs/ko/kakaonavi/flutter
- 포항시 지역별 기상 API: https://www.data.go.kr/data/15139550/openapi.do
- 해수부 파고 예측 API: https://www.data.go.kr/data/15112611/openapi.do
- 전체 출처는 Notion「기획서 자료조사 · 근거 및 출처」 참고
