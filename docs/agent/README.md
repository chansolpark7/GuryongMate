# agent — Planner Agent API 명세

사용자와 대화하며 일정을 짜고 고치는 LLM Agent다. 앱은 **어떤 LLM이 도는지 몰라도** 이 API만 쓰면 된다. LLM 선택은 서버 환경변수로만 한다(§7).

LLM은 일정을 직접 바꾸지 않는다. 결과는 두 종류의 **카드**로 사용자에게 전달된다.

| 카드 | 언제 | 사용자 동작 | 근거 |
|---|---|---|---|
| **변경 제안** (`proposal`) | "일정 짜줘", "비 오면 바꿔줘"처럼 여러 변경을 한 번에 할 때 | [승인] / [거절] / [자세히 보기] | planner §5 |
| **장소 추천** (`recommendation`) | "갈 만한 카페 추천해줘"처럼 사용자가 골라 넣을 때 | 후보마다 [추가] / [빼기] | planner §6 |

---

## 1. 공통 규칙

- 경로 앞부분은 `/agent`다. 앱은 게이트웨이(`:8000`)로 접속한다.
- **사용자 인증**: `Authorization: Bearer <access 토큰>`(개발 모드에서만 `X-User-Id`). 세션은 만든 사용자만 볼 수 있고, 다른 사용자의 세션은 404다. Agent는 planner·places를 부를 때 받은 인증을 그대로 전달한다.
- 본문은 JSON이고, 정의되지 않은 필드가 있으면 422다.
- 오류 형식은 planner와 같다: `{"detail": {"code": "...", "message": "...", ...}}`

| HTTP | code | 언제 |
|---|---|---|
| 422 | `invalid_request` | 스키마 위반 (빈 메시지·공백만 있는 메시지, 4000자 초과, 모르는 필드 등) |
| 404 | `session_not_found` | 세션이 없거나 다른 사용자 것 |
| 404 | `proposal_not_found` / `recommendation_not_found` / `candidate_not_found` | 대상이 없거나 **이 세션이 만든 카드가 아님** |
| 404 | `plan_not_found` | 세션 생성 시 계획이 없음 |
| 409 | `agent_busy` | 이 세션이 응답을 만드는 중 (§4.4) |
| 409·422 | planner 오류 코드 | 카드 동작(승인·추가 등)에서 planner가 거부한 경우, 코드와 추가 필드를 **그대로** 전달 (예: 409 `base_changed`) |
| 503 | `upstream_unavailable` | planner·places 서비스에 접근할 수 없거나, 형식 없는 서버 오류(게이트웨이 502 등)를 돌려줌. planner가 더 구체적인 코드(예: `places_unavailable`)를 주면 그 코드를 그대로 전달 |

---

## 2. 세션

계획 하나에 대한 대화다. 여행 전과 여행 중의 맥락이 이어진다.

| 메서드 | 경로 | 본문 | 성공 |
|---|---|---|---|
| POST | `/agent/sessions` | `{"plan_id": 3}` | 201 `SessionView` (계획이 없으면 404 `plan_not_found`) |
| GET | `/agent/sessions?plan_id=` | | 200 `SessionView[]` (최근 대화순) |
| GET | `/agent/sessions/{session_id}` | | 200 `SessionDetail` |
| DELETE | `/agent/sessions/{session_id}` | | 204 (대화 기록 삭제. 이미 만든 제안·추천은 planner에 남는다) |
| GET | `/agent/info` | | 200 `{"provider": "openai:gpt-5.4-mini"}` (디버깅용) |
| DELETE | `/agent/me` | | 204 — 이 사용자의 세션·메시지를 모두 삭제 (account 탈퇴가 부른다) |

```jsonc
// SessionView
{"id": "9288a236…", "plan_id": 3, "created_at": "...", "updated_at": "..."}

// SessionDetail = SessionView + messages (화면에 보일 것만, 오래된 순)
{"messages": [
  {"id": 101, "role": "user", "content": "비 오면 실내로 바꿔줘", "created_at": "...", "steps": [], "steps_summary": null, "cards": []},
  {"id": 102, "role": "assistant", "content": "15시부터 비 예보가 있어 …", "created_at": "...",
   "steps": [Step, ...],                                   // 이 답변을 만들며 거친 단계 (§3.2)
   "steps_summary": "3단계 완료 : 장소 찾기, 일정 조정, 변경안 만들기",
   "cards": [{"type": "proposal", "proposal": ProposalDetail},              // planner 의 현재 상태 그대로
             {"type": "recommendation", "recommendation": RecommendationView}]}]}
```
- 카드는 **조회할 때의 상태**로 돌려준다. 제안의 `status`·`base_changed`, 추천 후보의 `state`가 지금 일정 기준이다.
- 도구 실행 기록과 승인·거절 알림 같은 내부 메시지는 `messages`에 나오지 않는다.

---

## 3. 대화 (스트리밍)

| 메서드 | 경로 | 본문 | 성공 |
|---|---|---|---|
| POST | `/agent/sessions/{session_id}/chat` | `{"message": "1~4000자"}` | 200 `text/event-stream` (§3.1) |
| POST | `/agent/sessions/{session_id}/chat?stream=false` | 같음 | 200 `ChatResult` (끝난 뒤 한 번에) |
| POST | `/agent/sessions/{session_id}/events` | `{"event": Event, "trip": TripState}` | 200 `ChatResult` — 선제 턴 (trip 이 부른다, §11.2) |

- 응답은 **SSE**다. 각 이벤트는 `event: <이름>` 줄과 `data: <JSON>` 줄, 빈 줄로 끝난다.
- 같은 세션에서 응답이 진행 중이면 409 `agent_busy`이고, 스트림은 시작되지 않는다.
- 턴이 `error`로 끝나거나 연결이 끊겨도, 그때까지의 대화(사용자 메시지, 완결된 도구 기록)와 이미 만든 카드는 기록에 남는다. 오류 문구는 assistant 메시지로 남는다. 연결이 끊기면 서버는 그 턴을 중단한다.

### 3.1 이벤트

| event | data | 앱에서 |
|---|---|---|
| `start` | `{"session_id"}` | |
| `status` | `{"text": "일정을 조정하고 있어요."}` | 진행 영역 맨 위 한 줄. 새 status가 오면 바꾼다 |
| `step` | `Step` | 같은 `id`면 **그 줄을 갱신**, 새 `id`면 줄을 추가 |
| `thinking` | `{"delta"}` | 모델 추론 과정 (지원 모델만). 보통 표시하지 않거나 "생각 중…"으로만 |
| `text` | `{"delta"}` | 답변 말풍선에 이어 붙인다 |
| `proposal` | `ProposalDetail` | 변경 제안 카드 |
| `recommendation` | `RecommendationView` | 장소 추천 카드 |
| `steps_done` | `{"count": 3, "summary": "3단계 완료 : 장소 찾기, 일정 조정, 변경안 만들기"}` | 진행 영역을 이 한 줄로 접는다. 단계가 하나도 없으면 오지 않는다 |
| `done` | `{"text": "최종 답변 전체", "cards": [{"type": "proposal", "id": 13}, ...], "usage": {...}, "message_id": 102, "mode": "plan"}` | 턴 종료. `usage`는 LLM 토큰 사용량(`input`(캐시 포함 전체), `cached`, `output`, `reasoning`, `calls`, Claude 는 `cache_write`도)으로 비용 모니터링용. `message_id`는 저장된 답변 메시지, `mode`는 `plan`(계획) / `guide`(동행, §11) |
| `error` | `{"code", "message"}` | 오류. **이 뒤로 이벤트가 오지 않는다**. LLM 오류는 `llm_failed`, 모델이 요청을 거절하면 `llm_refused` (§7.1) |

순서: `start` → (`status` · `step` · `thinking` · `text` · `recommendation`이 섞여서) → `proposal`(있으면) → `steps_done`(있으면) → `done`. 오류가 나면 그 자리에서 `error`로 끝난다.

### 3.2 단계 `Step`

```json
{"id": "s2", "kind": "edit", "status": "done", "title": "일정 조정", "detail": "카페파도 추가 · 총 이동 +8분"}
```
- `status`: `running` → `done` 또는 `error`
- 같은 종류의 도구를 연달아 쓰면 **한 단계로 합친다**(같은 `id`가 `running`으로 돌아갔다가 다시 `done`). `detail`은 마지막 결과다.
- 제목과 결과는 서버가 도구 결과로 만든다(LLM이 쓰지 않는다).

| kind | title | `done` detail 예 | status 문구 |
|---|---|---|---|
| `search` | 장소 찾기 | `과메기문화관 외 3곳` / `결과 없음` | 장소를 찾고 있어요. |
| `place` | 장소 정보 확인 | `구룡포과메기문화관` | 장소 정보를 확인하고 있어요. |
| `plan` | 일정 확인 | `일정 5곳` | 일정을 확인하고 있어요. |
| `edit` | 일정 조정 | `카페파도 추가 · 총 이동 +8분` | 일정을 조정하고 있어요. |
| `rule` | 조건 반영 | `18:00 전 종료` | 조건을 반영하고 있어요. |
| `review` | 일정 검토 | `문제 2건` / `문제 없음` | 일정을 검토하고 있어요. |
| `recommend` | 추천 정리 | `4곳` | 추천할 곳을 고르고 있어요. |
| `proposal` | 변경안 만들기 | `running`: `잠시만요...` / `done`: `변경 3건` | 변경안을 만들고 있어요. |
| `route` | 이동시간 확인 | `호미곶 도보 160분·차량 27분` / `구룡포항에서 3곳` | 이동 시간을 계산하고 있어요. |
| `safety` | 안전 확인 | `위험 포함 2곳` / `위험 신호 없음` / `판단할 수 없는 일정 있음` / `10-11 주의 신호` | 날씨와 안전을 확인하고 있어요. |

- 실패한 도구 호출은 그 단계를 `error`로 표시하고, `detail`에 짧은 사유를 넣는다. LLM은 오류를 받고 다시 시도할 수 있으므로 턴은 계속된다.
- 새 도구가 생기면 같은 방식으로 `kind`가 늘어난다.

### 3.3 `ChatResult` (`stream=false`)

```json
{"text": "...", "steps": [Step, ...], "steps_summary": "...", "cards": [Card, ...], "error": null,
 "usage": {...}, "message_id": 102, "mode": "guide"}
```
`error`는 `{"code", "message"}` 또는 `null`이다. `message_id`·`mode`는 `done` 이벤트와 같다 (오류로 끝나면 `null`).

---

## 4. 카드 동작

카드 동작은 Agent 경로로 호출한다. 결과가 대화 기록에 남아 다음 턴의 LLM이 알 수 있기 때문이다(planner를 직접 호출해도 일정은 바뀌지만 LLM은 모른다).

| 메서드 | 경로 | 본문 | 성공 | 대표 오류 |
|---|---|---|---|---|
| POST | `/agent/sessions/{sid}/proposals/{pid}/accept` | `{"mode": "auto" \| "overwrite"}` (생략 = auto) | 200 `{"proposal", "plan"}` | 409 `progress_changed`·`base_changed`·`proposal_not_pending`·`agent_busy` |
| POST | `/agent/sessions/{sid}/proposals/{pid}/reject` | | 200 `ProposalDetail` | 409 `proposal_not_pending`·`agent_busy` |
| POST | `/agent/sessions/{sid}/proposals/{pid}/repropose` | | 200 `text/event-stream` (§3과 같은 이벤트) | 409 `agent_busy` |
| POST | `/agent/sessions/{sid}/recommendations/{rid}/candidates/{cid}/add` | | 200 `{"recommendation", "plan"}` | 409 `already_in_plan`·`agent_busy`, 422 `day_out_of_range` |
| POST | `/agent/sessions/{sid}/recommendations/{rid}/candidates/{cid}/remove` | | 200 `{"recommendation", "plan"}` | 409 `not_in_plan`·`agent_busy` |

- 모든 카드 동작은 **그 세션이 만든 카드**에만 허용된다. 다른 세션의 카드면 404다.

### 4.1 제안 승인 흐름 (앱)

```
[승인] 클릭 → accept {mode: auto}
   ├─ 200: 반영 완료. 카드를 "승인됨"으로
   └─ 409 base_changed: 확인창
        "이 제안 이후 일정이 바뀌었어요"
        - since_proposal 목록: "그대로 반영하면 사라지는 변경"
        [카드에서 본 일정 그대로 반영] → accept {mode: overwrite}
        [현재 일정에 맞춰 다시 제안]   → repropose (SSE)
   └─ 409 progress_changed: (여행 중) 제안 이후 그 제안이 바꾸는 곳을 다녀옴(done)
        "그사이 다녀온 곳이 있어 이 제안을 그대로 반영할 수 없어요" (items: [{item_id, title, done_at}])
        [현재 상황에 맞춰 다시 제안]   → repropose (SSE)     ※ 덮어쓰기 없음 (overwrite 도 같은 409)
```
- `progress_changed` 는 진행 상태가 `version` 을 올리지 않아 `base_changed` 로 잡히지 않는 경우다 (planner §5.4). 카드의 `proposal.progress_changed`
  (항목 id 목록)가 비어 있지 않으면 승인 전에 미리 알릴 수 있다. 다녀온 곳을 건드리지 않는 제안은 그대로 승인된다.

### 4.2 다시 제안 (`repropose`)
- 그 제안을 만든 요청(`request`)을 **지금 일정 기준으로** LLM에 다시 보낸다. 대화에는 사용자 메시지 `이전 제안을 현재 일정에 맞춰 다시 만들어줘.`가 남는다.
- 새 제안이 만들어지면 원래 제안은 `superseded`가 된다. 원래 제안이 이미 대기 상태가 아니면 409 `proposal_not_pending`이다.
- 동행 모드에서는 원래 요청(이벤트 메시지 등)의 시각·문제는 그때 기준이라고 알리고, 지금 일정(실제 진행 기준)의 문제로만 판단하게 한다.
  원래 문제가 지금은 없으면 변경 없이 그렇다고 답한다. 원래 제안이 `progress_changed` 였으면 다녀온 곳은 바꾸지 않는다고 함께 알린다.

### 4.3 추천 카드
- 후보 상태와 버튼은 planner §6.1을 따른다(`in_plan` → [빼기], `not_in_plan` → [추가], `day_out_of_range` → 버튼 없음).
- 응답의 `recommendation`으로 카드를 다시 그린다. 같은 장소가 다른 추천 카드에도 있으면, 그 카드는 다음 조회(또는 세션 조회) 때 바뀐 상태로 나온다.

### 4.4 응답 중 잠금
- 한 세션은 한 번에 한 작업(턴 또는 카드 동작)만 처리한다. 진행 중에는 그 세션의 `chat`, `repropose`, 모든 카드 동작이 409 `agent_busy`다.
  - 이유: 턴은 시작할 때의 일정을 보고 변경안을 만든다. 그 사이 일정이 바뀌면 안이 어긋난다.
- 턴이 시작할 때 계획의 `version`을 기억하고, 제안을 만들 때 `expected_version`으로 보낸다. 그 사이 다른 경로(앱의 직접 편집 등)로 일정이 바뀌었으면 제안을 만들지 않고 `error` (`plan_changed`)로 끝난다.

---

## 5. LLM 도구

| 도구 | 동작 | 단계 kind |
|---|---|---|
| `search_places` | places `GET /places?mode=hybrid` (키워드 + 의미). `near_place_id`로 주변 검색(결과에 직선거리). 이름이 일치하는 결과에 `name_match`, 지역·종류·반경 조건 때문에 같은 이름 장소가 빠지면 조건 없이 다시 찾아 `name_matches_outside_filter`로 함께 준다 | `search` |
| `get_place` | 장소 상세 | `place` |
| `view_plan` | 현재 일정 (초안 적용 상태) | `plan` |
| `add_item` / `update_item` / `remove_item` / `update_plan` | 일정 변경 → **초안** | `edit` |
| `add_rule` / `remove_rule` | 조건(규칙) → 초안 | `rule` |
| `discard_changes` | 초안 비우기 | `edit` |
| `review_plan` | 초안 일정을 검토해 문제 목록을 돌려준다 (일정 규칙 + 안전). `AGENT_REVIEW_ROUNDS` > 0 일 때만 | `review` |
| `travel_time` | 장소 사이 실제 길 기준 도보·차량 시간 (일정 밖 장소끼리도). routing `POST /routing/matrix`. 차량은 주차·승하차 5분 포함, 직선거리 추정이면 `approx`. `check_hazards`면 도착 3곳까지 '지금 출발' 기준 안전: 경로선(`POST /routing/route`)과 도착지를 safety `POST /safety/assess`로 평가한 `safety_now`(경로·도착지 위험 단계, 예보·특보·센서) + 길찾기가 지금 막는 구역·우회 시간 `route_zones` | `route` |
| `check_safety` | 초안 일정(방문 + 이동 구간)의 안전 단계. planner `POST /plans/{id}/safety/preview` (초안 없으면 `GET /safety`) | `safety` |
| `safety_outlook` | 날짜별 날씨·바다 전망 (`date`). safety `GET /safety/outlook` | `safety` |
| `recommend_places` | 추천 카드를 **바로** 만든다 (planner `POST /recommendations`) | `recommend` |

- 변경 도구는 일정을 바로 바꾸지 않고 초안에 쌓는다. 도구마다 planner `preview`로 결과와 위반을 계산해 LLM에 돌려준다.
- **턴이 끝나면** 초안을 제안 하나로 만든다(초안이 비었으면 만들지 않는다).
- 이어서 고치기: 이 세션의 최신 대기 제안이 있고 그 `base_version`이 지금 `version`과 같으면, 그 제안의 ops에서 초안을 시작하고 새 제안이 그것을 대체한다. 다르거나 그 제안이 `progress_changed`(그사이 다녀온 곳을 바꿈)면 빈 초안에서 시작하고(이유와 당시 변경을 LLM에 알린다), 새 제안이 만들어질 때 그것을 대체한다. `discard_changes`로 초안을 비우고 끝나면 이어받던 제안은 거절된다.
- 같은 턴에서 새로 추가한 항목은 음수 임시 id로 다시 가리킨다. 제안이 승인되기 전까지 이어지는 턴에서도 같은 번호다.
- 프롬프트 원칙: "골라 달라·추천해 달라"는 `recommend_places`, "짜 달라·바꿔 달라"는 변경 도구 → 제안.
- **답변과 제안 일치**: 초안이 있거나 이번 턴에 바뀌었으면(선제 턴은 첫 호출 뒤 항상) 하네스가 LLM 호출마다 기록에 남기지 않는 안내를 덧붙인다 —
  지금 초안의 확정 변경 요약(planner preview `changes`)과 변경 후 남는 문제(위반). 답변은 이 변경만 일정 변경으로 말하고,
  목록에 없는 변경(예: "가옥거리는 내일로")은 도구로 넣거나 "다른 방법으로 …도 있어요" 선택지로만 말한다. 초안이 비었으면 "카드가 만들어지지 않는다"고 알린다.
- `travel_time`의 `route_zones`는 길찾기가 막는 구역(실측이 오른 센서 주변 80m·방파제)만 본 것이라, 그것만으로 "안전"이라고 하지 않게 `safety_now`(날씨·특보·도착지 포함)를 함께 준다.

### 검토 루프 (계획 → 검토 → 수정 → 재검토)
- `backend/app/agent/review.py`가 초안을 규칙으로 검토한다.
  - `must`: planner error 위반 (종료 시각 초과, 고정 시각 지각, 사용자 규칙)
  - `should`: planner warning 위반 (운영시간 밖·휴무, 긴 도보 구간·하루 도보 합계 등), 차량 구간 40분 초과,
    점심(11:30~13:30)·저녁(17:30~19:30) 시간대에 식당 없음, 같은 날 중복, 그날 일정 없음,
    **안전 위험(danger) 방문·이동** (시간대 이동·실내 대체·수단 변경, 못 바꾸면 이유 설명)
  - `info`: 하루가 2시간 이상 일찍 끝남, **안전 주의(caution)** (사용자에게 알림), 안전 정보를 확인하지 못함
  - 넣지 않는 것: 직선거리 근사 경고(`travel_estimated`), 장소 정보 없음, 판단하지 않은 안전 항목(먼 날짜·데이터 장애 — 안전을 이유로 바꾸지 않는다)
  - 안전은 planner 안전 점검(`docs/planner/README.md` §7)을 그대로 쓴다. safety 에 접근할 수 없으면 검토는 계속하고 `info` 로 알린다.
- 프롬프트는 "조사 → 초안 → `review_plan` → 수정 → 재검토 → 답변" 순서를 지키게 한다.
- **검토 관문**: 일정을 바꾼 턴에서 LLM이 답을 끝내려 하면 하네스가 직접 검토한다(`review` 단계). `must`·`should`가 남아 있으면 목록을 주고 다시 고치게 한다. 최대 `AGENT_REVIEW_ROUNDS`번 되돌려 보내고, 그래도 남으면 그대로 제안을 만든다.
- 효과 비교: `scripts/planner/eval_agent.py`가 같은 시나리오를 돌려 최종 제안을 같은 규칙으로 채점한다.

## 6. 대화 기록과 맥락

- 매 턴 시스템 프롬프트에 **현재 일정**(이어받은 제안이 있으면 그것을 적용한 상태)이 들어간다. 옛 일정 스냅샷은 기록에 쌓지 않는다.
- 카드 동작(승인·거절·덮어쓰기·추가·빼기)은 숨김 메시지 `[알림] …`으로 기록되어 다음 턴의 LLM이 안다.
- 선제 턴은 숨김 메시지 `[이벤트] …`로 시작한다 (§11.2). 여행 중이면 현재 상황 블록이 함께 들어간다 (§11.3).
- 최근 메시지 `AGENT_HISTORY_MESSAGES`개(기본 40)만 LLM에 넘긴다.
- Claude(§7.1)는 현재 일정 등 맥락을 시스템 프롬프트가 아니라 이번 사용자 메시지 뒤 system 메시지로 받는다 (고정 규칙을 캐시하고 기록을 덧붙이기만 하려고).

## 7. LLM 설정 (서버 `backend/.env`)

| 변수 | 기본값 | 설명 |
|---|---|---|
| `AGENT_PROVIDER` | `openai` | `openai`(기본, gpt-5.4-mini), `local`(Ollama 등) 또는 `claude`(Claude API, §7.1) |
| `AGENT_LOCAL_API` | `ollama` | `ollama`(네이티브 API, 권장) 또는 `openai`(LM Studio·vLLM 같은 OpenAI 호환 서버) |
| `AGENT_LOCAL_BASE_URL` | `http://host.docker.internal:11434` | 호스트의 Ollama. OpenAI 호환 서버면 `…/v1`까지 |
| `AGENT_LOCAL_MODEL` | `qwen3.6:35b` | |
| `AGENT_LOCAL_NUM_CTX` | `16384` | Ollama 컨텍스트 길이 (기본 4096이면 프롬프트가 잘린다) |
| `AGENT_LOCAL_THINK` | `false` | 추론 사용. **qwen3.6은 `true`여야 도구를 제대로 쓴다** |
| `AGENT_OPENAI_API_KEY` | (`LLM_API_KEY`도 읽음) | ChatGPT |
| `AGENT_OPENAI_API` | `responses` | `responses`(Responses API, GPT-5.x에서 추론과 도구를 함께 쓰려면 필수) 또는 `chat`(Chat Completions) |
| `AGENT_OPENAI_MODEL` | `gpt-5.4-mini` | |
| `AGENT_OPENAI_REASONING_EFFORT` | `low` | |
| `AGENT_CLAUDE_API_KEY` | (`ANTHROPIC_API_KEY`도 읽음) | Claude API 키. 비우면 SDK 가 `ant auth login` 프로필 등에서 찾는다 |
| `AGENT_CLAUDE_MODEL` | `claude-sonnet-5-5` | `claude-haiku-5-5`로 바꿀 수 있다 |
| `AGENT_CLAUDE_EFFORT` | `medium` | `low`·`medium`·`high`·`xhigh`·`max`. 기본값에 기대지 않고 항상 보낸다 (§7.1) |
| `AGENT_CLAUDE_MAX_TOKENS` | `64000` | 응답 하나의 최대 토큰 (thinking 포함, 스트리밍) |
| `AGENT_CLAUDE_WEB_SEARCH_MAX_USES` | `0` | Anthropic 웹 검색 서버 도구를 응답당 이 횟수까지. `0`이면 끔 (§7.1) |
| `AGENT_CLAUDE_THINKING_MISMATCH` | `drop_block` | 기록이 바뀌어 thinking 블록이 무효일 때 `drop_block`(버리고 계속, 경고 로그) 또는 `error`(400) |
| `AGENT_MAX_STEPS` | `20` | 한 턴의 LLM 호출 최대 횟수 (마지막 호출은 도구 없이 답변 강제) |
| `AGENT_REVIEW_ROUNDS` | `2` | 검토 관문이 되돌려 보내는 최대 횟수. `0`이면 `review_plan` 도구와 관문을 끈다 |
| `AGENT_CLOCK` | `safety` | `safety`: 프롬프트의 '지금'이 `GET /safety/sim` 가상 시각을 따른다 / `system`: 시스템 시각 (§11.4) |
| `AGENT_CLOCK_CACHE_SEC` | `5` | safety 시각(시스템 시각과의 차이)을 기억하는 시간 |
| `AGENT_TRIP_TIMEOUT_SEC` | `3` | 매 턴 trip 상태 조회 제한 시간. 넘기면 계획 모드로 |

새 공급자는 `llm.py`에 같은 이벤트(`TextDelta`, `ReasoningDelta`, `ToolCall`, `Finish`)를 내는 Provider를 추가하면 된다.
`AGENT_TEMPERATURE`는 Claude 에는 보내지 않는다 (Sonnet 5.5·Haiku 5.5 는 기본값이 아닌 샘플링 값을 400 으로 거부).

### 모델·구조 비교 (2026-10-04, `scripts/planner/eval_agent.py`, 시나리오 3개 × 2회)

남은 문제는 최종 제안을 `review.py` 규칙으로 채점한 수의 평균이다(must / should).

| 모델 | 검토 루프 | 제안 생성 | 남은 문제 | 턴 평균 시간 |
|---|---|---|---|---|
| qwen3.6:35b (로컬) | 없음 | 4/6 | 0 / 3.0 | 약 3분 |
| qwen3.6:35b (로컬) | 있음 | 4/6 (1회 15분 초과) | 0.25 / 1.25 | 3~10분 |
| gpt-5.4-mini | 없음 | 6/6 | 0 / 0.8 | 13초 |
| gpt-5.4-mini | 있음 | 6/6 | **0 / 0** | 18초 |

- 검토 루프는 지적을 고칠 수 있는 모델에서 효과가 크다. qwen3.6은 지적을 받아도 고치지 못하는 경우가 많고(같은 장소 유지, 고치다 다른 문제 생김), 생각이 길어져 매우 느려진다.
- 검토에서 되돌려진 답변은 사용자에게 보내지 않는다(일정을 바꾼 턴의 마지막 답변은 검토를 통과한 뒤 내보낸다).

### 비용 (gpt-5.4-mini + 검토 루프 실측, 2026-10-04)
- 가격: 입력 $0.75, 캐시된 입력 $0.075, 출력(추론 포함) $4.50 / 100만 토큰
- 시나리오 4종 18턴(5턴 대화 포함): 턴당 평균 입력 4.1만 토큰(그중 83%가 캐시), 출력 800토큰, LLM 호출 5.3회
- **턴당 평균 약 $0.011 (약 16원), 최대 $0.025 (약 37원)**. 로그(`[agent] session=… usage`)와 `done.usage`로 확인할 수 있다.

### 7.1 Claude API (`AGENT_PROVIDER=claude`)

`backend/app/agent/claude_llm.py`의 `ClaudeProvider`. 공식 `anthropic` Python SDK(`backend/requirements/agent.txt`)로 스트리밍하고
하네스 이벤트로 바꾼다 (텍스트 → `text`, thinking 요약 → `thinking`, `tool_use` → 도구 호출, 끝에 usage).

**모델별로 보내는 것** (Sonnet 5.5 / Haiku 5.5, claude-api 스킬 문서 기준)

| | `claude-sonnet-5-5` | `claude-haiku-5-5` |
|---|---|---|
| thinking | `adaptive` (`budget_tokens`·`disabled`는 400), `display: "summarized"` (기본 `omitted`는 빈 문자열) | 같음 |
| effort | 명시 (`medium`). 모델 기본값은 `high` | 명시 (`medium`). 모델 기본값도 `medium` |
| 웹 검색 (켤 때) | `web_search_20260209` (동적 필터링) | `web_search_20250305` (기본 버전만) |
| 서버측 refusal fallback | 있음 (`fallbacks: "default"`, Claude API 만) — **쓰지 않음** (아래) | 없음 |
| `tool_choice` | `auto`(기본). 마지막 단계만 `none`. 강제(`any`/`tool`)는 400 이라 쓰지 않는다 | 같음 |

- **effort 기본값 `medium`의 근거**: 한 턴이 "장소 찾기 → 초안 → 검토 → 수정"처럼 도구를 여러 번 쓰는 작업이다. 스킬 문서는 Sonnet 5.5 에
  도구를 여러 번 쓰는 작업은 `medium`, 대화·검색은 `low`부터를 권하고(레벨이 Sonnet 5 와 달라 기본 `high`를 그대로 쓰지 말 것),
  Haiku 5.5 는 `medium`이 기본이자 "대부분의 작업" 시작점이다. 그래서 두 모델 모두 `medium`으로 시작하고, 바꾸려면 아래 eval 로 비교한다.
  답이 짧은 동행 모드는 `low`도 후보다 (낮추면 도구 호출이 줄고 검토를 건너뛸 수 있으니 남은 문제 수로 확인).
- **웹 검색은 기본 꺼짐**: 장소는 `search_places`의 place_id 만 쓰고 사실은 도구 결과로만 말하는 규칙(§5)과 겹치고, 검색당 비용이 든다.
  켜면(`AGENT_CLAUDE_WEB_SEARCH_MAX_USES` > 0) 모델에 맞는 버전을 붙이고, 서버 도구 루프가 `pause_turn`으로 멈추면 이어 받는다.
- **도구 입력 스트리밍(`eager_input_streaming`)은 쓰지 않는다**: 도구 인자가 짧고 하네스는 완성된 호출만 실행하므로, 서버의 입력 검증을 끌 이유가 없다.

**기록 편집 금지 (preserved thinking)** — 하네스의 `append_only` 경로

Sonnet 5.5·Haiku 5.5 의 thinking 블록은 그 앞의 `system`·`tools`·메시지 전체에 묶여 있어, 앞부분이 바뀐 기록에 다시 보내면 무효(400 또는 버림)다.
같은 턴의 도구 루프 동안 thinking 블록을 받은 그대로 다시 보내야 하므로, 하네스는 Claude 에게 기록을 **덧붙이기만** 한다.
OpenAI·Ollama 공급자는 전과 같은 경로(호출마다 붙였다 떼는 안내)를 그대로 쓴다.

| 전 (OpenAI·Ollama, 그대로) | Claude (`append_only`) |
|---|---|
| 시스템 프롬프트 = 규칙 + 현재 상황·시각·일정 | 시스템 프롬프트 = **고정 규칙만** (턴이 바뀌어도 같다). 맥락은 이번 사용자 메시지 뒤 `role: "system"` 메시지 |
| 확정 변경·빈 응답·도구 한도 안내를 그 호출에만 붙이고 뗌 | `clear_at: "next_user_message"` system 메시지로 **덧붙임** (다음 도구 결과가 오면 지워져 모델은 최신 것만 본다. 보낸 것은 그대로 남긴다) |
| 검토 관문: 끝내려던 답을 버리고 안내만 붙여 다시 호출 | 끝내려던 답(원본 블록 포함)을 assistant 로 **남기고** 검토 결과를 user 메시지로 덧붙임. 사용자에게는 여전히 보내지 않는다 |
| 마지막 단계: 도구 목록을 비움 | 도구 목록은 그대로, `tool_choice: none` |

- 같은 턴의 assistant 메시지에는 공급자 원본 블록(`native`: thinking 서명·text·tool_use)을 담아 다음 호출에 그대로 보낸다. 다른 모델이 만든 블록은 보내지 않는다.
- 지난 턴은 DB 기록(텍스트·도구 호출·짧은 도구 결과)으로 thinking 없이 보낸다 (맥락 메시지·안내는 DB 에 남지 않아 지난 턴의 thinking 은 무효이기 때문).
- system 메시지는 user 메시지 뒤에만 올 수 있어, 빈 응답을 되묻는 자리가 system 메시지 바로 뒤면 안내 없이 다시 호출한다.
- `thinking.block_binding.prefix_mismatch_behavior`를 명시한다(`AGENT_CLAUDE_THINKING_MISMATCH`, 기본 `drop_block`). 응답의 `input_transformations`에
  항목이 있으면 기록을 고친 버그이므로 경고 로그를 남긴다. 베타 헤더: `mid-conversation-system-clear-at-2026-08-21`, `thinking-binding-controls-2026-08-01`.

**프롬프트 캐시**: 도구 정의 → 고정 규칙(캐시 지점 1) → 지난 대화 → 이번 사용자 메시지(캐시 지점 2) → 맥락 system 메시지(현재 상황·지금 시각·현재 일정)
→ 이번 턴의 도구 루프(자동 캐시 지점 3이 끝을 따라간다). 같은 턴의 두 번째 호출부터는 앞부분 전체를, 다음 턴은 지난 대화까지를 캐시에서 읽는다.
`usage`의 `cached`(캐시에서 읽음)·`cache_write`(캐시에 씀)로 확인한다. 최소 캐시 길이는 두 모델 모두 512 토큰이다.

**거절(refusal)**: `stop_reason: "refusal"`이면 받은 조각을 완결된 답으로 쓰지 않고 `error` `llm_refused`("죄송해요, 이 요청은 처리할 수 없어요…")로 턴을 끝낸다.
같은 요청은 다시 거절되므로 하네스가 재시도하지 않는다. `max_tokens`에서 도구 호출이 잘리면 실행하지 않고 그 단계를 다시 시도한다.
API 오류(400·429·5xx·연결)는 SDK 가 이미 재시도(기본 2회)하므로 하네스는 다시 시도하지 않고 `llm_failed`로 끝낸다.

**서버측 fallback 을 켜지 않는 이유**: Sonnet 5.5 의 `fallbacks: "default"`는 `cyber`·`frontier_llm` 거절만 Claude Sonnet 5 로 다시 돌린다.
여행 일정 대화에서 나올 일이 거의 없는 분류이고, fallback 모델(Sonnet 5)은 이 하네스가 쓰는 중간 `role: "system"` 메시지를 지원하지 않으며
Sonnet 5.5 의 thinking 블록도 읽지 못한다. Haiku 5.5 에는 서버측 fallback 이 없다. 그래서 두 모델 모두 클라이언트에서 거절을 처리한다.

**확인 절차 (실제 API 는 키를 넣은 뒤에만)**

1. 키: 저장소 밖에 두고 `backend/.env`에 `AGENT_PROVIDER=claude`, `AGENT_CLAUDE_API_KEY=…` (또는 `ANTHROPIC_API_KEY`). `docker compose up -d --build agent`.
   `GET /agent/info` → `{"provider": "claude:claude-sonnet-5-5"}`.
2. chat 한 턴: `python scripts/planner/cli.py chat 1 --new --thinking -m "점심 넣어줘"`. 확인할 것 — 텍스트·thinking 이 스트리밍되고, 제안 카드가 생기고,
   로그 `[agent] session=… usage`에 `calls` ≥ 2 이면 두 번째 호출부터 `cached` > 0. `thinking 블록` 경고 로그(기록 편집)가 없어야 한다.
3. 장면 ③ 선제 턴: `backend/.env`에 `SAFETY_SOURCE=mock`, `AUTH_REQUIRED=false`로 `python scripts/planner/cli.py trip replay new scene3_delay --speed 0`.
   delay 선제 턴이 근거 + 추천안 1개(제안 카드) + "다른 방법" 한 줄로 답하는지, 이어서 "과메기문화관은 꼭 가고 싶어"에 `must_include` 규칙이 든 새 제안이 이전 제안을 대체하는지 본다 (docs/trip/README.md §8).
4. 모델 비교: 같은 서버 설정에서 `AGENT_CLAUDE_MODEL`만 바꿔 다시 띄우고 같은 시나리오를 돌린다.
   ```bash
   python scripts/planner/eval_agent.py --url http://127.0.0.1:$API_PORT --label sonnet55 --runs 2 --out claude.jsonl
   python scripts/planner/eval_agent.py --url http://127.0.0.1:$API_PORT --label haiku55 --runs 2 --out claude.jsonl
   python scripts/planner/eval_agent.py --out claude.jsonl --summary   # 남은 문제(must/should), 턴 시간, 턴당 토큰
   ```

**모델 비교** (`eval_agent.py` 시나리오 4개 × 2회, 검토 루프 있음, effort `medium`)

| 모델 | 제안 생성 | 남은 문제 (must / should) | 턴 평균 시간 | 턴당 입력 (그중 캐시) | 턴당 출력 |
|---|---|---|---|---|---|
| claude-sonnet-5-5 | (키를 넣은 뒤 측정) | | | | |
| claude-haiku-5-5 | (키를 넣은 뒤 측정) | | | | |

## 8. 로컬 LLM 메모 (qwen3.6:35b-a3b Q4, RTX 5070 12GB)

- 첫 호출은 모델 로드로 30초 이상 걸린다. 일정 하나를 새로 짜는 턴은 1~2분.
- thinking을 끄면 도구를 거의 쓰지 않고 장소를 지어낸다.
- 하네스의 보완: 깨진 도구 호출은 텍스트가 나가기 전이면 최대 2번 재시도, 마지막 단계의 도구 호출 무시, 빈 답변이면 기본 문장, `add_item`의 모르는 인자는 무시하고 알림.

## 9. CLI

```bash
export GURYONG_API_URL=http://127.0.0.1:8002 GURYONG_USER=seoyun
python scripts/planner/cli.py chat 1                  # 계획 1의 최근 세션 이어서 (없으면 생성)
python scripts/planner/cli.py chat 1 --new --thinking  # 새 세션, 추론 과정도 표시
python scripts/planner/cli.py chat 1 -m "점심 넣어줘" -m /accept
```

대화 중 명령:

| 명령 | 동작 |
|---|---|
| `/plan` | 현재 일정 |
| `/cards` | 이 세션의 카드 목록 (번호) |
| `/detail [카드번호]` | 제안의 변경 전·후 전체 일정 |
| `/accept [카드번호] [overwrite]` | 제안 승인. 일정이 바뀌었으면 1) 그대로 반영 2) 다시 제안 3) 취소 선택 (`overwrite`를 붙이면 묻지 않고 그대로 반영) |
| `/reject [카드번호]` | 제안 거절 |
| `/repropose [카드번호]` | 제안을 현재 일정에 맞춰 다시 만들기 |
| `/add <후보번호>` · `/remove <후보번호>` | 최근 추천 카드의 후보 추가·빼기 |
| `/exit` | 종료 |

카드번호를 생략하면 가장 최근 카드다. Git Bash에서 `-m /accept`를 쓸 때는 `MSYS_NO_PATHCONV=1`을 붙인다.

## 10. 테스트

```bash
docker compose run --rm agent pytest tests/agent   # 대본대로 움직이는 가짜 LLM. Ollama 없이 돈다
```
- `test_guide.py`: 동행 모드·선제 턴. trip 과 safety 시계(`/safety/sim`)는 가짜 HTTP 전송으로 바꾸고 planner 는 같은 앱을 쓴다.
- `test_claude_llm.py`: Claude 공급자. 가짜 Anthropic 스트림 이벤트를 실제 SDK 로 읽는다 (요청 본문·캐시 지점·thinking 블록 왕복·usage·거절·오류, 키 없이).
- `test_harness.py`의 append_only 테스트: 한 턴의 호출마다 앞 호출의 메시지가 그대로 앞부분인지(기록 편집 없음), 시스템 프롬프트·도구 목록이 그대로인지,
  Claude 형식으로 바꿨을 때 중간 system 메시지 배치 규칙을 지키는지 확인한다.
- 장소 검색을 쓰는 테스트는 장소 데이터·임베딩이 있어야 한다 (`docker compose up -d places`, 처음이면 적재에 몇 분).

## 11. 동행 모드 (여행 중)

명세의 다른 쪽: `docs/trip/README.md` §1.1(원칙), §3(이벤트), §4.2(선제 턴), §4.4(현재 상황), §5(시계).
**동행 모드 = 계획 모드 + 현장 맥락**이다. 도구·카드·승인·잠금·검토 관문은 계획 모드와 같다.

### 11.1 모드 판단
- 매 턴(chat, repropose) 시작할 때 trip `GET /trip/plans/{plan_id}/state`를 부른다 (받은 인증 그대로, 제한 시간 `AGENT_TRIP_TIMEOUT_SEC`).
- `active: true`면 **동행 모드**, 아니면 계획 모드.
- trip 에 접근할 수 없거나(연결 실패·시간 초과·5xx), 404(trip 미배포·계획 모름)거나, 형식이 이상하면 **계획 모드로 계속**한다. 턴은 실패하지 않는다 (404 가 아닌 오류는 로그만).
- 선제 턴(§11.2)은 본문의 `trip`을 그대로 쓰고 다시 조회하지 않는다.

### 11.2 선제 턴 `POST /agent/sessions/{session_id}/events`
trip 이 단계 3 이벤트(`delay`, `safety_raised`)에서 부른다. 앱은 부르지 않는다.

```jsonc
// 요청
{"event": Event,          // docs/trip/README.md §3.3. type 필수. agent 는 type·message·item_ids·payload 를 쓰고 나머지 필드는 무시
 "trip": TripState}       // docs/trip/README.md §1.3
// 응답 200 ChatResult (§3.3, stream=false)
{"text": "카페 도착이 늦어져 …", "cards": [{"type": "proposal", "proposal": ProposalDetail}], "steps": [...],
 "steps_summary": "...", "error": null, "usage": {...}, "message_id": 102, "mode": "guide"}
```
- 인증: 그 사용자의 토큰 (trip 이 `issue_token(user_id, ttl=5분)`으로 만든다). 다른 사용자 세션이면 404 `session_not_found`.
- 사용자 메시지 대신 **숨김 메시지** `[이벤트] …`로 턴을 시작한다. 내용: 이벤트 종류·`message`, 영향 항목 `item_ids`,
  `payload.new_violations`의 문구, `payload.delay_minutes`, 나머지 `payload`(근거, 1500자까지), 그리고 종류별 **할 일**:

  | type | 할 일 (LLM 에 주는 지시) |
  |---|---|
  | `delay` | 오늘 남은 일정을 고친 **추천안 하나**를 만든다 (체류 줄이기·순서 바꾸기·영향받는 곳 빼기/다른 날로·가까운 대체 장소). 완료·지난 시간은 바꾸지 않는다. + 답변 형식 |
  | `safety_raised` | `check_safety`로 근거를 확인하고, 신호 없는 시간대로 이동·실내 대체·이동 수단 변경 **추천안 하나**를 만든다. + 답변 형식. 못 바꾸면 이유와 조심할 점만 |
  | 그 밖 | 짧게 알리고, 바꿔야 하면 변경안을 만든다 |

  **답변 형식** (PROJECT.md §3 장면 ③·④): 현장에서 바로 듣기 좋은 짧은 세 문장 — ① 무엇이 왜 문제인지 근거·영향
  ② 추천안 (초안에 실제로 넣은 변경만, 제안 카드 하나로 전달) ③ "다른 방법으로 …도 있어요" 한 줄 (도구로 만들지 않은 선택지).
  대안을 카드로 여러 개 만들지 않는다: 선제 턴에는 `recommend_places` 도구를 주지 않고, 한 턴의 변경은 원래 제안 하나다.
  사용자가 이어서 대화로 조건을 붙이면("과메기문화관은 유지") 그 조건을 `add_rule`(예: `must_include`)로 등록하고 대기 중인 제안 위에서 고쳐
  다시 제안한다 (새 제안이 이전 제안을 `superseded` 로 대체).
- 대화 기록(`GET /sessions/{id}`)에는 숨김 메시지가 나오지 않고 **assistant 답변과 카드만** 보인다. 선제 턴 답변은 앞 턴의 말풍선에 합쳐지지 않고 새 말풍선이다.
  다음 턴의 LLM 은 이벤트 메시지와 답변을 기록으로 안다.
- 잠금·제안·검토 관문은 chat 과 같다: 응답 중이면 409 `agent_busy` (trip 은 큐에 두고 나중에 다시 보낸다), 초안이 있으면 턴 끝에 제안을 만들고,
  이어받을 대기 제안이 있으면 그 위에서 고친다. 제안의 `request`는 이벤트 메시지다 (다시 제안할 때 같은 의도로 쓰인다).
- trip 은 `message_id`와 `cards`의 proposal id 를 이벤트의 `agent` 필드에 기록한다. `error`가 있으면 턴은 끝났지만 제안이 없을 수 있다.
- 턴은 LLM 에 따라 수십 초 걸린다. trip 은 넉넉한 제한 시간(예: 120초)으로 부르고, 기다리는 동안 다른 단계 3 이벤트는 큐에 둔다.
- 본문 검증 오류(`event.type` 없음, `trip` 없음, 모르는 최상위 필드)는 422 `invalid_request`.

### 11.3 동행 모드 프롬프트와 제한
시스템 프롬프트 = 계획 규칙(그대로) + **동행 섹션** + **현재 상황 블록** + 현재 시각 + 현재 일정.

```
## 현재 상황 (여행 중)
- 1일차 2026-10-10 14:20 (시뮬레이션 시각)
- 위치: 물회집 (도착 13:40, 머무는 중)          // moving: "이동 중 (…에서 13:40 출발)", 위치 없음: "아직 받지 못함"
- 다음: 카페 15:00 예정, 예상 도착 15:12 (12분 늦음)   // delay_minutes 음수면 "N분 여유", 없으면 "오늘 남은 일정 없음"
- 오늘 남은 일정: 2곳 / 완료 1곳                 // 그날 항목의 진행 상태 (건너뜀 제외)
- 바꿀 수 없는 것: 완료한 항목, 지난 시각
```
- 동행 섹션: 답변은 **1~3문장**(현장, 나중에 음성), 목록·기호 없이. 모든 도구 사용 가능, 기본 대상은 오늘 남은 일정, 원하면 다른 날도.
  "지금 가도 돼?"는 `travel_time`(`check_hazards`), 늦어지면 체류 줄이기·순서·빼기로 조정. 변경안은 하나, 다른 선택지는 말로 한 줄.
  사용자가 대기 중인 제안에 조건을 붙이면 `add_rule`로 등록하고 그 제안 위에서 고쳐 다시 제안한다.
- **현재 일정은 실제 진행 기준**이다: 초안이 있으면 planner `preview`, 없으면 planner `projection` 의 일정 (실제 도착·완료 기록 + planner 의 지금,
  planner §8.4). 그래서 LLM 이 보는 시각·위반과 제안 카드의 전후 비교(`day_end` 등)가 현실과 맞는다. projection 이 실패하면(오늘이 기간 밖 등) 계획 기준.
- 하네스가 변경 도구에서 **직접 거부**한다 (도구 오류 `not_allowed_during_trip`, LLM 이 이유를 받고 다른 방법을 찾는다):
  - 완료(`done`) 항목의 수정·삭제
  - 오늘 항목을 지금보다 이른 `start_time`으로 고정
  - 지금 머무는(`visiting`) 항목의 체류 시간·메모 외 변경
  - 진행 상태를 `visiting`·`done`으로 바꾸기 (진행 상태는 trip 이 위치로 기록한다. `skipped`는 일정 변경이라 허용)
- 계획 모드에서는 위 제한이 없다.

### 11.4 시계
- 프롬프트의 '현재 시각'과 현재 상황 블록의 시각:
  - 동행 모드: `TripState.now` (trip 도 safety 시각을 따른다). `clock: "sim"`이면 "(시뮬레이션 시각)"을 붙인다.
  - 계획 모드: `AGENT_CLOCK=safety`이면 `GET /safety/sim`의 `now`(`SAFETY_SOURCE=mock`일 때만 있음)를 따른다. 시스템 시각과의 차이를
    `AGENT_CLOCK_CACHE_SEC`(5초) 동안 기억한다. 404(`sim_disabled`)면 시스템 시각, 접근할 수 없으면 20초 동안 시스템 시각.
- 동행 모드의 '지난 시각' 판정(§11.3)도 이 시각을 쓴다.
