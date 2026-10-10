# trip — 여행 중 상태·이벤트 API 명세

> 상태: **구현됨** (§10). agent 쪽 선제 턴 `POST /agent/sessions/{sid}/events` 와 동행 모드 프롬프트(§4.2, §4.4)도 연결됐다.
> 선제 턴 전송이 실패하면 간격을 늘려 다시 보내고, `TRIP_PROACTIVE_MAX_ATTEMPTS`(3)번 실패하면 이벤트를 `failed` 로 남긴다 (§4.1).
> planner 쪽 기반(§6.1 진행 상태, §6.2 현재 기준 재계산, §5 planner 시계)은 `docs/planner/README.md` §8.

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
| 이미 진행 중인 여행일이 있음 | 같은 날이면 그대로 돌려준다(멱등, `session_id` 를 주면 그것만 바꾼다). 다른 날이면 이전 것을 종료(`auto`)하고 새로 시작 |
| 같은 날 종료했던 여행일이 있음 | 그 여행일을 다시 연다. 이미 알린 원인(§4.1 중복 억제)도 그대로 기억한다 |
| planner 에 이미 머무는 항목(`visiting`)이 있음 | 그 항목에 머무는 상태(`arrived_at` 부터)로 시작한다 |
| 실수로 시작 | `POST …/end` 로 종료. 진행 기록 외에는 바뀐 것이 없다 |
| 종료하지 않음 | 그날 마지막 일정 종료 + `TRIP_AUTO_END_MIN`(기본 120분) 또는 자정이 지나면 자동 종료 (`ended_reason: auto`). 마지막 일정 종료는 **기준 일정의 종료와 실제 완료 시각(`done_at`) 중 늦은 것**이다 — 재계산 종료는 아무 데도 가지 않으면 지금을 따라 계속 밀려서 쓰지 않는다 |
| 종료될 때 | 아직 보내지 못한 단계 3(`queued`)은 `suppressed`. `day_ended` 이벤트(`payload.reason`)를 남긴다 |

앱 권장 동작: 오늘이 여행일이고 (첫 일정 시작 1시간 전 이후 또는 구룡포 반경 3km 안) 이면 "오늘 여행 시작할까요?"를 띄운다. **자동으로 시작하지는 않는다** (여행일에 집에서 계획만 고치는 경우).

### 1.3 API

| 메서드 | 경로 | 본문 | 성공 | 오류 |
|---|---|---|---|---|
| POST | `/trip/plans/{plan_id}/start` | `{"session_id"?: "agent 세션 id"}` | 200 `TripState` | 404 `plan_not_found`, 409 `not_trip_day` |
| POST | `/trip/plans/{plan_id}/end` | | 200 `TripState` (`active=false`) | 404 |
| GET | `/trip/plans/{plan_id}/state` | | 200 `TripState` | 404 |
| POST | `/trip/plans/{plan_id}/tick?now=` | | 200 `{"state": TripState, "events": [Event]}` | 409 `trip_not_active` |
| DELETE | `/trip/me` | | 204 (탈퇴: 이 사용자의 여행 상태·이벤트 삭제) | |

- `session_id`: 선제 턴(§4)을 보낼 Agent 세션. 생략하면 그 계획의 가장 최근 세션을 쓰고, 없으면 trip 이 Agent 에 새로 만든다.
  agent 에 접근할 수 없으면 `null` 로 두고 선제 턴을 보낼 때 다시 정한다.
- `state`: 여행일을 시작한 적이 없으면 `active=false` 이고 나머지는 `null` (계획이 없거나 남의 것이면 404 `plan_not_found`). 종료한 여행일이 있으면 그 마지막 상태.
- `tick`: 백그라운드 틱(§4.3)을 이 계획에 지금 한 번 돌리고 큐를 보낸다. CLI·재생기가 틱 주기를 기다리지 않을 때 쓴다.
  `now`(ISO 시각)를 주면 그 시각으로 판정한다 (재생기가 재생 시각을 넘긴다).
- 인증은 다른 서비스와 같다 (`app.core.auth.CurrentUser`, planner 를 부를 때는 받은 인증을 그대로 전달).
- 오류 형식은 planner 와 같다 (`{"detail": {"code", "message", …}}`, 요청 형식 오류는 422 `invalid_request`). planner 에 접근할 수 없으면 503 `planner_unavailable`.

```jsonc
// TripState
{"plan_id": 3, "active": true, "day": 1, "date": "2026-10-10",
 "started_at": "…", "ended_at": null, "ended_reason": null,          // user | auto
 "now": "2026-10-10T14:20:00+09:00", "clock": "sim",                 // system | sim (§5)
 "location": {"lat": 35.987, "lng": 129.558, "accuracy": 12, "recorded_at": "…", "source": "app"},  // 마지막 위치. 없으면 null
 "current": {"item_id": 12, "state": "visiting", "since": "14:05"},  // visiting: 장소 안 / moving: 이동 중 (item_id = 떠나온 항목) / idle: 시작 전·끝
 "next": {"item_id": 13, "title": "구룡포과메기문화관", "planned_start": "15:00", "eta": "15:12"},
 "delay_minutes": 12,                                                // 다음 일정 기준 예상 지연 (음수 = 여유)
 "session_id": "9288a236…"}
```

---

## 2. 위치

### 2.1 API

| 메서드 | 경로 | 본문 | 성공 |
|---|---|---|---|
| POST | `/trip/plans/{plan_id}/location` | `LocationIn` 또는 `{"points": [LocationIn, …]}` (최대 100, 오래된 순) | 200 `{"state": TripState, "events": [Event, …], "accepted": n, "ignored": n}` (이번 요청으로 생긴 이벤트, 판정에 쓴 점·버린 점 수) |

```jsonc
// LocationIn
{"lat": 35.987, "lng": 129.558,
 "accuracy": 12,                         // m, 선택. 없으면 30 으로 본다
 "recorded_at": "2026-10-10T14:20:00+09:00",   // 선택. 없으면 서버 now
 "source": "app"}                        // app | cli | replay | sim
```

- 진행 중인 여행일이 없으면 409 `trip_not_active` (위치를 저장하지 않는다).
- `accuracy` > `TRIP_MAX_ACCURACY_M`(기본 100)인 점은 버린다 (응답 `ignored` 수에 포함).
- `recorded_at` 이 마지막 점보다 이르면 버린다 (순서 뒤바뀜). 같은 시각은 받는다. 시간대가 없으면 KST 로 본다.
- 개인정보: **마지막 위치와 이벤트만 저장**한다. 전체 이동 기록은 남기지 않는다 (판정에는 §7 의 시각 몇 개만 쓴다).
- 처리 순서: 점마다 도착·출발 판정 → 그때마다 planner 진행 상태 반영(§6.1) → 마지막 점 기준으로 재계산(§6.2)·지연 판정 → 위험 구역 판정 → Event Policy.
  진행 상태 반영이 planner 장애로 실패하면 503 `planner_unavailable` 이고 아무것도 저장하지 않는다 (앱이 다시 보내면 다시 판정).
  그 사이 항목이 빠졌거나 건너뜀이 된 경우(404·409·422)는 기록만 못 하고 계속한다. 재계산·safety 실패는 그 판정만 건너뛴다.

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
| `delay` | 현재 위치·시각 기준으로 남은 일정을 다시 계산(§6.2)했을 때 **기준 일정에 없던 위반이 새로 생김**: `fixed_time_missed`, `day_overflow`, `outside_open_hours`, `closed_day`, hard 규칙 위반 | 3 | Agent 선제 턴: 근거·영향 설명 + 추천안 1개(제안 카드) + "다른 방법" 한 줄 → 승인 (대화로 조건을 붙이면 다시 제안) | ③ |
| (가벼운 지연) | 늦어졌지만 새 위반 없음 | 0 | `delay_minutes`·`eta` 만 갱신. 알리지 않음 | |
| `safety_raised` | planner 안전 경고 중 `needs_action=true`, 오늘 남은 항목에 해당 (`GET /planner/plans/{id}/safety/alerts?status=new`) | 3 | Agent 선제 턴: 위험 근거 + 추천 대체안 1개(시간 이동·실내 대체, 제안 카드) + "다른 방법" 한 줄 → 승인 | ④ |
| `safety_eased` | planner 경고의 `lowered`/`cleared` | 1 | "방파제 풍랑 주의가 해제됐어요" | |
| `near_hazard` | **지금 위치**가 safety `/safety/avoid` 구역 안이거나 `TRIP_HAZARD_NEAR_M`(50m) 안 | 1 | **LLM 없이 즉시** 고정 경고: "침수 감시 구역(구룡포수협 주변) 근처예요. 물이 고인 길은 피하세요." | ④ 보조 |
| `day_started` / `day_ended` | 시작·종료 | 0 | 기록만 | |

- 지연을 "N분 늦음"이 아니라 **"새 위반이 생김"**으로 정의한다. 늦어서 실제로 문제가 생길 때만 알리고, 운영시간 종료(`closure`)도 같은 판정으로 잡힌다. 판정 로직은 planner 검증을 그대로 쓴다.
  `new_violations` 중 위 코드와 hard 규칙(`constraint_id` 가 있고 `error`)만 본다. 이동 근사(`travel_estimated`)·긴 도보 같은 경고는 지연이 아니다.
- `delay` 판정은 위치가 들어올 때와 백그라운드 틱(§4.3)마다 한다. 위치가 안 들어와도 시간이 흐르면 지연이 생기기 때문이다.
- 재계산의 위치: 머무는 중이면 `visiting`(+ 도착 시각), 떠난 뒤면 `moving`(마지막 위치), 아직 아무 데도 도착하지 않았으면 위치 없이 보낸다.
  위치 없이 보내면 planner 는 첫 항목부터 지금 출발로 본다 — 첫 일정 시각이 한참 지나 여행을 시작하면 그것만으로 `delay` 가 나올 수 있다 (실제로 늦은 것이다).
- `arrived`·`departed` 시각은 **반경에 처음 들어온 시각 / 처음 나간 시각**이고, planner 진행 상태의 `at` 도 이 시각이다.
  부정확한 점·순서가 뒤바뀐 점은 머무름을 끊지도 잇지도 않는다. 머무는 항목이 일정에서 빠지거나 다른 곳에서 완료되면 이벤트 없이 `idle` 이 된다.
- `departed` 문구: "가옥거리을(를) 떠났어요. 다음은 과메기문화관, 도보 약 8분 (15:12 도착 예정)." (다음 항목·예상 도착은 같은 요청의 재계산 결과)
- `near_hazard`: 걸린 구역을 하나로 묶고, 가까운 구역(같은 거리면 더 위험한 구역)으로 문구를 만든다. 원인 키는 구역 + 단계라 같은 구역이 `caution` → `danger` 로 오르면 다시 알린다.
  구역은 safety 상황(`fingerprint`)이 바뀔 때만 다시 받고, `TRIP_HAZARD_CACHE_SEC`(5초) 동안은 상황도 다시 묻지 않는다. 틱마다 새로 본다.
- `safety_raised`·`safety_eased`: 경고의 `changes` 중 **오늘 날짜, 오늘 남은 항목(planned·visiting)** 의 것만 본다. 여러 항목이면 첫 항목으로 문구를 만들고 "(그 밖에 n건)".
  - 한계: 위험했던 항목이 이미 지난 뒤 상황이 풀리면 내려갈 항목이 없어 `safety_eased` 가 나오지 않는다 ("상황이 끝났다"는 신호 없음). 열린 문제: [docs/console/README.md §7.6](../console/README.md).

### 3.3 Event 형식

```jsonc
{"id": 41, "plan_id": 3, "day": 1, "type": "delay", "level": 3,        // 3.1 단계 (arrived 2, departed·near_hazard·safety_eased 1, delay·safety_raised 3, day_* 0)
 "severity": "warning",                                              // info | warning | danger
 "created_at": "…", "now": "2026-10-10T15:05:00+09:00",
 "item_ids": [13, 14],                                               // 영향받는 항목
 "message": "과메기문화관 도착이 18:10으로 예상돼 운영시간(18:00) 이후예요.",   // 사람이 읽는 한 줄 (고정 문구)
 "payload": {"new_violations": [Violation, …], "delay_minutes": 35},          // 타입별 근거
 "status": "delivered",                       // queued | delivered | suppressed | handled | failed
 "agent": {"session_id": "…", "message_id": 102, "proposal_id": 57},          // 단계 3: Agent 결과. 아니면 null
 "card": {"kind": "docent", "place_id": "kto-…", "item_id": 12},             // 단계 2: 카드 정보. 아니면 null
 "seen_at": null, "attempts": 1, "last_error": null}                          // 확인 시각 / 단계 3 전송 시도 수(agent_busy 는 세지 않음)·마지막 실패 사유
```
- `status`: 단계 0~2 는 만들 때 `delivered` (앱이 폴링으로 가져간다). 단계 3 은 `queued` 로 만들고 Agent 에 보내면 `delivered`. `seen` 을 부르면 `handled`.
  단계 3 전송이 `TRIP_PROACTIVE_MAX_ATTEMPTS` 번 실패하면 `failed` (더 보내지 않는다, `last_error` 에 사유). 앱은 이벤트 목록에서 보고 "Agent 에 전하지 못했어요" 같이 알릴 수 있다.
  `failed` 에 `seen` 을 불러도 `failed` 그대로다 (`seen_at` 만 남는다).
- `payload`: `arrived` {item_id, place_id, title, out_of_order} / `departed` {item_id, title, stayed_minutes} /
  `delay` {new_violations, delay_minutes, next} / `near_hazard` {zones: [{id, name, kind, risk, reason, distance_m}]} /
  `safety_raised` {alert_id, situation, changes} / `safety_eased` {alert_id, changes} / `day_ended` {reason}

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/trip/plans/{plan_id}/events?after={id}&limit=50` | `after` 이후 이벤트 (오래된 순). 앱은 5~10초마다 폴링 |
| POST | `/trip/plans/{plan_id}/events/{event_id}/seen` | 확인 표시 (`seen_at`, `delivered` → `handled`). 없거나 남의 것이면 404 `event_not_found` |

---

## 4. Event Policy

### 4.1 규칙
- **우선순위**: 안전(`near_hazard`, `safety_raised`) > 일정 영향(`delay`) > 안내(`arrived`, `departed`). 한 요청에서 여러 이벤트가 나오면 이 순서로 id 를 매긴다.
- **단계 3은 한 번에 하나.** 진행 중인 선제 턴이 있거나 Agent 세션이 응답 중(409 `agent_busy`)이면 `queued` 로 두고 끝난 뒤 보낸다. 큐에서는 우선순위 높은 것부터, 같은 원인의 오래된 것은 `suppressed`.
  - 같은 원인 묶음: `delay` 는 하나 (새 이벤트가 그날 일정의 지금 위반 전체를 담는다), `safety_raised` 는 같은 항목 묶음끼리.
  - 보내는 때: 위치·틱 요청의 응답 뒤(백그라운드, LLM 응답을 기다리지 않게)와 백그라운드 틱마다. 한 여행일의 전송은 한 번에 하나만 돈다 (프로세스 안 잠금).
  - Agent 가 응답 중(409 `agent_busy`)이면 `queued` 로 두고 `last_error` 만 남긴 뒤 다음 틱에 다시 보낸다 (실패로 세지 않는다 — 대화가 길어져도 버리지 않게).
  - 그 밖의 실패(접근 불가, 5xx, 404, 세션을 정하지 못함)는 `attempts`·`last_error` 를 남기고 **간격을 늘려** 다시 보낸다:
    `TRIP_PROACTIVE_RETRY_SEC`(30초) × 2^(실패 수 − 1) 뒤 (30초 → 60초 …, trip 의 지금 기준). `TRIP_PROACTIVE_MAX_ATTEMPTS`(3)번 실패하면 `failed` 로 남기고
    큐의 다음 이벤트로 넘어간다. 기다리는 시각은 프로세스 메모리에만 둔다 (다시 시작하면 한 번 바로 보내 볼 뿐, 실패 수는 DB 에 있어 상한은 지킨다).
    가상 시각을 되돌리면(실패 시각보다 이름) 기다리지 않는다.
    404 `session_not_found` 면 세션을 비우고 다음에 다시 정한다.
- **중복 억제**: 같은 원인 키로는 다시 알리지 않는다. 사용자가 그 이벤트로 나온 제안을 **거절**하면, 상황이 더 나빠질 때(새 위반, 단계 상승)만 다시 알린다.
  거절을 따로 추적하지 않고 원인 키로 처리한다: 거절해도 같은 원인 키만 남아 조용하고, 나빠지면 새 원인 키가 생긴다. 원인 키는 여행일마다 따로다.

  | type | 원인 키 |
  |---|---|
  | `delay` | 새 위반마다 `delay:{code}:{day}:{item_id}:{constraint_id}` 를 `\|` 로 묶음. **하나라도 새것**이면 알린다 |
  | `near_hazard` | 걸린 구역마다 `near_hazard:{zone_id}:{risk}` (`\|` 로 묶음) |
  | `safety_raised` / `safety_eased` | planner 경고 id (planner 가 단계가 바뀔 때만 경고를 만든다) |
  | `arrived` / `departed` | 항목 id |
- **쿨다운**: 단계 3은 `TRIP_PROACTIVE_COOLDOWN_MIN`(기본 10분, trip 의 지금 기준) 안에 하나만. 안전(`safety_raised`)은 쿨다운을 무시한다.
- **여행 중일 때만**: 단계 1~3은 진행 중인 여행일에만 낸다. 여행 전의 안전 경고는 planner 경고로 남아 앱이 계획 화면에 배지로 보여준다 (trip 관여 없음).

### 4.2 Agent 선제 턴 (agent 쪽에 새로 필요)

```
POST /agent/sessions/{session_id}/events          (내부 호출, 앱은 부르지 않는다)
{"event": Event, "trip": TripState}
→ 200 ChatResult (stream=false)
```
- Agent 는 사용자 메시지 대신 숨김 메시지 `[이벤트] …`(이벤트 요약 + 근거)로 턴을 시작한다. 대화 기록에는 assistant 답변과 카드만 보인다.
- 시스템 프롬프트에 동행 모드 섹션과 "현재 상황" 블록(§4.4)을 넣는다.
- 보내는 `TripState.now` 는 이벤트의 판정 시각(`event.now`)보다 이르지 않다 (가상 시각을 크게 옮긴 직후 trip 시계 캐시가 늦어도 Agent 가 이벤트보다 앞선 '지금'을 보지 않게).
- 결과(답변 message id, 만든 proposal id)를 trip 이 이벤트의 `agent` 필드에 기록한다. 앱은 이벤트를 보고 대화 화면에 새 메시지를 띄운다 (또는 세션을 다시 조회).
  trip 은 `ChatResult` 의 `message_id`(agent 가 응답에 넣어 주어야 한다, 없으면 null)와 `cards` 의 첫 `proposal` 카드 id(`id` 또는 `proposal.id`)를 읽는다.
- 본문의 `event` 는 §3.3 Event, `trip` 은 §1.3 TripState 그대로다 (보낼 때 `event.status` 는 `queued`, `agent` 는 null).
- **인증**: 백그라운드에서 부를 때는 사용자 요청이 없다. trip 은 `app.core.auth.issue_token(user_id, ttl=5분)` 으로 그 사용자의 짧은 토큰을 만들어 부른다 (모든 서비스가 같은 `JWT_SECRET` 을 쓴다).

### 4.3 백그라운드 틱
`backend/app/trip/background.py` 의 `running()` (규칙: `docs/COLLABORATION.md` §1). `TRIP_TICK_SEC`(compose 기본 30초, 코드 기본 0 = 끔)마다:
1. 진행 중인 여행일마다 지연 재판정 (§6.2)
2. planner 안전 경고 수집 (`status=new`) → `safety_raised` / `safety_eased`, 가져간 경고는 `seen` 처리.
   오늘 남은 항목의 변화가 없는 경고(다른 날·완료 항목)는 가져가지 않는다 — `new` 로 남아 계획 화면 배지로 보인다
3. 자동 종료 판정
4. 큐에 있는 단계 3 이벤트 전송 시도 (여행일마다 차례로)

틱은 위험 구역 캐시도 비운다. 사용자 요청 없이 다른 서비스를 부르므로 그 사용자의 5분짜리 토큰을 만들어 쓴다.

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

- 기준은 safety 의 시각이다 (`PUT /safety/sim` 의 `now`, `SAFETY_SOURCE=mock` 또는 `SAFETY_SIM_ENABLED=true` 일 때 — 후자는 실제 데이터 모드에서도 가상 시각을 쓴다, docs/safety/README.md §1). safety 가 시각의 단일 원천이다.
- trip 은 `TRIP_CLOCK=safety` 이면 `GET /safety/sim` 의 `now`(실제 시간만큼 흘러감)를 5초 캐시해서 쓰고, 실패하면 시스템 시각. `TRIP_CLOCK=system` 이면 시스템 시각만.
- 위치의 `recorded_at` 은 판정에 쓰는 시각이다. 재생기는 시뮬레이션 시각 기준 `recorded_at` 을 넣는다.
- planner 는 같은 규칙(`PLANNER_CLOCK=safety`, `GET /safety/sim` 5초 캐시, 실패하면 시스템 시각)으로 맞췄다 (`docs/planner/README.md` §8.3).
  진행 상태 `at`·재계산 `day`/`now` 의 기본값, 안전 점검 날짜 범위(`safety_watch._today`)가 이 시계를 쓴다.
- **확인 필요**: agent 의 날짜 표시(`prompts.py`)는 아직 시스템 시각이다. 데모 전에 같은 시계를 쓰도록 맞춘다.
- trip 의 `TripState.clock` 은 safety 가상 시각을 쓰는 중이면 `sim`, 아니면 `system`.
- 가상 시각을 크게 옮긴 직후 몇 초(캐시)는 trip·planner 가 이전 시각을 본다. 재생기는 시작 전에 trip 의 지금이 맞춰질 때까지 기다리고,
  틱에는 재생 시각(`now`)을 넘긴다. 위치는 `recorded_at` 으로 판정하므로 캐시와 상관없다.
  `--speed 0` 이면 틱 전과 재생이 끝난 뒤에도 trip 의 지금이 재생 시각을 따라올 때까지 기다린다 (틱이 보내는 선제 턴과 이어서 하는 `trip tick` 이 맞는 시각을 보게).
  다만 위치 응답 뒤 백그라운드로 바로 보내는 선제 턴·백그라운드 틱은 몇 초 동안 이전 시각을 볼 수 있다 (선제 턴의 `TripState.now` 는 §4.2 처럼 이벤트 시각으로 맞춘다).

---

## 6. 다른 서비스와의 연결

### 6.1 planner — 진행 상태 (구현됨)
도착·출발은 planner 항목의 `status`(`visiting`·`done`)로 반영한다. 그래야 planner 의 검증·안전 점검이 완료 항목을 뺀다.
명세: `docs/planner/README.md` §8.1.

```
POST /planner/plans/{id}/items/{item_id}/progress
{"status": "visiting" | "done" | "planned", "at": "HH:MM"}     // at 생략 시 planner 의 지금 (§5)
→ 200 PlanDetail
```
- **`version` 을 올리지 않는다.** 대신 `progress_version` 이 1 오른다 (앱 화면 갱신용). 여행 중 도착할 때마다 대기 중인 Agent 제안이 `base_changed`(409)가 되지 않는다.
  대신 제안을 만든 뒤 그 제안이 바꾸는 곳을 떠나면(`done`) 승인은 409 `progress_changed` 이고 앱은 다시 제안(`repropose`)으로 잇는다 (planner §5.4).
- 실제 도착·완료 시각을 항목의 `arrived_at`(visiting), `done_at`(done)에 기록하고 `ItemView` 에 노출한다 (§6.2 재계산 기준). `planned` 는 둘 다 지운다.
- 제안을 승인할 때(`auto`·`overwrite` 모두) 진행 상태는 지금 값을 유지한다. 변경 요약에도 진행 상태 변화는 나오지 않는다.
- `skipped` 는 일정 변경이므로 지금처럼 `PATCH …/items/{item_id}` 로 하고 `version` 이 오른다. 건너뛴 항목에 진행 상태를 보내면 409 `item_skipped`.
- planner 는 한 항목만 바꾼다. 출발(`departed`) 때 `done`, 다음 장소 도착 때 `visiting` 을 보내는 것은 trip 이 정한다.

### 6.2 planner — 현재 기준 재계산 (구현됨)
지연 판정에 필요하다. 시간 모델은 planner 소유이므로 planner 가 제공한다. 명세: `docs/planner/README.md` §8.2.

```
POST /planner/plans/{id}/projection        (저장 안 함, 본문·필드 모두 선택)
{"day": 1, "now": "15:05",                                                    // 생략 시 planner 의 오늘·지금 (§5)
 "position": {"state": "visiting", "item_id": 12, "arrived_at": "14:05"}     // 장소 안 (arrived_at 생략 시 항목의 기록)
         | {"state": "moving", "lat": 35.98, "lng": 129.55}}                  // 이동 중. 생략하면 저장된 진행 상태로 판단
→ 200 {"day": 1, "now": "15:05", "plan": PlanDetail, "baseline_violations": [...], "new_violations": [...],
       "delay_minutes": 35,                                                   // 다음 항목 시작 시각 차이 (음수 = 여유)
       "next": {"item_id": 13, "planned_start": "15:00", "eta": "15:35", "start_at": "15:35"}}   // TripState.next 에 그대로 쓴다
```
- `done` 항목은 기록된 시각 그대로, 현재 항목은 실제 도착 시각부터 (예정 체류가 이미 지났으면 `now` 에 끝난다고 본다), 이동 중이면 `now + 현재 위치 → 다음 장소 이동시간`(routing)에 도착한다고 보고 남은 항목을 다시 계산한다.
  이동 중의 '다음 장소'는 마지막으로 다녀온·머문 항목 뒤의 첫 `planned` 항목이다.
- 지난 항목과 지금 머무는 항목의 고정 시각 놓침은 따지지 않는다 (이미 지난 일). 지금 머무는 곳의 운영시간 종료는 따진다.
- 사용자가 고정한 `start_time` 은 그대로 고정이다 (늦으면 `fixed_time_missed`).
- `new_violations` = 재계산 위반 중 기준 일정(`GET /validate`)에 없던 것 (코드 + 날짜 + 항목 + 규칙 기준). trip 의 `delay` 판정은 이것만 본다.
  늦어도 새 위반이 없으면 빈 목록이다 (가벼운 지연: `delay_minutes`·`next.eta` 만 갱신).
- 오늘이 계획 기간 밖이면(`day` 생략 시) 422 `day_out_of_range`.

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
| `trip_day` | plan_id, user_id, day, date, session_id, started_at, ended_at, ended_reason, 마지막 위치(lat, lng, accuracy, recorded_at, source), current_item_id, current_state, current_since, 머무름 판정(cand_item_id, cand_since, outside_since), delay_minutes, next(JSON) |
| `trip_event` | 3.3 Event 필드 + 원인 키(`cause_key`, 중복 억제용), delivered_at(쿨다운 기준) |

판정용 최근 위치(머무름 계산)는 `trip_day` 에 "반경 안에 처음 들어온 항목·시각(`cand_*`) / 반경 밖으로 처음 나간 시각(`outside_since`)"만 둔다. 전체 궤적은 저장하지 않는다.
planner 테이블을 참조하지 않는다 (`plan_id` 는 숫자일 뿐). 시각은 trip 의 지금(§5) 기준이다.

---

## 8. CLI·재생기·테스트

`scripts/planner/cli.py` 에 trip·sim 명령을 등록한다 (명령 구현: `scripts/trip/trip_cli.py`, 같은 `GURYONG_API_URL`, 로그인).
planner 의 진행 상태·재계산(`progress`·`projection`)과 safety·routing·docent 명령은 `docs/planner/README.md` §11.

```text
trip start 3 [--session SID]          여행일 시작 / trip end 3 / trip state 3 [--json]
trip loc 3 35.987 129.558 [--acc 10] [--at 14:20]     위치 한 점
trip goto 3 kto-2660046 [--wait 120]  그 장소 좌표로 30초마다 점을 보내고, 가상 시각을 머무름 시간만큼 진행 (도착 판정 확인용)
trip replay 3 scene3_delay [--speed 10] [-v]   경로 재생 (plan 자리에 new: 재생 파일의 계획을 새로 만듦)
trip events 3 [--after N] [--follow]  이벤트 보기 (--follow: 3초마다 폴링. failed: 선제 턴 전송 실패, 시도 수·사유 표시)
trip events 3 --seen 41               이벤트 확인 표시 (delivered -> handled)
trip tick 3 [--at 15:45]              틱을 지금 한 번 (지연·안전 경고·자동 종료·선제 턴 전송)
sim                                   가상 상황·가상 시각·날씨 시간표 보기 (GET /safety/sim)
sim scenario storm [--until 17:00]    시간표를 프리셋으로 (지금 기준 절대 구간, --until: 끝 시각 HH:MM 또는 ISO)
sim add storm 15:00 17:00 [--date 2026-10-10]   날씨 구간 추가 (storm|rain|flood|wind, 끝은 포함 안 함, 생략한 날짜는 가상 시각의 날짜)
sim del 2 / sim clear                 n번째 구간 삭제 / 시간표 비우기 (데이터 모드는 그대로)
sim now 2026-10-10T14:00 (또는 14:00) / sim reset   가상 시각 / 실제 시각으로 (PUT /safety/sim)
sim live / sim mock                   실제 데이터 <-> 가상 상황 (SAFETY_SIM_ENABLED=true). 시간표는 가상 상황에서만 적용
```

```bash
export GURYONG_API_URL=http://127.0.0.1:8000     # backend/.env 에 SAFETY_SOURCE=mock, AUTH_REQUIRED=false
python scripts/planner/cli.py trip replay new scene2_arrival --speed 0   # 장면 ② 도착 → 해설 카드 (LLM 없음)
python scripts/planner/cli.py trip replay new scene3_delay --speed 0     # 장면 ③ (기다리지 않고)
python scripts/planner/cli.py trip replay new scene4_safety --speed 60   # 장면 ④ (60배속)
python scripts/planner/cli.py trip end 7      # 재생이 끝나도 여행일은 진행 중이다. 끝내지 않으면 백그라운드 틱이 계속 판정한다
```

**재생 파일** (`scripts/trip/scenes/*.json`): 데모 장면과 테스트를 같은 파일로 만든다. 장면 ①~④ 를 한 계획으로 이어서 하는 발표 진행은 `scripts/demo/run_demo.py` (같은 재생기 `trip_cli.Player`, docs/demo/README.md). 펼침(시간순 동작 목록)은 `scripts/trip/scene.py` 하나를 CLI 와 pytest 가 같이 쓴다.
세 파일 모두 데모 데이터(`docs/app/README.md` §6.1)의 계획이다: 가옥거리 12:30 → 구룡포항 13:40 → 과메기문화관 14:47 → 북방파제 16:00 → 카페파도 16:53(차량) → 17:38 종료, `end_before 18:00`.
북방파제(`poi-gr-breakwater-north`)는 places 에 직접 정의한 장소다 (docs/places/README.md "직접 정의한 장소").

| 파일 | 장면 | 기대 이벤트 |
|---|---|---|
| `scene2_arrival.json` | ② 12:30 가옥거리 도착 | `arrived`(단계 2) + 해설 카드(`card.kind=docent`), 지연 없음 |
| `scene3_delay.json` | ③ 가옥거리에서 30분 더 머묾 | 처음 60분은 `delay` 없음 → 카페 종료가 18:00 을 넘으면 `delay`(단계 3) 한 번 → 구룡포항 도착 |
| `scene4_safety.json` | ④ 15:20 과메기문화관에서 storm | `safety_raised`(단계 3, 북방파제 위험) → 15:25 선제 제안 승인(북방파제 → 근대문화역사관) → 역사관 도착 → 차로 카페 가는 길에 구룡포수협 주변 `near_hazard`(단계 1) → 카페 도착 |

```jsonc
{"name": "장면 ③ 지연 대응", "description": "…",
 "plan_fixture": "seoyun_mom_1010",          // 이름표 (계획은 아래 plan 으로 만든다)
 "plan": {"title": "엄마랑 구룡포", "start_date": "2026-10-10", "day_start": "12:30", "day_end": "18:00",
          "items": [{"place_id": "kto-1909659", "stay_minutes": 60}, …,
                    {"place_id": "kto-2834344", "stay_minutes": 45, "travel_mode": "car"}],   // travel_mode: 직전 장소에서 오는 수단
          "constraints": [{"type": "end_before", "params": {"time": "18:00"}, "hard": true}]},  // replay new·pytest 가 이 계획을 만든다
 "start": "2026-10-10T12:25:00+09:00", "scenario": "calm",
 "steps": [
   {"at": "12:30", "goto": "kto-1909659", "stay": 60},        // 12:30 에 장소로 가서 60분 머묾 (처음 3분은 30초마다, 그 뒤 5분마다 점)
   {"walk_to": "kto-2610501"},                                // routing 도보 경로선을 따라 걸음 (70m/분, 점 간격 30초. 경로선이 없으면 직선)
   {"drive_to": [35.9912, 129.5572]},                         // 차량 경로선을 따라 감 (400m/분, 점 간격 10초). 장소 자리에 [lat, lng] (경유 지점)도 된다
   {"at": "+95", "sim": {"scenario": "storm"}, "tick": true}, // 시작 +95분(또는 "HH:MM")까지 그 자리에서 기다린 뒤 안전 상황 변경, 틱
   {"sim": {"scenario": "storm", "until": "17:00"}},          // 날씨 시간표 프리셋 + 끝 시각 (HH:MM 은 재생 시각의 날짜). 시나리오는 그 순간 기준 절대 구간
   {"skip": "kto-2660046"},                                   // 그 장소 항목을 건너뜀으로 (사용자가 일정에서 뺌, PATCH status=skipped)
   {"accept": {"remove": "poi-gr-breakwater-north", "add": "kto-2603493"}},  // 가장 최근 선제 턴(단계 3)의 제안을 승인 (앱 [전체 적용]).
                                                              // remove·add 는 기대하는 변경: CLI 는 Agent 로 승인하고 확인만, pytest 의 가짜 planner 는 이대로 바꾼다
   {"expect": {"type": "delay", "level": 3}},                 // 지금까지 이런 이벤트가 있어야 함. 키가 이벤트에 없으면 payload 에서 찾음 (place_id 등)
   {"expect": {"card": {"kind": "docent"}}},                  // 값이 dict 면 그 키만 비교
   {"expect": {"type": "delay", "not": true}}]}               // "not": 없어야 함
```
- 한 단계 안에서는 at → sim → skip → accept → goto → walk_to → drive_to → stay → tick → expect 순서다.
- `skip`·`accept` 는 보내는 중인 선제 턴(단계 3 `queued`)이 끝난 뒤에 한다 (CLI). 사용자는 Agent 의 말을 보고 일정을 바꾸므로,
  `--speed 0` 에서 일정 변경이 응답 도중에 끼어들면 agent 가 "응답을 만드는 동안 일정이 바뀌어" 제안을 버리기 때문이다.
- `--speed` 는 시뮬레이션 시각이 흐르는 배속이다 (0 이면 기다리지 않음). 배속과 상관없이 동작(위치·틱·기대 확인)마다 safety sim 의 `now` 를 재생 시각으로 옮기고,
  `sim` 단계 뒤에는 planner 안전 점검(`POST /planner/safety/scan`, 개발 모드)과 trip 틱(`now` = 재생 시각)을 바로 부른다.
- pytest(`backend/tests/trip/test_scenes.py`)는 같은 파일을 서버 없이 재생한다: 이벤트 판정 함수는 **순수 함수**(`judge.py`, 입력: 오늘 일정·마지막 상태·위치·now → 출력: 새 상태·감지)와
  Event Policy(`policy.py`)로 두고, planner·safety·agent 는 가짜 클라이언트(`tests/trip/fakes.py`)로 바꾼다. 가짜 planner 는 planner 시간 모델을 단순하게 흉내 낸다 (도보 = 직선 × 1.3 / 70m).
  컨테이너에서 재생 파일을 읽도록 compose 가 `./scripts` 를 `/scripts` 로 마운트한다.
- 북방파제 계획으로 되돌린 재생 파일을 각본 LLM(`AGENT_PROVIDER=scripted`, docs/agent/README.md §7.2)으로 확인 (2026-10-09, `SAFETY_SOURCE=mock`, `--speed 0`, LLM 호출 없음):
  장면 ② 3개, ③ 4개, ④ 6개 기대 모두 통과. 같은 재생을 `scripts/app/record_fixtures.py` 가 앱 fixture(`app/test/fixtures/trip/`)로 녹화한다 (docs/app/fixtures.md).
  - ③: 13:53 에 delay (`day_overflow` 18:01 + `end_before`) 한 번 → 선제 턴 "과메기문화관 빼기"(18:08 → 16:53) → "과메기문화관은 유지해줘" 에 `must_include` + 구룡포항 30분(17:45) 새 제안이 이전 제안을 대체.
  - ④: 15:20 safety_raised — 북방파제 방문 안전 → 위험(풍속 16m/s·파고 3.5m 예보, 강풍·풍랑주의보, 방파제 CCTV 실족 감지), 가는 길(도보)과 카페 가는 차량 구간(구룡포수협 맨홀 등급 3)도 위험.
    선제 턴은 북방파제를 근대문화역사관(실내)으로 바꾸는 제안(17:30 → 17:22). 15:25 승인 → 15:52 역사관 도착 → 16:33~16:35 near_hazard 3번(수협 맨홀·파출소 지표면·구룡포교 하천) → 16:39 카페 도착.
  - pytest 의 가짜 planner 는 직선거리 모델이라, 방파제(항구를 돌아감)·카페(해안도로) 구간에 실제 길찾기 거리 비율(`road`)을 줘 실제와 같은 시각에 delay 가 나게 맞췄다 (`tests/trip/fakes.py`).
- 해수욕장 계획(북방파제 대신 구룡포해수욕장, 2026-10-09 잠시 사용)으로 실제 서비스 확인 (`SAFETY_SOURCE=mock`, `--speed 0`, Claude Haiku 5.5): 장면 ② 3개, ③ 4개, ④ 5개 기대 모두 통과.
  - ③: 13:53 에 delay (`day_overflow` 18:02 + `end_before`) 한 번. 선제 턴은 "구룡포항 50분 → 30분, 17:46 종료" 제안.
  - ④: 15:20 safety_raised (해수욕장·가는 길 주의, 카페 가는 차량 구간 위험 — 구룡포수협 맨홀 등급 3). 선제 턴은 "해수욕장 대신 근대문화역사관(실내)" 제안.
    차로 카페에 가는 길에 near_hazard 가 구역마다 하나씩 3번 나온다 (구룡포수협 맨홀 15:49, 파출소 지표면 15:50, 구룡포교 하천 15:51 — 원인 키가 구역별이라).
  - 재생이 끝나도 여행일은 진행 중이라, 끝내지 않은 재생 계획에 백그라운드 틱이 delay·safety_raised 선제 턴을 더 보냈다. 재생 뒤에는 `trip end` 를 부른다.
- 이전 재생 파일(짬홍 → 구룡포항 → 가옥거리 → 과메기문화관 → 근대문화역사관)로 확인한 결과 (2026-10-09): 장면 ③ 기대 4개, 장면 ④ 기대 3개 모두 통과.
  장면 ③ 에서 실제 planner 는 15:37 에 역사관 운영시간(`outside_open_hours`), 16:07 에 하루 초과(`day_overflow`, 더 나빠짐)로 delay 를 두 번 냈고,
  agent 선제 턴 API 가 아직 없어 앞의 것은 `suppressed`, 뒤의 것은 `queued` 로 남아 틱마다 다시 시도했다.
- 선제 턴까지 확인 (2026-10-09, `--speed 0`, gpt-5.4-mini): 장면 ③ 은 delay 선제 턴이 근거 + 추천안 1개(역사관 빼기) + "다른 방법" 한 줄로 답했고,
  카드의 전후 비교는 실제 진행 기준(종료 18:07 → 17:20)이었다. "과메기문화관은 꼭 가고 싶어" 에는 `must_include` 규칙을 넣은 새 제안이 이전 제안을 대체했다.
  재생 도중에 만든 제안이 바꾸는 곳(과메기문화관)을 떠난 뒤 승인하면 409 `progress_changed` 였고, `repropose` 로 다시 제안됐다.
  장면 ④ 는 safety_raised 선제 턴이 위험 근거 + 추천 대체안 1개 + "다른 방법" 한 줄로 답했다.

### 꼭 있어야 할 테스트
- 반경 경계에서 위치가 튀어도(한 점만 안/밖) 도착·출발이 생기지 않는다.
- accuracy 큰 점은 무시된다. 순서가 뒤바뀐 점은 무시된다.
- 같은 지연으로 이벤트가 두 번 나오지 않는다. 제안 거절 후 더 나빠질 때만 다시 나온다.
- 단계 3이 동시에 두 개 나가지 않는다 (큐).
- 여행일이 아니면 시작이 409 `not_trip_day`.
- 가벼운 지연(새 위반 없음)은 이벤트가 없다.
- 장면 ②·③·④ 재생 파일이 기대 이벤트를 낸다.
- 선제 턴 전송이 실패하면 간격을 늘려 다시 보내고, 상한에 닿으면 `failed` (agent_busy 는 세지 않음).

모두 `backend/tests/trip/` 에 있다: `test_judge.py`·`test_policy.py` (순수 함수, DB 없음), `test_api.py` (API + 실제 DB + 가짜 서비스), `test_scenes.py` (재생 파일).
```bash
docker compose run --rm trip pytest tests/trip
```

---

## 9. 설정 (`backend/.env`, 접두사 `TRIP_`)

| 변수 | 기본 | 설명 |
|---|---|---|
| `TRIP_TICK_SEC` | 0 (compose 30) | 백그라운드 틱. 0 이면 끔 (테스트, `tests/conftest.py` 가 0 으로 둔다) |
| `TRIP_CLOCK` | `safety` | `safety` \| `system` (§5) |
| `TRIP_CLOCK_CACHE_SEC` | 5 | safety 가상 시각을 다시 묻지 않는 시간 |
| `TRIP_ARRIVE_RADIUS_M` | 60 | 도착 반경 (+ accuracy) |
| `TRIP_MAX_RADIUS_M` | 150 | 도착·출발 반경 상한 |
| `TRIP_ARRIVE_DWELL_SEC` | 90 | 도착 판정 머무름 |
| `TRIP_DEPART_DWELL_SEC` | 120 | 출발 판정 |
| `TRIP_MAX_ACCURACY_M` | 100 | 이보다 부정확한 점은 버림 |
| `TRIP_DEFAULT_ACCURACY_M` | 30 | accuracy 를 보내지 않은 점 |
| `TRIP_HAZARD_NEAR_M` | 50 | 위험 구역 접근 거리 |
| `TRIP_HAZARD_CACHE_SEC` | 5 | safety 상황(fingerprint)을 다시 묻지 않는 시간 |
| `TRIP_PROACTIVE_COOLDOWN_MIN` | 10 | 단계 3 쿨다운 (안전 제외) |
| `TRIP_PROACTIVE_MAX_ATTEMPTS` | 3 | 선제 턴 전송이 이만큼 실패하면 `failed` (agent_busy 는 세지 않음, §4.1) |
| `TRIP_PROACTIVE_RETRY_SEC` | 30 | 실패 뒤 다시 보내기까지 기다림. 실패마다 두 배 |
| `TRIP_AUTO_END_MIN` | 120 | 마지막 일정 후 자동 종료 |
| `TRIP_PLANNER_BASE_URL`·`TRIP_SAFETY_BASE_URL`·`TRIP_AGENT_BASE_URL` | `http://127.0.0.1:8000` (compose `http://gateway`) | 다른 서비스 주소 |
| `TRIP_TIMEOUT_SEC` / `TRIP_AGENT_TIMEOUT_SEC` | 15 / 300 | 다른 서비스 호출 제한 시간 (선제 턴은 LLM 응답을 기다린다) |

---

## 10. 구현 순서 (권장)

1. ~~**planner 변경** (§6.1 progress, §6.2 projection)~~ — 구현됨 (`docs/planner/README.md` §8).
2. ~~trip 골격: 여행일 시작·종료·상태, 위치 API, 시계, 테이블, compose·게이트웨이 등록~~ — 구현됨
3. ~~도착·출발 판정 + 해설 카드 이벤트 (장면 ②의 이벤트 부분)~~ — 구현됨
4. ~~지연 판정 (projection) + 백그라운드 틱~~ — 구현됨
5. ~~planner 안전 경고 수집 + `near_hazard`~~ — 구현됨
6. ~~Event Policy (큐·중복 억제·쿨다운)~~ — 구현됨
7. ~~agent: `POST /agent/sessions/{sid}/events`, 동행 모드 프롬프트·현재 상황 블록 (agent 담당). 응답 `ChatResult` 에 `message_id` 를 넣어 줄 것 (§4.2)~~ — 구현됨 (`docs/agent/README.md` §11)
8. ~~CLI·재생기, 장면 ③·④ 재생 파일과 테스트~~ — 구현됨

## 11. 구조

| 파일 | 내용 |
|---|---|
| `router.py` | API (§1.3, §2.1, §3.3). 단계 3 이 생기면 응답 뒤에 큐 전송 |
| `service.py` | 흐름: 시작·종료, 위치 → 판정 → 이벤트, 틱(`tick_trip`·`tick_all`), 큐 전송(`dispatch`), 위험 구역 캐시 |
| `judge.py` | **판정 순수 함수**: 도착·출발(`step`), 지연(`judge_delay`), 위험 구역(`judge_hazard`), 안전 경고(`judge_alert`), 자동 종료 |
| `policy.py` | **Event Policy 순수 함수**: 중복 억제(`fresh`), 큐에서 보낼 것·억제할 것(`pick_next`) |
| `clients.py` | planner·safety·agent HTTP 클라이언트 (`get_clients`, 테스트는 가짜로 바꾼다) |
| `clock.py` | 지금 시각 (§5) |
| `geo.py` | 거리, 점 ↔ 다각형 거리 |
| `models.py` / `schemas.py` / `errors.py` / `config.py` | 테이블 (§7) / API 스키마 / 오류 형식 / 설정 (§9) |
| `background.py` | 백그라운드 틱 (§4.3) |
