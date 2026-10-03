"""LLM 공급자 추상화.

로컬(Ollama 등)과 ChatGPT 모두 OpenAI 호환 Chat Completions(스트리밍 + tool calling)로 호출한다.
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
        except httpx.HTTPError as e:
            raise LLMError(f"{self.name} 연결 실패: {e!r}") from e
        for c in calls:
            yield c
        yield Finish(finish)


@lru_cache
def get_llm() -> LLMProvider:
    """FastAPI 의존성. 테스트에서는 가짜 Provider 로 바꾼다."""
    s = settings
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
