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
| `done` | `{"text": "최종 답변 전체", "cards": [{"type": "proposal", "id": 13}, ...], "usage": {...}}` | 턴 종료. `usage`는 LLM 토큰 사용량(`input`, `cached`, `output`, `reasoning`, `calls`)으로 비용 모니터링용 |
| `error` | `{"code", "message"}` | 오류. **이 뒤로 이벤트가 오지 않는다** |

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
| `safety` | 안전 확인 | `위험 포함 2곳` / `위험 신호 없음` / `판단할 수 없는 일정 있음` / `10-11 주의 신호` | 날씨와 안전을 확인하고 있어요. |

- 실패한 도구 호출은 그 단계를 `error`로 표시하고, `detail`에 짧은 사유를 넣는다. LLM은 오류를 받고 다시 시도할 수 있으므로 턴은 계속된다.
- 새 도구가 생기면 같은 방식으로 `kind`가 늘어난다.

### 3.3 `ChatResult` (`stream=false`)

```json
{"text": "...", "steps": [Step, ...], "steps_summary": "...", "cards": [Card, ...], "error": null}
```
`error`는 `{"code", "message"}` 또는 `null`이다.

---

## 4. 카드 동작

카드 동작은 Agent 경로로 호출한다. 결과가 대화 기록에 남아 다음 턴의 LLM이 알 수 있기 때문이다(planner를 직접 호출해도 일정은 바뀌지만 LLM은 모른다).

| 메서드 | 경로 | 본문 | 성공 | 대표 오류 |
|---|---|---|---|---|
| POST | `/agent/sessions/{sid}/proposals/{pid}/accept` | `{"mode": "auto" \| "overwrite"}` (생략 = auto) | 200 `{"proposal", "plan"}` | 409 `base_changed`·`proposal_not_pending`·`agent_busy` |
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
```

### 4.2 다시 제안 (`repropose`)
- 그 제안을 만든 요청(`request`)을 **지금 일정 기준으로** LLM에 다시 보낸다. 대화에는 사용자 메시지 `이전 제안을 현재 일정에 맞춰 다시 만들어줘.`가 남는다.
- 새 제안이 만들어지면 원래 제안은 `superseded`가 된다. 원래 제안이 이미 대기 상태가 아니면 409 `proposal_not_pending`이다.

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
| `check_safety` | 초안 일정(방문 + 이동 구간)의 안전 단계. planner `POST /plans/{id}/safety/preview` (초안 없으면 `GET /safety`) | `safety` |
| `safety_outlook` | 날짜별 날씨·바다 전망 (`date`). safety `GET /safety/outlook` | `safety` |
| `recommend_places` | 추천 카드를 **바로** 만든다 (planner `POST /recommendations`) | `recommend` |

- 변경 도구는 일정을 바로 바꾸지 않고 초안에 쌓는다. 도구마다 planner `preview`로 결과와 위반을 계산해 LLM에 돌려준다.
- **턴이 끝나면** 초안을 제안 하나로 만든다(초안이 비었으면 만들지 않는다).
- 이어서 고치기: 이 세션의 최신 대기 제안이 있고 그 `base_version`이 지금 `version`과 같으면, 그 제안의 ops에서 초안을 시작하고 새 제안이 그것을 대체한다. 다르면 빈 초안에서 시작하고(LLM에 알린다), 새 제안이 만들어질 때 그것을 대체한다. `discard_changes`로 초안을 비우고 끝나면 이어받던 제안은 거절된다.
- 같은 턴에서 새로 추가한 항목은 음수 임시 id로 다시 가리킨다. 제안이 승인되기 전까지 이어지는 턴에서도 같은 번호다.
- 프롬프트 원칙: "골라 달라·추천해 달라"는 `recommend_places`, "짜 달라·바꿔 달라"는 변경 도구 → 제안.

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
- 최근 메시지 `AGENT_HISTORY_MESSAGES`개(기본 40)만 LLM에 넘긴다.

## 7. LLM 설정 (서버 `backend/.env`)

| 변수 | 기본값 | 설명 |
|---|---|---|
| `AGENT_PROVIDER` | `openai` | `openai`(기본, gpt-5.4-mini) 또는 `local`(Ollama 등) |
| `AGENT_LOCAL_API` | `ollama` | `ollama`(네이티브 API, 권장) 또는 `openai`(LM Studio·vLLM 같은 OpenAI 호환 서버) |
| `AGENT_LOCAL_BASE_URL` | `http://host.docker.internal:11434` | 호스트의 Ollama. OpenAI 호환 서버면 `…/v1`까지 |
| `AGENT_LOCAL_MODEL` | `qwen3.6:35b` | |
| `AGENT_LOCAL_NUM_CTX` | `16384` | Ollama 컨텍스트 길이 (기본 4096이면 프롬프트가 잘린다) |
| `AGENT_LOCAL_THINK` | `false` | 추론 사용. **qwen3.6은 `true`여야 도구를 제대로 쓴다** |
| `AGENT_OPENAI_API_KEY` | (`LLM_API_KEY`도 읽음) | ChatGPT |
| `AGENT_OPENAI_API` | `responses` | `responses`(Responses API, GPT-5.x에서 추론과 도구를 함께 쓰려면 필수) 또는 `chat`(Chat Completions) |
| `AGENT_OPENAI_MODEL` | `gpt-5.4-mini` | |
| `AGENT_OPENAI_REASONING_EFFORT` | `low` | |
| `AGENT_MAX_STEPS` | `20` | 한 턴의 LLM 호출 최대 횟수 (마지막 호출은 도구 없이 답변 강제) |
| `AGENT_REVIEW_ROUNDS` | `2` | 검토 관문이 되돌려 보내는 최대 횟수. `0`이면 `review_plan` 도구와 관문을 끈다 |

새 공급자는 `llm.py`에 같은 이벤트(`TextDelta`, `ReasoningDelta`, `ToolCall`, `Finish`)를 내는 Provider를 추가하면 된다.

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
