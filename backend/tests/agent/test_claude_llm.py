"""Claude 공급자: 형식 변환·스트림 파싱·오류 (네트워크 없이 가짜 Anthropic 스트림 이벤트를 실제 SDK 로 읽는다)."""
import json

import anthropic
import anyio
import httpx2
import pytest

from app.agent.claude_llm import ClaudeProvider
from app.agent.llm import Citation, Finish, LLMError, LLMRefusal, ReasoningDelta, TextDelta, ToolCall, WebSearchCall

TOOLS = [{"type": "function", "function": {"name": "add_item", "description": "d",
                                           "parameters": {"type": "object", "properties": {}}}}]
# 하네스가 append_only 공급자에게 주는 형식: 고정 규칙, 지난 기록, 이번 사용자 메시지, 맥락 system 메시지
MESSAGES = [
    {"role": "system", "content": "규칙"},
    {"role": "user", "content": "[알림] 제안 #3 승인됨"},  # 숨김 알림 + 사용자 메시지 -> 한 user 메시지로
    {"role": "user", "content": "물회 넣어줘"},
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": "add_item", "arguments": '{"place_id": "p"}'}}]},
    {"role": "tool", "tool_call_id": "call_1", "content": '{"ok": true}'},
    {"role": "assistant", "content": "넣었어요"},
    {"role": "user", "content": "카페도"},
    {"role": "system", "content": "## 현재 일정\n{}"},
]


def ev(kind, **data):
    return {"type": kind, **data}


def sse(*events) -> str:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events)


def message(blocks, stop="end_turn", usage=None, stop_details=None, model="claude-sonnet-5-5") -> str:
    """content 블록 목록 -> Anthropic 스트림 이벤트 (message_start ... message_stop)."""
    u = usage or {"input_tokens": 100, "output_tokens": 1, "cache_read_input_tokens": 0,
                  "cache_creation_input_tokens": 0}
    out = [ev("message_start", message={"id": "msg_1", "type": "message", "role": "assistant", "model": model,
                                        "content": [], "stop_reason": None, "stop_sequence": None, "usage": u})]
    for i, b in enumerate(blocks):
        if b["type"] == "thinking":
            out += [ev("content_block_start", index=i, content_block={"type": "thinking", "thinking": "", "signature": ""}),
                    ev("content_block_delta", index=i, delta={"type": "thinking_delta", "thinking": b["thinking"]}),
                    ev("content_block_delta", index=i, delta={"type": "signature_delta", "signature": b["signature"]})]
        elif b["type"] == "text":
            out.append(ev("content_block_start", index=i, content_block={"type": "text", "text": ""}))
            out += [ev("content_block_delta", index=i, delta={"type": "citations_delta", "citation": c})
                    for c in b.get("citations", [])]
            out += [ev("content_block_delta", index=i, delta={"type": "text_delta", "text": t}) for t in b["parts"]]
        elif b["type"] == "server_tool_use":
            out += [ev("content_block_start", index=i, content_block={**b, "input": {}}),
                    ev("content_block_delta", index=i,
                       delta={"type": "input_json_delta", "partial_json": json.dumps(b["input"], ensure_ascii=False)})]
        elif b["type"] == "web_search_tool_result":
            out.append(ev("content_block_start", index=i, content_block=b))
        elif b["type"] == "tool_use":
            out += [ev("content_block_start", index=i,
                       content_block={"type": "tool_use", "id": b["id"], "name": b["name"], "input": {}}),
                    ev("content_block_delta", index=i,
                       delta={"type": "input_json_delta", "partial_json": json.dumps(b["input"])})]
        out.append(ev("content_block_stop", index=i))
    out += [ev("message_delta", delta={"stop_reason": stop, "stop_sequence": None, "stop_details": stop_details},
               usage={"output_tokens": 40}),
            ev("message_stop")]
    return sse(*out)


class Fake:
    """요청을 기록하고 준비한 응답을 차례로 돌려주는 가짜 Anthropic API."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[dict] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append({"body": json.loads(request.content), "headers": dict(request.headers),
                              "url": str(request.url)})
        status, body = self.responses.pop(0)
        if status != 200:
            return httpx2.Response(status, json=body)
        return httpx2.Response(200, text=body, headers={"content-type": "text/event-stream"})


def provider(fake: Fake, model="claude-sonnet-5-5", **kw) -> ClaudeProvider:
    client = anthropic.AsyncAnthropic(api_key="test", max_retries=0,
                                      http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(fake)))
    return ClaudeProvider(model, client=client, **kw)


def run(p, messages=MESSAGES, tools=TOOLS, **kw):
    async def collect():
        return [e async for e in p.stream(messages, tools, **kw)]
    return anyio.run(collect)


TURN = [{"type": "thinking", "thinking": "물회집 찾기", "signature": "sig-A"},
        {"type": "text", "parts": ["찾아", "볼게요"]},
        {"type": "tool_use", "id": "toolu_1", "name": "add_item", "input": {"place_id": "p-food"}}]


def test_stream_converts_request_and_events():
    fake = Fake((200, message(TURN, stop="tool_use", usage={
        "input_tokens": 30, "output_tokens": 1, "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 200})))
    events = run(provider(fake))
    req = fake.requests[0]
    body = req["body"]
    assert req["url"].endswith("/v1/messages?beta=true")
    assert set(req["headers"]["anthropic-beta"].split(",")) == {
        "mid-conversation-system-clear-at-2026-08-21", "thinking-binding-controls-2026-08-01"}
    assert body["model"] == "claude-sonnet-5-5" and body["stream"] is True and body["max_tokens"] == 64000
    assert body["thinking"] == {"type": "adaptive", "display": "summarized",
                                "block_binding": {"prefix_mismatch_behavior": "drop_block"}}
    assert body["output_config"] == {"effort": "medium"}  # 기본값에 기대지 않고 항상 보낸다
    assert "tool_choice" not in body and "fallbacks" not in body and "temperature" not in body
    # 캐시: 고정 규칙 끝, 이번 사용자 메시지(맥락 앞), 자동(끝)
    assert body["cache_control"] == {"type": "ephemeral"}
    assert body["system"] == [{"type": "text", "text": "규칙", "cache_control": {"type": "ephemeral"}}]
    assert body["tools"] == [{"name": "add_item", "description": "d",
                              "input_schema": {"type": "object", "properties": {}}}]
    assert body["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "[알림] 제안 #3 승인됨"},
                                     {"type": "text", "text": "물회 넣어줘"}]},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "call_1", "name": "add_item",
                                           "input": {"place_id": "p"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": '{"ok": true}'}]},
        {"role": "assistant", "content": [{"type": "text", "text": "넣었어요"}]},
        {"role": "user", "content": [{"type": "text", "text": "카페도", "cache_control": {"type": "ephemeral"}}]},
        {"role": "system", "content": "## 현재 일정\n{}"},
    ]
    assert events[:3] == [ReasoningDelta("물회집 찾기"), TextDelta("찾아"), TextDelta("볼게요")]
    assert events[3] == ToolCall("toolu_1", "add_item", '{"place_id": "p-food"}')
    fin = events[4]
    assert isinstance(fin, Finish) and fin.reason == "tool_use"
    # input 은 캐시 포함 전체, cached 는 그중 캐시에서 읽은 것
    assert fin.usage == {"input": 1230, "cached": 1000, "cache_write": 200, "output": 40}
    assert fin.native["model"] == "claude-sonnet-5-5"
    assert fin.native["content"][0][0] == {"type": "thinking", "thinking": "물회집 찾기", "signature": "sig-A"}


def test_thinking_blocks_round_trip_within_turn():
    """같은 턴의 다음 호출에는 받은 블록(thinking 서명 포함)을 그대로, 안내는 clear_at system 메시지로 덧붙인다."""
    fake = Fake((200, message(TURN, stop="tool_use")), (200, message([{"type": "text", "parts": ["끝"]}])))
    p = provider(fake)
    first = run(p)
    fin, call = first[-1], first[-2]
    turn = [*MESSAGES,
            {"role": "assistant", "content": "찾아볼게요", "native": fin.native,
             "tool_calls": [{"id": call.id, "type": "function",
                             "function": {"name": call.name, "arguments": call.arguments}}]},
            {"role": "tool", "tool_call_id": call.id, "content": "결과"},
            {"role": "system", "content": "[시스템] 확정 변경", "clear_at": "next_user_message"}]
    run(p, turn)
    a, b = (r["body"] for r in fake.requests)
    assert b["messages"][:len(a["messages"])] == a["messages"]  # 덧붙이기만 했다
    assert b["system"] == a["system"] and b["tools"] == a["tools"]
    assert b["messages"][len(a["messages"]):] == [
        {"role": "assistant", "content": fin.native["content"][0]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "결과"}]},
        {"role": "system", "content": "[시스템] 확정 변경", "clear_at": "next_user_message"}]
    sent = b["messages"][len(a["messages"])]["content"]
    assert sent[0] == {"type": "thinking", "thinking": "물회집 찾기", "signature": "sig-A"}
    assert sent[1]["type"] == "text" and sent[1]["text"] == "찾아볼게요"
    assert sent[2] == {"type": "tool_use", "id": "toolu_1", "name": "add_item", "input": {"place_id": "p-food"}}


def test_native_blocks_of_other_model_are_not_replayed():
    fake = Fake((200, message([{"type": "text", "parts": ["네"]}])))
    native = {"model": "claude-haiku-5-5", "content": [[{"type": "thinking", "thinking": "", "signature": "x"},
                                                       {"type": "text", "text": "넣었어요"}]]}
    msgs = [*MESSAGES[:5], {"role": "assistant", "content": "넣었어요", "native": native}, *MESSAGES[6:]]
    run(provider(fake), msgs)
    assert fake.requests[0]["body"]["messages"][3] == {"role": "assistant",
                                                       "content": [{"type": "text", "text": "넣었어요"}]}


def test_final_step_keeps_tools_with_tool_choice_none():
    fake = Fake((200, message([{"type": "text", "parts": ["정리"]}])))
    run(provider(fake), allow_tools=False)
    body = fake.requests[0]["body"]
    assert body["tool_choice"] == {"type": "none"} and [t["name"] for t in body["tools"]] == ["add_item"]


POHANG = {"type": "approximate", "country": "KR", "region": "Gyeongsangbuk-do", "city": "Pohang",
          "timezone": "Asia/Seoul"}


@pytest.mark.parametrize("model, tool", [("claude-sonnet-5-5", "web_search_20260209"),
                                         ("claude-haiku-5-5", "web_search_20250305")])
def test_model_specific_web_search_and_effort(model, tool):
    fake = Fake((200, message([{"type": "text", "parts": ["네"]}], model=model)))
    p = provider(fake, model, effort="low", thinking_mismatch="error")
    assert p.builtin_web_search and p.append_only
    run(p, web_search=2)
    body = fake.requests[0]["body"]
    assert body["tools"][-1] == {"type": tool, "name": "web_search", "max_uses": 2, "user_location": POHANG}
    assert body["output_config"] == {"effort": "low"} and "fallbacks" not in body
    assert body["thinking"]["block_binding"] == {"prefix_mismatch_behavior": "error"}


def test_web_search_only_when_asked():
    fake = Fake((200, message([{"type": "text", "parts": ["네"]}])), (200, message([{"type": "text", "parts": ["네"]}])))
    run(provider(fake))
    assert [t["name"] for t in fake.requests[0]["body"]["tools"]] == ["add_item"]
    run(provider(fake), tools=[], web_search=1)  # 함수 도구 없이 웹 검색만이어도 도구 목록을 보낸다
    assert [t["name"] for t in fake.requests[1]["body"]["tools"]] == ["web_search"]


CITE = {"type": "web_search_result_location", "url": "https://www.pohang.go.kr/x", "title": "포항시",
        "cited_text": "1920년대", "encrypted_index": "ei"}
SEARCH_TURN = [
    {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {"query": "구룡포 돌계단"}},
    {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1", "content": [
        {"type": "web_search_result", "url": "https://www.pohang.go.kr/x", "title": "포항시", "encrypted_content": "ec",
         "page_age": None}]},
    {"type": "server_tool_use", "id": "srvtoolu_2", "name": "web_search", "input": {"query": "한 번 더"}},
    {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_2",
     "content": {"type": "web_search_tool_result_error", "error_code": "max_uses_exceeded"}},
    {"type": "text", "parts": ["1920년대에 만들어졌어요."], "citations": [CITE]},
]


def test_web_search_events_and_server_blocks_round_trip(caplog):
    """검색 결과마다 WebSearchCall(검색어), 인용마다 Citation. 오류 블록(HTTP 200)은 세지 않고 경고.
    서버 도구 블록은 원본 그대로 같은 턴의 다음 호출에 다시 보낸다."""
    fake = Fake((200, message(SEARCH_TURN, model="claude-haiku-5-5")),
                (200, message([{"type": "text", "parts": ["네"]}], model="claude-haiku-5-5")))
    p = provider(fake, "claude-haiku-5-5")
    events = run(p, web_search=2)
    assert [e for e in events if isinstance(e, WebSearchCall | Citation)] == [
        WebSearchCall("구룡포 돌계단"), Citation("https://www.pohang.go.kr/x", "포항시")]
    assert "max_uses_exceeded" in caplog.text
    fin = events[-1]
    assert [b["type"] for b in fin.native["content"][0]] == [
        "server_tool_use", "web_search_tool_result", "server_tool_use", "web_search_tool_result", "text"]
    run(p, [*MESSAGES, {"role": "assistant", "content": "1920년대에 만들어졌어요.", "native": fin.native},
            {"role": "user", "content": "고마워"}], web_search=2)
    a, b = (r["body"] for r in fake.requests)
    assert b["tools"] == a["tools"] and b["messages"][:len(a["messages"])] == a["messages"]
    replay = b["messages"][len(a["messages"])]["content"]
    assert replay == fin.native["content"][0]
    assert replay[1]["content"][0]["encrypted_content"] == "ec" and replay[4]["citations"][0]["encrypted_index"] == "ei"


def test_pause_turn_with_server_search_resumes():
    fake = Fake((200, message(SEARCH_TURN[:1], stop="pause_turn")),
                (200, message(SEARCH_TURN[1:2] + [{"type": "text", "parts": ["찾았어요"]}])))
    events = run(provider(fake), web_search=1)
    assert WebSearchCall("구룡포 돌계단") in events  # 검색어는 앞 응답의 server_tool_use 에서
    second = fake.requests[1]["body"]["messages"]
    assert second[-1] == {"role": "assistant", "content": [
        {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {"query": "구룡포 돌계단"}}]}
    assert events[-1].reason == "end_turn" and len(events[-1].native["content"]) == 2


def test_pause_turn_is_resumed():
    """서버 도구 루프가 pause_turn 으로 멈추면 받은 내용을 붙여 이어 받는다. 원본 블록은 두 응답 모두 남는다."""
    fake = Fake((200, message([{"type": "text", "parts": ["찾는 중"]}], stop="pause_turn")),
                (200, message([{"type": "text", "parts": ["찾았어요"]}])))
    events = run(provider(fake))
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["찾는 중", "찾았어요"]
    second = fake.requests[1]["body"]["messages"]
    assert second[:-1] == fake.requests[0]["body"]["messages"]
    assert second[-1]["role"] == "assistant" and second[-1]["content"][0]["text"] == "찾는 중"
    fin = events[-1]
    assert fin.reason == "end_turn" and len(fin.native["content"]) == 2
    assert fin.usage["input"] == 200 and fin.usage["output"] == 80


def test_refusal_raises():
    fake = Fake((200, message([{"type": "text", "parts": ["음"]}], stop="refusal",
                              stop_details={"type": "refusal", "category": "general_harms", "explanation": None})))
    with pytest.raises(LLMRefusal) as e:
        run(provider(fake))
    assert e.value.category == "general_harms" and not e.value.retryable and e.value.code == "llm_refused"


def test_truncated_tool_call_is_not_run():
    fake = Fake((200, message(TURN, stop="max_tokens")))
    with pytest.raises(LLMError, match="max_tokens"):
        run(provider(fake))


@pytest.mark.parametrize("status, text", [(400, "요청 오류 400"), (429, "사용량 한도 429"), (529, "HTTP 529")])
def test_api_errors(status, text):
    fake = Fake((status, {"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}}))
    with pytest.raises(LLMError, match=text) as e:
        run(provider(fake))
    assert not e.value.retryable  # SDK 가 이미 재시도한 오류는 하네스가 다시 시도하지 않는다
