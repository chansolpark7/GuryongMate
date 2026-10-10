"""각본 LLM (AGENT_PROVIDER=scripted, docs/agent/README.md §7.2). 도구는 진짜로 실행되고(planner 는 같은 앱) LLM 만 각본이다.

    docker compose run --rm agent pytest tests/agent/test_scripted.py
"""
import asyncio
import json
import re

import pytest

from app.agent.guide import PREP_SYSTEM, note_body, parse_json
from app.agent.harness import REVIEW_NOTE
from app.agent.llm import LLM_UNAVAILABLE, LLMError, TextDelta, ToolCall, get_llm
from app.agent.scripted_llm import DEFAULT_DIR, Context, ScriptedProvider, ScriptError, load_scripts
from app.agent.tools import GUIDE_NOTE_SPEC, MEMORY_SPECS, PASSAGES_SPEC, TOOL_SPECS, WEB_SPECS, Draft, Toolbox
from app.main import app
from tests.agent import test_harness as th
from tests.agent.test_guide import behind_event, delay_event, trip_plan, trip_state, up  # noqa: F401  (픽스처)
from tests.agent.test_harness import H, _reset, client, ok, review_on, session, sse  # noqa: F401  (픽스처)

ALL_TOOLS = {t["function"]["name"] for t in [*TOOL_SPECS, *MEMORY_SPECS, PASSAGES_SPEC, GUIDE_NOTE_SPEC, *WEB_SPECS]}
SPECS = [*TOOL_SPECS, *MEMORY_SPECS, PASSAGES_SPEC]

SCRIPT = {"fallback": "각본에 없는 말이에요.", "turns": [
    {"id": "error", "when": {"user": "^\\[테스트\\] LLM 오류"}, "error": "llm_unavailable"},
    {"id": "lunch", "when": {"user": "점심"}, "steps": [
        {"thinking": "점심을 넣는다.", "tools": [{"name": "add_item", "args": {"place_id": "p-food", "stay_minutes": 50}}]},
        {"tools": [{"name": "add_item", "args": {"place_id": "p-cafe", "stay_minutes": 30}}]},
        {"text": "물회집({{start:p-food}})과 카페를 넣어 {{end:1}}에 끝나요. 카페 항목 {{item:p-cafe}}"}]},
    {"id": "gate", "when": {"user": "늦게까지"}, "steps": [
        {"tools": [{"name": "add_rule", "args": {"type": "end_before", "time": "14:00"}}]},
        {"tools": [{"name": "add_item", "args": {"place_id": "p-food", "stay_minutes": 120}}]},
        {"text": "검토 전 답"}],
     "on_review": [
        {"tools": [{"name": "remove_item", "args": {"item_id": "$item:p-food"}}]},
        {"text": "고친 답 {{end:1}}"}]},
    {"id": "recommend", "when": {"user": "추천"}, "steps": [
        {"tools": [{"name": "recommend_places", "args": {"title": "카페", "candidates": [{"place_id": "p-cafe"}]}}]},
        {"text": "골라 보세요."}]},
    {"id": "delay", "when": {"event": "delay"}, "steps": [
        {"tools": [{"name": "recommend_places", "args": {"title": "x", "candidates": [{"place_id": "p-cafe"}]}},
                   {"name": "update_item", "args": {"item_id": "$item:p-cafe", "stay_minutes": 20}}]},
        {"text": "카페를 20분으로 줄였어요."}]},
    {"id": "keep", "when": {"user": "유지", "pending": True, "mode": "guide"}, "steps": [{"text": "대기 제안 위에서"}]},
    {"id": "note", "when": {"guide_place": "p-house"}, "steps": [
        {"json": {"short": "짧은 해설", "long": "긴 해설", "qa": [{"q": "언제?", "a": "옛날"}], "sources": ["P1", "P9"]}}]},
]}


@pytest.fixture()
def scripted(tmp_path, client):  # noqa: F811
    (tmp_path / "test.json").write_text(json.dumps(SCRIPT, ensure_ascii=False), encoding="utf-8")
    provider = ScriptedProvider(tmp_path, delay_ms=0, latency_ms=0)
    app.dependency_overrides[get_llm] = lambda: provider
    yield provider
    app.dependency_overrides[get_llm] = lambda: th.llm


def chat(client, sid, message):  # noqa: F811
    return sse(client.post(f"/agent/sessions/{sid}/chat", json={"message": message}, headers=H))


def names(events):
    return [e for e, _ in events]


def run(provider, messages, tools=SPECS):
    async def go():
        return [ev async for ev in provider.stream(messages, tools)]
    return asyncio.run(go())


# ---------- 대화 (실제 하네스·planner) ----------

def test_chat_streams_steps_text_and_proposal(client, session, scripted):  # noqa: F811
    sid, pid = session
    events = chat(client, sid, "점심 넣어줘")
    seq = names(events)
    assert seq[0] == "start" and seq[-3:] == ["proposal", "steps_done", "done"]
    assert "thinking" in seq and seq.index("thinking") < seq.index("step")
    steps = [d for e, d in events if e == "step"]
    assert {s["id"] for s in steps} == {"s1", "s2"}  # 일정 조정(두 번 합침) + 변경안 만들기
    assert steps[-1]["kind"] == "proposal" and steps[-1]["status"] == "done"
    texts = [d["delta"] for e, d in events if e == "text"]
    assert len(texts) > 3  # 조각으로 스트리밍
    plan = th.plan_of(client, pid)  # 제안은 아직 승인 전: 일정에는 가옥거리만
    assert th.places_in(plan) == ["p-house"]
    proposal = th.last(events, "proposal")
    after = {i["place_id"]: i for i in proposal["after"]["days"][0]["items"]}
    done = th.last(events, "done")
    # 도구 결과에서 고른 값: 물회집 시작 시각, 종료 시각, 새 항목의 음수 임시 id
    assert done["text"] == (f"물회집({after['p-food']['start_at']})과 카페를 넣어 {proposal['after']['days'][0]['end_at']}에 "
                            f"끝나요. 카페 항목 {after['p-cafe']['id']}")
    assert after["p-cafe"]["id"] < 0
    assert done["usage"]["calls"] == 3 and done["usage"]["input"] == 0


def test_review_gate_uses_on_review(client, session, scripted, monkeypatch):  # noqa: F811
    from app.agent.config import settings
    monkeypatch.setattr(settings, "review_rounds", 2)
    sid, _ = session
    events = chat(client, sid, "늦게까지 있고 싶어")
    text = "".join(d["delta"] for e, d in events if e == "text")
    assert "검토 전 답" not in text  # 관문이 되돌린 답은 보내지 않는다
    done = th.last(events, "done")
    assert done["text"].startswith("고친 답 ")
    reviews = [d for e, d in events if e == "step" and d["kind"] == "review" and d["status"] == "done"]
    assert reviews[0]["detail"] == "문제 1건" and reviews[-1]["detail"] == "문제 없음"
    proposal = th.last(events, "proposal")  # 규칙만 남은 제안 (물회집은 넣었다가 뺐다)
    assert [c["kind"] for c in proposal["changes"] if c["kind"] != "day_end"] == ["rule_added"]


def test_recommendation_fallback_and_error(client, session, scripted):  # noqa: F811
    sid, _ = session
    rec = chat(client, sid, "카페 추천해줘")
    assert "recommendation" in names(rec) and th.last(rec, "recommendation")["candidates"][0]["place_id"] == "p-cafe"

    fb = chat(client, sid, "아무 말")
    assert th.last(fb, "done")["text"] == "각본에 없는 말이에요." and "step" not in names(fb)

    bad = chat(client, sid, "[테스트] LLM 오류 내줘")
    assert names(bad)[-1] == "error"
    assert th.last(bad, "error") == {"code": "llm_unavailable", "message": LLM_UNAVAILABLE}
    detail = ok(client.get(f"/agent/sessions/{sid}", headers=H))
    assert detail["messages"][-1]["content"] == LLM_UNAVAILABLE  # 오류 문구는 assistant 메시지로 남는다


def test_event_turn_skips_tools_not_offered(client, trip_plan, up, scripted):  # noqa: F811
    sid, pid, items = trip_plan
    up.trip = trip_state(pid, items)
    res = ok(client.post(f"/agent/sessions/{sid}/events", json={"event": delay_event(pid, items),
                                                                "trip": trip_state(pid, items)}, headers=H))
    assert res["error"] is None and res["mode"] == "guide" and res["text"] == "카페를 20분으로 줄였어요."
    # 선제 턴에는 recommend_places 를 주지 않는다 -> 각본에 있어도 부르지 않는다
    [card] = res["cards"]
    assert card["type"] == "proposal"
    assert card["proposal"]["ops"] == [{"op": "update_item", "item_id": items["p-cafe"], "stay_minutes": 20}]
    # 이어서 대화: 동행 모드 + 대기 제안이 있을 때만 맞는 각본
    events = chat(client, sid, "물회집은 유지해줘")
    assert th.last(events, "done")["text"].startswith("대기 제안 위에서")


@pytest.mark.parametrize("kind", ["extend_stay", "shift_next"])
def test_demo_behind_schedule_turn(client, trip_plan, up, review_on, kind):  # noqa: F811
    """데모 각본의 일정 밀림 선제 턴: trip 이 계산한 추천 변경(payload.suggest)을 그대로 제안한다 (실제 스택처럼 검토 단계 포함)."""
    sid, pid, items = trip_plan
    provider = ScriptedProvider(DEFAULT_DIR, delay_ms=0, latency_ms=0)
    app.dependency_overrides[get_llm] = lambda: provider
    try:
        suggest = ({"kind": "extend_stay", "item_id": items["p-food"], "title": "물회집", "stay_minutes": 90,
                    "from_stay_minutes": 60} if kind == "extend_stay" else
                   {"kind": "shift_next", "item_id": items["p-cafe"], "title": "카페", "start_time": "15:35",
                    "from_start": "15:00"})
        up.trip = trip_state(pid, items)
        res = ok(client.post(f"/agent/sessions/{sid}/events", json={"event": behind_event(pid, items, suggest),
                                                                    "trip": trip_state(pid, items)}, headers=H))
    finally:
        app.dependency_overrides[get_llm] = lambda: th.llm
    assert res["error"] is None and res["text"].startswith("카페 도착이 15:00 예정에서 15:35으로 35분쯤 늦어질 것 같아요.")
    assert "다른 방법으로" in res["text"]
    [card] = res["cards"]
    change = {"stay_minutes": 90} if kind == "extend_stay" else {"start_time": "15:35"}
    assert card["proposal"]["ops"] == [{"op": "update_item", "item_id": suggest["item_id"], **change}]


def test_event_reason_detection():
    msg = "[이벤트] 일정 밀림: 늦음\n근거: " + json.dumps({"reason": "behind_schedule", "suggest": {"item_id": 7}})
    ctx = Context([{"role": "user", "content": msg}])
    assert (ctx.event, ctx.reason, ctx.fill("$event:suggest.item_id"), ctx.fill("$event:suggest.stay_minutes")) == (
        "delay", "behind_schedule", 7, None)
    # reason 을 지정한 각본은 그 이유에만, 지정하지 않은 각본은 그 event 의 모든 이유에 맞는다
    p = ScriptedProvider(DEFAULT_DIR, delay_ms=0, latency_ms=0)
    assert p.pick(Context([{"role": "user", "content": "[이벤트] 일정 지연: 위반"}]))["id"] == "scene3_delay"
    assert Context([{"role": "user", "content": "[이벤트] 일정 지연: 위반"}]).reason is None


# ---------- 공급자 단위 ----------

def test_context_turn_step_and_review():
    msgs = [{"role": "system", "content": "규칙\n## 현재 일정\n" + json.dumps({"days": [{"day": 1, "end": "17:00", "items": [
                {"item_id": 7, "order": 1, "place_id": "p-a", "time": "12:30~13:30"}]}]})},
            {"role": "user", "content": "이전 턴"}, {"role": "assistant", "content": "답"},
            {"role": "user", "content": "이번 턴"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function",
                                                                 "function": {"name": "add_item", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": json.dumps({"plan_after": {"days": [{"day": 1, "end": "18:10", "items": [
                {"item_id": 7, "order": 1, "place_id": "p-a", "time": "12:30~13:30"},
                {"item_id": -1, "order": 2, "place_id": "p-b", "time": "13:40~14:40"}]}]}})},
            {"role": "user", "content": REVIEW_NOTE + "- [must] 종료 초과"}]
    ctx = Context(msgs)
    assert (ctx.user, ctx.step, ctx.review, ctx.mode, ctx.event) == ("이번 턴", 1, True, "plan", None)
    assert ctx.fill({"a": "$item:p-b", "b": "$order:p-b", "c": "$item:p-x"}) == {"a": -1, "b": 2, "c": None}
    assert ctx.fill("{{end:1}} {{start:p-b}} {{last:add_item.plan_after.days[0].items[-1].place_id}}") == "18:10 13:40 p-b"
    # 도구 결과가 없으면 시스템 프롬프트의 현재 일정
    assert Context(msgs[:4]).fill("$item:p-a") == 7 and Context(msgs[:4]).step == 0
    mem = Context([{"role": "user", "content": "x"},
                   {"role": "assistant", "content": "", "tool_calls": [{"id": "m", "function": {"name": "view_memory"}}]},
                   {"role": "tool", "tool_call_id": "m", "content": json.dumps({"items": [{"id": 3, "category": "style"}]})}])
    assert mem.fill("$memory:style") == 3 and mem.fill("$memory:food") is None
    with pytest.raises(ScriptError):
        mem.ref("nope:1")


def test_event_and_guide_detection(tmp_path):
    (tmp_path / "s.json").write_text(json.dumps(SCRIPT, ensure_ascii=False), encoding="utf-8")
    p = ScriptedProvider(tmp_path, delay_ms=0, latency_ms=0)
    ev = Context([{"role": "system", "content": "## 현재 상황 (여행 중)\n"}, {"role": "user", "content": "[이벤트] 일정 지연: 늦음"}])
    assert (ev.event, ev.mode) == ("delay", "guide") and p.pick(ev)["id"] == "delay"
    assert Context([{"role": "user", "content": "[이벤트] 안전 경고: 비"}]).event == "safety_raised"
    # 가이드 준비: 노트 JSON 을 내고, 본 자료(P1)만 출처가 된다 (P9 는 버린다)
    guide = [{"role": "system", "content": PREP_SYSTEM}, {"role": "user", "content": "## 장소 (place_id p-house)\n{}"}]
    out = run(p, guide, [PASSAGES_SPEC])
    raw = parse_json("".join(e.text for e in out if isinstance(e, TextDelta)))
    toolbox = Toolbox(None, 1, Draft(), "s")
    toolbox.add_source("P", "한국관광공사 소개 · 가옥거리", "https://api.visitkorea.or.kr/")
    body, error = note_body(raw, toolbox)
    assert error == "" and body["short"] == "짧은 해설" and [s["title"] for s in body["sources"]] == ["한국관광공사 소개 · 가옥거리"]
    # 일반 대화의 '유지'는 동행 모드·대기 제안이 아니면 맞지 않는다 -> fallback
    out = run(p, [{"role": "system", "content": ""}, {"role": "user", "content": "유지"}])
    assert "".join(e.text for e in out if isinstance(e, TextDelta)) == "각본에 없는 말이에요."


def test_error_turn_raises_llm_error(tmp_path):
    (tmp_path / "s.json").write_text(json.dumps(SCRIPT, ensure_ascii=False), encoding="utf-8")
    p = ScriptedProvider(tmp_path, delay_ms=0, latency_ms=0)
    with pytest.raises(LLMError) as e:
        run(p, [{"role": "user", "content": "[테스트] LLM 오류"}])
    assert e.value.code == "llm_unavailable" and not e.value.retryable


def test_script_reload_and_validation(tmp_path):
    (tmp_path / "s.json").write_text(json.dumps({"turns": [{"id": "a", "when": {"user": "안녕"},
                                                            "steps": [{"text": "하나"}]}]}), encoding="utf-8")
    p = ScriptedProvider(tmp_path, delay_ms=0, latency_ms=0)
    msgs = [{"role": "user", "content": "안녕"}]
    assert [e.text for e in run(p, msgs) if isinstance(e, TextDelta)] == ["하나"]
    import os
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"turns": [{"id": "a", "when": {"user": "안녕"}, "steps": [{"text": "둘"}]}]}), encoding="utf-8")
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 5))
    assert [e.text for e in run(p, msgs) if isinstance(e, TextDelta)] == ["둘"]  # 고친 각본을 다시 읽는다
    (tmp_path / "bad.json").write_text(json.dumps({"turns": [{"id": "x", "when": {"user": "("}, "steps": [{"text": "a"}]}]}),
                                       encoding="utf-8")
    with pytest.raises(ScriptError):
        load_scripts(tmp_path)


# ---------- 데모 각본 (backend/app/agent/scripts) ----------

REF_KINDS = {"item", "order", "start", "end", "last", "memory", "event"}


def _walk(value):
    if isinstance(value, dict):
        for v in value.values():
            yield from _walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v)
    elif isinstance(value, str):
        yield value


def test_demo_scripts_are_valid():
    data = load_scripts(DEFAULT_DIR)
    ids = [t["id"] for t in data["turns"]]
    assert len(ids) == len(set(ids))
    for t in data["turns"]:
        for step in [*t.get("steps", []), *t.get("on_review", [])]:
            for call in step.get("tools") or []:
                assert call["name"] in ALL_TOOLS, f"{t['id']}: 모르는 도구 {call['name']}"
            for s in _walk(step):
                refs = ([s[1:]] if s.startswith("$") else []) + re.findall(r"\{\{([^{}]+)\}\}", s)
                for r in refs:
                    assert r.split(":")[0] in REF_KINDS, f"{t['id']}: 모르는 참조 {r}"
        if t.get("steps"):
            assert not t["steps"][-1].get("tools"), f"{t['id']}: 마지막 단계는 답변이어야 한다"


@pytest.mark.parametrize("message,system,turn", [
    ("엄마랑 구룡포 일정 짜줘", "", "scene1_plan"),
    ("갈 만한 카페 추천해줘", "", "scene1_recommend_cafe"),
    ("언제 지어진 집들이에요?", "## 현재 상황 (여행 중)", "scene2_house_built"),
    ("다음부터는 더 짧게 해줘요", "## 현재 상황 (여행 중)", "scene2_shorter"),
    ("과메기문화관은 유지해줘", "## 현재 상황 (여행 중)\n## 승인 대기 중인 제안 #3", "scene3_keep_museum"),
    ("[이벤트] 일정 지연: 늦어요", "", "scene3_delay"),
    ("[이벤트] 일정 밀림: 30분 늦어요", "", "behind_schedule"),
    ("[이벤트] 안전 경고: 위험", "", "scene4_safety"),
    ("[테스트] LLM 오류", "", "error_llm_unavailable"),
    ("이전 제안을 현재 일정에 맞춰 다시 만들어줘.", "## 다시 제안 요청\n원래 요청: 구룡포항 점심은 1시간으로 늘려줘", "f2_repropose_lunch"),
])
def test_demo_scripts_match_scenes(message, system, turn):
    p = ScriptedProvider(DEFAULT_DIR, delay_ms=0, latency_ms=0)
    ctx = Context([{"role": "system", "content": system}, {"role": "user", "content": message}])
    assert p.pick(ctx)["id"] == turn


@pytest.mark.parametrize("place_id", ["kto-1909659", "kto-2610501", "kto-2660046", "poi-gr-breakwater-north",
                                      "kto-2834344", "kto-2603493"])
def test_demo_guide_notes(place_id):
    """데모 계획의 장소마다 고정 노트가 있고, 형식이 노트 JSON 이다."""
    p = ScriptedProvider(DEFAULT_DIR, delay_ms=0, latency_ms=0)
    msgs = [{"role": "system", "content": PREP_SYSTEM}, {"role": "user", "content": f"## 장소 (place_id {place_id})\n{{}}"}]
    while True:  # 도구 단계를 지나 노트 JSON 까지
        out = run(p, msgs, [PASSAGES_SPEC])
        calls = [e for e in out if isinstance(e, ToolCall)]
        if not calls:
            break
        msgs += [{"role": "assistant", "content": "", "tool_calls": [
                     {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}} for c in calls]},
                 *[{"role": "tool", "tool_call_id": c.id, "content": "{}"} for c in calls]]
    raw = parse_json("".join(e.text for e in out if isinstance(e, TextDelta)))
    assert raw["short"] and raw["long"] and raw["sources"] and all(isinstance(q, dict) for q in raw["qa"])
