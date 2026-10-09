"""사용자 맞춤 가이드(메모리·근거 조각·가이드 노트·웹 검색·가이드 준비 작업)가 Claude 공급자에서 동작하는지.

실제 ClaudeProvider + 실제 SDK 에 대본대로 답하는 가짜 Anthropic API(스트림 이벤트)를 붙인다. 나머지는 test_docent_tools 와 같다.
확인하는 것: 도구 왕복(tool_use -> tool_result), 기록을 덧붙이기만 하는지(요청마다 앞 요청의 messages 가 그대로 앞부분),
도구 목록(웹 검색 서버 도구 포함)이 턴·장소 내내 같은지, 웹 검색 이벤트가 단계·출처로 이어지는지.

    docker compose run --rm agent pytest tests/agent/test_claude_docent.py
"""
import json

import anthropic
import httpx2
import pytest

from app.agent.claude_llm import ClaudeProvider
from app.agent.config import settings
from app.agent.llm import get_llm
from app.main import app
from tests.agent.test_claude_llm import CITE, POHANG, message
from tests.agent.test_docent_tools import (  # noqa: F401  (픽스처)
    NOTE,
    memory,
    new_plan,
    new_session,
    note,
    prepare,
    steps_of,
    trip_state,
    up,
    user,
    wait_job,
)
from tests.agent.test_harness import _reset, client, last, ok, sse  # noqa: F401  (픽스처)

MODEL = "claude-haiku-5-5"


class ScriptedClaude:
    """가짜 Anthropic API. 요청마다 대본 한 줄을 스트림 이벤트로 돌려준다.
    대본 항목: str(텍스트), ("도구", 인자)(tool_use), ("search", 검색어)(서버 웹 검색 + 결과), ("cite", 텍스트)(인용 붙은 텍스트)."""

    def __init__(self):
        self.script: list[list] = []
        self.bodies: list[dict] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.bodies.append(json.loads(request.content))
        n = len(self.bodies)
        blocks, stop = [{"type": "thinking", "thinking": "", "signature": f"sig{n}"}], "end_turn"
        for k, part in enumerate(self.script.pop(0)):
            if isinstance(part, str):
                blocks.append({"type": "text", "parts": [part]})
            elif part[0] == "search":
                sid = f"srvtoolu_{n}_{k}"
                blocks += [{"type": "server_tool_use", "id": sid, "name": "web_search", "input": {"query": part[1]}},
                           {"type": "web_search_tool_result", "tool_use_id": sid, "content": [
                               {"type": "web_search_result", "url": CITE["url"], "title": CITE["title"],
                                "encrypted_content": "ec", "page_age": None}]}]
            elif part[0] == "cite":
                blocks.append({"type": "text", "parts": [part[1]], "citations": [CITE]})
            else:
                blocks.append({"type": "tool_use", "id": f"toolu_{n}_{k}", "name": part[0], "input": part[1]})
                stop = "tool_use"
        return httpx2.Response(200, text=message(blocks, stop=stop, model=MODEL, usage={
            "input_tokens": 50, "output_tokens": 1, "cache_read_input_tokens": 900, "cache_creation_input_tokens": 100}),
            headers={"content-type": "text/event-stream"})


def claude_provider(fake: ScriptedClaude, cls=ClaudeProvider) -> ClaudeProvider:
    client = anthropic.AsyncAnthropic(api_key="test", max_retries=0, http_client=anthropic.DefaultAsyncHttpxClient(
        transport=httpx2.MockTransport(fake)))
    return cls(MODEL, client=client)


@pytest.fixture()
def claude(up):
    fake = ScriptedClaude()
    app.dependency_overrides[get_llm] = lambda: claude_provider(fake)
    return fake  # up 픽스처가 끝날 때 get_llm 을 되돌린다


def chat(client, h, sid, message_, script, fake):  # noqa: F811
    fake.script = script
    return sse(client.post(f"/agent/sessions/{sid}/chat", json={"message": message_}, headers=h))


def tool_names(body) -> list[str]:
    return [t["name"] for t in body["tools"]]


def tool_results(body) -> list[dict]:
    return [json.loads(b["content"]) for m in body["messages"] if m["role"] == "user" and isinstance(m["content"], list)
            for b in m["content"] if b["type"] == "tool_result"]


def assert_append_only(bodies: list[dict]):
    """같은 턴(장소)의 요청: system·tools 는 그대로, 앞 요청의 messages 가 그대로 다음 요청의 앞부분."""
    for a, b in zip(bodies, bodies[1:]):
        assert b["system"] == a["system"] and b["tools"] == a["tools"]
        assert b["messages"][:len(a["messages"])] == a["messages"]


def test_memory_and_passages_tools_on_claude(client, claude, user):  # noqa: F811
    sid, pid = new_session(client, user)
    events = chat(client, user, sid, "해설은 짧게 해줘. 가옥거리 얘기도 해줘", [
        [("remember", {"category": "style", "text": "해설은 짧게"}),
         ("search_passages", {"query": "돌계단", "place_id": "p-house"})],
        [("view_memory", {})],
        ["기억해 둘게요. 가옥거리는 백 년 전 어민들이 살던 거리예요."]], claude)
    assert last(events, "done")["text"].startswith("기억해 둘게요")
    assert [(s["kind"], s["status"]) for s in steps_of(events)] == [("memory", "done"), ("research", "done"),
                                                                      ("recall", "done")]
    assert [m["text"] for m in memory(client, user)["items"]] == ["해설은 짧게"]
    bodies = claude.bodies
    assert_append_only(bodies)
    assert {"remember", "forget", "view_memory", "search_passages"} <= set(tool_names(bodies[0]))
    # 메모리 요약은 고정 규칙(system)이 아니라 이번 사용자 메시지 뒤 맥락 메시지에 있다
    assert "## 사용자 메모리 (기억)" in bodies[0]["system"][0]["text"]
    assert "메모리 요약" not in bodies[0]["system"][0]["text"]
    assert "## 사용자에 대해 기억하는 것 (메모리 요약)\n(아직 없음)" in bodies[0]["messages"][-1]["content"]
    found = tool_results(bodies[1])
    assert found[0]["memory"]["text"] == "해설은 짧게" and found[1]["items"][0]["source_id"] == "P1"
    # 같은 턴의 응답은 thinking 서명까지 그대로 다시 간다
    assert bodies[2]["messages"][2]["content"][0] == {"type": "thinking", "thinking": "", "signature": "sig1"}
    # forget 도 같은 도구 왕복
    mid = memory(client, user)["items"][0]["id"]
    chat(client, user, sid, "그건 잊어줘", [[("forget", {"memory_id": mid})], ["지웠어요."]], claude)
    assert memory(client, user)["items"] == [] and tool_results(claude.bodies[-1])[-1]["result"] == "메모리에서 지웠다."


def test_guide_mode_note_and_builtin_web_search_on_claude(client, up, claude, user, monkeypatch):  # noqa: F811
    """동행 모드 대화: 가이드 노트 + Claude 웹 검색 서버 도구 (턴당 상한, 도구 목록은 턴 내내 같다)."""
    monkeypatch.setattr(settings, "web_search_per_turn", 1)
    sid, pid = new_session(client, user)
    up.trip = trip_state(client, user, pid)
    ok(client.put(f"/docent/plans/{pid}/notes/p-house", json={
        "status": "ready", **{k: NOTE[k] for k in ("short", "long", "for_you")},
        "sources": [{"title": "포항시", "url": "https://www.pohang.go.kr/a"}]}, headers=user), 201)
    events = chat(client, user, sid, "저 계단은 언제 만들어졌어요?", [
        [("get_guide_note", {"place_id": "p-house"})],
        [("search", "구룡포 돌계단 역사"), ("search_passages", {"query": "돌계단", "place_id": "p-house"})],
        [("cite", "1920년대에 만들어졌어요.")]], claude)
    assert last(events, "done")["mode"] == "guide" and last(events, "done")["text"] == "1920년대에 만들어졌어요."
    bodies = claude.bodies
    assert_append_only(bodies)
    names = tool_names(bodies[0])
    assert "get_guide_note" in names and "read_page" not in names and names.count("web_search") == 1  # 함수 도구 대신 서버 도구
    assert bodies[0]["tools"][-1] == {"type": "web_search_20250305", "name": "web_search", "max_uses": 1,
                                      "user_location": POHANG}  # Haiku 5.5 는 기본 버전, max_uses = 턴 상한
    assert "## 현장 해설" in bodies[0]["system"][0]["text"] and "한 턴에 1번까지" in bodies[0]["system"][0]["text"]
    assert tool_results(bodies[1])[0]["short"] == NOTE["short"]
    # 검색 한 번으로 상한을 다 씀 -> 다음 호출에 안내를 덧붙인다 (도구는 그대로)
    note_msg = bodies[2]["messages"][-1]
    assert note_msg["role"] == "system" and note_msg["clear_at"] == "next_user_message"
    assert "웹 검색 한도(1번)를 다 썼다" in note_msg["content"]
    server = [b["type"] for b in bodies[2]["messages"][-3]["content"]]
    assert server[:3] == ["thinking", "server_tool_use", "web_search_tool_result"]  # 서버 도구 블록도 그대로
    assert [d["detail"] for e, d in events if e == "step" and d["status"] == "done"][1] == "웹 검색 · 구룡포 돌계단 역사"
    assert last(events, "done")["usage"]["cached"] == 2700


def test_function_web_tools_on_claude_without_builtin(client, up, claude, user, monkeypatch):  # noqa: F811
    """내장 검색을 끈 Claude(builtin_web_search=False)면 docent web_search·read_page 함수 도구를 같은 형식으로 왕복한다."""
    class NoBuiltin(ClaudeProvider):
        builtin_web_search = False

    monkeypatch.setattr(settings, "web_search_per_turn", 2)
    app.dependency_overrides[get_llm] = lambda: claude_provider(claude, NoBuiltin)
    sid, pid = new_session(client, user)
    up.trip = trip_state(client, user, pid)
    chat(client, user, sid, "계단?", [
        [("web_search", {"query": "구룡포 돌계단"}), ("read_page", {"url": "https://www.pohang.go.kr/guryongpo"})],
        ["공원으로 오르는 길이에요."]], claude)
    names = tool_names(claude.bodies[0])
    assert {"web_search", "read_page"} <= set(names) and all("type" not in t for t in claude.bodies[0]["tools"])
    search, page = tool_results(claude.bodies[1])
    assert search["results"][0]["source_id"] == "W1" and page["title"] == "읽은 페이지" and page["searches_left"] == 0
    assert_append_only(claude.bodies)


def test_guide_prepare_on_claude(client, claude, user, monkeypatch):  # noqa: F811
    """가이드 준비 작업: 서버 웹 검색 인용이 출처가 되고, 장소 하나의 요청들은 덧붙이기만 한다."""
    monkeypatch.setattr(settings, "web_search_per_place", 2)
    pid = new_plan(client, user, ("p-house",))
    claude.script = [
        [("search", "구룡포 일본인가옥거리 역사"), ("search_passages", {"query": "돌계단", "place_id": "p-house"})],
        ["노트를 썼어요!"],  # JSON 이 아님 -> 한 번 더 (답 뒤에 user 안내를 덧붙인다)
        [("cite", json.dumps(NOTE | {"sources": ["P2"]}, ensure_ascii=False))],
    ]
    job = wait_job(client, user, pid, prepare(client, user, pid)["job_id"])
    place = job["places"][0]
    assert place["status"] == "ready" and place["web_searches"] == 1, place
    assert job["usage"]["calls"] == 3 and job["usage"]["cache_write"] == 300
    n = note(client, user, pid, "p-house")
    assert n["model"] == MODEL and n["short"] == NOTE["short"]
    assert [(s["title"], s["tier"]) for s in n["sources"]] == [
        ("한국관광공사 오디오 가이드 · 가옥거리 - 돌계단 이야기", "official"), ("포항시", "official")]
    bodies = claude.bodies
    assert_append_only(bodies)
    assert tool_names(bodies[0]) == ["search_passages", "web_search"]
    assert bodies[0]["tools"][-1]["max_uses"] == 2  # 장소당 상한
    retry = bodies[2]["messages"]
    assert retry[-2]["role"] == "assistant" and retry[-2]["content"][-1]["text"] == "노트를 썼어요!"
    assert retry[-1] == {"role": "user", "content": [{"type": "text", "text": "[시스템] 노트를 위 형식의 JSON 객체 하나로만 "
                                                                          "답하라. 설명이나 코드블록 없이 {로 시작해 }로 끝나야 한다."}]}


def test_guide_prepare_final_step_keeps_tools_on_claude(client, claude, user, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "web_search_per_place", 1)
    monkeypatch.setattr(settings, "guide_max_steps", 2)
    pid = new_plan(client, user, ("p-house",))
    claude.script = [[("search_passages", {"query": "더"})],
                     [json.dumps(NOTE | {"sources": ["P1"]}, ensure_ascii=False)]]
    job = wait_job(client, user, pid, prepare(client, user, pid)["job_id"])
    assert job["places"][0]["status"] == "ready"
    first, final = claude.bodies
    assert_append_only(claude.bodies)
    assert "tool_choice" not in first and final["tool_choice"] == {"type": "none"}  # 목록은 그대로, 쓰지 못하게만
    assert final["messages"][-1]["role"] == "system" and "더는 도구를 쓸 수 없다" in final["messages"][-1]["content"]
