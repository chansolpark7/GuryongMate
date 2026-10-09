"""Claude API 공급자 (공식 anthropic SDK, 스트리밍). 명세: docs/agent/README.md §7.1

하네스 내부 형식(OpenAI 식 role/tool_calls/tool)을 Claude 메시지(tool_use/tool_result 블록)로 바꾸고,
스트림 이벤트를 하네스 이벤트(TextDelta·ReasoningDelta·ToolCall·Finish)로 바꾼다.

기록 편집 금지 (preserved thinking): thinking 블록의 서명은 그 앞의 system·tools·메시지 전체에 묶여 있어서,
앞부분이 바뀐 기록에 다시 보내면 무효가 된다. 그래서 이 공급자는 append_only 이고 하네스는
- 시스템 프롬프트(고정 규칙)와 도구 목록을 턴 내내 그대로 둔다 (마지막 단계도 도구를 빼지 않고 tool_choice none)
- 바뀌는 맥락과 호출마다의 안내는 메시지 끝에 role system 메시지로 덧붙이기만 한다
같은 턴 안의 응답은 원본 블록(native)을 그대로 다시 보낸다. 지난 턴은 DB 에 텍스트·도구 호출만 남으므로
thinking 없이 변환한다 (지난 턴의 thinking 은 다시 보내지 않는다).

프롬프트 캐시 (앞에서부터): 도구 정의 -> 고정 규칙(캐시 지점) -> 지난 대화 -> 이번 사용자 메시지(캐시 지점)
-> 맥락 system 메시지(현재 상황·지금 시각·현재 일정) -> 이번 턴의 도구 루프 (자동 캐시 지점이 끝을 따라간다).
"""
import json
import logging
from collections.abc import AsyncIterator

import anthropic

from app.agent.llm import Finish, LLMError, LLMEvent, LLMRefusal, ReasoningDelta, TextDelta, ToolCall

log = logging.getLogger("app.agent")

# 모델마다 다른 것 (claude-api 스킬 문서 기준)
# - 웹 검색 서버 도구: 동적 필터링 버전(web_search_20260209)은 Opus·Sonnet 계열만, Haiku 5.5 는 기본 버전
# - thinking: 둘 다 adaptive 만 받는다 (budget_tokens 는 400). display 기본값이 omitted(빈 문자열)라 summarized 로 요청
# - effort: 둘 다 low~max. 기본값이 Sonnet 5.5 는 high, Haiku 5.5 는 medium 이라 항상 명시해서 보낸다
# - 서버측 refusal fallback: Sonnet 5.5 만 있고 Haiku 5.5 는 없다. 쓰지 않는다 (docs/agent/README.md §7.1)
WEB_SEARCH_TOOL = {"claude-sonnet-5-5": "web_search_20260209", "claude-haiku-5-5": "web_search_20250305"}
DEFAULT_WEB_SEARCH_TOOL = "web_search_20250305"

BETAS = [
    "mid-conversation-system-clear-at-2026-08-21",  # 호출마다의 안내: clear_at=next_user_message 인 system 메시지
    "thinking-binding-controls-2026-08-01",  # thinking.block_binding 으로 기록이 바뀌었을 때의 동작을 명시
]
PAUSE_LIMIT = 4  # 서버 도구(웹 검색) 루프가 pause_turn 으로 멈췄을 때 이어 받는 최대 횟수
CACHE = {"type": "ephemeral"}


def _args(arguments: str) -> dict:
    try:
        value = json.loads(arguments or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _add(out: list[dict], role: str, blocks: list[dict]) -> None:
    """같은 역할이 이어지면 한 메시지로 합친다 (지난 기록의 숨김 알림 + 사용자 메시지 등)."""
    if not blocks:
        return
    if out and out[-1]["role"] == role:
        out[-1]["content"].extend(blocks)
    else:
        out.append({"role": role, "content": list(blocks)})


class ClaudeProvider:
    append_only = True  # 하네스가 기록을 고치지 않고 덧붙이기만 한다 (harness.py)

    def __init__(self, model: str, *, api_key: str | None = None, effort: str = "medium", max_tokens: int = 64000,
                 web_search_max_uses: int = 0, thinking_mismatch: str = "drop_block", timeout: float = 300,
                 client: anthropic.AsyncAnthropic | None = None):
        self.name = f"claude:{model}"
        self.model = model
        self._api_key = api_key
        self._effort = effort
        self._max_tokens = max_tokens
        self._web_search_max_uses = web_search_max_uses
        self._thinking_mismatch = thinking_mismatch
        self._timeout = timeout
        self._client = client

    @property
    def client(self) -> anthropic.AsyncAnthropic:
        # 키가 없으면 SDK 가 ANTHROPIC_API_KEY·ant auth 프로필 순으로 찾는다. 첫 호출 때 만든다 (/agent/info 는 키 없이도 동작)
        if self._client is None:
            self._client = anthropic.AsyncAnthropic(api_key=self._api_key, timeout=self._timeout)
        return self._client

    # ---------- 형식 변환 ----------

    def convert(self, messages: list[dict]) -> tuple[list[dict], list[dict]]:
        """하네스 메시지 -> (system 블록, Claude messages)."""
        system: list[dict] = []
        out: list[dict] = []
        marked = False
        for i, m in enumerate(messages):
            role = m["role"]
            if role == "system" and i == 0:
                system = [{"type": "text", "text": m["content"], "cache_control": CACHE}]
            elif role == "system":
                msg = {"role": "system", "content": m["content"]}
                if m.get("clear_at"):
                    msg["clear_at"] = m["clear_at"]
                elif not marked and out and out[-1]["role"] == "user":
                    # 맥락 바로 앞(이번 사용자 메시지)까지는 다음 턴에도 같다 -> 지난 대화까지의 캐시 지점
                    out[-1]["content"][-1] = {**out[-1]["content"][-1], "cache_control": CACHE}
                    marked = True
                out.append(msg)
            elif role == "assistant":
                native = m.get("native")
                if native and native.get("model") == self.model:
                    blocks = [b for content in native["content"] for b in content]  # 받은 그대로 (thinking 포함)
                else:
                    blocks = [{"type": "text", "text": m["content"]}] if (m.get("content") or "").strip() else []
                    blocks += [{"type": "tool_use", "id": c["id"], "name": c["function"]["name"],
                                "input": _args(c["function"]["arguments"])} for c in m.get("tool_calls") or []]
                _add(out, "assistant", blocks)
            elif role == "tool":
                _add(out, "user", [{"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}])
            elif (m.get("content") or "").strip():
                _add(out, "user", [{"type": "text", "text": m["content"]}])
        return system, out

    def tools(self, tools: list[dict]) -> list[dict]:
        out = [{"name": t["function"]["name"], "description": t["function"]["description"],
                "input_schema": t["function"]["parameters"]} for t in tools]
        if self._web_search_max_uses > 0:
            out.append({"type": WEB_SEARCH_TOOL.get(self.model, DEFAULT_WEB_SEARCH_TOOL), "name": "web_search",
                        "max_uses": self._web_search_max_uses})
        return out

    def request(self, messages: list[dict], tools: list[dict], allow_tools: bool = True) -> dict:
        system, msgs = self.convert(messages)
        params: dict = {
            "model": self.model, "max_tokens": self._max_tokens, "system": system, "messages": msgs,
            "thinking": {"type": "adaptive", "display": "summarized",
                         "block_binding": {"prefix_mismatch_behavior": self._thinking_mismatch}},
            "output_config": {"effort": self._effort},
            "cache_control": CACHE,  # 자동 캐시 지점: 마지막 블록을 따라간다 (도구 루프의 앞부분을 다음 호출이 읽는다)
            "betas": BETAS,
        }
        if tools:
            params["tools"] = self.tools(tools)
            if not allow_tools:  # 도구 목록은 그대로 두고 쓰지 못하게만 한다 (목록을 바꾸면 기록 편집)
                params["tool_choice"] = {"type": "none"}
        return params

    # ---------- 스트림 ----------

    def _log_transformations(self, msg) -> None:
        # 같은 턴 안에서는 기록을 덧붙이기만 하므로 비어 있어야 한다. 항목이 있으면 기록을 고친 버그다
        for t in getattr(msg, "input_transformations", None) or []:
            log.warning("%s thinking 블록 %s %s (%s)", self.name, getattr(t, "type", "?"), getattr(t, "path", "?"),
                        getattr(t, "reason", "?"))

    async def stream(self, messages: list[dict], tools: list[dict], allow_tools: bool = True) -> AsyncIterator[LLMEvent]:
        params = self.request(messages, tools, allow_tools)
        usage = {"input": 0, "cached": 0, "cache_write": 0, "output": 0}
        contents: list[list[dict]] = []
        try:
            for _ in range(PAUSE_LIMIT + 1):
                async with self.client.beta.messages.stream(**params) as stream:
                    async for event in stream:
                        if event.type != "content_block_delta":
                            continue
                        if event.delta.type == "text_delta" and event.delta.text:
                            yield TextDelta(event.delta.text)
                        elif event.delta.type == "thinking_delta" and event.delta.thinking:
                            yield ReasoningDelta(event.delta.thinking)
                    msg = await stream.get_final_message()
                u = msg.usage
                read, write = u.cache_read_input_tokens or 0, u.cache_creation_input_tokens or 0
                usage["input"] += u.input_tokens + read + write  # 하네스의 input 은 캐시 포함 전체
                usage["cached"] += read
                usage["cache_write"] += write
                usage["output"] += u.output_tokens
                self._log_transformations(msg)
                contents.append([b.to_dict() for b in msg.content])
                if msg.stop_reason != "pause_turn":
                    break
                # 서버 도구 루프가 멈춤 -> 받은 내용을 붙여 다시 보내면 이어서 한다
                params = {**params, "messages": [*params["messages"], {"role": "assistant", "content": contents[-1]}]}
        except anthropic.BadRequestError as e:
            raise LLMError(f"{self.name} 요청 오류 400: {e.message}", retryable=False) from e
        except anthropic.RateLimitError as e:
            raise LLMError(f"{self.name} 사용량 한도 429: {e.message}", retryable=False) from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"{self.name} HTTP {e.status_code}: {e.message}", retryable=False) from e
        except anthropic.APIConnectionError as e:
            raise LLMError(f"{self.name} 연결 실패: {e!r}", retryable=False) from e
        except anthropic.AnthropicError as e:  # 키 없음 등
            raise LLMError(f"{self.name}: {e}", retryable=False) from e

        if msg.stop_reason == "refusal":
            # 안전 분류기 거절. 이미 받은 조각은 완결된 답이 아니다 (하네스가 오류로 끝낸다)
            category = getattr(msg.stop_details, "category", None) if msg.stop_details else None
            raise LLMRefusal(f"{self.name} 거절 (category={category})", category)
        tool_uses = [b for b in msg.content if b.type == "tool_use"]
        if msg.stop_reason == "max_tokens" and tool_uses:
            raise LLMError(f"{self.name} max_tokens 에서 도구 호출이 잘렸다")
        for b in tool_uses:
            yield ToolCall(id=b.id, name=b.name, arguments=json.dumps(b.input, ensure_ascii=False))
        yield Finish(msg.stop_reason or "end_turn", usage, {"model": self.model, "content": contents})
