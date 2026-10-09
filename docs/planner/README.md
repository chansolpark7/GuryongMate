# planner — 여행 일정 API 명세

여행 일정의 단일 진실 원천(Source of Truth)이다. 앱·CLI·Agent가 모두 이 API로 일정을 조회하고 바꾼다.

일정을 바꾸는 경로는 세 가지다. 모두 같은 연산 엔진(`ops.py`)을 쓰고, 성공하면 계획의 `version`이 1 오른다.

| 경로 | 누가 | 반영 시점 |
|---|---|---|
| **직접 편집** (`items`, `constraints`, `PATCH`, `ops`) | 사용자 | 즉시 |
| **변경 제안** (`proposals`) | LLM·이벤트가 만들고 사용자가 승인 | 승인할 때 |
| **장소 추천** (`recommendations`) | LLM이 후보를 만들고 사용자가 하나씩 추가·빼기 | 누를 때마다 즉시 |

여행 중 **진행 상태**(도착·완료 기록, §8.1)는 일정 변경이 아니다. `version` 대신 `progress_version`이 오른다.

일정을 바꾸지 않는 점검은 두 가지다. 둘 다 결정론적이고, 서로 섞지 않는다.

| 점검 | 무엇을 | 결과 |
|---|---|---|
| **검증** (`validate`, §2.4) | 일정 자체의 문제 (운영시간·휴무·고정 시각·규칙) | 위반 `error`/`warning` |
| **안전 점검** (`safety`, §7) | 방문 장소·시각의 안전 (safety 서비스 판단) | 위험 3단계 `safe`/`caution`/`danger` + 주기 점검 경고 |

여행 중에는 검증을 **지금 시각·위치 기준으로 다시 계산**해(`projection`, §8.2) 기준 일정에 없던 위반만 골라 준다 (trip 의 지연 판정).

---

## 1. 공통 규칙

### 1.1 요청
- 경로 앞부분은 `/planner`다. 앱은 게이트웨이(`:8000`)로 접속한다.
- **사용자 인증**: `Authorization: Bearer <access 토큰>`(account 서비스가 발급, `docs/account/README.md`). 개발 모드(`AUTH_REQUIRED=false`)에서만 토큰 대신 `X-User-Id` 헤더를 쓸 수 있다. 토큰이 잘못되면 401 `unauthorized`이고, 다른 사용자의 계획은 존재하지 않는 것과 같게 404로 응답한다.
- 본문은 JSON이다. **정의되지 않은 필드가 들어오면 422**로 거부한다(오타를 조용히 무시하지 않기 위해).
- 시각은 `"HH:MM"`(24시간), 날짜는 `"YYYY-MM-DD"`다.
- 경로의 id는 정수다. 정수가 아니면 422, 없으면 404다.
- `place_id`는 영문·숫자·`-`·`_`만 쓴다 (예: `kto-126128`). 다른 문자가 있으면 422다.
- `null`은 필드 설명에 "null 가능"이라고 적힌 곳에서만 받는다. `ItemCreate`의 `order`·`start_time`, `PlanCreate`의 `end_date`는 `null`이 "생략"과 같고, `ItemUpdate.start_time`의 `null`은 "고정 해제"다.

### 1.2 응답
- 시각은 `"HH:MM"`으로 내보낸다. 자동 계산된 시각이 자정을 넘으면 `"24:30"`처럼 24 이상으로 표기한다.
- 일정을 바꾸는 요청은 모두 **바뀐 뒤의 전체 계획(`PlanDetail`)**을 응답한다. 따로 다시 조회하지 않아도 된다.

### 1.3 오류 형식

모든 오류는 같은 형식이다.

```json
{"detail": {"code": "day_out_of_range", "message": "day 는 1~2 사이여야 합니다 (여행 2일).", "...": "코드별 추가 필드"}}
```

| HTTP | code | 언제 | 추가 필드 |
|---|---|---|---|
| 422 | `invalid_request` | 헤더·경로·쿼리·본문이 스키마에 맞지 않음 (필수값 누락, 타입·형식 오류, 범위 밖 값, 모르는 필드, 바꿀 값이 없는 PATCH, null 불가 필드에 null) | `errors: [{loc, msg}]` |
| 422 | `invalid_range` | 기간·하루 시간대 조건 위반 (종료일 < 시작일, 14일 초과, `day_end` ≤ `day_start`), 진행 상태의 완료 시각 < 도착 시각 | |
| 422 | `invalid_position` | 재계산의 `position.item_id`가 그날의 (건너뛰지 않은) 항목이 아님 | `item_id` |
| 422 | `place_not_found` | `place_id`가 places에 없음 | `place_id` |
| 422 | `day_out_of_range` | `day`가 1~여행 일수 밖 | `day`, `num_days` |
| 422 | `duplicate_candidate` | 추천 후보에 같은 (장소, 날짜)가 두 번 | |
| 404 | `plan_not_found` / `item_not_found` / `constraint_not_found` / `proposal_not_found` / `recommendation_not_found` / `candidate_not_found` / `alert_not_found` | 대상이 없음 (또는 다른 사용자 것) | |
| 404 | `not_available` | 개발 모드 전용 API(`POST /planner/safety/scan`)를 `AUTH_REQUIRED=true` 에서 부름 | |
| 409 | `range_conflict` | 기간을 줄이면 잘리는 날에 일정이 남아 있음 | `num_days` |
| 409 | `version_conflict` | 제안 생성 시 `expected_version`이 현재 `version`과 다름 | `expected_version`, `current_version` |
| 409 | `proposal_not_pending` | 이미 승인·거절·대체된 제안을 승인·거절 | `status` |
| 409 | `supersede_conflict` | `supersedes`로 지정한 제안이 대기 상태가 아님 | `status` |
| 409 | `base_changed` | 제안을 기본 모드로 승인하려는데 제안 이후 일정이 바뀜 (§5.4) | `base_version`, `current_version`, `since_proposal`, `overwrite_effect` |
| 409 | `progress_changed` | 제안 이후 다녀온(`done`) 항목을 제안이 바꾸거나 지움 — 어느 모드로도 반영하지 않음 (§5.4) | `items: [{item_id, title, done_at}]` |
| 409 | `already_in_plan` | 추천 후보 [추가]인데 이미 그날 일정에 있음 | `item_id` |
| 409 | `not_in_plan` | 추천 후보 [빼기]인데 그날 일정에 없음 | |
| 409 | `item_skipped` | 건너뛴(`skipped`) 항목의 진행 상태를 바꾸려 함 (다시 넣으려면 `PATCH`) | |
| 503 | `places_unavailable` | places 서비스에 접근할 수 없음 | |
| 503 | `safety_unavailable` | safety 서비스에 접근할 수 없음 (안전 점검) | `reason` |

여러 연산을 한 번에 보내는 요청(`ops`, `preview`, `proposals`)에서 특정 연산이 실패하면 추가 필드 `op_index`(0부터)가 붙는다.

---

## 2. 데이터

### 2.1 시간 모델
- 계획은 기간(`start_date`~`end_date`, 최대 14일)과 매일의 `day_start`/`day_end`를 가진다.
- 항목은 `day`(n일차, 1부터), `order`(그날 순서, 1부터 빈틈없이), `stay_minutes`(5~720)를 가진다.
- **시각은 자동 계산한다.** 하루는 `day_start`에 시작하고, 각 항목은 `직전 항목 종료 + 이동시간`에 도착한다.
  - `start_time`이 있는 항목은 그 시각에 고정된다. 일찍 도착하면 기다리고, 늦으면 `fixed_time_missed` 위반이다.
  - `status=skipped` 항목은 타임라인에서 빠진다(시각 `null`).
- 이동시간은 **routing 서비스**(OSM 포항 데이터로 직접 띄운 길찾기, `docs/routing/README.md`)로 실제 길을 따라 계산한다.
  - **이동 수단은 구간마다 정한다.** 항목의 `travel_mode`(직전 장소에서 여기까지 오는 수단, `walk`|`car`)가 있으면 그 수단,
    없으면(`null`) 계획의 `travel_mode`(기본 수단)를 쓴다. 실제로 계산에 쓴 수단은 `leg_mode`다.
  - 비교용으로 **모든 구간에 도보·차량 시간을 둘 다** 계산해 `travel_options`에 준다 ("이 구간만 차로 가면 22분").
  - 도보는 4.2km/h(70m/분). 차량은 엔진의 주행 시간에 주차·승하차 5분을 더한다.
  - 일정을 계산할 때 필요한 구간(그날 직전에 알려진 장소 → 다음 장소)을 모아 수단별로 **한 번씩** 묻고, 결과는 하루 동안 캐시한다.
  - routing이 응답하지 않거나 그 구간의 길을 못 찾으면 이전 방식으로 근사한다: `직선거리 × 1.3`을 도보 70m/분, 차량 500m/분 + 5분으로 나눈 값. 이때 항목의 `travel_source`가 `estimate`다.
  - routing이 죽었을 때는 20초 동안 묻지 않고 바로 근사한다 (요청마다 타임아웃을 기다리지 않도록).
  - 위험 구역 우회는 일정 시간에 넣지 않는다. 우회는 여행 중 경로 안내(`/routing/route`)에서만 쓴다.

### 2.2 `version`
- 계획을 만들면 `1`이다. **일정을 바꾸는 요청이 성공할 때마다 1 오른다**(직접 편집, `ops`, 제안 승인, 추천 추가·빼기).
- 조회, `preview`, 제안 생성·거절, 추천 생성, **진행 상태 기록(§8.1)**, 재계산(§8.2)은 `version`을 바꾸지 않는다.
- `progress_version`: 계획을 만들면 `0`이다. 진행 상태 API(§8.1)가 성공할 때마다 1 오른다 (앱 화면 갱신용). 제안의 기준 비교에는 쓰지 않는다.
  그래서 여행 중 도착·완료를 기록해도 대기 중인 제안은 `base_changed`가 되지 않는다.
  대신 제안이 그사이 다녀온(`done`) 항목을 바꾸거나 지우면 승인할 때 409 `progress_changed`다 (§5.4).
- 일정을 바꾸는 요청은 계획 행을 잠그고 처리한다. 동시에 들어온 요청은 차례로 처리되므로 같은 `version` 위에 두 변경이 겹치지 않는다(예: 같은 기준의 제안 둘을 동시에 승인하면 하나만 반영되고 나머지는 409 `base_changed`).
- 제안은 만들 때의 `version`(`base_version`)을 기억한다. 둘이 같으면 "제안 이후 일정이 안 바뀜"이다.

### 2.3 `PlanSummary` / `PlanDetail`

```jsonc
// PlanSummary
{"id": 3, "user_id": "seoyun", "title": "엄마랑 구룡포", "start_date": "2026-10-10", "end_date": "2026-10-10",
 "day_start": "12:30", "day_end": "18:00", "travel_mode": "walk",   // 구간 기본 수단
 "memo": "", "version": 7, "progress_version": 3,
 "item_count": 4, "created_at": "...", "updated_at": "..."}

// PlanDetail = PlanSummary + 아래
{"days": [{"day": 1, "date": "2026-10-10", "weekday": "토", "end_at": "16:40",
           "items": [ItemView, ...]}],
 "constraints": [ConstraintView, ...],
 "violations": [Violation, ...]}

// ItemView
{"id": 12, "day": 1, "order": 2, "place_id": "kto-2660046",
 "place": {"id": "kto-2660046", "title": "구룡포과메기문화관", "content_type": "문화시설", "lat": 35.99, "lng": 129.55,
           "addr": "...", "open_hours": "09:00~18:00", "rest_date": "매주 월요일"},   // 못 불러오면 null
 "stay_minutes": 60, "start_time": null, "memo": "", "status": "planned",
 "travel_mode": null,                          // 이 항목에 정한 '여기까지 오는 수단'. null = 계획 기본
 "leg_mode": "walk",                           // 실제로 계산한 수단 (첫 항목·건너뜀은 null)
 "travel_minutes": 10, "travel_distance_m": 584,                                     // leg_mode 기준. 첫 항목·건너뜀은 null
 "travel_source": "road",                     // road: 실제 길 / estimate: 직선거리 근사 (앱은 "약 10분") / 첫 항목·건너뜀은 null
 "travel_options": {"walk": {"minutes": 10, "distance_m": 584, "source": "road"},   // 같은 구간 도보·차량 비교
                    "car": {"minutes": 8, "distance_m": 628, "source": "road"}},     // 첫 항목·건너뜀은 null
 "arrive_at": "14:43", "start_at": "15:00", "end_at": "16:00",                     // 계획 기준 계산값. 건너뜀은 null
 "arrived_at": "14:50", "done_at": null}           // 여행 중 실제 도착·완료 시각 (§8.1). 기록이 없으면 null

// ConstraintView
{"id": 4, "type": "end_before", "params": {"time": "18:00"}, "hard": true, "source": "user", "note": ""}

// Violation
{"code": "day_overflow", "severity": "error", "message": "...", "day": 1, "item_id": 15, "constraint_id": null}
```

### 2.4 검증 (결정론적)

| code | 수준 | 조건 |
|---|---|---|
| `day_overflow` | error | 그날 마지막 일정이 `day_end` 이후에 끝남 |
| `fixed_time_missed` | error | 고정 시작 시각보다 늦게 도착 |
| `closed_day` | warning | 휴무 원문에서 뽑은 "매주 ○요일" 휴무에 해당 (격주·명절은 판단하지 않음) |
| `outside_open_hours` | warning | 운영시간 원문에 시간 범위가 하나뿐일 때 방문이 그 범위를 벗어남 |
| `place_unavailable` | warning | 장소 정보를 불러오지 못함 |
| `long_walk` | warning | 도보 구간 하나가 30분 초과. 메시지에 차량 시간을 함께 준다 (`max_walk_minutes` 규칙이 있으면 그 규칙으로만 본다) |
| `daily_walk_total` | warning | 그날 도보 이동 합계가 90분 초과 (`max_daily_walk_minutes` 규칙이 있으면 그 규칙으로만 본다) |
| `travel_estimated` | warning | 길찾기를 쓰지 못해 직선거리로 추정한 구간이 있음 (그날 하나로 묶음, 시간이 실제와 다를 수 있음) |

이동 검사(`long_walk`·`daily_walk_total`·`travel_estimated`·도보 규칙)는 이미 다녀온(`done`) 항목으로 간 이동을 빼고 본다.
| 규칙 `type` | hard→error, soft→warning | 아래 규칙 위반 |

| 규칙 type | params | 의미 |
|---|---|---|
| `must_include` | `{"place_id"}` | 반드시 포함 |
| `exclude` | `{"place_id"}` | 포함하지 않음 |
| `end_before` | `{"time": "HH:MM", "day"?: n}` | 그날(생략 시 매일) 이 시각 전에 끝남 |
| `max_travel_minutes` | `{"minutes": n}` | 구간 이동시간 상한 (수단 무관) |
| `max_walk_minutes` | `{"minutes": n}` | 도보 구간 하나의 상한 (예: 어르신 동행). 위반 메시지에 차량 시간 |
| `max_daily_walk_minutes` | `{"minutes": n}` | 그날 도보 이동 합계 상한 |

params에 정의되지 않은 키가 있으면 422 `invalid_request`다.

### 2.5 변경 요약 `Change`
사람이 읽는 한 줄이다. 제안 카드와 확인창에 그대로 쓴다.

```json
{"kind": "added", "message": "추가: 1일차 2번째 '카페파도' (30분)", "item_id": 21, "day": 1}
```
`kind`: `added` · `removed` · `moved` · `updated` · `plan`(계획 설정) · `rule_added` · `rule_removed` · `day_end`(그날 종료 시각 변화)

`moved`는 실제로 옮긴 항목만이다. 다른 날로 간 항목과, 같은 날 남은 항목들 사이의 상대 순서가 바뀐 항목이다. 앞에 항목이 추가·삭제돼 번호만 밀린 항목은 `moved`가 아니다.

---

## 3. 계획

| 메서드 | 경로 | 본문 | 성공 | 오류 |
|---|---|---|---|---|
| GET | `/planner/plans` | | 200 `PlanSummary[]` (시작일 순) | |
| POST | `/planner/plans` | `PlanCreate` | 201 `PlanDetail` | 422 `invalid_request`·`invalid_range` |
| GET | `/planner/plans/{plan_id}` | | 200 `PlanDetail` | 404 `plan_not_found` |
| PATCH | `/planner/plans/{plan_id}` | `PlanUpdate` | 200 `PlanDetail` | 404, 422 `invalid_request`·`invalid_range`, 409 `range_conflict` |
| DELETE | `/planner/plans/{plan_id}` | | 204 (제안·추천도 함께 삭제) | 404 |
| DELETE | `/planner/me` | | 204 — 이 사용자의 계획을 모두 삭제 (account 탈퇴가 부른다) | 401 |
| GET | `/planner/plans/{plan_id}/validate` | | 200 `{"ok": bool, "violations": [...]}` (`ok` = error 위반 없음) | 404 |

```jsonc
// PlanCreate
{"title": "1~200자", "start_date": "2026-10-10", "end_date": "생략 시 start_date",
 "day_start": "기본 10:00", "day_end": "기본 18:00", "travel_mode": "walk|car (구간 기본 수단, 기본 walk)", "memo": "0~500자"}
// PlanUpdate: 위 필드 중 바꿀 것만. 하나도 없으면 422, null 은 422. 병합한 결과로 invalid_range·range_conflict 를 검사한다.
```

## 4. 직접 편집

| 메서드 | 경로 | 본문 | 성공 | 오류 |
|---|---|---|---|---|
| POST | `/planner/plans/{plan_id}/items` | `ItemCreate` | 201 `PlanDetail` | 404, 422 `invalid_request`·`place_not_found`·`day_out_of_range`, 503 |
| PATCH | `/planner/plans/{plan_id}/items/{item_id}` | `ItemUpdate` | 200 `PlanDetail` | 404 `item_not_found`, 422 |
| DELETE | `/planner/plans/{plan_id}/items/{item_id}` | | 200 `PlanDetail` | 404 |
| GET | `/planner/plans/{plan_id}/constraints` | | 200 `ConstraintView[]` | 404 |
| POST | `/planner/plans/{plan_id}/constraints` | `ConstraintCreate` | 201 `PlanDetail` | 404, 422, 503 |
| DELETE | `/planner/plans/{plan_id}/constraints/{constraint_id}` | | 200 `PlanDetail` | 404 `constraint_not_found` |
| POST | `/planner/plans/{plan_id}/ops` | `{"ops": PlanOp[1..100]}` | 200 `PlanDetail` | 404, 409, 422, 503 (+`op_index`) |
| POST | `/planner/plans/{plan_id}/preview` | `{"ops": PlanOp[1..100]}` | 200 `{"plan": PlanDetail, "changes": Change[]}` (저장 안 함. 여행 중인 날은 실제 진행 기준, §8.4) | 위와 같음 |

```jsonc
// ItemCreate
{"place_id": "kto-…", "day": 1, "order": "1 이상, 생략 시 맨 뒤 (그날 항목 수보다 크면 맨 뒤)",
 "stay_minutes": "5~720 (기본 60)", "start_time": "HH:MM, 생략 시 자동 계산", "memo": "0~500자",
 "travel_mode": "walk|car — 직전 장소에서 여기까지 오는 수단. 생략·null 이면 계획 기본"}
// ItemUpdate: 보낸 필드만 바꾼다. 하나도 없으면 422. null 이 허용되는 필드는 start_time·travel_mode 둘뿐.
{"day": "다른 날로 이동", "order": "순서 (day 와 함께 주면 그 날의 해당 순서)", "stay_minutes": 30,
 "start_time": "HH:MM 고정 | null = 고정 해제", "memo": "...",
 "status": "planned|visiting|done|skipped",   // 일정 변경이라 version 이 오른다. 여행 중 도착·완료는 §8.1 progress 로
 "travel_mode": "walk|car | null = 계획 기본 수단으로"}
// status 를 planned·skipped 로 바꾸면 arrived_at·done_at 이, visiting 으로 바꾸면 done_at 이 지워진다.
// ConstraintCreate
{"type": "must_include|exclude|end_before|max_travel_minutes|max_walk_minutes|max_daily_walk_minutes", "params": {...}, "hard": true,
 "source": "user|llm (기본 user)", "note": "0~300자"}
```

### PlanOp (`ops`, `preview`, `proposals`에서 사용)

| op | 필드 |
|---|---|
| `add_item` | `ItemCreate`와 같음 |
| `update_item` | `item_id` + `ItemUpdate`와 같음 |
| `remove_item` | `item_id` |
| `update_plan` | `PlanUpdate`와 같음 |
| `add_constraint` | `ConstraintCreate`와 같음 |
| `remove_constraint` | `constraint_id` |

- 한 목록은 **원자적**이다. 하나라도 실패하면 아무것도 반영되지 않는다.
- 같은 목록에서 새로 만든 항목·규칙은 만든 순서대로 **음수 임시 id**(`-1`, `-2`…)로 다시 가리킬 수 있다. `preview` 응답에서도 새 항목의 id는 이 음수다.

---

## 5. 변경 제안 (Proposal)

LLM이 만든 "여러 변경을 묶은 안"이다. 사용자가 승인해야 반영된다.

### 5.1 상태

```
pending ──accept──▶ accepted (decision: applied | overwritten)
   │──reject──▶ rejected
   └──같은 출처의 새 제안이 supersedes 로 지정──▶ superseded
```
- **만료는 없다.** 오래된 제안도 기록으로 남고, 대기 중이면 언제든 승인·거절할 수 있다.
- 승인·거절·대체된 제안은 다시 바꿀 수 없다(409 `proposal_not_pending`).

### 5.2 기록 (스냅샷)
제안을 만들 때 다음을 저장하고 **이후 바뀌지 않는다.** 언제 열어도 그때의 카드가 그대로 보인다.
- `before`: 제안 당시 일정(`PlanDetail`)
- `after`: 승인하면 될 일정(`PlanDetail`, 새 항목 id는 음수)
- `changes`: `before` → `after` 변경 요약(`Change[]`). 카드에는 이 델타를 보여주고, [자세히 보기]에서 `before`/`after` 전체를 보여준다.
- `base_version`: 제안 당시 계획의 `version`
- 여행 중인 날(planner 의 오늘에 진행 기록 `visiting`·`done` 이 있음)은 `before`/`after`·`changes`(특히 `day_end`)를
  **실제 진행 + planner 의 지금** 기준으로 계산한다 (§8.4). 카드의 "1일차 종료: A → B" 가 지금 예상과 맞는다.

### 5.3 API

| 메서드 | 경로 | 본문 | 성공 | 오류 |
|---|---|---|---|---|
| POST | `/planner/plans/{plan_id}/proposals` | `ProposalCreate` | 201 `ProposalDetail` | 404 (`supersedes`가 없는 제안이면 `proposal_not_found`), 409 `version_conflict`·`supersede_conflict`, 422, 503 (+`op_index`) |
| GET | `/planner/plans/{plan_id}/proposals?status=` | | 200 `ProposalSummary[]` (최신순) | 404, 422 (모르는 status) |
| GET | `/planner/plans/{plan_id}/proposals/{proposal_id}` | | 200 `ProposalDetail` | 404 `proposal_not_found` |
| POST | `/planner/plans/{plan_id}/proposals/{proposal_id}/accept` | `{"mode": "auto" \| "overwrite"}` (본문 생략 = auto) | 200 `{"proposal": ProposalDetail, "plan": PlanDetail}` | 404, 409 `proposal_not_pending`·`progress_changed`·`base_changed`, 422 |
| POST | `/planner/plans/{plan_id}/proposals/{proposal_id}/reject` | | 200 `ProposalDetail` | 404, 409 `proposal_not_pending` |

```jsonc
// ProposalCreate
{"ops": [PlanOp, ...],            // 1~100개. 현재 일정에 적용 가능해야 생성된다
 "summary": "0~2000자. 카드 설명",
 "request": "0~4000자. 이 제안을 만든 사용자 요청 원문 (다시 제안할 때 사용)",
 "source": "llm|user|event (기본 llm)", "origin": "0~64자. 출처 식별자 (예: agent 세션 id)",
 "supersedes": 12,                 // 선택. 이 제안으로 대체할 대기 제안 (같은 계획, pending 이어야 함)
 "expected_version": 7}            // 선택. 현재 version 과 다르면 409 version_conflict

// ProposalSummary
{"id": 13, "plan_id": 3, "status": "pending", "decision": null, "summary": "...", "source": "llm", "origin": "…",
 "base_version": 7, "changes": [Change, ...], "created_at": "...", "decided_at": null}
// ProposalDetail = ProposalSummary + 아래
{"request": "...", "ops": [PlanOp, ...], "before": PlanDetail, "after": PlanDetail,
 "current_version": 9,            // 지금 계획의 version
 "base_changed": true,            // current_version != base_version
 "progress_changed": [12]}        // 제안 이후 다녀온(done) 항목 중 이 제안이 바꾸거나 지우는 것 (대기 중일 때만). 있으면 승인은 409
```

### 5.4 승인 규칙

| 승인 요청 | 제안 이후 일정이 안 바뀜 (`base_changed=false`) | 바뀜 (`base_changed=true`) |
|---|---|---|
| `mode: "auto"` (기본) | 반영 (`decision: applied`) | **409 `base_changed`** — 반영하지 않음 |
| `mode: "overwrite"` | 반영 (`applied`와 결과 같음, `decision: overwritten`) | **카드의 `after` 일정 그대로 반영** (`decision: overwritten`) |

409 `base_changed`의 추가 필드로 확인창을 그린다.
- `since_proposal: Change[]` — 제안 이후 일정에 생긴 변경(`before` → 현재). "카드 그대로 반영"을 고르면 **사라지는 변경**이다.
- `overwrite_effect: Change[]` — 지금 일정에서 `after`로 바꾸면 생기는 변경(현재 → `after`).

앱 확인창의 두 버튼:
1. **[카드에서 본 일정 그대로 반영]** → 같은 accept를 `{"mode": "overwrite"}`로 다시 보낸다.
2. **[현재 일정에 맞춰 다시 제안]** → Agent의 `repropose` API(`docs/agent/README.md`)를 부른다.

**덮어쓰기(`overwrite`) 방식**: 계획 설정(제목·기간·시간대·이동 수단·메모)은 `after` 값으로 바꾼다. 항목·규칙은 아래와 같이 맞춘다.
- `after`에 있고 지금 계획에도 있는 항목(양수 id): 그 항목을 `after` 값으로 고친다. id가 유지된다.
- `after`에 있지만 지금 없는 항목(음수 id, 또는 그사이 삭제된 항목): 새로 만든다. 새 id가 붙는다.
- 지금 있지만 `after`에 없는 항목: 삭제한다.
- **진행 상태는 지금 값을 유지한다.** 남아 있는 항목의 `status`(planned/visiting/done)와 `arrived_at`·`done_at`은 바꾸지 않는다
  (스냅샷은 제안 당시라 그 뒤의 도착·완료를 모른다). 건너뜀(`skipped`)으로 바꾸거나 되돌리는 것만 `after`를 따른다.
- 같은 이유로 변경 요약(`changes`, `since_proposal`, `overwrite_effect`)에 planned/visiting/done 사이의 상태 변화는 나오지 않는다.

`auto` 승인은 제안의 `ops`를 지금 일정에 다시 적용하므로 진행 상태는 (ops가 직접 바꾸지 않는 한) 그대로다.

**진행 상태가 바뀐 옛 제안 (409 `progress_changed`)**: 진행 상태는 `version`을 올리지 않으므로(§2.2) `base_changed`로는 잡히지 않는다.
그래서 승인할 때(`auto`·`overwrite` 모두, `base_changed`보다 먼저) 제안 당시(`before`) `done`이 아니었다가 지금 `done`인 항목을
제안이 바꾸거나(변경·이동) 지우면 반영하지 않고 409 `progress_changed`로 거절한다. 제안은 `pending` 그대로 남는다.
- 예: 짬홍에 머무는 동안 만든 "짬홍 체류 60→35분" 제안을 짬홍을 떠난(`done`) 뒤 승인 → 409, `items: [{"item_id", "title": "짬홍", "done_at": "13:50"}]`
- 앱: "그사이 다녀온 곳이 있어 이 제안을 그대로 반영할 수 없어요" + **[현재 상황에 맞춰 다시 제안]** → Agent `repropose`.
  덮어쓰기 버튼은 없다. 승인 전에 `ProposalDetail.progress_changed`가 비어 있지 않으면 카드에 미리 표시할 수 있다.
- 다녀온 곳을 건드리지 않는 제안(남은 곳의 체류·순서 등)은 그대로 승인된다.

---

## 6. 장소 추천 (Recommendation)

LLM이 고른 장소 후보 묶음이다. 사용자가 후보마다 [추가]/[빼기]를 눌러 **바로** 일정에 넣거나 뺀다. 승인 단계는 없다.

### 6.1 후보 상태: 저장하지 않고 지금 일정에서 계산

후보의 상태는 저장하지 않는다. 조회할 때마다 **지금 일정**을 보고 정한다. 그래서 사용자가 버튼 대신 직접 추가·삭제해도, 오래된 추천 카드라도 항상 지금 일정과 맞는다.

| 후보의 `day`에 같은 `place_id` 항목이 | `state` | 앱 버튼 |
|---|---|---|
| 있음 (상태 무관, 건너뜀 포함) | `in_plan` | **[빼기]** |
| 없음 | `not_in_plan` | **[추가]** |
| 계획 기간이 줄어 후보의 `day`가 없어짐 | `day_out_of_range` | 버튼 없음 |

- **시간은 상태 판단에 쓰지 않는다.** 일정 시각은 자동 계산이라 앞 일정이 바뀌면 움직이기 때문이다.
- 같은 날 같은 장소가 둘 이상이면, `time_hint`에 시작 시각이 가장 가까운 항목을 그 후보의 항목(`item`)으로 본다. `time_hint`가 없으면 그날 가장 앞 순서의 항목이다. [빼기]는 이 항목만 지운다.
- 다른 날에 같은 장소가 있어도 상태는 바뀌지 않는다. 참고용으로 `other_days`에 그 날짜들을 준다(같은 장소를 다른 날 또 가는 것은 정상이다).

### 6.2 [추가] 위치
누르는 순간의 일정을 기준으로 정한다. 그래서 오래된 카드여도 동작한다.
- `time_hint`가 있으면: 그날 각 위치(1 ~ 항목 수+1)에 넣어 본 뒤, 계산된 시작 시각이 `time_hint`에 가장 가까운 위치. 같으면 앞 위치.
- `time_hint`가 없으면: 그날 총 이동시간이 가장 적게 늘어나는 위치. 같으면 뒤 위치.
- 체류 시간은 후보의 `stay_minutes`, 메모는 `reason`이다.

### 6.3 API

| 메서드 | 경로 | 본문 | 성공 | 오류 |
|---|---|---|---|---|
| POST | `/planner/plans/{plan_id}/recommendations` | `RecommendationCreate` | 201 `RecommendationView` | 404, 422 `invalid_request`·`place_not_found`·`day_out_of_range`·`duplicate_candidate`, 503 |
| GET | `/planner/plans/{plan_id}/recommendations` | | 200 `RecommendationView[]` (최신순) | 404 |
| GET | `/planner/plans/{plan_id}/recommendations/{recommendation_id}` | | 200 `RecommendationView` | 404 `recommendation_not_found` |
| POST | `/planner/plans/{plan_id}/recommendations/{recommendation_id}/candidates/{candidate_id}/add` | | 200 `{"recommendation": RecommendationView, "plan": PlanDetail}` | 404 `candidate_not_found`, 409 `already_in_plan`, 422 `day_out_of_range`, 503 |
| POST | `/planner/plans/{plan_id}/recommendations/{recommendation_id}/candidates/{candidate_id}/remove` | | 200 위와 같음 | 404, 409 `not_in_plan` (후보의 날짜가 없어진 경우 포함) |

```jsonc
// RecommendationCreate
{"title": "1~100자 (예: 비 올 때 갈 만한 실내 장소)", "source": "llm|user|event (기본 llm)", "origin": "0~64자",
 "candidates": [                   // 1~10개, 같은 (place_id, day) 중복 불가
   {"place_id": "kto-…", "day": 1, "time_hint": "HH:MM (선택)", "stay_minutes": "5~720 (기본 60)", "reason": "0~300자"}]}

// RecommendationView
{"id": 5, "plan_id": 3, "title": "...", "source": "llm", "origin": "…", "created_at": "...",
 "candidates": [{
   "id": 11, "place_id": "kto-2660046", "place": PlaceInfo,          // 못 불러오면 null
   "day": 1, "time_hint": "15:00", "stay_minutes": 60, "reason": "비를 피해 바다를 볼 수 있어요",
   "state": "in_plan",                                               // in_plan | not_in_plan | day_out_of_range
   "item": {"item_id": 23, "day": 1, "order": 3, "start_at": "15:05", "end_at": "16:05"},   // in_plan 일 때만
   "other_days": [2]}]}
```

## 7. 안전 점검 (Safety)

방문과 **그 사이 이동 구간**의 위험 단계를 매긴다. **판단은 safety 서비스가 하고, planner 는 일정 기준으로 묻고 정리하고 기록한다.**
safety 는 안전 정보만 관리하고, 어떤 일정이 있는지는 모른다. 검증(`validation.py`)과는 별개 파일(`safety.py`)이다.

```
planner ── GET /safety/situation (fingerprint) ──▶ safety      바뀌었을 때만
        ── POST /safety/assess (방문 장소·시각) ──▶ safety
        ◀─ 항목별 위험 단계·근거·행동 지침 ─────────
```

**이동 구간**: 직전 장소에서 다음 장소로 가는 길을 routing 의 실제 경로선(`POST /routing/route`)으로 받아,
그 구간 시간(출발 = 도착 − 이동시간 ~ 도착) 동안 그 길을 지나는 것으로 safety 에 묻는다 (`/safety/assess` 의 `path`).
- 도보: 야외 기준. 경로가 지나는 방파제·침수 감시 구역을 그대로 적용한다.
- 차량: 차 안이라 비·바람·특보는 참고(`info`)까지. 침수 감시 구역(잠긴 길)은 그대로, 방파제는 차가 들어가지 않으므로 보지 않는다.
- 경로선을 못 받으면(routing 장애) 두 장소를 잇는 직선으로 대신한다.
- 경로선은 **오늘~4일 뒤 이동만** 받는다 (그 뒤는 예보가 없어 판단하지 않으므로 직선으로 충분). 하루 캐시하고,
  엔진이 길을 못 찾은 구간도 캐시한다. 연결 장애·5xx 면 20초 동안 묻지 않고, 한 구간의 4xx 는 그 구간만 직선으로 대신한다.
- 3시간 안의 이동이 주의·위험이면 routing 의 우회(`hazards=avoid`)를 물어 `detour`(피한 구역, 늘어나는 시간)를 붙인다.
  routing 은 실측이 오른 센서 주변 80m 만 막으므로, 그 안을 지나지 않으면 `detour` 는 없다 (판단 반경 200m 와 다름).

### 7.1 위험 단계

| `risk` | 의미 |
|---|---|
| `safe` | 주의·위험 신호 없음. `hazards` 에 `info`(참고: 약한 비 등)가 있을 수 있다 |
| `caution` | 주의. 사용자에게 알리고 시간 조정·대안 고려 |
| `danger` | 위험. 그 시간대 방문을 피한다 |
| `null` | 판단하지 않음 (단계 아님). `unassessed_reason`: `past` / `beyond_forecast` (약 3일 넘게 남음) / `data_missing` |

`info` 근거는 단계를 올리지 않지만 그대로 돌려준다. 사람·LLM 이 순서·시간을 정할 때 참고할 수 있게 하기 위해서다.
판단 기준·장소 성질(방파제·침수 감시 구역·실내)은 [`docs/safety/README.md` §3](../safety/README.md).

### 7.2 점검 API

| 메서드 | 경로 | 설명 |
|---|---|---|
| `GET` | `/planner/plans/{id}/safety` | 지금 일정 점검 → `SafetyCheck` (저장 안 함) |
| `POST` | `/planner/plans/{id}/safety/preview` | 본문 `{"ops": [...]}` 를 적용했다고 치고 점검 (저장 안 함, 새 항목 `item_id` 는 음수). 변경안 초안용 |

- 점검 대상: 계산된 시작·종료 시각이 있는 항목과 그 항목까지 오는 이동. **건너뜀(`skipped`)·완료(`done`) 항목(과 그 항목까지의 이동)은 뺀다.**
  시작이 자정을 넘는 항목은 날짜가 애매해 빼고, 종료만 자정을 넘으면 60분 방문으로 본다.
- `preview` 의 ops 오류는 `/preview` 와 같다 (`op_index` 포함).
- safety 에 접근할 수 없으면 `503 safety_unavailable`. 일부 데이터 소스만 실패하면 200 이고 해당 항목이 `risk=null (data_missing)`, `data_ok` 에 실패 소스가 `false`.

`SafetyCheck`
```json
{
  "plan_id": 344, "plan_version": 5, "checked_at": "2026-10-04T18:52:00+09:00",
  "overall_risk": "caution",
  "counts": {"safe": 5, "caution": 4, "danger": 0, "unassessed": 0, "info": 3},   // 방문 + 이동 구간
  "guidance": "주의·위험 2곳: 1일차 13:07 구룡포항(주의), … 항목별 advice 를 따르고 …",
  "items": [
    {"item_id": 1201, "kind": "visit", "mode": null, "day": 1, "date": "2026-10-05", "place_id": "kto-2610501", "title": "구룡포항",
     "start_at": "13:07", "end_at": "14:07", "risk": "caution", "unassessed_reason": null, "data_missing": false,
     "horizon": "short_range",
     "hazards": [{"type": "wind", "level": "caution", "reason": "10/05 13~15시 풍속 최대 15m/s 예보", "source": "기상청 단기예보"}],
     "advice": "주의: … 시간 조정이나 대안을 함께 제시하라. 이 장소에 주의·위험 신호가 없는 시간대: …",
     "better_times": [{"start": "2026-10-05T08:00:00+09:00", "end": "2026-10-05T12:00:00+09:00", "has_info": false}],
     "sensitivity": ["침수 감시 구역"], "notes": [], "recheck_from": null},
    {"item_id": 1203, "kind": "travel", "mode": "walk", "title": "구룡포 일본인 가옥거리 → 구룡포 근대문화역사관",
     "start_at": "12:09", "end_at": "12:12", "risk": "caution",            // 출발 ~ 도착
     "hazards": [{"type": "wind", "level": "caution", "reason": "10/05 12시 풍속 15m/s 예보", "source": "기상청 단기예보"}],
     "detour": null, "…": "…"},
    {"item_id": 1203, "kind": "visit", "title": "구룡포 근대문화역사관", "risk": "safe",
     "hazards": [{"type": "wind", "level": "info", "reason": "… (실내라 이동할 때만 영향)", "source": "기상청 단기예보"}], "…": "…"}
  ],
  "data_ok": {"forecast": true, "warnings": true, "sensors": true, "breakwater": true, "places": true}
}
```
- `overall_risk`: 주의·위험이 있으면 그중 최고, 없는데 판단하지 않은 미래 항목이 있으면 `null`, 아니면 `safe` (항목이 없어도 `safe`).
- `counts.info`: `safe` 이면서 참고 신호가 있는 항목 수.
- `items` 는 일정 순서 (그 항목까지 오는 이동 → 방문). 이동의 `item_id`·`place_id` 는 도착 항목 기준이다.
- `better_times` 는 caution·danger 일 때 같은 장소(이동이면 같은 길)에 신호가 없는 시간대.
- `detour` (이동만): `{"on_route": [...], "avoided": [...], "detour_minutes": 4, "note": null}` — 3시간 안 위험 이동에서 우회할 구역이 있을 때.

### 7.3 경고 (Alert)

주기 점검(§7.4)이 **항목의 위험 단계가 지난 점검과 달라졌을 때만** 계획에 경고를 남긴다. 나중에 Guide Agent 를 부르는 기준이 된다.

| 변화 `kind` | 조건 |
|---|---|
| `raised` | 단계가 올라감 (처음 보는 항목은 지난 단계를 `safe` 로 봄 → 주의·위험이면 바로 경고) |
| `lowered` | 내려갔지만 아직 `caution` (`danger` → `caution`) |
| `cleared` | `safe` 로 돌아옴 |

- 판단하지 않은 항목(`risk=null`, 데이터 장애 등)은 **지난 단계를 유지**한다. 장애를 '해제'로 오인하지 않는다.
- 근거 일부가 빠진 결과(`data_missing: true`)로는 단계를 **내리지 않는다** (올리는 것만 반영).
- 항목은 `item_id@날짜` 로, 이동은 `item_id@날짜#수단` 으로 구분한다. 앞 항목을 고쳐 시작 시각이 밀려도 같은 방문이라,
  단계가 같으면 다시 경고하지 않는다. 구간 수단을 바꾸면 새 구간으로 본다.
  다른 날로 옮기면 새 방문으로 본다. 점검할 방문이 없어지면(모두 건너뜀·완료) 지난 상태를 지운다.
- 한 번의 점검에서 바뀐 항목을 계획별로 묶어 경고 하나를 만든다. 바뀐 게 없으면 경고도 없다.

| 메서드 | 경로 | 설명 |
|---|---|---|
| `GET` | `/planner/plans/{id}/safety/alerts?status=new\|seen` | 경고 목록 (최신순). `status` 는 다른 값이면 422 |
| `POST` | `/planner/plans/{id}/safety/alerts/{alert_id}/seen` | 확인 표시 → `SafetyAlertView`. 다른 계획의 경고면 404 `alert_not_found` |

`SafetyAlertView`
```json
{"id": 97, "plan_id": 344, "created_at": "…", "level": "caution", "needs_action": true, "status": "new",
 "message": "일정 2곳의 위험 단계가 올라갔습니다.\n- 1일차 13:07 구룡포항: 안전 → 주의 (10/05 13~15시 풍속 최대 15m/s 예보)\n- …",
 "plan_version": 5, "situation": "8f2c61d0a4b7e913", "checked_at": "2026-10-04T18:52:00+09:00",
 "changes": [{"kind": "raised", "from_risk": "safe", "to_risk": "caution", "item": {"…": "SafetyItem"}}]}
```
- `level`: `raised`·`lowered` 항목의 최고 단계. 해제만 있으면 `safe`.
- `needs_action`: `raised` 가 있으면 true (일정 조정이 필요할 수 있음 → 나중에 Guide Agent 호출 조건).

### 7.4 주기 점검

planner 가 `PLANNER_SAFETY_WATCH_SEC`(compose 기본 60초)마다 `GET /safety/situation` 을 보고, 다음일 때만 점검한다.

| 점검 이유 | 조건 |
|---|---|
| 첫 점검 | 서비스 시작 후 처음 |
| 안전 상황이 바뀜 | `fingerprint` 가 지난번과 다름 (특보·센서·예보 신호·데이터 장애, 새 예보 발표) |
| 재점검 주기 | 상황이 그대로여도 `PLANNER_SAFETY_RECHECK_MIN`(기본 10분)이 지남 — 일정 수정, 실측 센서·CCTV 적용 시간대(방문 3시간 전) 진입은 fingerprint 로 알 수 없어서 |

- 대상: 시작일 ≤ 예보 마지막 날, 종료일 ≥ 오늘인 계획의, **오늘 ~ 예보 마지막 날** 사이 방문. 먼 일정은 부르지도 않는다.
- safety 를 부르는 동안에는 DB 연결·잠금을 잡지 않는다. 그 사이 일정이 바뀌면(`version` 다름) 그 계획은 건너뛰고 다음 점검에서 본다.
- safety 에 접근할 수 없으면 로그만 남기고 다음 주기에 다시 시도한다.
- `PLANNER_SAFETY_WATCH_SEC=0` 이면 하지 않는다 (코드 기본값, 테스트). compose 는 planner 서비스에서만 켠다.

`POST /planner/safety/scan?force=false` (개발·시연용, `AUTH_REQUIRED=false` 일 때만): 주기를 기다리지 않고 지금 한 번 점검한다.
`force=true` 면 상황이 같아도 점검한다. 모든 사용자의 계획이 대상이라 운영에서는 404 `not_available`.
```json
{"ran": true, "reason": "안전 상황이 바뀜", "situation": "8f2c…", "checked_at": "…", "plans": 3, "alerts": [{"…": "SafetyAlertView"}]}
```

### 7.5 가상 상황으로 확인하기

`backend/.env` 에 `SAFETY_SOURCE=mock` 으로 띄운 뒤 safety 의 `PUT /safety/sim` 으로 시나리오·가상 시각을 바꾼다
([`docs/safety/README.md` §1](../safety/README.md)). 실제 장소로 확인한 결과 (내일 일정: 짬홍 → 구룡포항 → 가옥거리 → 근대문화역사관):

| 시나리오 | 경고 | 점검 결과 |
|---|---|---|
| `calm` | 없음 | 모두 safe |
| `wind_tomorrow` | caution, 2곳 raised | 구룡포항·가옥거리 caution, 실내 2곳 safe+info(바람) |
| `rain` (오늘 저녁 비) | safe, 2곳 cleared | 내일은 영향 없음 |
| `flood` + 가상 시각 내일 10:30 | danger, 짬홍 raised | 짬홍(수협 맨홀 등급 3 반경 안) danger, 나머지 safe+info(비) |
| `storm` | caution, 2곳 raised | 짬홍 danger 유지(경고 없음), 야외 caution, 실내 safe+info |
| `calm` | safe, 3곳 cleared | 모두 safe |
| 12/20 일정 | 없음 (점검 대상 아님) | `risk=null (beyond_forecast)` |

---

## 8. 여행 중 (진행 상태 · 현재 기준 재계산)

trip 서비스(`docs/trip/README.md`)가 위치로 도착·출발·지연을 판정할 때 쓴다. 앱·CLI도 같은 API를 쓸 수 있다.

### 8.1 진행 상태

| 메서드 | 경로 | 본문 | 성공 | 오류 |
|---|---|---|---|---|
| POST | `/planner/plans/{plan_id}/items/{item_id}/progress` | `{"status": "visiting"\|"done"\|"planned", "at"?: "HH:MM"}` | 200 `PlanDetail` | 404 `plan_not_found`·`item_not_found`, 409 `item_skipped`, 422 `invalid_request`·`invalid_range` |

- **`version`을 올리지 않고 `progress_version`을 1 올린다.** 도착할 때마다 대기 중인 제안이 `base_changed`가 되지 않게 하기 위해서다.
  그 대신 제안 이후 `done`이 된 항목을 바꾸는 제안은 승인 때 409 `progress_changed`로 막는다 (§5.4).
- `visiting`: `arrived_at = at`, `done_at`은 지운다. `done`: `done_at = at` (`arrived_at`은 그대로, 없으면 null). `planned`: 둘 다 지운다 (`at` 무시).
- `at`을 생략하면 planner의 지금 시각(§8.3)을 분 단위로 쓴다. `done`의 `at`이 `arrived_at`보다 이르면 422 `invalid_range`.
- 건너뜀(`skipped`)은 일정 변경이다. `PATCH …/items/{item_id}` `{"status": "skipped"}`로 하고 `version`이 오른다.
  건너뛴 항목에 진행 상태를 보내면 409 `item_skipped`.
- 한 항목만 바꾼다. 다른 항목을 자동으로 `done`으로 만들지 않는다 (판단은 trip).
- 진행 상태는 계획 기준 시각(`arrive_at` 등)을 바꾸지 않는다. 검증(§2.4)·안전 점검(§7)은 지금처럼 `done` 항목(과 그 항목까지의 이동)을 뺀다.

### 8.2 현재 기준 재계산 (projection)

| 메서드 | 경로 | 본문 | 성공 | 오류 |
|---|---|---|---|---|
| POST | `/planner/plans/{plan_id}/projection` | `ProjectionRequest` (생략 가능) | 200 `ProjectionResult` (저장 안 함) | 404, 422 `invalid_request`·`day_out_of_range`·`invalid_position` |

```jsonc
// ProjectionRequest (모두 선택)
{"day": 1,                 // 생략하면 planner 의 오늘 (§8.3). 계획 기간 밖이면 422 day_out_of_range
 "now": "15:05",           // 생략하면 planner 의 지금
 "position": {"state": "visiting", "item_id": 12, "arrived_at": "14:05"}   // 장소 안. arrived_at 생략 시 항목의 arrived_at, 없으면 now
          // | {"state": "moving", "lat": 35.98, "lng": 129.55}           // 이동 중
          // 생략: 저장된 진행 상태로 본다 (그날 visiting 항목이 있으면 거기 머무는 중, 여럿이면 일정상 마지막)
}

// ProjectionResult
{"day": 1, "now": "15:05",
 "plan": PlanDetail,                    // 재계산한 일정. plan.violations = 재계산 위반
 "baseline_violations": [Violation],    // 기준 일정 (GET /validate 와 같음)
 "new_violations": [Violation],         // 재계산 위반 중 기준에 없던 것. trip 의 delay 판정은 이것만 본다
 "delay_minutes": 35,                   // 다음 항목의 시작 시각 차이 (재계산 - 기준, 음수 = 여유). 다음 항목이 없으면 그날 종료 시각 차이
 "next": {"item_id": 13, "planned_start": "15:00", "eta": "15:35", "start_at": "15:35"}}   // 다음에 갈 항목. 없으면 null
```

**계산** (`validation.build_schedule(actual=Actual(...))`, 순수 함수). 그날만 다시 계산하고 다른 날은 기준 그대로다.

| 항목 | 시각 | 위반 검사 |
|---|---|---|
| 지난 항목: `done`, 또는 기준점보다 앞의 항목 | 기록된 `arrived_at`~`done_at` (없으면 계산값) | 시각 위반(고정·운영시간·휴무) 안 봄 |
| 기준점 — 머무는 항목 (`visiting`) | 실제 도착부터. 예정 체류가 이미 지났으면 `now`에 끝난다고 본다. 고정 시각이 있으면 그때까지 기다린다 | 고정 시각 놓침은 안 봄 (이미 와 있다). 운영시간·휴무는 봄 |
| 기준점 — 이동 중 향하는 항목 | `now + 지금 위치 → 그 장소 이동시간` (routing, 실패하면 직선 근사. 구간 수단은 그 항목의 `leg_mode`) | 모두 봄 |
| 그 뒤 항목 | 평소처럼 이어서 계산. 고정한 `start_time`은 그대로 고정 (늦으면 `fixed_time_missed`) | 모두 봄 |
| 기준점이 없을 때 (시작 전·사이에 위치 모름) | 아직 다녀오지 않은 첫 항목부터 `max(직전 종료, now)`에 직전 장소에서 출발 | 모두 봄 |

- 이동 중 향하는 항목: 마지막으로 다녀온·머문 항목 뒤의 첫 `planned` 항목. 없으면 기준점 없음으로 본다.
- 순서를 건너뛰어 뒤 항목에 와 있으면(`position.item_id`가 뒤쪽), 그 앞의 다녀오지 않은 항목은 지난 항목으로 보고 위반을 따지지 않는다.
  순서를 바꾸려면 일정 변경(제안)으로 한다.
- 규칙(`end_before` 등)·하루 초과(`day_overflow`)는 재계산한 종료 시각으로 본다.
- `new_violations`: (`code`, `day`, `item_id`, `constraint_id`)가 기준 위반에 없는 것. 메시지는 비교하지 않는다.
  예: 늦게 도착해 운영시간 종료(`outside_open_hours`), 하루 초과(`day_overflow`), 고정 시각 놓침(`fixed_time_missed`), hard 규칙 위반.
  늦어졌어도 새 위반이 없으면 빈 목록이다 (가벼운 지연 → trip 은 알리지 않음).

### 8.3 지금 시각 (시계)

planner의 '지금'은 safety의 시각을 따른다 (`docs/trip/README.md` §5). trip·agent·safety와 같은 시각을 봐야 데모의 가상 시각이 맞기 때문이다.

- `PLANNER_CLOCK=safety`(기본): `GET /safety/sim`의 `now`와 시스템 시각의 차이를 `PLANNER_CLOCK_CACHE_SEC`(기본 5초) 동안 기억해 쓴다.
  safety가 가상 상황이 아니면(`404 sim_disabled`) 시스템 시각, 접근할 수 없으면 20초 동안 시스템 시각.
- `PLANNER_CLOCK=system`: 시스템 시각만.
- 쓰는 곳: 진행 상태 `at`·재계산 `day`/`now`의 기본값, 안전 점검의 경로선 날짜 범위(`safety_watch._today`), 점검할 방문이 없을 때의 `checked_at`,
  여행 중 미리보기·제안의 기준 시각(§8.4).
  주기 점검(§7.4)은 원래 safety 상황의 `now`를 쓴다. 기록용 시각(`updated_at`, `decided_at`, `created_at`)은 실제 시각 그대로다.

### 8.4 여행 중 미리보기·제안의 기준
여행 중에는 계획 기준 시각(`GET /plans/{id}`)이 현실과 다르다 (늦게 도착·오래 머묾). 변경안을 볼 때는 현실 기준이어야 하므로
아래 API 는 **planner 의 오늘(§8.3)에 진행 기록(`visiting`·`done`)이 있으면** 그날을 projection(§8.2)과 같은 계산으로 다시 낸다
(`ops.live_actual`: 위치는 저장된 진행 상태, 시각은 planner 의 지금). 다른 날·여행 전·진행 기록 없는 날은 계획 기준 그대로다.

| API | 실제 진행 기준으로 계산하는 것 |
|---|---|
| `POST /preview` | `plan`(적용 후)과 `changes`(적용 전도 같은 기준으로 비교 — `day_end` 가 지금 예상 종료 기준) |
| `POST /proposals` | 스냅샷 `before`/`after` 와 `changes` (§5.2) |
| `POST /safety/preview` | 점검할 방문·이동 시각 |
| accept 의 409 `base_changed` | `since_proposal`·`overwrite_effect` 의 현재 일정 (`before` 와 같은 기준) |

- 지난 항목은 기록 시각, 머무는 항목은 실제 도착부터 (예정 체류가 지났으면 지금 끝남), 머무는 곳이 없으면 남은 첫 항목이 지금 출발이다.
  지난 항목의 시각 위반은 따지지 않으므로 Agent(LLM)가 보는 위반·시각도 현실과 맞는다.
- 예 (장면 ③): 계획 기준이면 "1일차 종료: 17:14 → 16:44" 였던 카드가 실제 예상 종료(18:04) 기준 "18:04 → 17:34" 로 나온다.
- 이동 중(마지막 위치)은 저장하지 않으므로 반영하지 않는다. 정확한 이동 중 예상 도착은 trip 의 `TripState.next`(projection) 를 쓴다.

## 9. 구조

| 파일 | 역할 |
|---|---|
| `backend/app/planner/models.py` | `planner_plan`, `planner_item`, `planner_constraint`, `planner_proposal`, `planner_recommendation`, `planner_recommendation_candidate`, `planner_safety_state`, `planner_safety_alert` |
| `backend/app/planner/schemas.py` | 공개 요청·응답 스키마 |
| `backend/app/planner/errors.py` | 오류 형식(§1.3)과 요청 검증 오류 변환 |
| `backend/app/planner/router.py` | API |
| `backend/app/planner/ops.py` | 변경 연산 엔진, 스냅샷 덮어쓰기, 변경 요약(diff), 현재 기준 재계산(`project`) |
| `backend/app/planner/recommend.py` | 추천 후보 상태 계산, [추가] 위치 계산 |
| `backend/app/planner/validation.py` | 타임라인 계산·위반 검사, 현재 기준 재계산·비교 (순수 함수) |
| `backend/app/planner/clock.py` | 지금 시각 (safety 가상 시각을 따름, §8.3) |
| `backend/app/planner/travel.py` | 이동시간 조회 (routing HTTP API, 캐시 하루) + 직선거리 근사 |
| `backend/app/planner/places.py` | places HTTP API 조회 (캐시 5분), planner 설정 |
| `backend/app/planner/safety.py` | 안전 점검: 방문 목록 만들기, 결과 정리, 지난 점검과 비교 (순수 함수) |
| `backend/app/planner/safety_client.py` | safety HTTP API (`assess`, `situation`) |
| `backend/app/planner/safety_watch.py` | 주기 점검·경고 저장 |
| `backend/app/planner/background.py` | 서버가 떠 있는 동안 주기 점검 스레드 실행 (`main.py` 가 연다) |

## 10. 실행·테스트

```bash
# worktree 루트 .env: API_PORT=8002, DB_PORT=5434
docker compose up -d --build                       # 장소 데이터·임베딩은 places-init 이 자동으로 채운다
docker compose run --rm planner pytest tests/planner
```

`PLANNER_PLACES_BASE_URL`·`PLANNER_ROUTING_BASE_URL`·`PLANNER_SAFETY_BASE_URL`로 places·routing·safety 주소를 바꿀 수 있다 (시계도 `PLANNER_SAFETY_BASE_URL`을 쓴다). compose에서는 게이트웨이(`http://gateway`), 단독 실행(`SERVICE=all`) 기본값은 `http://127.0.0.1:8000`이다.

테스트는 이동시간 기대값을 고정하려고 routing 대신 직선거리 근사를 쓰고, 시계는 시스템 시각이다 (`tests/planner/conftest.py`, 필요한 테스트는 `FixedClock`). routing 클라이언트 자체는 `tests/planner/test_travel.py`가 가짜 응답으로 확인한다.

## 11. CLI

호스트 Python에 `httpx`가 있으면 된다. 로그인하지 않았으면 개발 모드처럼 `--user`(`X-User-Id`)로 보낸다(서버가 `AUTH_REQUIRED=false`일 때만 동작). Windows에서는 `localhost` 대신 `127.0.0.1`을 쓴다(`localhost`는 요청마다 약 10초 지연).

```bash
export GURYONG_API_URL=http://127.0.0.1:8000 GURYONG_USER=seoyun
python scripts/planner/cli.py login seoyun          # 로그인하면 이후 요청은 토큰으로 (signup / logout / whoami / passwd)
python scripts/planner/cli.py -h
python scripts/planner/cli.py shell
```

```text
search 구룡포 --type 관광지 / place kto-2660046
new "엄마랑 구룡포" --start 2026-10-10 --from 12:30 --to 18:00 [--end ..] [--car]
plans / show 1 [--json] / set 1 --end 2026-10-11 --mode car / drop 1
add 1 kto-2660046 --stay 60 [--day 1] [--order 2] [--at 15:00] [--memo ..] [--mode car]
edit 1 7 --order 1 | --day 2 | --stay 40 | --at 15:00 | --at none | --status skipped | --mode car | --mode default
rm 1 7
rule 1 must_include place_id=kto-2660046 --note "엄마가 꼭 보고 싶어함" / rules 1 / unrule 1 3 / check 1
proposals 1 [--status pending] / proposal 1 5 [--full] / accept 1 5 [--overwrite] / reject 1 5
recs 1 / rec 1 2 / rec-add 1 2 11 / rec-remove 1 2 11
safety 1 / alerts 1 [--status new]            안전 점검 (방문·이동 구간) / 주기 점검 경고
chat 1                                          Planner Agent 와 대화 (docs/agent/README.md)
```

## 12. 다음 단계
- 운영시간·휴무 구조화: places가 파싱해 제공하면 `validation.py`를 그 값으로 바꾼다.
- 걷기 편한 길: routing의 `easy_walk`(계단·언덕 회피, 3.6km/h)를 계획 설정으로 노출한다 ("오래 걷기 부담" 사용자).
- CLI 에 진행 상태·재계산 명령 추가 (trip CLI 와 함께, `docs/trip/README.md` §8).
- 경고 → Guide Agent 호출 (`needs_action` 경고). Planning Agent 는 §7.2 를 `check_safety` 도구·검토 관문으로 이미 쓴다 (`docs/agent/README.md` §5).
