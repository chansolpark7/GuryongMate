# agent — Planner Agent 하네스

사용자와 대화하며 일정을 짜고 고치는 LLM Agent. **LLM은 일정을 직접 바꾸지 않는다.**
변경은 제안(Proposal)으로 만들어지고, 사용자가 승인(accept)해야 planner에 반영된다.

앱은 어떤 LLM이 돌고 있는지 몰라도 된다. 아래 API만 호출하면 된다. LLM 선택은 서버 환경변수로만 한다.

## 흐름

```
앱 ──POST /agent/sessions {plan_id}──────────────▶ 세션 (계획 하나당 대화 하나, 여행 전·중 맥락 유지)
앱 ──POST /agent/sessions/{sid}/chat {message}──▶ SSE 스트림
        text…(실시간)  tool_call/tool_result(“장소 검색 중…”)  proposal(변경 제안 카드)  done
앱 ──POST /agent/sessions/{sid}/proposals/{pid}/accept──▶ 일정 반영 → {proposal, plan}
                                         /reject ──▶ 일정 그대로
```

- 한 턴 안에서 LLM이 부른 변경 도구(add_item 등)는 **초안**에 쌓인다. 도구마다 planner `preview`로 적용 결과와 위반을 계산해 LLM에 돌려주므로, LLM이 결과를 보고 스스로 다시 조정할 수 있다.
- 턴이 끝나면 초안을 planner 제안으로 저장하고 `proposal` 이벤트를 보낸다. 제안에는 변경 요약(`changes`), 현재(`before`), 승인 시(`after`) 일정이 모두 들어 있어 비교 카드를 그릴 수 있다.
- 승인하기 전에 사용자가 "그건 빼줘"처럼 이어서 말하면, 대기 중인 제안 위에 수정을 더한 **새 제안**이 나온다. 이전 제안은 `superseded`가 된다.
- 승인·거절 결과는 숨김 메시지로 대화 기록에 남는다. 그래서 다음 턴의 LLM은 그 사실을 알지만 화면에는 보이지 않는다.

## API

모든 요청에 `X-User-Id` 헤더가 필요하다(planner와 같음).

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/agent/sessions` | 세션 생성 `{plan_id}` |
| GET | `/agent/sessions?plan_id=` | 세션 목록 (최근 대화 순) |
| GET | `/agent/sessions/{sid}` | 화면용 대화 기록과 대기 중인 제안(`pending_proposal`) |
| DELETE | `/agent/sessions/{sid}` | 세션 삭제 |
| POST | `/agent/sessions/{sid}/chat` | 메시지 전송. 기본은 **SSE 스트림**. `?stream=false`이면 끝난 뒤 JSON(`ChatResult`)으로 받는다. 같은 세션에서 응답이 진행 중이면 409 |
| POST | `/agent/sessions/{sid}/proposals/{pid}/accept` | 제안 승인 → `{proposal, plan}` |
| POST | `/agent/sessions/{sid}/proposals/{pid}/reject` | 제안 거절 |
| GET | `/agent/info` | (디버깅용) 현재 LLM 이름 |

### SSE 이벤트 (`event:` / `data:` JSON)

| event | data | 앱에서 |
|---|---|---|
| `start` | `{session_id}` | |
| `thinking` | `{delta}` | 모델의 추론 과정 (지원 모델만). "생각 중…" 표시용 |
| `text` | `{delta}` | 답변 말풍선에 이어 붙인다 |
| `tool_call` | `{id, name, arguments}` | "장소 검색 중…" 같은 진행 표시 |
| `tool_result` | `{id, name, ok, summary}` | |
| `proposal` | planner `ProposalDetail` | 제안 카드 (`changes`, `before`/`after`, `after.violations`) |
| `done` | `{text, proposal_id}` | 턴 종료 |
| `error` | `{message}` | 오류. 이 뒤로 이벤트가 오지 않는다 |

Flutter에서는 `http`의 `Client.send(StreamedRequest)`로 응답 바이트 스트림을 받아 줄 단위로 파싱하면 된다. SSE 패키지를 써도 된다(`POST`를 지원하는 것으로).

## 도구 (LLM용)

| 도구 | 동작 |
|---|---|
| `search_places` | places `GET /places?mode=hybrid` (키워드 + 의미 검색). `near_place_id`로 주변 검색 |
| `get_place` | 장소 상세 (운영시간, 휴무, 메뉴, 소개) |
| `view_plan` | 현재 일정 (초안 적용 상태). 시스템 프롬프트에도 매 턴 들어간다 |
| `add_item` / `update_item` / `remove_item` | 일정 변경 → 초안 |
| `update_plan` | 기간, 하루 시간대, 이동 수단 → 초안 |
| `add_rule` / `remove_rule` | 사용자 조건(규칙) → 초안 |
| `discard_changes` | 초안 비우기 (대기 중인 제안 거절) |

같은 턴에서 새로 추가한 항목은 음수 임시 id(-1, -2…)로 다시 참조한다.

## LLM 설정 (서버 `backend/.env`)

| 변수 | 기본값 | 설명 |
|---|---|---|
| `AGENT_PROVIDER` | `local` | `local` 또는 `openai` |
| `AGENT_LOCAL_API` | `ollama` | `ollama`(네이티브 API, 권장) 또는 `openai`(LM Studio, vLLM 같은 OpenAI 호환 서버) |
| `AGENT_LOCAL_BASE_URL` | `http://host.docker.internal:11434` | 컨테이너에서 호스트의 Ollama. OpenAI 호환 서버면 `…/v1`까지 쓴다 |
| `AGENT_LOCAL_MODEL` | `qwen3.6:35b` | |
| `AGENT_LOCAL_NUM_CTX` | `16384` | Ollama 컨텍스트 길이. 기본 4096이면 프롬프트가 잘려 도구를 무시한다 |
| `AGENT_LOCAL_THINK` | `false` | 추론 사용. qwen3.6은 **켜야 도구를 제대로 쓴다** (아래 참고) |
| `AGENT_OPENAI_API_KEY` | (`LLM_API_KEY`도 읽음) | ChatGPT |
| `AGENT_OPENAI_MODEL` | `gpt-5-mini` | |
| `AGENT_OPENAI_REASONING_EFFORT` | `low` | |
| `AGENT_MAX_STEPS` | `8` | 한 턴의 LLM 호출 최대 횟수 (마지막 호출은 도구 없이 답변 강제) |

새 공급자(예: Responses API, Anthropic)는 `llm.py`에 같은 이벤트(`TextDelta`, `ReasoningDelta`, `ToolCall`, `Finish`)를 내는 Provider를 추가하면 된다.

## 로컬 LLM 실측 (qwen3.6:35b-a3b Q4, RTX 5070 12GB, 2026-10-03)

- 첫 호출은 모델 로드 때문에 30초 이상 걸린다. 이후 일정 하나를 새로 짜는 턴(검색 5~8회, 추가 3~5회)에 1~2분 걸린다.
- **thinking을 끄면** 도구를 거의 쓰지 않고 장소를 지어내 텍스트로 일정을 쓴다. 켜면 검색 → 추가 → 제안 흐름을 따른다.
- 로컬 모델이 가끔 하는 실수와 하네스의 대응은 다음과 같다.
  - 형식이 깨진 도구 호출을 내서 Ollama 파서가 실패한다 → 텍스트를 내기 전이면 같은 단계를 최대 2번 다시 시도한다.
  - 도구를 주지 않은 마지막 단계에서도 도구를 부른다 → 무시한다.
  - 답변 없이 끝난다 → 기본 안내 문장을 넣는다.
  - 없는 인자를 붙인다 → `add_item`은 무시한다. 나머지 도구는 오류를 LLM에 돌려주고, LLM이 다시 시도한다.
- 지시를 해도 체류·순서가 어색하거나 error 위반을 남긴 채 끝날 때가 있다. 제안 카드에 위반이 표시되므로 사용자가 거절하거나 말로 고칠 수 있다. 품질이 중요한 데모는 ChatGPT로 비교해 볼 것.

## CLI

```bash
export GURYONG_API_URL=http://127.0.0.1:8002 GURYONG_USER=seoyun
python scripts/planner/cli.py chat 1                  # 계획 1의 최근 세션 이어서 (없으면 생성)
python scripts/planner/cli.py chat 1 --new --thinking  # 새 세션, 추론 과정도 표시
python scripts/planner/cli.py chat 1 -m "점심 넣어줘" -m /accept   # 한 번에 보내고 종료
```

대화 중 명령은 `/plan`, `/proposal`, `/accept`, `/reject`, `/exit`이다.
Git Bash에서 `-m /accept`를 쓸 때는 `MSYS_NO_PATHCONV=1`을 붙여야 한다. 붙이지 않으면 경로로 바뀐다.

## 테스트

```bash
docker compose run --rm api pytest tests/agent   # 대본대로 움직이는 가짜 LLM. Ollama 없이 돈다
```
