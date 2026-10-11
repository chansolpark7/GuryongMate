// 하루 타임라인 (하단). 영상 편집 프로그램처럼 줄마다 하는 일이 하나다 (README §2.3):
//  - 눈금자 줄 = 시간 전용. 바늘 머리를 끌거나 눈금자를 누르면 가상 시각 이동 (놓을 때 한 번 PUT)
//  - 일정 줄 = 누르기만 (지도에서 그 장소)
//  - 날씨 줄 = 날씨 편집 전용. 구간 끝 손잡이로 길이, 가운데로 이동, 누르면 선택. 그리기 도구가 켜지면 끌어서 새 구간
//  바늘 선은 일정·날씨 줄 위로 지나가며 보이기만 한다 (pointer-events: none).
import { S, canSim, on, prefs, setPref, simNow } from "./store.js";
import {
  canUndo, cut, normalizeTimeline, planStartMs, resetClock, saveTimeline, segLabel, setClock, setPlaySpeed, shiftClock,
  startPlay, stopPlay, timelineMs, undoWeather,
} from "./actions.js";
import { arm } from "./weather.js";
import { focusItem } from "./map.js";
import { ITEM_STATUS_KO, KIND_META, kindMeta, toast } from "./ui.js";
import { fmtKst, fmtMinutes, h, kstIso } from "./util.js";

const $ = (id) => document.getElementById(id);
const HOUR = 3600e3;
const DAY_MS = 24 * HOUR;
const MIN = 60e3;
const SNAP = 5 * MIN; // 날씨 편집 5분 단위
const MIN_LEN = 10 * MIN; // 구간 최소 길이
const WEEK = ["일", "월", "화", "수", "목", "금", "토"];

let viewDay = null; // 사용자가 ◀▶ 로 고른 날짜 (null = 자동)
let autoDay = null; // 자동으로 고른 날짜가 바뀌면 사용자 선택을 푼다
let fullDay = false; // 0~24시 전체
let drag = null; // 끄는 중: {mode: "needle"|"ruler"|"start"|"end"|"move"|"draw", …}
let saving = false; // 놓은 뒤 PUT 이 끝날 때까지 미리보기를 그대로 둔다 (깜빡임 방지)
let sel = null; // 선택한 날씨 구간 키 "kind|a|b"
let range = [0, 1];
let sig = "";

const dayMs = (d) => Date.parse(`${d}T00:00:00+09:00`);
const dayOfMs = (t) => kstIso(t).slice(0, 10);
const hm = (t) => fmtKst(t, { date: false, seconds: false });
const hmMs = (d, s) => Date.parse(`${d}T${s.slice(0, 5)}:00+09:00`);
const snap = (t, step = SNAP) => Math.round(t / step) * step;
const keyOf = (x) => `${x.kind}|${x.a}|${x.b}`;

export function initTimeline() {
  on("sim plan plans trip arm motion play undo", render);
  // 바늘은 매 프레임 (재생·걷기 중에도 부드럽게)
  const loop = () => { tickNeedle(); requestAnimationFrame(loop); };
  requestAnimationFrame(loop);
  document.addEventListener("keydown", onKey);
  render();
}

function planObj() {
  return S.plan || S.plans.find((p) => p.id === S.planId) || null;
}

function planDates() {
  const p = planObj();
  if (!p) return [];
  if (S.plan?.days?.length) return S.plan.days.map((d) => d.date);
  const out = [];
  for (let t = dayMs(p.start_date); t <= dayMs(p.end_date || p.start_date); t += DAY_MS) out.push(dayOfMs(t));
  return out;
}

/** 보여줄 날짜: 가상 시각 날짜가 계획 날짜(또는 계획 없음)면 그 날, 아니면 계획 첫날. */
function chooseDay() {
  const today = dayOfMs(simNow());
  const dates = planDates();
  const auto = !dates.length || dates.includes(today) ? today : dates[0];
  if (auto !== autoDay) { autoDay = auto; viewDay = null; }
  return viewDay || auto;
}

/** 축 범위: 계획 day_start-2시간 ~ day_end+2시간 (정시로), 오늘이면 지금도 들어가게. */
function computeRange(day) {
  const d0 = dayMs(day);
  if (fullDay) return [d0, d0 + DAY_MS];
  const p = planObj();
  let a = d0 + 8 * HOUR;
  let b = d0 + 20 * HOUR;
  if (p?.day_start && p?.day_end) {
    a = Math.floor((hmMs(day, p.day_start) - 2 * HOUR) / HOUR) * HOUR;
    b = Math.ceil((hmMs(day, p.day_end) + 2 * HOUR) / HOUR) * HOUR;
  }
  const now = simNow();
  if (dayOfMs(now) === day) {
    a = Math.min(a, Math.floor((now - 30 * 60e3) / HOUR) * HOUR);
    b = Math.max(b, Math.ceil((now + 30 * 60e3) / HOUR) * HOUR);
  }
  return [Math.max(d0, a), Math.min(d0 + DAY_MS, b)];
}

const pct = (t) => ((t - range[0]) / (range[1] - range[0])) * 100;

function dayItemsOf(day) {
  return S.plan?.days?.find((d) => d.date === day)?.items || [];
}

// ---------- 그리기 ----------

function render() {
  const box = $("day");
  if (drag || saving) return; // 끄는 중·저장 중에는 다시 만들지 않는다
  const active = document.activeElement;
  if (active?.tagName === "INPUT" && box.querySelector(".seg-panel")?.contains(active)) return; // 시각 입력 중
  const day = chooseDay();
  range = computeRange(day);
  const items = dayItemsOf(day);
  const cur = S.trip?.current;
  const ok = canSim();
  const segs = timelineMs();
  if (sel && !segs.some((x) => keyOf(x) === sel)) sel = null; // 다른 곳(CLI·다른 탭)에서 바뀌어 없어졌으면 선택 해제
  const s = JSON.stringify([day, range, fullDay, ok, items.map((i) => [i.id, i.status, i.start_at, i.end_at, i.place?.title]),
    cur?.state === "visiting" ? cur.item_id : null, S.sim?.timeline, S.sim?.mode, S.arm, !!S.motion, S.play?.speed ?? null,
    planDates(), dayOfMs(simNow()), sel, canUndo()]);
  if (s === sig && box.firstChild) return;
  sig = s;

  const today = dayOfMs(simNow());
  const dates = planDates();
  const d = new Date(dayMs(day) + 9 * HOUR);
  const label = `${day.slice(5).replace("-", "/")} (${WEEK[d.getUTCDay()]})`;
  const shift = (n) => { viewDay = dayOfMs(dayMs(day) + n * DAY_MS + HOUR); render(); };
  const start = planStartMs();
  const head = h("div.day-head",
    h("button.icon-btn", { title: "전날", "aria-label": "전날", onclick: () => shift(-1) }, "‹"),
    h("b.day-label", label),
    h("button.icon-btn", { title: "다음 날", "aria-label": "다음 날", onclick: () => shift(1) }, "›"),
    day === today ? h("span.tag.now", "지금") : h("button.link", { onclick: () => { viewDay = today; render(); } }, "지금 날짜 보기"),
    dates.includes(day) ? h("span.tag.plan", "계획") : null,
    start != null && !dates.includes(today) && ok
      ? h("button.btn.sm.primary", { onclick: () => setClock(start), title: "가상 시각을 첫 일정 10분 전으로" },
        `계획 날짜로 (${fmtKst(start, { seconds: false })})`) : null,
    h("span.grow"),
    S.arm ? h("span.armed-hint", `✏️ ${kindMeta(S.arm).icon} ${S.arm === "calm" ? "지우개" : `${kindMeta(S.arm).ko} 그리기`} `,
      h("button.link", { onclick: () => arm(null), title: "그리기 도구 끄기 (Esc)" }, "끄기")) : null,
    h("button.btn.sm", {
      disabled: !ok || !canUndo(), onclick: undoWeather, title: "날씨 시간표를 직전으로 (Ctrl+Z, 한 단계)",
    }, "↶ 되돌리기"),
    h("button.link.small", { onclick: () => { fullDay = !fullDay; render(); } }, fullDay ? "일정 시간만" : "하루 전체"));

  // 눈금자 (시간 전용)
  const span = range[1] - range[0];
  const step = span > 14 * HOUR ? 2 : 1;
  const scale = h("div.scale", { title: "누르면 그 시각으로 · 바늘 머리를 끌면 시간 이동" });
  for (let t = range[0]; t <= range[1]; t += HOUR) {
    const hr = Math.round((t - dayMs(day)) / HOUR);
    const major = hr % step === 0;
    scale.append(h(`span.tick${major ? ".major" : ""}`, { style: { left: `${pct(t)}%` }, dataset: { l: major ? `${hr}시` : "" } }));
  }
  scale.addEventListener("pointerdown", (ev) => onTimeDown(ev, "ruler"));

  // 일정 블록 (누르기만)
  const row = h("div.items");
  let n = 0;
  for (const it of items) {
    if (it.status === "skipped" || !it.start_at) continue;
    n += 1;
    const a = hmMs(day, it.start_at);
    const b = it.end_at ? hmMs(day, it.end_at) : a + 30 * 60e3;
    const here = cur?.state === "visiting" && cur.item_id === it.id;
    const title = it.place?.title || it.place_id;
    row.append(h(`div.blk.st-${here ? "here" : it.status}`, {
      style: { left: `${pct(a)}%`, width: `${Math.max(0.6, pct(b) - pct(a))}%` },
      title: `${n}. ${title} ${it.start_at.slice(0, 5)}~${(it.end_at || "").slice(0, 5)} · ${here ? "지금 머무는 중" : ITEM_STATUS_KO[it.status] || it.status} — 누르면 지도에서 보기`,
      onclick: () => focusItem(it.id),
    }, h("span.n", String(n)), h("span.t", title)));
  }
  if (!n) row.append(h("span.empty", dates.includes(day) ? "일정이 아직 없어요" : "이 날은 계획이 없어요"));

  // 날씨 줄 (편집 전용)
  const band = h(`div.wband${S.sim?.mode === "sim" ? "" : ".off"}${S.arm ? ".arming" : ""}`,
    { title: S.sim?.mode === "sim" ? "" : "실시간 DT 사용 중 — 시간표는 가상 시나리오에서만 적용" });
  band.addEventListener("pointerdown", onBandDown);
  drawBand(band, segs);

  const knob = h("div.knob", { title: "끌어서 시간 이동" }, h("span", ""));
  knob.addEventListener("pointerdown", (ev) => onTimeDown(ev, "needle"));
  const needle = h("div.needle", { hidden: true }, knob, h("div.line"));
  const lanes = h("div.lanes", scale, row, band, needle, h("div.drag-tip", { hidden: true }));
  const track = h("div.track",
    h("div.lane-label.l0", "시각"), h("div.lane-label.l1", "일정"), h("div.lane-label.l2", S.arm ? "✏️ 날씨" : "날씨"),
    lanes);

  // 아래 버튼
  const playing = !!S.play;
  const dis = !ok || !!S.motion;
  const speedSel = h("select.speed", { "aria-label": "재생 배속", title: "재생 배속 (실제 1초 = 가상 N초)" },
    ...[10, 20, 60].map((v) => h("option", { value: String(v) }, `×${v}`)));
  speedSel.value = String(S.play?.speed ?? prefs.playSpeed);
  speedSel.addEventListener("change", () => { const v = Number(speedSel.value); setPref("playSpeed", v); setPlaySpeed(v); });
  const ctrl = h("div.day-ctrl",
    h("button.btn", { disabled: dis || playing, onclick: () => shiftClock(10) }, "+10분"),
    h("button.btn", { disabled: dis || playing, onclick: () => shiftClock(30) }, "+30분"),
    h(`button.btn${playing ? ".playing" : ""}`, {
      disabled: dis,
      title: "가상 시각을 배속으로 흘려요 (위치는 보내지 않고 시간만)",
      onclick: () => (playing ? stopPlay({ sync: true }) : startPlay(Number(speedSel.value))),
    }, playing ? "■ 멈춤" : "▶ 재생"),
    speedSel,
    h("span.grow"),
    h("button.btn.ghost", { disabled: !ok || !!S.motion, onclick: resetClock, title: "가상 시각을 실제 시각으로 (reset_clock)" }, "지금(실제)으로"));

  box.replaceChildren(head, track, ctrl);
  const selSeg = sel && segs.find((x) => keyOf(x) === sel);
  if (selSeg) {
    const panel = segPanel(selSeg, segs);
    track.append(panel);
    // 트랙 밖으로 나가면 안으로 민다 (붙인 뒤라 크기를 바로 잴 수 있다)
    const tr = track.getBoundingClientRect();
    const r = panel.getBoundingClientRect();
    const dx = r.left < tr.left ? tr.left - r.left : r.right > tr.right ? tr.right - r.right : 0;
    if (dx) panel.style.marginLeft = `${dx}px`;
  }
  tickNeedle();
}

/** 날씨 줄 안을 다시 그린다 (끄는 중 미리보기에도 쓴다). segs = [{kind, a, b}] (ms, 정리된 것). */
function drawBand(band, segs, { tool = S.arm, edit = null } = {}) {
  // edit: 끄는 중인 구간 {kind, a, b} — 정리 뒤 그 가운데를 덮는 같은 종류 구간에 손잡이·테두리를 보인다
  const mid = edit && (edit.a + edit.b) / 2;
  const editing = edit && segs.find((y) => y.kind === edit.kind && y.a <= mid && mid < y.b);
  const kids = [];
  for (const x of segs) {
    const a = Math.max(range[0], x.a);
    const b = Math.min(range[1], x.b);
    if (b <= a) continue;
    const m = kindMeta(x.kind);
    const key = keyOf(x);
    kids.push(h(`div.wseg.k-${x.kind}${key === sel ? ".sel" : ""}${x === editing ? ".editing" : ""}`, {
      style: { left: `${pct(a)}%`, width: `${pct(b) - pct(a)}%` },
      title: tool ? "" : `${m.ko} ${hm(x.a)}~${hm(x.b)} — 끝을 끌어 길이, 가운데를 끌어 이동, 누르면 선택`,
      dataset: { key },
    },
    h("span.lbl", `${m.icon} ${m.ko}`),
    x.a >= range[0] ? h("i.hdl.l", { title: "끌어서 시작 시각" }) : null,
    x.b <= range[1] ? h("i.hdl.r", { title: "끌어서 끝 시각" }) : null));
  }
  // 안내는 구간 뒤에 (빈 곳에서만 읽히게)
  if (tool) kids.unshift(h("div.band-hint", `끌어서 ${kindMeta(tool).icon} ${tool === "calm" ? "평상시로 지우기" : `${kindMeta(tool).ko} 그리기`}`));
  else if (!segs.some((x) => x.b > range[0] && x.a < range[1])) kids.unshift(h("div.band-hint.dim", "날씨 카드에서 종류를 고르면 여기에 그릴 수 있어요"));
  band.replaceChildren(...kids);
}

/** 바늘만 옮긴다 (매 프레임). */
function tickNeedle() {
  const box = $("day");
  const needle = box.querySelector(".needle");
  if (!needle) return;
  const timeDrag = drag?.mode === "needle" || (drag?.mode === "ruler" && drag.moved);
  const t = timeDrag ? drag.t : simNow();
  const inside = t >= range[0] && t <= range[1];
  needle.hidden = !inside;
  needle.classList.toggle("dragging", timeDrag);
  if (!inside) return;
  needle.style.left = `${pct(t)}%`;
  const label = hm(t);
  const span = needle.querySelector(".knob span");
  if (span.textContent !== label) span.textContent = label;
  // 오늘 범위를 지나면 다시 그린다 (축이 지금을 따라가게)
  if (!drag && !saving && t > range[1] - 60e3) { sig = ""; render(); }
}

// ---------- 끌기 공통 ----------

function tAt(clientX) {
  const lanes = $("day").querySelector(".lanes");
  const r = lanes.getBoundingClientRect();
  const f = Math.min(1, Math.max(0, (clientX - r.left) / r.width));
  return range[0] + f * (range[1] - range[0]);
}

/** pointerdown 뒤 놓을 때까지 window 에서 move·up 을 받는다 (요소를 다시 그려도 끊기지 않게). */
function capture(ev, onMove, onUp) {
  document.body.dataset.drag = drag.mode; // 끄는 동안 커서 고정 (style.css body[data-drag])
  const move = (e) => onMove(e);
  const end = (e) => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", end);
    window.removeEventListener("pointercancel", end);
    delete document.body.dataset.drag;
    onUp(e, e.type === "pointercancel");
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", end);
  window.addEventListener("pointercancel", end);
  ev.preventDefault();
  ev.stopPropagation();
}

function showTip(clientX, text) {
  const lanes = $("day").querySelector(".lanes");
  const tip = lanes?.querySelector(".drag-tip");
  if (!tip) return;
  const r = lanes.getBoundingClientRect();
  tip.hidden = !text;
  tip.textContent = text || "";
  tip.style.left = `${Math.min(r.width - 4, Math.max(4, clientX - r.left))}px`;
}

// ---------- 시간 (눈금자·바늘 머리) ----------

function onTimeDown(ev, mode) {
  if (ev.button !== 0 || !canSim()) return;
  if (S.motion) { toast("이동 중에는 시각을 옮길 수 없어요. [멈춤] 후 다시 하세요", "warn"); return; }
  drag = { mode, t: snap(tAt(ev.clientX), MIN), x: ev.clientX, moved: mode === "needle" };
  capture(ev, (e) => {
    if (Math.abs(e.clientX - drag.x) > 3) drag.moved = true;
    if (drag.moved) drag.t = snap(tAt(e.clientX), MIN);
  }, async (e, cancelled) => {
    const d = drag;
    drag = null;
    if (cancelled) { render(); return; }
    // 눈금자는 누르기만 해도 그 시각으로, 바늘 머리는 끌었을 때만
    const t = d.mode === "ruler" && !d.moved ? snap(tAt(e.clientX), MIN) : d.t;
    if ((d.moved || d.mode === "ruler") && Math.abs(t - simNow()) >= 30e3) {
      if (S.play) stopPlay();
      await setClock(t); // 뒤로 옮기면 "이전 위치보다 이른 시각" 확인창 (README §4)
    }
    render();
  });
}

// ---------- 날씨 줄 ----------

function onBandDown(ev) {
  if (ev.button !== 0 || !canSim() || saving) return;
  const segs = timelineMs();
  const segEl = ev.target.closest(".wseg");
  const idx = segEl ? segs.findIndex((x) => keyOf(x) === segEl.dataset.key) : -1;
  const t = tAt(ev.clientX);
  if (S.arm) {
    // 그리기 도구: 어디서든 끌면 새 구간 (기존 구간 위에도 — 겹친 곳은 센 것이 차지). 누르기만 하면 선택
    drag = { mode: "draw", kind: S.arm, segs, t0: snap(t), x: ev.clientX, moved: false, hit: idx >= 0 ? keyOf(segs[idx]) : null };
  } else if (idx >= 0) {
    const hdl = ev.target.closest(".hdl");
    const mode = hdl ? (hdl.classList.contains("l") ? "start" : "end") : "move";
    drag = { mode, idx, orig: segs[idx], segs, t0: t, x: ev.clientX, moved: false };
  } else {
    select(null); // 그리기 도구가 꺼져 있으면 빈 곳은 선택 해제만
    return;
  }
  capture(ev, onBandMove, onBandUp);
}

function onBandMove(e) {
  const d = drag;
  if (!d || d.cancelled) return;
  if (!d.moved && Math.abs(e.clientX - d.x) <= 3) return;
  d.moved = true;
  const t = tAt(e.clientX);
  let preview;
  let tip;
  if (d.mode === "draw") {
    const t1 = snap(t);
    const a = Math.min(d.t0, t1);
    const b = Math.max(d.t0, t1);
    d.next = { kind: d.kind, a, b };
    preview = b > a ? previewDraw(d.segs, d.next) : d.segs;
    tip = `${kindMeta(d.kind).icon} ${hm(a)}~${hm(b)} · ${fmtMinutes((b - a) / MIN)}${b - a < MIN_LEN ? " (10분 이상)" : ""}`;
  } else {
    const o = d.orig;
    let { a, b } = o;
    if (d.mode === "start") a = Math.min(snap(t), b - MIN_LEN);
    else if (d.mode === "end") b = Math.max(snap(t), a + MIN_LEN);
    else { a = snap(o.a + (t - d.t0)); b = a + (o.b - o.a); }
    d.next = { kind: o.kind, a, b };
    preview = normalizeTimeline(d.segs.map((x, i) => (i === d.idx ? d.next : x)));
    tip = d.mode === "start" ? `시작 ${hm(a)} · ${fmtMinutes((b - a) / MIN)}`
      : d.mode === "end" ? `끝 ${hm(b)} · ${fmtMinutes((b - a) / MIN)}` : `${hm(a)}~${hm(b)}`;
  }
  // 미리보기도 정리 규칙대로 (서버에 저장될 모양 그대로)
  drawBand($("day").querySelector(".wband"), preview, { tool: d.mode === "draw" ? d.kind : null, edit: d.mode === "draw" ? null : d.next });
  $("day").querySelector(".seg-panel")?.remove();
  showTip(e.clientX, tip);
}

function previewDraw(segs, x) {
  return x.kind === "calm" ? normalizeTimeline(cutMs(segs, x.a, x.b)) : normalizeTimeline([...segs, x]);
}

/** [a, b) 를 비운다 (☀️ 지우개). */
function cutMs(segs, a, b) {
  return timelineMs(cut(segs.map((x) => ({ kind: x.kind, start: kstIso(x.a), end: kstIso(x.b) })), a, b));
}

async function onBandUp(e, cancelled) {
  const d = drag;
  drag = null;
  showTip(0, null);
  if (!d || cancelled || d.cancelled) { sig = ""; render(); return; }
  if (!d.moved) {
    // 누르기 = 선택 (그리기 도구 중에도 구간을 누르면 선택, 빈 곳이면 해제)
    select(d.mode === "draw" ? d.hit : keyOf(d.orig));
    return;
  }
  const x = d.next;
  if (d.mode === "draw") {
    if (x.b - x.a < MIN_LEN) { toast("구간은 10분 이상이어야 해요 — 옆으로 더 끌어 주세요", "warn"); sig = ""; render(); return; }
    if (x.kind === "calm") await commit(cutMs(d.segs, x.a, x.b), `☀️ 평상시로 지움 ${hm(x.a)}~${hm(x.b)}`, null);
    else await commit([...d.segs, x], `${segLabel(x.kind, x.a, x.b)} 추가`, x);
    return;
  }
  if (x.a === d.orig.a && x.b === d.orig.b) { sig = ""; render(); return; }
  await commit(d.segs.map((y, i) => (i === d.idx ? x : y)), `${segLabel(x.kind, x.a, x.b)} ${d.mode === "move" ? "로 옮김" : "로 바꿈"}`, x);
}

/** 시간표 저장 (놓을 때 한 번 PUT). 저장하는 동안 미리보기를 그대로 두고, 끝나면 follow(정리 뒤 그 자리의 같은 종류 구간)를 선택. */
async function commit(segs, label, follow) {
  saving = true;
  let ok;
  try {
    ok = await saveTimeline(segs, label);
  } finally {
    saving = false;
  }
  if (ok && follow) {
    const mid = (follow.a + follow.b) / 2;
    const hit = timelineMs().find((y) => y.kind === follow.kind && y.a <= mid && mid < y.b);
    sel = hit ? keyOf(hit) : null;
  }
  sig = "";
  render();
}

function select(key) {
  if (sel === key) return;
  sel = key;
  sig = "";
  render();
}

// ---------- 선택한 구간: 작은 창 (종류 바꾸기 · 시작·끝 · 삭제) ----------

function segPanel(x, segs) {
  const others = segs.filter((y) => keyOf(y) !== keyOf(x));
  const m = kindMeta(x.kind);
  const day = dayOfMs(x.a);
  const startIn = h("input", { type: "time", step: 300, value: hm(x.a), "aria-label": "시작 시각" });
  const endIn = h("input", { type: "time", step: 300, value: hm(x.b), "aria-label": "끝 시각" });
  const apply = () => {
    if (!startIn.value || !endIn.value) return;
    const a = Date.parse(`${day}T${startIn.value}:00+09:00`);
    let b = Date.parse(`${day}T${endIn.value}:00+09:00`);
    if (b <= a) b += DAY_MS; // 끝이 시작보다 이르면 다음 날
    if (b - a < MIN_LEN) { toast("구간은 10분 이상이어야 해요", "warn"); return; }
    if (a === x.a && b === x.b) return;
    const y = { kind: x.kind, a, b };
    commit([...others, y], `${segLabel(y.kind, a, b)} 로 바꿈`, y);
  };
  for (const inp of [startIn, endIn]) inp.addEventListener("keydown", (ev) => { if (ev.key === "Enter") apply(); });
  const setKind = (k) => {
    if (k === x.kind) return;
    if (k === "calm") { remove(x, others); return; }
    const y = { ...x, kind: k };
    commit([...others, y], `${m.icon} ${m.ko} → ${segLabel(k, x.a, x.b)}`, y);
  };
  const panel = h("div.seg-panel", { role: "dialog", "aria-label": "날씨 구간" },
    h("div.sp-head",
      h("b", `${m.icon} ${m.ko}`), h("span.muted", ` ${hm(x.a)}~${hm(x.b)} · ${fmtMinutes((x.b - x.a) / MIN)}`),
      h("span.grow"),
      h("button.icon-btn.sm", { title: "선택 해제 (Esc)", "aria-label": "닫기", onclick: () => select(null) }, "×")),
    h("div.sp-kinds", ...Object.entries(KIND_META).map(([k, km]) => h(`button.sp-kind${k === x.kind ? ".on" : ""}`, {
      title: k === "calm" ? "평상시로 (이 구간 지우기)" : `${km.ko}로 바꾸기`, "aria-pressed": String(k === x.kind),
      onclick: () => setKind(k),
    }, km.icon))),
    h("div.sp-row", startIn, h("span.muted", "~"), endIn, h("button.btn.sm", { onclick: apply }, "적용")),
    h("div.sp-row",
      h("button.btn.sm.danger-ghost", { onclick: () => remove(x, others), title: "Delete·Backspace" }, "🗑 삭제"),
      h("span.grow"),
      h("span.muted.small", "Delete 삭제 · Esc 해제")));
  // 구간 가운데 위에 (트랙 밖으로 나가지 않게). 트랙 = 줄 이름 40px + 간격 8px + 줄
  const mid = pct((Math.max(range[0], x.a) + Math.min(range[1], x.b)) / 2);
  panel.style.left = `calc(48px + (100% - 48px) * ${mid / 100})`;
  return panel;
}

function remove(x, others) {
  sel = null;
  return commit(others, `${segLabel(x.kind, x.a, x.b)} 삭제`, null);
}

// ---------- 키보드: Delete·Backspace 삭제, Esc 해제·도구 끄기·끌기 취소, Ctrl+Z 되돌리기 ----------

function onKey(ev) {
  const tag = ev.target?.tagName;
  const typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || ev.target?.isContentEditable;
  if (ev.key === "Escape") {
    if (drag && drag.mode !== "needle" && drag.mode !== "ruler") {
      drag.cancelled = true; // 놓을 때 저장하지 않는다. 미리보기는 바로 원래대로
      drawBand($("day").querySelector(".wband"), drag.segs, { tool: null });
      showTip(0, null);
      return;
    }
    if (typing) return;
    if (sel) select(null);
    else if (S.arm) arm(null);
    return;
  }
  if (typing || drag || saving || !canSim()) return;
  if ((ev.key === "Delete" || ev.key === "Backspace") && sel) {
    const segs = timelineMs();
    const x = segs.find((y) => keyOf(y) === sel);
    if (x) { ev.preventDefault(); remove(x, segs.filter((y) => y !== x)); }
    return;
  }
  if ((ev.ctrlKey || ev.metaKey) && !ev.shiftKey && ev.key.toLowerCase() === "z" && canUndo()) {
    ev.preventDefault();
    undoWeather();
  }
}
