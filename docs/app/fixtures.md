# fixtures — 앱 화면 검증용 녹화 응답

앱(Flutter)은 백엔드를 띄우지 않고도 **매번 같은 응답**으로 화면을 검증한다. 응답은 지어내지 않고, 각본 LLM 으로 띄운 실제 스택에
데모 흐름을 요청해 **녹화**한다. 그래서 형식·필드·오류 코드·SSE 순서가 서버와 같다.

| | |
|---|---|
| 파일 | `app/test/fixtures/` — 흐름 파일 `f*.json`·`errors.json`, 여행 재생 파일 `trip/*.json`, 목록 `manifest.json` |
| 만드는 것 | `scripts/app/record_fixtures.py` |
| LLM | 각본 LLM (`AGENT_PROVIDER=scripted`, `docs/agent/README.md` §7.2). 실제 LLM 은 부르지 않는다 (각본 LLM 이 아니면 녹화기가 멈춘다) |
| 안전 정보 | `SAFETY_SOURCE=mock` (가상 상황·시각 `PUT /safety/sim`) |
| 데모 데이터 | [README.md §6.1](README.md#61-데모-데이터-모든-예시-화면-공통) — 엄마랑 구룡포 10/10 |
| 다시 녹화 | 앱이 받는 응답(API·SSE·trip 이벤트·데모 데이터·각본)이 바뀌면 같은 브랜치에서 다시 돌려 커밋한다 (`docs/COLLABORATION.md` §2) |

## 1. 녹화

```bash
# backend/.env: AGENT_PROVIDER=scripted, SAFETY_SOURCE=mock (AUTH_REQUIRED 는 상관없음, 녹화는 Bearer 토큰으로 한다)
docker compose up -d --build
python scripts/app/record_fixtures.py --url http://127.0.0.1:$API_PORT            # 전부 (약 6분)
python scripts/app/record_fixtures.py --url http://127.0.0.1:$API_PORT --only f1,f5
python scripts/app/record_fixtures.py --url http://127.0.0.1:$API_PORT --no-docker  # 503 예시 빼고
```

- 데모 사용자 `seoyun-demo`(서윤)를 녹화마다 탈퇴 후 다시 가입한다 (메모리 등이 쌓이지 않게).
- 계획 모드 흐름은 가상 시각 `2026-10-09 10:00`(여행 전날, calm), 여행 흐름은 재생 파일의 시각을 따른다.
- `errors` 의 503 예시는 `docker compose stop safety`·`stop planner` 로 잠깐 멈췄다가 다시 띄운다 (`--no-docker` 면 뺀다).
- 녹화 중 기대(재생 파일의 `expect`, 카드·검토 관문·메모리 칩 등)를 확인하고, 어긋나면 끝에 목록을 출력하고 종료 코드 2.
- **바뀌는 값**: 계획·세션·제안 id, `created_at` 같은 시각은 녹화마다 다르다. 앱 테스트는 id 를 하드코딩하지 말고 fixture 안의 값을 읽어 쓴다.
  토큰은 `<access_token>`·`<refresh_token>` 으로 바꿔 저장한다.

## 2. 흐름 파일 (`f*.json`, `errors.json`)

| 파일 | 흐름 | 화면 | 담긴 것 |
|---|---|---|---|
| `f11_login.json` | F11 | 01·34·35 | 가입 409 `login_id_taken`·422, 로그인 401 `invalid_credentials`·200, `GET /users/me`, refresh 200·401 `refresh_token_reused` |
| `f1_plan_with_ai.json` | F1 (장면 ①) | 03·05·06·06B·08 | 계획 만들기 → 세션 → **"엄마랑 구룡포 일정 짜줘" SSE**(기억하기·장소 찾기·조건·일정 조정·검토 관문 되돌림·제안) → 승인 → 안전 → **"갈 만한 카페 추천해줘" SSE**(추천 카드) → 후보 추가·409 `already_in_plan`·빼기 |
| `f2_proposal_actions.json` | F2 | 06·36 | 제안 → 앱 직접 편집 → 승인 409 `base_changed` → **다시 제안 SSE** → 이전 `superseded` → 거절·409 `proposal_not_pending` → 409 `base_changed` → `overwrite` 200 → 응답 중 409 `agent_busy` |
| `f10_guide_prep.json` | F10 | 32·17 | 노트 목록 → 준비 202 → 작업 폴링(`running`→`done`) → 노트(가옥거리, 북방파제: 출처 "구룡메이트 장소 정보") → 다시 준비(할 일 없음) → 404 `job_not_found` |
| `f3_trip_start.json` | F3 | 11·12·14·30 | 상태(비활성) → 다른 날 시작 409 `not_trip_day` → 시작 → 같은 날 다시(멱등) → `day_started` → 실수 종료 |
| `f4_arrival_docent.json` | F4 (장면 ②) | 14·16·17·18 | (재생 `trip/scene2_arrival.json` 뒤) 노트·메모리 → 이벤트 확인 표시 → **장소 질문 SSE**(가이드 노트 확인·자료 찾기) → **"더 짧게" SSE**(기억하기 단계 = 메모리 칩) → `style_length: short` |
| `f5_delay.json` | F5 (장면 ③) | 14-S3·19·20·21-S1 | (재생 `trip/scene3_delay.json` 뒤) delay 이벤트(`agent{session_id, message_id, proposal_id}`) → 선제 메시지가 있는 세션 → **"과메기문화관은 유지해줘" SSE**(새 제안) → 이전 `superseded`·409 → 새 제안 승인 |
| `f6_safety.json` | F6·F7 (장면 ④) | 22·21·23·28 | safety_raised 이벤트 → 선제 메시지 세션 → 제안 → 비교(전 `GET …/safety`, 후 `POST …/safety/preview`) → 경고 → 승인 → `GET /safety/avoid` → 우회 경로 `POST /routing/route {hazards: avoid}` |
| `errors.json` | 공통 | 05·States_Feedback | SSE `error` `llm_unavailable`, 각본 없는 말(고정 안내), 422 `invalid_request`, 404 `session_not_found`·`plan_not_found`, 503 `safety_unavailable`(planner)·`upstream_unavailable`(agent) |

```jsonc
{"flow": "F1", "title": "…", "screens": "03·05·06·06B·08", "recorded_at": "2026-10-09T23:40:00+09:00",
 "backend": {"agent_provider": "scripted:scripts", "safety_source": "mock", "git": "b3e244f"},
 "exchanges": [
   {"name": "create_plan", "method": "POST", "route": "/planner/plans", "path": "/planner/plans", "query": {},
    "body": {…}, "status": 201, "elapsed_ms": 35, "response": {PlanDetail}},
   {"name": "chat_make_plan", "method": "POST", "route": "/agent/sessions/{session_id}/chat",
    "path": "/agent/sessions/5f37…/chat", "query": {}, "body": {"message": "엄마랑 구룡포 일정 짜줘"},
    "status": 200, "elapsed_ms": 7300, "content_type": "text/event-stream",
    "sse": [{"t_ms": 12, "event": "start", "data": {"session_id": "5f37…"}},
            {"t_ms": 340, "event": "thinking", "data": {"delta": "어머니와 함"}},
            {"t_ms": 2010, "event": "status", "data": {"text": "기억해 두고 있어요."}}, …,
            {"t_ms": 7290, "event": "done", "data": {"text": "…", "cards": [{"type": "proposal", "id": 12}], …}}]},
   {"name": "accept_base_changed", "status": 409, "response": {"detail": {"code": "base_changed", …}}, …}]}
```
- `route` 는 자리표시자 경로(`{plan_id}`·`{session_id}`·`{proposal_id}` …), `path` 는 실제 경로. `name` 은 흐름 안에서 유일하다.
- `response` 는 JSON 응답 그대로 (204 는 `null`). 오류는 서버 형식 `{"detail": {"code", "message", …}}` 그대로.
- SSE 는 `sse` 목록 — 이벤트 이름·data 와 **요청 시작부터 받은 시각(ms)**. 각본 LLM 이 6자 조각을 30ms 간격, 호출마다 300ms 뒤에 보내므로
  간격이 실제 모델 스트리밍과 비슷하다. `steps_done` 이 없는 턴(단계 0개)도 있다.

## 3. 여행 재생 파일 (`trip/*.json`)

`scripts/trip/scenes/*.json`(장면 ②③④)을 실제 trip 에 보낸 기록이다. 위치 응답에 오는 이벤트(도착·출발·위험 구역)와 시간이 지나 생기는 이벤트(지연·안전)를
**재생 시각 순서대로** 담는다.

| 파일 | 장면 | 이벤트 |
|---|---|---|
| `trip/scene2_arrival.json` | ② | `day_started` → 12:30 `arrived`(단계 2, 해설 카드) |
| `trip/scene3_delay.json` | ③ | `arrived` → 13:53 `delay`(단계 3, 선제 턴 → 제안) → `departed` → `arrived` |
| `trip/scene4_safety.json` | ④ | 도착·출발 … → 15:20 `safety_raised`(단계 3, 북방파제 위험 → 근대문화역사관 교체 제안) → 15:25 승인 → 역사관 `arrived` → 차로 카페 가는 길 `near_hazard` ×3 → `arrived` |

```jsonc
{"scene": "scene4_safety", "title": "장면 ④ DT 안전 대응", "plan_id": 41, "session_id": "…", "plan": {PlanDetail (시작 전)},
 "timeline": [
   {"at": "2026-10-10T12:25:00+09:00", "kind": "sim", "scenario": "calm"},                 // 가상 상황 (앱은 무시해도 됨)
   {"at": "…12:25…", "kind": "http", "method": "POST", "route": "/trip/plans/{plan_id}/start", "body": {…}, "status": 200, "response": {TripState}},
   {"at": "…12:30…", "kind": "location", "body": {"lat", "lng", "accuracy", "recorded_at", "source"},
    "response": {"state": {TripState}, "events": [Event, …], "accepted": 1, "ignored": 0}},   // POST /trip/plans/{id}/location
   {"at": "…15:20…", "kind": "tick", "response": {…}},                                     // 서버 틱 (시간 경과 판정)
   {"at": "…15:22…", "kind": "poll", "route": "/trip/plans/{plan_id}/events", "query": {"after": 0, "limit": 200},
    "response": [Event, …]},                                                                // 그 시각의 이벤트 전체 (폴링 응답)
   {"at": "…15:22…", "kind": "http", "method": "GET", "route": "/agent/sessions/{session_id}", "response": {SessionDetail},
    "for_event": 57},                                                                       // 단계 3 이벤트의 선제 메시지·카드
   {"at": "…15:25…", "kind": "http", "method": "POST", "route": "/agent/sessions/{session_id}/proposals/{proposal_id}/accept", …}],
 "final": {"events": [Event, …], "state": {TripState}}}
```

## 4. 앱에서 재생하기

기존 `test/flow_test.dart` 의 `FakeGateway`(요청을 받아 정해 둔 응답을 돌려주는 `MockClient`)를 fixture 를 읽는 방식으로 바꾼다.

1. **흐름 파일 → 가짜 게이트웨이**: `exchanges` 를 `"METHOD route"` 별 큐로 만든다. 요청이 오면 경로를 `route` 와 맞춰 보고(자리표시자 `{…}` 는 `[^/]+`)
   그 큐에서 **순서대로** 하나 꺼내 `status`·`response` 로 응답한다. 같은 경로가 여러 번이면(폴링, 승인 409 → overwrite 200) 녹화 순서대로 나온다.
   `content_type` 이 `text/event-stream` 이면 `sse` 를 `event: …\ndata: …\n\n` 로 이어 스트림으로 보낸다. 위젯 테스트는 지연 없이, 데모·화면 확인은
   `t_ms` 간격대로(`Future.delayed`) 보내면 진행 단계·말풍선이 실제처럼 흐른다.
2. **id 맞추기**: 앱이 보내는 경로의 id 는 앞 응답에서 받은 값이므로 fixture 안에서 일관된다 (예: `create_session` 응답 id 로 `chat` 경로). 테스트에서
   경로를 직접 만들 때는 fixture 의 `response` 에서 꺼낸다.
3. **여행 재생 파일 → 동행 화면**: 데모 위치 모드(33)처럼 앱이 위치를 보내는 대신 `timeline` 을 시간순으로 흘린다.
   - `location` 항목: 앱이 위치를 보냈을 때의 응답으로 쓴다 (`response.events` 를 바로 그린다 — 도착 카드, 출발 토스트, 위험 구역 패널).
   - `poll` 항목: 그 시각 이후의 `GET …/events?after=` 응답 (마지막으로 본 id 뒤만 잘라서). 지연·안전 선제 이벤트와 `agent` 필드가 여기서 온다.
   - `http` + `for_event`: 단계 3 이벤트의 `GET /agent/sessions/{id}` 응답 — 선제 메시지(`message_id` 매칭, events.md §3.1)와 제안 카드.
   - `at` 은 가상 시각이다. 화면 시계·ETA 는 `state.now` 를 쓴다.
4. 흐름 사이를 이을 때(F4 의 대화는 `trip/scene2_arrival.json` 재생 뒤): 흐름 파일은 같은 녹화에서 이어진 요청이라 계획·세션 id 가 재생 파일과 같다.

```dart
// 예: 흐름 파일을 읽는 가짜 게이트웨이 (요약)
final fx = jsonDecode(File('test/fixtures/f1_plan_with_ai.json').readAsStringSync());
final queues = <String, List<Map>>{};
for (final e in fx['exchanges']) {
  queues.putIfAbsent('${e['method']} ${e['route']}', () => []).add(e);
}
Map? take(String method, String path) {
  for (final key in queues.keys) {
    final parts = key.split(' ');
    final re = RegExp('^${parts[1].replaceAll(RegExp(r'\{[^}]+\}'), '[^/]+')}\$');
    if (parts[0] == method && re.hasMatch(path) && queues[key]!.isNotEmpty) return queues[key]!.removeAt(0);
  }
  return null;
}
```

## 5. 주의

- fixture 는 **각본 LLM** 의 답이다. 문구는 실제 Claude 와 다르다 (형식·카드·단계·오류는 같다). 실제 모델 결과는 `docs/agent/README.md` §7.1·§11.3.2 실측을 본다.
- 각본에 없는 말을 하면 고정 안내("지금은 데모용 각본 모드라…")가 온다 (`errors.json` 의 `chat_no_script`).
- `usage` 는 0 이다 (토큰을 쓰지 않음). `calls` 는 각본 단계 수.
