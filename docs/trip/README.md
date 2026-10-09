# trip — 여행 중 상태·이벤트 API 명세

> 상태: **설계 (미구현)**. 2026-10-09 합의안. 구현하면서 바뀐 부분은 이 문서를 같이 고친다.

여행 당일의 **실행 상태**(지금 몇 일차를 여행 중인지, 어디에 있는지, 어떤 일정을 하고 있는지)를 관리하고,
위치·시각·안전 정보를 바탕으로 **이벤트**를 판정해 앱과 Agent에 전달한다.

- 코드: `backend/app/trip/` · 테스트: `backend/tests/trip/` · API 문서: `/trip/docs`
- 판단은 **서버(trip)에서만** 한다. 앱은 위치를 보내고 이벤트를 받아 그리기만 한다.
- 위치를 보내는 쪽은 바꿔 끼울 수 있다: 휴대폰 GPS / CLI 수동 입력 / 경로 재생기. **모두 같은 API**를 쓰므로 CLI·테스트 결과가 실제 앱과 같다.
- 일정의 단일 원천은 계속 planner 다. trip 은 일정을 직접 바꾸지 않는다 (진행 상태 표시만 planner API 로 갱신, §6.1).

```
[휴대폰 GPS] ─┐
[CLI loc/goto] ┼─► POST /trip/plans/{plan_id}/location ─► trip
[replay 재생기]─┘                                          │ 도착·출발 판정 → planner 진행 상태
                                                           │ 지연 판정      → planner projection (§6.2)
                                                           │ 위험 구역 접근 → safety /safety/avoid
                    planner 안전 경고 (needs_action) ─────►│
                                                           ▼
                                        Event Policy (우선순위·중복 억제·쿨다운)
                                                           │
                                  이벤트 기록 ─────────────┼─► Agent 선제 턴 (단계 3만, §4)
                                                           ▼
                         GET /trip/plans/{plan_id}/events?after=  ◄── 앱 (폴링, 나중에 SSE)
```

---

## 1. 여행일 (모드 전환)

### 1.1 원칙
- **동행 모드 = 계획 모드 + 추가 기능**이다. 동행 모드라고 일정 수정이 막히지 않는다. 실수로 켜져도 잃는 기능이 없다.
- 모드는 스위치가 아니라 **상태**로 정한다: 진행 중인 여행일(`trip_day`)이 있으면 동행 모드.
- 여행일은 **하루 단위**다. 여러 날 여행은 날마다 시작한다.

### 1.2 시작·종료 규칙

| 상황 | 처리 |
|---|---|
| 오늘(`now` 기준, §5)이 계획 기간 안의 날 | 시작 가능. `day` = 오늘의 n일차 |
| 오늘이 계획 기간 밖 | 409 `not_trip_day` (추가 필드 `start_date`, `end_date`). 앱은 "일정은 10/10이에요. 오늘로 옮길까요?"를 묻고, 옮기기를 고르면 Agent 제안(일정 날짜 변경)으로 처리한다 |
| 이미 진행 중인 여행일이 있음 | 같은 날이면 그대로 돌려준다(멱등). 다른 날이면 이전 것을 종료하고 새로 시작 |
| 실수로 시작 | `POST …/end` 로 종료. 진행 기록 외에는 바뀐 것이 없다 |
| 종료하지 않음 | 그날 마지막 일정 종료 + `TRIP_AUTO_END_MIN`(기본 120분) 또는 자정이 지나면 자동 종료 (`ended_reason: auto`) |

앱 권장 동작: 오늘이 여행일이고 (첫 일정 시작 1시간 전 이후 또는 구룡포 반경 3km 안) 이면 "오늘 여행 시작할까요?"를 띄운다. **자동으로 시작하지는 않는다** (여행일에 집에서 계획만 고치는 경우).

### 1.3 API

| 메서드 | 경로 | 본문 | 성공 | 오류 |
|---|---|---|---|---|
| POST | `/trip/plans/{plan_id}/start` | `{"session_id"?: "agent 세션 id"}` | 200 `TripState` | 404 `plan_not_found`, 409 `not_trip_day` |
| POST | `/trip/plans/{plan_id}/end` | | 200 `TripState` (`active=false`) | 404 |
| GET | `/trip/plans/{plan_id}/state` | | 200 `TripState` | 404 |
| DELETE | `/trip/me` | | 204 (탈퇴: 이 사용자의 여행 상태·이벤트 삭제) | |

- `session_id`: 선제 턴(§4)을 보낼 Agent 세션. 생략하면 그 계획의 가장 최근 세션을 쓰고, 없으면 trip 이 Agent 에 새로 만든다.
- 인증은 다른 서비스와 같다 (`app.core.auth.CurrentUser`, planner 를 부를 때는 받은 인증을 그대로 전달).

```jsonc
// TripState
{"plan_id": 3, "active": true, "day": 1, "date": "2026-10-10",
 "started_at": "…", "ended_at": null, "ended_reason": null,          // user | auto
 "now": "2026-10-10T14:20:00+09:00", "clock": "sim",                 // system | sim (§5)
 "location": {"lat": 35.987, "lng": 129.558, "accuracy": 12, "recorded_at": "…", "source": "app"},  // 마지막 위치. 없으면 null
 "current": {"item_id": 12, "state": "visiting", "since": "14:05"},  // visiting: 장소 안 / moving: 이동 중 / idle: 시작 전·끝
 "next": {"item_id": 13, "title": "구룡포과메기문화관", "planned_start": "15:00", "eta": "15:12"},
 "delay_minutes": 12,                                                // 다음 일정 기준 예상 지연 (음수 = 여유)
 "session_id": "9288a236…"}
```

---

## 2. 위치

### 2.1 API

| 메서드 | 경로 | 본문 | 성공 |
|---|---|---|---|
| POST | `/trip/plans/{plan_id}/location` | `LocationIn` 또는 `{"points": [LocationIn, …]}` (최대 100, 오래된 순) | 200 `{"state": TripState, "events": [Event, …]}` (이번 요청으로 생긴 이벤트) |

```jsonc
// LocationIn
{"lat": 35.987, "lng": 129.558,
 "accuracy": 12,                         // m, 선택. 없으면 30 으로 본다
 "recorded_at": "2026-10-10T14:20:00+09:00",   // 선택. 없으면 서버 now
 "source": "app"}                        // app | cli | replay | sim
```

- 진행 중인 여행일이 없으면 409 `trip_not_active` (위치를 저장하지 않는다).
- `accuracy` > `TRIP_MAX_ACCURACY_M`(기본 100)인 점은 버린다 (응답 `ignored` 수에 포함).
- `recorded_at` 이 마지막 점보다 이르면 버린다 (순서 뒤바뀜).
- 개인정보: **마지막 위치와 이벤트만 저장**한다. 전체 이동 기록은 남기지 않는다 (판정에 필요한 최근 몇 점만 메모리/짧은 보관).

### 2.2 앱 전송 규칙 (권장)
- 화면이 켜져 있는 동안(When-in-Use): 30m 이상 움직였거나 30~60초마다.
- 끊겼다가 다시 연결되면 모아 둔 점을 `points` 로 한 번에 보낸다.
- 백그라운드 위치·OS 지오펜스는 이번 범위가 아니다. 나중에 붙여도 "OS가 앱을 깨움 → 같은 API로 전송"이라 서버는 바뀌지 않는다.
- **데모 위치 모드**: 앱 설정 스위치를 켜면 GPS 를 보내지 않고, 서버에 들어온 위치(CLI·재생기)를 지도에 그린다. 발표장은 구룡포가 아니기 때문이다.

---

## 3. 이벤트

### 3.1 개입 단계
모든 이벤트에 LLM 을 쓰지 않는다.

| 단계 | 처리 | LLM |
|---|---|---|
| 0 | 상태만 갱신 (앱 화면에 반영) | X |
| 1 | 고정 문구 알림 | X |
| 2 | 가벼운 카드 (버튼). 사용자가 누르면 그때 LLM | 누를 때만 |
| 3 | **Agent 선제 턴**: Agent 가 먼저 말을 걸고 변경 제안 | O |

### 3.2 이벤트 목록

| `type` | 판정 (결정론적) | 단계 | 처리 | 데모 |
|---|---|---|---|---|
| `arrived` | 오늘 남은 항목 장소 반경(`TRIP_ARRIVE_RADIUS_M` 60 + accuracy, 최대 150m) 안에 `TRIP_ARRIVE_DWELL_SEC`(90초) 이상 머묾. 예정 순서가 아닌 항목이어도 오늘 남은 항목이면 도착으로 본다 (`out_of_order: true`) | 0 + 2 | 항목을 `visiting` 으로. **해설 카드**: "일본인가옥거리예요. 이야기 들어볼까요? [짧게] [자세히]" → 누르면 해설 (`docs/docent/README.md`) | ② |
| `departed` | `visiting` 장소 반경 밖이 `TRIP_DEPART_DWELL_SEC`(120초) 이상 이어짐 | 0 + 1 | 항목을 `done` 으로. "다음은 과메기문화관, 도보 8분" | |
| `delay` | 현재 위치·시각 기준으로 남은 일정을 다시 계산(§6.2)했을 때 **기준 일정에 없던 위반이 새로 생김**: `fixed_time_missed`, `day_overflow`, `outside_open_hours`, `closed_day`, hard 규칙 위반 | 3 | Agent 선제 턴: 영향 설명 + 대안 → 제안 → 승인 | ③ |
| (가벼운 지연) | 늦어졌지만 새 위반 없음 | 0 | `delay_minutes`·`eta` 만 갱신. 알리지 않음 | |
| `safety_raised` | planner 안전 경고 중 `needs_action=true`, 오늘 남은 항목에 해당 (`GET /planner/plans/{id}/safety/alerts?status=new`) | 3 | Agent 선제 턴: 위험 근거 + 대안(시간 이동·실내 대체) → 제안 | ④ |
| `safety_eased` | planner 경고의 `lowered`/`cleared` | 1 | "방파제 풍랑 주의가 해제됐어요" | |
| `near_hazard` | **지금 위치**가 safety `/safety/avoid` 구역 안이거나 `TRIP_HAZARD_NEAR_M`(50m) 안 | 1 | **LLM 없이 즉시** 고정 경고: "침수 감시 구역(구룡포수협 주변) 근처예요. 물이 고인 길은 피하세요." | ④ 보조 |
| `day_started` / `day_ended` | 시작·종료 | 0 | 기록만 | |

- 지연을 "N분 늦음"이 아니라 **"새 위반이 생김"**으로 정의한다. 늦어서 실제로 문제가 생길 때만 알리고, 운영시간 종료(`closure`)도 같은 판정으로 잡힌다. 판정 로직은 planner 검증을 그대로 쓴다.
- `delay` 판정은 위치가 들어올 때와 백그라운드 틱(§4.3)마다 한다. 위치가 안 들어와도 시간이 흐르면 지연이 생기기 때문이다.

### 3.3 Event 형식

```jsonc
{"id": 41, "plan_id": 3, "day": 1, "type": "delay", "level": 3,        // 3.1 단계
 "severity": "warning",                                              // info | warning | danger
 "created_at": "…", "now": "2026-10-10T15:05:00+09:00",
 "item_ids": [13, 14],                                               // 영향받는 항목
 "message": "과메기문화관 도착이 18:10으로 예상돼 운영시간(18:00) 이후예요.",   // 사람이 읽는 한 줄 (고정 문구)
 "payload": {"new_violations": [Violation, …], "delay_minutes": 35},          // 타입별 근거
 "status": "delivered",                       // queued | delivered | suppressed | handled
 "agent": {"session_id": "…", "message_id": 102, "proposal_id": 57},          // 단계 3: Agent 결과. 아니면 null
 "card": {"kind": "docent", "place_id": "kto-…", "item_id": 12}}              // 단계 2: 카드 정보. 아니면 null
```

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/trip/plans/{plan_id}/events?after={id}&limit=50` | `after` 이후 이벤트 (오래된 순). 앱은 5~10초마다 폴링 |
| POST | `/trip/plans/{plan_id}/events/{event_id}/seen` | 확인 표시 |

---

## 4. Event Policy

### 4.1 규칙
- **우선순위**: 안전(`near_hazard`, `safety_raised`) > 일정 영향(`delay`) > 안내(`arrived`, `departed`).
- **단계 3은 한 번에 하나.** 진행 중인 선제 턴이 있거나 Agent 세션이 응답 중(409 `agent_busy`)이면 `queued` 로 두고 끝난 뒤 보낸다. 큐에서는 우선순위 높은 것부터, 같은 원인의 오래된 것은 `suppressed`.
- **중복 억제**: 같은 원인 키(`type` + 항목 + 위반 코드 / 경고 단계)로는 다시 알리지 않는다. 사용자가 그 이벤트로 나온 제안을 **거절**하면, 상황이 더 나빠질 때(새 위반, 단계 상승)만 다시 알린다.
- **쿨다운**: 단계 3은 `TRIP_PROACTIVE_COOLDOWN_MIN`(기본 10분) 안에 하나만. 안전(`safety_raised`)은 쿨다운을 무시한다.
- **여행 중일 때만**: 단계 1~3은 진행 중인 여행일에만 낸다. 여행 전의 안전 경고는 planner 경고로 남아 앱이 계획 화면에 배지로 보여준다 (trip 관여 없음).

### 4.2 Agent 선제 턴 (agent 쪽에 새로 필요)

```
POST /agent/sessions/{session_id}/events          (내부 호출, 앱은 부르지 않는다)
{"event": Event, "trip": TripState}
→ 200 ChatResult (stream=false)
```
- Agent 는 사용자 메시지 대신 숨김 메시지 `[이벤트] …`(이벤트 요약 + 근거)로 턴을 시작한다. 대화 기록에는 assistant 답변과 카드만 보인다.
- 시스템 프롬프트에 동행 모드 섹션과 "현재 상황" 블록(§4.4)을 넣는다.
- 결과(답변 message id, 만든 proposal id)를 trip 이 이벤트의 `agent` 필드에 기록한다. 앱은 이벤트를 보고 대화 화면에 새 메시지를 띄운다 (또는 세션을 다시 조회).
- **인증**: 백그라운드에서 부를 때는 사용자 요청이 없다. trip 은 `app.core.auth.issue_token(user_id, ttl=5분)` 으로 그 사용자의 짧은 토큰을 만들어 부른다 (모든 서비스가 같은 `JWT_SECRET` 을 쓴다).

### 4.3 백그라운드 틱
`backend/app/trip/background.py` 의 `running()` (규칙: `docs/COLLABORATION.md` §1). `TRIP_TICK_SEC`(compose 기본 30초, 코드 기본 0 = 끔)마다:
1. 진행 중인 여행일마다 지연 재판정 (§6.2)
2. planner 안전 경고 수집 (`status=new`) → `safety_raised` / `safety_eased`, 가져간 경고는 `seen` 처리
3. 큐에 있는 단계 3 이벤트 전송 시도
4. 자동 종료 판정

### 4.4 Agent "현재 상황" 블록 (agent 쪽)
Agent 는 매 턴 `GET /trip/plans/{plan_id}/state` 를 부르고, `active` 면 동행 모드로 프롬프트를 만든다.

```
## 현재 상황 (여행 중)
- 1일차 2026-10-10 14:20 (시뮬레이션 시각)
- 위치: 구룡포 일본인 가옥거리 (도착 14:05, 머무는 중)
- 다음: 구룡포과메기문화관 15:00 예정, 예상 도착 15:12 (12분 늦음)
- 오늘 남은 일정: 3곳 / 완료 2곳
- 바꿀 수 없는 것: 완료한 항목, 지난 시각
```
- 동행 모드에서도 모든 일정 도구를 쓸 수 있다. 기본 대상은 오늘 남은 일정이고, 사용자가 원하면 다른 날도 바꾼다 (승인을 거치므로 안전).
- 완료(`done`) 항목·지난 시각은 바꾸지 않는다.

---

## 5. 시계 (데모·테스트)

trip·agent·planner·safety 가 **같은 '지금'**을 봐야 한다. 시뮬레이션 시각이 서비스마다 다르면 데모 중 "아직 여행일이 아니에요"가 나온다.

- 기준은 safety 의 시각이다 (`PUT /safety/sim` 의 `now`, `SAFETY_SOURCE=mock` 일 때). safety 가 시각의 단일 원천이다.
- trip 은 `TRIP_CLOCK=safety` 이면 `GET /safety/sim` 의 `now`(실제 시간만큼 흘러감)를 5초 캐시해서 쓰고, 실패하면 시스템 시각. `TRIP_CLOCK=system` 이면 시스템 시각만.
- 위치의 `recorded_at` 은 판정에 쓰는 시각이다. 재생기는 시뮬레이션 시각 기준 `recorded_at` 을 넣는다.
- **확인 필요**: planner 는 안전 점검에서 safety 의 `now` 를 쓰지만 일부(`safety_watch._today`)는 시스템 날짜다. agent 의 날짜 표시(`prompts.py`)도 시스템 시각이다. 데모 전에 같은 시계를 쓰도록 맞춘다.

---

## 6. 다른 서비스와의 연결

### 6.1 planner — 진행 상태 (**변경 필요**)
도착·출발은 planner 항목의 `status`(`visiting`·`done`)로 반영한다. 그래야 planner 의 검증·안전 점검이 완료 항목을 뺀다.

**문제**: 지금은 `PATCH /planner/plans/{id}/items/{item_id}` 로 `status` 만 바꿔도 `version` 이 오른다. 여행 중 도착할 때마다 대기 중인 Agent 제안이 모두 `base_changed`(409)가 된다.

**권장 (planner 담당이 결정)**: 진행 상태 변경은 일정 변경이 아니다.
- 새 API `POST /planner/plans/{id}/items/{item_id}/progress` `{"status": "visiting"|"done"|"planned", "at": "HH:MM"}` 는 **`version` 을 올리지 않는다.** 대신 `progress_version` 을 올린다 (앱 화면 갱신용).
- 실제 도착·완료 시각(`arrived_at`, `done_at`)을 항목에 기록한다 (§6.2 재계산 기준).
- 제안을 승인할 때(덮어쓰기 포함) 진행 상태는 지금 값을 유지한다.
- `skipped` 는 일정 변경이므로 지금처럼 `version` 을 올린다.

### 6.2 planner — 현재 기준 재계산 (**추가 필요**)
지연 판정에 필요하다. 시간 모델은 planner 소유이므로 planner 가 제공한다.

```
POST /planner/plans/{id}/projection        (저장 안 함)
{"day": 1, "now": "15:05",
 "position": {"item_id": 12, "state": "visiting", "arrived_at": "14:05"}     // 장소 안
         | {"state": "moving", "lat": 35.98, "lng": 129.55}}                  // 이동 중
→ 200 {"plan": PlanDetail, "baseline_violations": [...], "new_violations": [...], "delay_minutes": 35}
```
- `done` 항목은 기록된 시각 그대로, 현재 항목은 실제 도착 시각부터 (예정 체류가 이미 지났으면 `now` 에 끝난다고 본다), 이동 중이면 `now + 현재 위치 → 다음 장소 이동시간`(routing)에 도착한다고 보고 남은 항목을 다시 계산한다.
- 사용자가 고정한 `start_time` 은 그대로 고정이다 (늦으면 `fixed_time_missed`).
- `new_violations` = 재계산 위반 중 기준 일정(`GET /validate`)에 없던 것 (코드 + 항목 기준). trip 의 `delay` 판정은 이것만 본다.

### 6.3 그 밖의 호출

| trip → | API | 용도 |
|---|---|---|
| planner | `GET /planner/plans/{id}` | 오늘 항목·장소 좌표 |
| planner | `GET /planner/plans/{id}/safety/alerts?status=new`, `POST …/alerts/{id}/seen` | `safety_raised`/`safety_eased` |
| safety | `GET /safety/avoid` (fingerprint 가 바뀔 때만 다시 받기: `GET /safety/situation`) | `near_hazard` |
| safety | `GET /safety/sim` | 시계 (§5) |
| routing | (planner projection 이 부름) | 이동 중 예상 도착 |
| agent | `POST /agent/sessions` (세션이 없을 때), `POST /agent/sessions/{sid}/events` | 선제 턴 |

| → trip | API | 용도 |
|---|---|---|
| 앱 | start / end / state / location / events | 화면 |
| agent | `GET /trip/plans/{id}/state` | 현재 상황 블록, 동행 모드 판단 |
| CLI·재생기 | location, start/end, `PUT /safety/sim` | 개발·테스트·데모 |

모든 호출은 게이트웨이(`http://gateway`) 경유 (`TRIP_*_BASE_URL`, compose 가 넣는다).

---

## 7. 테이블 (`trip_*`)

| 테이블 | 내용 |
|---|---|
| `trip_day` | plan_id, user_id, day, date, session_id, started_at, ended_at, ended_reason, 마지막 위치(lat, lng, accuracy, recorded_at, source), current_item_id, current_state, current_since, delay_minutes |
| `trip_event` | 3.3 Event 필드 + 원인 키(`cause_key`, 중복 억제용) |

판정용 최근 위치(머무름 계산)는 `trip_day` 에 "반경 안에 처음 들어온 시각 / 반경 밖으로 처음 나간 시각"만 두면 충분하다. 전체 궤적은 저장하지 않는다.

---

## 8. CLI·재생기·테스트

`scripts/planner/cli.py` 에 trip 명령을 추가한다 (같은 `GURYONG_API_URL`, 로그인).

```text
trip start 3 [--session SID]          여행일 시작 / trip end 3 / trip state 3
trip loc 3 35.987 129.558 [--acc 10] [--at 14:20]     위치 한 점
trip goto 3 kto-2660046 [--wait 120]  그 장소 좌표로 보내고 머무름 시간만큼 시계를 진행 (도착 판정 확인용)
trip replay 3 scene3.json [--speed 10]     경로 재생
trip events 3 [--follow]              이벤트 보기 (--follow: 폴링)
sim scenario storm / sim now 2026-10-10T14:00   safety 가상 상황·시각 (PUT /safety/sim)
```

**재생 파일** (`scripts/trip/scenes/*.json`): 데모 장면과 테스트를 같은 파일로 만든다.
```jsonc
{"plan_fixture": "seoyun_mom_1010",          // 테스트용 계획 (장소·시간 고정)
 "start": "2026-10-10T12:20:00+09:00", "scenario": "calm",
 "steps": [
   {"at": "+0", "goto": "kto-2610501", "stay": 50},          // 장소로 가서 50분 머묾
   {"walk_to": "kto-2660046"},                                // routing 경로선을 따라 걸음 (점 간격 30초)
   {"at": "+95", "sim": {"scenario": "wind_tomorrow"}},       // 안전 상황 변경
   {"stay": 40},
   {"expect": {"type": "delay"}}]}                            // 테스트에서 기대 이벤트
```
- `--speed` 는 시뮬레이션 시각을 빨리 흘린다 (safety sim 의 `now` 를 함께 옮긴다).
- pytest 는 같은 파일을 서버 없이 재생한다: 이벤트 판정 함수는 **순수 함수**(입력: 오늘 일정·마지막 상태·위치·now → 출력: 새 상태·이벤트)로 두고, planner·safety·agent 호출은 가짜 클라이언트로 바꾼다 (planner 테스트의 방식과 같다).

### 꼭 있어야 할 테스트
- 반경 경계에서 위치가 튀어도(한 점만 안/밖) 도착·출발이 생기지 않는다.
- accuracy 큰 점은 무시된다. 순서가 뒤바뀐 점은 무시된다.
- 같은 지연으로 이벤트가 두 번 나오지 않는다. 제안 거절 후 더 나빠질 때만 다시 나온다.
- 단계 3이 동시에 두 개 나가지 않는다 (큐).
- 여행일이 아니면 시작이 409 `not_trip_day`.
- 가벼운 지연(새 위반 없음)은 이벤트가 없다.
- 장면 ③·④ 재생 파일이 기대 이벤트를 낸다.

---

## 9. 설정 (`backend/.env`, 접두사 `TRIP_`)

| 변수 | 기본 | 설명 |
|---|---|---|
| `TRIP_TICK_SEC` | 0 (compose 30) | 백그라운드 틱. 0 이면 끔 (테스트) |
| `TRIP_CLOCK` | `safety` | `safety` \| `system` (§5) |
| `TRIP_ARRIVE_RADIUS_M` | 60 | 도착 반경 (+ accuracy, 최대 150) |
| `TRIP_ARRIVE_DWELL_SEC` | 90 | 도착 판정 머무름 |
| `TRIP_DEPART_DWELL_SEC` | 120 | 출발 판정 |
| `TRIP_MAX_ACCURACY_M` | 100 | 이보다 부정확한 점은 버림 |
| `TRIP_HAZARD_NEAR_M` | 50 | 위험 구역 접근 거리 |
| `TRIP_PROACTIVE_COOLDOWN_MIN` | 10 | 단계 3 쿨다운 (안전 제외) |
| `TRIP_AUTO_END_MIN` | 120 | 마지막 일정 후 자동 종료 |
| `TRIP_PLANNER_BASE_URL` 등 | `http://gateway` | 다른 서비스 주소 (compose) |

---

## 10. 구현 순서 (권장)

1. **planner 변경** (§6.1 progress, §6.2 projection) — 다른 모든 판정의 기반. planner 담당.
2. trip 골격: 여행일 시작·종료·상태, 위치 API, 시계, 테이블, compose·게이트웨이 등록 (공용 파일은 별도 커밋).
3. 도착·출발 판정 + 해설 카드 이벤트 (장면 ②의 이벤트 부분)
4. 지연 판정 (projection) + 백그라운드 틱
5. planner 안전 경고 수집 + `near_hazard`
6. Event Policy (큐·중복 억제·쿨다운)
7. agent: `POST /agent/sessions/{sid}/events`, 동행 모드 프롬프트·현재 상황 블록 (agent 담당)
8. CLI·재생기, 장면 ③·④ 재생 파일과 테스트
