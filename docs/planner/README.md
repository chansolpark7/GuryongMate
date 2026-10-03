# planner — 여행 일정 API

여행 일정의 단일 진실 원천(Source of Truth). **앱·CLI·LLM tool이 모두 같은 API**로 일정을 조회·수정한다.
일정을 바꾸는 요청은 모두 **바뀐 뒤의 전체 계획**(자동 계산된 타임라인과 위반 목록)을 응답한다.
따라서 호출한 쪽(특히 LLM)은 변경 결과와 문제점을 바로 확인할 수 있다.

## 구조

| 파일 | 역할 |
|---|---|
| `backend/app/planner/models.py` | `planner_plan`, `planner_item`, `planner_constraint` 테이블 |
| `backend/app/planner/schemas.py` | 공개 요청/응답 스키마 (LLM tool 정의의 근거) |
| `backend/app/planner/router.py` | `/planner/...` API |
| `backend/app/planner/validation.py` | 타임라인 계산과 결정론적 위반 검사 (순수 함수) |
| `backend/app/planner/travel.py` | 이동시간 추정 (직선거리 근사. 나중에 Kakao 경로 API로 교체) |
| `backend/app/planner/places.py` | places 모듈 HTTP API로 장소 조회 (캐시 5분) |
| `scripts/planner/client.py` | Python API 클라이언트 (CLI가 쓰고, LLM tool 구현에서도 재사용) |
| `scripts/planner/cli.py` | 동작 확인용 CLI |

## 시간 모델

- 계획에는 기간(`start_date`~`end_date`, 최대 14일)과 매일의 `day_start`/`day_end`가 있다.
- 각 항목은 `day`(n일차, 1부터), `order`(그날 순서, 1부터), `stay_minutes`(체류)를 가진다.
- **시각은 자동 계산한다.** 하루는 `day_start`에서 시작하고, 각 항목은 `직전 종료 + 이동시간`에 도착한다.
  - `start_time`을 지정한 항목은 그 시각에 고정된다. 일찍 도착하면 기다리고, 늦게 도착하면 `fixed_time_missed` 위반이다.
  - `status=skipped` 항목은 타임라인에서 빠진다(시각 null). 여행 중 "이번 장소 건너뛰기"에 쓴다.
- 이동시간은 `직선거리 × 1.3`으로 계산한다. 속도는 도보(`walk`) 70m/분, 차량(`car`) 500m/분에 주차·승하차 5분을 더한다.
- 출력 시각은 `"HH:MM"`이고, 자정을 넘기면 `"24:30"`처럼 표기한다.

## 검증 (결정론적)

| code | 수준 | 조건 |
|---|---|---|
| `day_overflow` | error | 그날 마지막 일정이 `day_end` 이후에 끝남 |
| `fixed_time_missed` | error | 고정 시작 시각보다 늦게 도착 |
| `closed_day` | warning | 휴무일 원문에서 뽑은 "매주 ○요일" 휴무에 해당 (격주·명절은 판단하지 않음) |
| `outside_open_hours` | warning | 운영시간 원문에 시간 범위가 하나뿐일 때, 방문이 그 범위를 벗어남 |
| `place_unavailable` | warning | 장소 정보를 불러오지 못함 (이동시간 계산에서 제외) |
| 규칙 `type` | hard→error, soft→warning | 아래 규칙 위반 |

규칙(constraint)은 사용자나 LLM이 등록한다(`source: user|llm`).

| type | params | 의미 |
|---|---|---|
| `must_include` | `{"place_id"}` | 반드시 포함 ("과메기문화관은 유지") |
| `exclude` | `{"place_id"}` | 포함하지 않음 |
| `end_before` | `{"time": "HH:MM", "day"?}` | 그날(생략 시 매일) 이 시각 전에 끝남 |
| `max_travel_minutes` | `{"minutes"}` | 장소 간 이동시간 상한 ("오래 걷기 부담") |

## API

모든 요청에 `X-User-Id` 헤더가 필요하다. 로그인 모듈이 생기기 전까지 소유자 구분에만 쓴다. 다른 사용자의 계획은 404로 응답한다.

| 메서드 | 경로 | 설명 | 응답 |
|---|---|---|---|
| GET | `/planner/plans` | 내 계획 목록 | `PlanSummary[]` |
| POST | `/planner/plans` | 계획 생성 `{title, start_date, end_date?, day_start?, day_end?, travel_mode?, memo?}` | `PlanDetail` |
| GET | `/planner/plans/{id}` | 전체 일정 (날짜별 타임라인 + 규칙 + 위반) | `PlanDetail` |
| PATCH | `/planner/plans/{id}` | 계획 정보 수정. 기간을 줄일 때 잘리는 날에 일정이 있으면 409 | `PlanDetail` |
| DELETE | `/planner/plans/{id}` | 삭제 | 204 |
| GET | `/planner/plans/{id}/validate` | 위반만 조회 `{ok, violations}` | `ValidationResult` |
| POST | `/planner/plans/{id}/items` | 일정 추가 `{place_id, day?, order?, stay_minutes?, start_time?, memo?}` | `PlanDetail` |
| PATCH | `/planner/plans/{id}/items/{item_id}` | 순서 변경·날짜 이동·체류·시각 고정/해제(`start_time: null`)·메모·상태. 보낸 필드만 반영 | `PlanDetail` |
| DELETE | `/planner/plans/{id}/items/{item_id}` | 일정 삭제 | `PlanDetail` |
| GET | `/planner/plans/{id}/constraints` | 규칙 목록 | `ConstraintView[]` |
| POST | `/planner/plans/{id}/constraints` | 규칙 추가 `{type, params, hard?, source?, note?}` | `PlanDetail` |
| DELETE | `/planner/plans/{id}/constraints/{cid}` | 규칙 삭제 | `PlanDetail` |

- 장소 검색과 상세 조회는 places 모듈의 `GET /places`, `GET /places/{place_id}`를 그대로 쓴다.
- `place_id`가 없으면 422, places API에 접근할 수 없으면 503으로 응답한다.
- 요청·응답 스키마 전체는 `/docs` 또는 `/openapi.json`에서 볼 수 있다. LLM tool 정의는 이 스키마에서 만들면 된다.

## 실행

```bash
# worktree 루트 .env: API_PORT=8002, DB_PORT=5434
docker compose up -d --build
docker compose run --rm api python -m app.places.ingest load    # 장소 데이터 (스냅샷)
docker compose run --rm api pytest tests/planner
```

환경변수 `PLANNER_PLACES_BASE_URL`(기본값 `http://127.0.0.1:8000`, 같은 컨테이너의 API)로 places API 주소를 바꿀 수 있다.

## CLI

호스트 Python에 `httpx`가 있으면 바로 실행된다. Windows에서는 `localhost` 대신 `127.0.0.1`을 써야 한다. `localhost`는 IPv6를 먼저 시도해서 요청마다 약 10초씩 지연된다.

```bash
export GURYONG_API_URL=http://127.0.0.1:8002 GURYONG_USER=seoyun
python scripts/planner/cli.py -h
python scripts/planner/cli.py shell                 # 대화형: 아래 명령을 한 줄씩
```

```text
search 구룡포 --type 관광지                     장소 검색 (--tag, --area, --near LAT,LNG --radius)
place kto-2660046                              장소 상세
new "엄마랑 구룡포" --start 2026-10-10 --from 12:30 --to 18:00 [--end ..] [--car]
plans / show 1 [--json] / set 1 --end 2026-10-11 --mode car / drop 1
add 1 kto-2660046 --stay 60 [--day 1] [--order 2] [--at 15:00] [--memo ..]
edit 1 7 --order 1 | --day 2 | --stay 40 | --at 15:00 | --at none | --status skipped
rm 1 7
rule 1 must_include place_id=kto-2660046 --note "엄마가 꼭 보고 싶어함"
rule 1 end_before time=17:30 --soft / rules 1 / unrule 1 3 / check 1
```

## 다음 단계

- **LLM tool**: `PlannerClient` 메서드를 그대로 tool로 노출한다(search_places, get_plan, add_item, update_item, delete_item, add_constraint 등). 변경 응답에 위반 목록이 들어 있으므로 LLM이 결과를 보고 스스로 다시 수정할 수 있다.
- 운영시간·휴무 구조화: places 쪽에서 파싱해 제공하면 `validation.py`를 그 값으로 교체한다.
- 이동시간: Kakao 도보 경로 API로 `travel.estimate_minutes`를 교체한다(캐시 필요).
- 동시 수정: 앱과 LLM이 같은 계획을 동시에 고치는 경우가 생기면 `version` 필드로 낙관적 잠금을 추가한다.
