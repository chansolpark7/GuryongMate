"""LLM 공급자 추상화.

- OllamaProvider: Ollama 네이티브 /api/chat (요청마다 컨텍스트 길이 지정). 로컬 기본값
- OpenAIResponsesProvider: OpenAI Responses API. ChatGPT 기본값 (GPT-5.x 의 추론 + 도구)
- OpenAICompatProvider: OpenAI 호환 Chat Completions. LM Studio, vLLM 등 (ChatGPT 도 추론 없이 가능)
하네스는 LLMProvider 인터페이스만 알고, 어떤 모델인지는 설정(config.py)에서만 정해진다.
다른 형식의 API(예: Responses API, Anthropic)를 붙이려면 같은 이벤트를 내는 Provider 를 하나 더 만들면 된다.
"""
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import httpx

from app.agent.config import settings


@dataclass
class TextDelta:
    text: str


@dataclass
class ReasoningDelta:
    text: str


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON 문자열 (모델이 깨진 JSON 을 줄 수도 있다)


@dataclass
class Finish:
    reason: str
    # 토큰 사용량 {"input", "cached", "output", "reasoning"} (공급자가 알려줄 때만)
    usage: dict | None = None


LLMEvent = TextDelta | ReasoningDelta | ToolCall | Finish


class LLMError(RuntimeError):
    pass


class LLMProvider(Protocol):
    name: str

    def stream(self, messages: list[dict], tools: list[dict]) -> AsyncIterator[LLMEvent]:
        """텍스트·추론 조각은 오는 대로, ToolCall 은 완성된 뒤, 마지막에 Finish 를 낸다."""
        ...


class OpenAICompatProvider:
    def __init__(self, name: str, base_url: str, api_key: str, model: str, reasoning_effort: str | None = None,
                 temperature: float | None = None, timeout: float = 300):
        self.name = name
        self.model = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._reasoning_effort = reasoning_effort
        self._temperature = temperature
        self._timeout = timeout

    async def stream(self, messages: list[dict], tools: list[dict]) -> AsyncIterator[LLMEvent]:
        body: dict = {"model": self.model, "messages": messages, "stream": True}
        if tools:
            body["tools"] = tools
        if self._reasoning_effort:
            body["reasoning_effort"] = self._reasoning_effort
        if self._temperature is not None:
            body["temperature"] = self._temperature

        calls: dict[int, dict] = {}
        finish = "stop"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                async with client.stream("POST", self._url, json=body, headers=self._headers) as resp:
                    if resp.status_code >= 400:
                        raise LLMError(f"{self.name} HTTP {resp.status_code}: {(await resp.aread()).decode(errors='replace')[:500]}")
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        chunk = json.loads(data)
                        if chunk.get("error"):
                            raise LLMError(f"{self.name}: {chunk['error']}")
                        for choice in chunk.get("choices", []):
                            delta = choice.get("delta") or {}
                            if delta.get("content"):
                                yield TextDelta(delta["content"])
                            reasoning = delta.get("reasoning") or delta.get("reasoning_content")
                            if reasoning:
                                yield ReasoningDelta(reasoning)
                            for tc in delta.get("tool_calls") or []:
                                slot = calls.setdefault(tc.get("index", len(calls)), {"id": "", "name": "", "arguments": ""})
                                fn = tc.get("function") or {}
                                slot["id"] = tc.get("id") or slot["id"]
                                slot["name"] += fn.get("name") or ""
                                slot["arguments"] += fn.get("arguments") or ""
                            if choice.get("finish_reason"):
                                finish = choice["finish_reason"]
        except httpx.HTTPError as e:
            raise LLMError(f"{self.name} 연결 실패: {e!r}") from e

        for idx in sorted(calls):
            c = calls[idx]
            yield ToolCall(id=c["id"] or f"call_{uuid.uuid4().hex[:8]}", name=c["name"], arguments=c["arguments"] or "{}")
        yield Finish(finish)


class OllamaProvider:
    """Ollama 네이티브 /api/chat. OpenAI 호환 엔드포인트와 달리 요청마다 컨텍스트 길이(num_ctx)를 지정할 수 있다.
    (Ollama 기본 컨텍스트는 4096 토큰이라 시스템 프롬프트·도구 결과가 잘려 도구를 무시하게 된다)"""

    def __init__(self, name: str, base_url: str, model: str, num_ctx: int, think: bool,
                 temperature: float | None = None, timeout: float = 300):
        self.name = name
        self.model = model
        self._url = base_url.rstrip("/") + "/api/chat"
        self._num_ctx = num_ctx
        self._think = think
        self._temperature = temperature
        self._timeout = timeout

    @staticmethod
    def _to_ollama(messages: list[dict]) -> list[dict]:
        """OpenAI 형식 -> Ollama 형식 (도구 인자는 dict, 도구 응답에는 tool_name)."""
        names: dict[str, str] = {}
        out = []
        for m in messages:
            m = dict(m)
            if m.get("tool_calls"):
                calls = []
                for c in m["tool_calls"]:
                    names[c["id"]] = c["function"]["name"]
                    try:
                        args = json.loads(c["function"]["arguments"] or "{}")
                    except ValueError:
                        args = {}
                    calls.append({"function": {"name": c["function"]["name"], "arguments": args}})
                m["tool_calls"] = calls
            if m["role"] == "tool":
                m = {"role": "tool", "content": m["content"], "tool_name": names.get(m.get("tool_call_id", ""), "")}
            out.append(m)
        return out

    async def stream(self, messages: list[dict], tools: list[dict]) -> AsyncIterator[LLMEvent]:
        usage = None
        options: dict = {"num_ctx": self._num_ctx}
        if self._temperature is not None:
            options["temperature"] = self._temperature
        body: dict = {"model": self.model, "messages": self._to_ollama(messages), "stream": True,
                      "think": self._think, "options": options}
        if tools:
            body["tools"] = tools

        calls: list[ToolCall] = []
        finish = "stop"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                async with client.stream("POST", self._url, json=body) as resp:
                    if resp.status_code >= 400:
                        raise LLMError(f"{self.name} HTTP {resp.status_code}: {(await resp.aread()).decode(errors='replace')[:500]}")
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        chunk = json.loads(line)
                        if chunk.get("error"):
                            raise LLMError(f"{self.name}: {chunk['error']}")
                        msg = chunk.get("message") or {}
                        if msg.get("thinking"):
                            yield ReasoningDelta(msg["thinking"])
                        if msg.get("content"):
                            yield TextDelta(msg["content"])
                        for c in msg.get("tool_calls") or []:
                            fn = c.get("function") or {}
                            calls.append(ToolCall(id=c.get("id") or f"call_{uuid.uuid4().hex[:8]}", name=fn.get("name", ""),
                                                  arguments=json.dumps(fn.get("arguments") or {}, ensure_ascii=False)))
                        if chunk.get("done"):
                            finish = chunk.get("done_reason") or finish
                            usage = {"input": chunk.get("prompt_eval_count", 0), "cached": 0,
                                     "output": chunk.get("eval_count", 0), "reasoning": 0}
        except httpx.HTTPError as e:
            raise LLMError(f"{self.name} 연결 실패: {e!r}") from e
        for c in calls:
            yield c
        yield Finish(finish, usage)


class OpenAIResponsesProvider:
    """OpenAI Responses API (/v1/responses). GPT-5.x 는 추론(reasoning)과 도구를 함께 쓰려면 이 API 가 필요하다.
    하네스는 Chat Completions 형식 메시지를 쓰므로 여기서 Responses 입력 형식으로 바꾼다."""

    def __init__(self, name: str, base_url: str, api_key: str, model: str, reasoning_effort: str | None = None,
                 timeout: float = 300, transport: httpx.AsyncBaseTransport | None = None):
        self.name = name
        self.model = model
        self._url = base_url.rstrip("/") + "/responses"
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._reasoning_effort = reasoning_effort
        self._timeout = timeout
        self._transport = transport

    @staticmethod
    def _to_input(messages: list[dict]) -> list[dict]:
        items: list[dict] = []
        for m in messages:
            if m["role"] == "tool":
                items.append({"type": "function_call_output", "call_id": m["tool_call_id"], "output": m["content"]})
                continue
            if m.get("content"):
                items.append({"role": m["role"], "content": m["content"]})
            for c in m.get("tool_calls") or []:
                items.append({"type": "function_call", "call_id": c["id"], "name": c["function"]["name"],
                              "arguments": c["function"]["arguments"] or "{}"})
        return items

    @staticmethod
    def _to_tools(tools: list[dict]) -> list[dict]:
        return [{"type": "function", "name": t["function"]["name"], "description": t["function"]["description"],
                 "parameters": t["function"]["parameters"], "strict": False} for t in tools]

    async def stream(self, messages: list[dict], tools: list[dict]) -> AsyncIterator[LLMEvent]:
        usage = None
        body: dict = {"model": self.model, "input": self._to_input(messages), "stream": True, "store": False}
        if tools:
            body["tools"] = self._to_tools(tools)
        if self._reasoning_effort:
            body["reasoning"] = {"effort": self._reasoning_effort, "summary": "auto"}

        calls: list[ToolCall] = []
        finish = "stop"
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                async with client.stream("POST", self._url, json=body, headers=self._headers) as resp:
                    if resp.status_code >= 400:
                        raise LLMError(f"{self.name} HTTP {resp.status_code}: {(await resp.aread()).decode(errors='replace')[:500]}")
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if not data or data == "[DONE]":
                            continue
                        ev = json.loads(data)
                        kind = ev.get("type", "")
                        if kind == "response.output_text.delta":
                            yield TextDelta(ev["delta"])
                        elif kind == "response.reasoning_summary_text.delta":
                            yield ReasoningDelta(ev["delta"])
                        elif kind == "response.output_item.done" and ev.get("item", {}).get("type") == "function_call":
                            item = ev["item"]
                            calls.append(ToolCall(id=item.get("call_id") or f"call_{uuid.uuid4().hex[:8]}",
                                                  name=item.get("name", ""), arguments=item.get("arguments") or "{}"))
                        elif kind in ("response.completed", "response.incomplete"):
                            if kind == "response.incomplete":
                                finish = "length"
                            u = (ev.get("response") or {}).get("usage") or {}
                            if u:
                                usage = {"input": u.get("input_tokens", 0),
                                         "cached": (u.get("input_tokens_details") or {}).get("cached_tokens", 0),
                                         "output": u.get("output_tokens", 0),
                                         "reasoning": (u.get("output_tokens_details") or {}).get("reasoning_tokens", 0)}
                        elif kind in ("error", "response.failed"):
                            err = ev.get("error") or ev.get("response", {}).get("error")
                            raise LLMError(f"{self.name}: {err}")
        except httpx.HTTPError as e:
            raise LLMError(f"{self.name} 연결 실패: {e!r}") from e
        for c in calls:
            yield c
        yield Finish(finish, usage)


@lru_cache
def get_llm() -> LLMProvider:
    """FastAPI 의존성. 테스트에서는 가짜 Provider 로 바꾼다."""
    s = settings
    if s.provider == "openai" and s.openai_api == "responses":
        return OpenAIResponsesProvider(f"openai:{s.openai_model}", s.openai_base_url, s.openai_api_key, s.openai_model,
                                       s.openai_reasoning_effort, s.request_timeout_sec)
    if s.provider == "openai":
        return OpenAICompatProvider(f"openai:{s.openai_model}", s.openai_base_url, s.openai_api_key, s.openai_model,
                                    s.openai_reasoning_effort, s.temperature, s.request_timeout_sec)
    if s.provider == "local" and s.local_api == "ollama":
        return OllamaProvider(f"local:{s.local_model}", s.local_base_url, s.local_model, s.local_num_ctx,
                              s.local_think, s.temperature, s.request_timeout_sec)
    if s.provider == "local":
        return OpenAICompatProvider(f"local:{s.local_model}", s.local_base_url, s.local_api_key, s.local_model,
                                    None, s.temperature, s.request_timeout_sec)
    raise ValueError(f"알 수 없는 AGENT_PROVIDER: {s.provider}")
