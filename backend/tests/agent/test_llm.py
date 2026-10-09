"""LLM Provider 형식 변환·스트림 파싱 (네트워크 없이 MockTransport)."""
import json

import anyio
import httpx

from app.agent.llm import Finish, OpenAIResponsesProvider, ReasoningDelta, TextDelta, ToolCall

MESSAGES = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "물회 넣어줘"},
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": "add_item", "arguments": '{"place_id": "p"}'}}]},
    {"role": "tool", "tool_call_id": "call_1", "content": '{"ok": true}'},
]
TOOLS = [{"type": "function", "function": {"name": "add_item", "description": "d", "parameters": {"type": "object"}}}]


def sse(*events):
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)


def test_responses_provider_converts_and_parses():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"], seen["body"] = str(request.url), json.loads(request.content)
        return httpx.Response(200, text=sse(
            {"type": "response.reasoning_summary_text.delta", "delta": "생각"},
            {"type": "response.output_text.delta", "delta": "추가"},
            {"type": "response.output_text.delta", "delta": "할게요"},
            {"type": "response.output_item.done",
             "item": {"type": "function_call", "call_id": "call_2", "name": "add_item", "arguments": '{"a": 1}'}},
            {"type": "response.completed", "response": {"usage": {
                "input_tokens": 1200, "input_tokens_details": {"cached_tokens": 1000},
                "output_tokens": 300, "output_tokens_details": {"reasoning_tokens": 120}}}},
        ), headers={"content-type": "text/event-stream"})

    p = OpenAIResponsesProvider("openai:m", "https://x/v1", "k", "m", "low", transport=httpx.MockTransport(handler))

    async def collect():
        return [ev async for ev in p.stream(MESSAGES, TOOLS)]

    events = anyio.run(collect)
    assert seen["url"] == "https://x/v1/responses"
    body = seen["body"]
    assert body["reasoning"] == {"effort": "low", "summary": "auto"} and body["store"] is False
    assert body["tools"] == [{"type": "function", "name": "add_item", "description": "d",
                              "parameters": {"type": "object"}, "strict": False}]
    assert body["input"] == [
        {"role": "system", "content": "sys"}, {"role": "user", "content": "물회 넣어줘"},
        {"type": "function_call", "call_id": "call_1", "name": "add_item", "arguments": '{"place_id": "p"}'},
        {"type": "function_call_output", "call_id": "call_1", "output": '{"ok": true}'}]
    assert events == [ReasoningDelta("생각"), TextDelta("추가"), TextDelta("할게요"),
                      ToolCall("call_2", "add_item", '{"a": 1}'),
                      Finish("stop", {"input": 1200, "cached": 1000, "output": 300, "reasoning": 120})]


def test_responses_provider_errors():
    from app.agent.llm import LLMError

    def handler(request):
        return httpx.Response(400, text='{"error": "bad"}')

    p = OpenAIResponsesProvider("openai:m", "https://x/v1", "k", "m", transport=httpx.MockTransport(handler))

    async def run():
        return [ev async for ev in p.stream(MESSAGES, [])]

    try:
        anyio.run(run)
        raise AssertionError("LLMError 가 나야 한다")
    except LLMError as e:
        assert "HTTP 400" in str(e)


def test_responses_provider_builtin_web_search():
    """web_search=n: 내장 web_search 도구와 max_tool_calls 를 붙이고, 검색 호출·url_citation 인용을 이벤트로 낸다."""
    from app.agent.llm import Citation, WebSearchCall

    seen = {}

    def handler(request: httpx.Request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, text=sse(
            {"type": "response.output_item.done",
             "item": {"type": "web_search_call", "status": "completed",
                      "action": {"type": "search", "query": "구룡포 일본인가옥거리 역사"}}},
            {"type": "response.output_text.delta", "delta": "1920년대"},
            {"type": "response.output_item.done", "item": {"type": "message", "content": [
                {"type": "output_text", "text": "1920년대 ...", "annotations": [
                    {"type": "url_citation", "url": "https://www.pohang.go.kr/x", "title": "포항시", "start_index": 0,
                     "end_index": 5},
                    {"type": "file_citation", "file_id": "f"}]}]}},
            {"type": "response.completed", "response": {"usage": {"input_tokens": 10, "output_tokens": 2}}},
        ), headers={"content-type": "text/event-stream"})

    p = OpenAIResponsesProvider("openai:m", "https://x/v1", "k", "m", transport=httpx.MockTransport(handler))

    async def collect(**kw):
        return [ev async for ev in p.stream(MESSAGES, TOOLS, **kw)]

    events = anyio.run(lambda: collect(web_search=2))
    body = seen["body"]
    assert body["max_tool_calls"] == 2
    assert body["tools"][0]["type"] == "function" and body["tools"][1]["type"] == "web_search"
    assert body["tools"][1]["search_context_size"] == "low" and body["tools"][1]["user_location"]["country"] == "KR"
    assert events[:3] == [WebSearchCall("구룡포 일본인가옥거리 역사"), TextDelta("1920년대"),
                          Citation("https://www.pohang.go.kr/x", "포항시")]
    anyio.run(collect)  # 기본(web_search=0)은 내장 검색을 붙이지 않는다
    assert "max_tool_calls" not in seen["body"] and [t["type"] for t in seen["body"]["tools"]] == ["function"]
