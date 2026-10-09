"""각본 LLM (AGENT_PROVIDER=scripted). 실제 LLM 을 부르지 않고 각본 파일대로 답한다. 명세: docs/agent/README.md §7.2

앱 화면 검증·fixture 녹화·테스트·비상 데모용이다. 매번 같은 입력에 같은 출력(도구 호출·텍스트)을 낸다.
도구는 하네스가 진짜로 실행하므로 planner 제안·추천 카드·docent 메모리·검토 관문·base_changed 같은 흐름은 실제와 같다.

공급자는 상태가 없다. 호출마다 메시지 목록에서 지금 상황을 읽는다:
- 턴의 시작 = 마지막 user 메시지 중 하네스 안내('[시스템] …')가 아닌 것. 그 뒤 assistant 메시지 수 = 각본의 몇 번째 단계인지
- 선제 턴: '[이벤트] 일정 지연: …' (prompts.event_message) -> event type
- 가이드 준비: 시스템 프롬프트가 guide.PREP_SYSTEM, 사용자 메시지의 '## 장소 (place_id …)' -> 장소
- 검토 관문이 되돌렸으면 마지막 메시지가 하네스의 REVIEW_NOTE 다 -> 각본의 on_review 단계

각본 파일 (backend/app/agent/scripts/*.json, AGENT_SCRIPTED_DIR):
{"fallback": "맞는 각본이 없을 때의 답",
 "turns": [{"id": "scene1_plan",
            "when": {"user": "정규식", "event": "delay", "guide_place": "정규식", "mode": "plan|guide",
                     "pending": true, "system": "정규식"},          // 모두 맞아야 한다. 위에서부터 처음 맞는 것
            "steps": [{"thinking": "…", "text": "…", "tools": [{"name": "search_places", "args": {…}}]},
                      …, {"text": "최종 답변"}],                  // 마지막 단계(도구 없음)가 답변. {"json": {…}} 은 JSON 텍스트로
            "on_review": [ … ],                                  // 검토 관문이 되돌리면 이어서 (없으면 같은 답을 다시 낸다)
            "error": "llm_unavailable"}]}                        // 이 턴은 LLM 오류 (llm_unavailable | llm_quota | llm_refused)

도구 결과에 따라 달라지는 값은 참조로 쓴다 (인자 값 전체가 문자열 "$…" 이거나, 텍스트 안의 {{…}}):
- item:<place_id>   지금 일정(가장 최근 도구 결과의 일정, 없으면 시스템 프롬프트의 현재 일정)에서 그 장소 항목의 item_id
- order:<place_id>  그 항목의 순서 / start:<place_id> 시작 시각 HH:MM / end:<n> n일차 종료 시각
- last:<도구>.<경로>  이번 턴에서 그 도구의 가장 최근 결과 JSON 의 값 (예: last:search_places.items[0].place_id)
- memory:<분류>      view_memory·remember 결과에서 그 분류의 첫 항목 id
찾지 못하면 인자는 빠지고(null), 텍스트에는 빈 문자열이 들어간다.
"""
import asyncio
import json
import logging
import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from app.agent.llm import Finish, LLMError, LLMRefusal, ReasoningDelta, TextDelta, ToolCall

log = logging.getLogger("app.agent")

DEFAULT_DIR = Path(__file__).parent / "scripts"
FALLBACK = ("지금은 데모용 각본 모드라 이 요청에는 답할 수 없어요. "
            "\"엄마랑 구룡포 일정 짜줘\"나 \"갈 만한 카페 추천해줘\"처럼 데모 문장으로 말해 주세요.")
REVIEW_PREFIX = "[시스템] 방금 쓴 답변은 아직"
NOTE_PREFIX = "[시스템]"
EVENT_RE = re.compile(r"^\[이벤트\] ([^:]+):")
GUIDE_PLACE_RE = re.compile(r"## 장소 \(place_id ([^)]+)\)")
PLAN_HEADER = "## 현재 일정"
REF_RE = re.compile(r"\{\{([^{}]+)\}\}")
PATH_RE = re.compile(r"([^.\[\]]+)|\[(-?\d+)\]")
JSON_CHUNK = 400


class ScriptError(ValueError):
    """각본 파일 형식 오류 (서버 시작·첫 호출 때 드러나게)."""


def load_scripts(directory: Path) -> dict:
    """디렉터리의 *.json 을 이름순으로 읽어 turns 를 이어 붙인다. fallback 은 마지막으로 정의한 것."""
    turns, fallback = [], FALLBACK
    for path in sorted(directory.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        fallback = data.get("fallback", fallback)
        for t in data.get("turns", []):
            when = t.get("when") or {}
            for key in ("user", "guide_place", "system"):
                if key in when:
                    try:
                        re.compile(when[key])
                    except re.error as e:
                        raise ScriptError(f"{path.name} {t.get('id')}: when.{key} 정규식 오류: {e}") from e
            if not t.get("error") and not t.get("steps"):
                raise ScriptError(f"{path.name} {t.get('id')}: steps 또는 error 가 필요합니다")
            turns.append({**t, "file": path.name})
    return {"turns": turns, "fallback": fallback}


def _json(text: str):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


class Context:
    """한 번의 호출에서 본 대화 상태."""

    def __init__(self, messages: list[dict]):
        from app.agent.prompts import EVENT_LABEL  # 순환 import 방지

        self.system = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
        self.review = bool(messages) and messages[-1]["role"] == "user" and messages[-1]["content"].startswith(REVIEW_PREFIX)
        start = 0
        for n, m in enumerate(messages):
            if m["role"] == "user" and not m["content"].startswith(NOTE_PREFIX):
                start = n
        self.user = messages[start]["content"] if messages else ""
        after = messages[start + 1:]
        self.step = sum(1 for m in after if m["role"] == "assistant")
        self.event = None
        if m := EVENT_RE.match(self.user):
            labels = {v: k for k, v in EVENT_LABEL.items()}
            self.event = labels.get(m.group(1).strip(), m.group(1).strip())
        g = GUIDE_PLACE_RE.search(self.user)
        self.guide_place = g.group(1) if g and "현장 해설 작가" in self.system else None
        self.mode = "guide" if "## 현재 상황 (여행 중)" in self.system else "plan"
        self.pending = "## 승인 대기 중인 제안" in self.system
        # 이번 턴의 도구 결과 [(도구 이름, JSON)] (오래된 순)
        names = {c["id"]: c["function"]["name"] for m in after for c in m.get("tool_calls") or []}
        self.results = [(names.get(m.get("tool_call_id"), ""), _json(m["content"])) for m in after if m["role"] == "tool"]

    # ----- 참조 -----
    def plan_view(self) -> dict | None:
        for _, r in reversed(self.results):
            if isinstance(r, dict):
                for key in ("plan_after", "plan"):
                    if isinstance(r.get(key), dict) and "days" in r[key]:
                        return r[key]
                if "days" in r:
                    return r
        if PLAN_HEADER in self.system:
            line = self.system.split(PLAN_HEADER, 1)[1].split("\n", 2)
            if len(line) > 1:
                return _json(line[1])
        return None

    def _item(self, place_id: str) -> dict | None:
        view = self.plan_view() or {}
        for d in view.get("days", []):
            for i in d["items"]:
                if i["place_id"] == place_id and i.get("time") != "건너뜀":
                    return {**i, "day": d["day"]}
        return None

    def ref(self, ref: str):
        kind, _, arg = ref.strip().partition(":")
        if kind == "item":
            return (self._item(arg) or {}).get("item_id")
        if kind == "order":
            return (self._item(arg) or {}).get("order")
        if kind == "start":
            item = self._item(arg)
            return item["time"].split("~")[0] if item else None
        if kind == "end":
            days = (self.plan_view() or {}).get("days", [])
            return next((d.get("end") for d in days if str(d["day"]) == arg), None)
        if kind == "last":
            tool, _, path = arg.partition(".")
            value = next((r for name, r in reversed(self.results) if name == tool), None)
            for key, idx in PATH_RE.findall(path):
                try:
                    value = value[key] if key else value[int(idx)]
                except (KeyError, IndexError, TypeError):
                    return None
            return value
        if kind == "memory":
            for name, r in reversed(self.results):
                if not isinstance(r, dict):
                    continue
                pool = r.get("items") or r.get("same_category") or []
                if name == "remember" and isinstance(r.get("memory"), dict):
                    pool = [r["memory"], *pool]
                for m in pool:
                    if isinstance(m, dict) and m.get("category") == arg:
                        return m.get("id")
            return None
        raise ScriptError(f"모르는 참조: {ref}")

    def fill(self, value):
        """인자 값의 "$…" 참조와 텍스트의 {{…}} 참조를 푼다."""
        if isinstance(value, str) and value.startswith("$"):
            return self.ref(value[1:])
        if isinstance(value, str):
            return REF_RE.sub(lambda m: "" if (v := self.ref(m.group(1))) is None else str(v), value)
        if isinstance(value, list):
            return [self.fill(v) for v in value]
        if isinstance(value, dict):
            return {k: self.fill(v) for k, v in value.items()}
        return value


def _matches(when: dict, ctx: Context) -> bool:
    if "event" in when and when["event"] != ctx.event:
        return False
    if "guide_place" in when and not (ctx.guide_place and re.fullmatch(when["guide_place"], ctx.guide_place)):
        return False
    if "user" in when and (ctx.event or ctx.guide_place or not re.search(when["user"], ctx.user)):
        return False
    if "mode" in when and when["mode"] != ctx.mode:
        return False
    if "pending" in when and bool(when["pending"]) != ctx.pending:
        return False
    if "system" in when and not re.search(when["system"], ctx.system):
        return False
    # 조건이 하나도 없으면 맞지 않는다 (실수로 모든 요청을 덮지 않게)
    return bool(when)


def _chunks(text: str, size: int) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]


class ScriptedProvider:
    """각본대로 도구 호출·텍스트를 스트리밍한다. delay_ms: 조각 사이 지연, latency_ms: 호출마다 첫 조각 전 지연."""

    builtin_web_search = False

    def __init__(self, directory: Path | str | None = None, delay_ms: float = 30, latency_ms: float = 300,
                 chunk_chars: int = 6):
        self.directory = Path(directory) if directory else DEFAULT_DIR
        self.name = f"scripted:{self.directory.name}"
        self.model = "scripted"
        self.delay = max(0.0, delay_ms) / 1000
        self.latency = max(0.0, latency_ms) / 1000
        self.chunk_chars = max(1, chunk_chars)
        self.data = load_scripts(self.directory)
        self._stamp = self._mtimes()

    def _mtimes(self) -> tuple:
        return tuple((p.name, p.stat().st_mtime) for p in sorted(self.directory.glob("*.json")))

    def pick(self, ctx: Context) -> dict | None:
        stamp = self._mtimes()
        if stamp != self._stamp:  # 각본 파일을 고치면 서버를 다시 띄우지 않아도 다음 호출부터 반영
            self.data, self._stamp = load_scripts(self.directory), stamp
            log.info("scripted: 각본 다시 읽음 (%d턴)", len(self.data["turns"]))
        return next((t for t in self.data["turns"] if _matches(t.get("when") or {}, ctx)), None)

    def step_for(self, turn: dict, ctx: Context) -> dict:
        steps = turn["steps"]
        tool_steps = len(steps) - 1  # 마지막 단계는 답변
        review = turn.get("on_review") or []
        if ctx.step < tool_steps:
            return steps[ctx.step]
        j = ctx.step - tool_steps  # 답변이 되돌려진 뒤 on_review 를 몇 단계 했는지
        if review and (ctx.review or j > 0):
            return review[min(j, len(review) - 1)]
        return steps[-1]

    async def _sleep(self, sec: float) -> None:
        if sec:
            await asyncio.sleep(sec)

    async def _stream_text(self, text: str, kind, size: int | None = None) -> AsyncIterator:
        for piece in _chunks(text, size or self.chunk_chars):
            await self._sleep(self.delay)
            yield kind(piece)

    async def stream(self, messages: list[dict], tools: list[dict], **_) -> AsyncIterator:
        ctx = Context(messages)
        turn = self.pick(ctx)
        await self._sleep(self.latency)
        if turn is None:
            log.info("scripted: 맞는 각본 없음 (user=%r event=%s guide=%s)", ctx.user[:60], ctx.event, ctx.guide_place)
            async for ev in self._stream_text(self.data["fallback"], TextDelta):
                yield ev
            yield Finish("stop", _usage())
            return
        if turn.get("error"):
            code = turn["error"]
            if code == "llm_refused":
                raise LLMRefusal(f"scripted {turn.get('id')}: 거절 각본")
            raise LLMError(f"scripted {turn.get('id')}: 오류 각본 ({code})", retryable=False, quota=code == "llm_quota")
        step = self.step_for(turn, ctx)
        log.info("scripted: %s/%s step=%d review=%s", turn["file"], turn.get("id"), ctx.step, ctx.review)
        if step.get("thinking"):
            async for ev in self._stream_text(ctx.fill(step["thinking"]), ReasoningDelta):
                yield ev
        if "json" in step:  # 노트 JSON 은 사용자에게 스트리밍되지 않으니 큰 조각으로
            text, size = json.dumps(ctx.fill(step["json"]), ensure_ascii=False), JSON_CHUNK
        else:
            text, size = ctx.fill(step.get("text", "")), self.chunk_chars
        async for ev in self._stream_text(text, TextDelta, size):
            if ev.text:
                yield ev
        offered = {t["function"]["name"] for t in tools}
        for call in step.get("tools") or []:
            if call["name"] not in offered:  # 실제 모델도 받지 않은 도구는 부르지 못한다
                log.warning("scripted: %s 는 이번 호출에 없는 도구라 건너뜀", call["name"])
                continue
            args = {k: v for k, v in ctx.fill(call.get("args") or {}).items() if v is not None}
            # id 는 턴마다 달라야 한다 (같은 세션을 나중에 실제 공급자로 이어 가도 기록의 도구 호출 id 가 겹치지 않게)
            yield ToolCall(id=f"scripted-{uuid.uuid4().hex[:12]}", name=call["name"],
                           arguments=json.dumps(args, ensure_ascii=False))
        yield Finish("stop", _usage())


def _usage() -> dict:
    """토큰을 쓰지 않는다 (calls 는 하네스가 센다)."""
    return {"input": 0, "cached": 0, "output": 0, "reasoning": 0}
