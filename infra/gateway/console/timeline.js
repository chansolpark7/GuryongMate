// 하루 타임라인 (하단): 일정 블록 · 날씨 구간 띠 · 지금 시각 바늘. 바늘을 끌면 가상 시각 이동 (redesign.md §2.3)
import { S, canSim, on, prefs, setPref, simNow } from "./store.js";
import {
  addWeather, deleteSegment, planStartMs, resetClock, setClock, setPlaySpeed, setSegmentEnd, shiftClock, startPlay, stopPlay,
} from "./actions.js";
import { arm } from "./weather.js";
import { focusItem } from "./map.js";
import { ITEM_STATUS_KO, closePopover, kindMeta, popover, toast } from "./ui.js";
import { fmtKst, h, kstIso, ms } from "./util.js";

const $ = (id) => document.getElementById(id);
const HOUR = 3600e3;
const DAY_MS = 24 * HOUR;
const WEEK = ["일", "월", "화", "수", "목", "금", "토"];

let viewDay = null; // 사용자가 ◀▶ 로 고른 날짜 (null = 자동)
let autoDay = null; // 자동으로 고른 날짜가 바뀌면 사용자 선택을 푼다
let fullDay = false; // 0~24시 전체
let drag = null; // {mode: "needle"|"draw", t, t0, moved}
let range = [0, 1];
let sig = "";

const dayMs = (d) => Date.parse(`${d}T00:00:00+09:00`);
const dayOfMs = (t) => kstIso(t).slice(0, 10);
const hm = (t) => fmtKst(t, { date: false, seconds: false });
const hmMs = (d, s) => Date.parse(`${d}T${s.slice(0, 5)}:00+09:00`);

export function initTimeline() {
  on("sim plan plans trip arm motion play", render);
  setInterval(tickNeedle, 250);
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
  const day = chooseDay();
  range = computeRange(day);
  const items = dayItemsOf(day);
  const cur = S.trip?.current;
  const ok = canSim();
  const s = JSON.stringify([day, range, fullDay, ok, items.map((i) => [i.id, i.status, i.start_at, i.end_at, i.place?.title]),
    cur?.state === "visiting" ? cur.item_id : null, S.sim?.timeline, S.sim?.mode, S.arm, !!S.motion, S.play?.speed ?? null, planDates(), dayOfMs(simNow())]);
  if (s === sig && box.firstChild) { tickNeedle(); return; }
  if (drag) return; // 끄는 중에는 다시 만들지 않는다
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
    S.arm ? h("span.armed-hint", `${kindMeta(S.arm).icon} 날씨 띠 위를 끌어 구간을 그리세요 `, h("button.link", { onclick: () => arm(null) }, "취소")) : null,
    h("button.link.small", { onclick: () => { fullDay = !fullDay; render(); } }, fullDay ? "일정 시간만" : "하루 전체"));

  // 눈금
  const span = range[1] - range[0];
  const step = span > 14 * HOUR ? 2 : 1;
  const scale = h("div.scale");
  for (let t = range[0]; t <= range[1]; t += HOUR) {
    const hr = Math.round((t - dayMs(day)) / HOUR);
    const major = hr % step === 0;
    scale.append(h(`span.tick${major ? ".major" : ""}`, { style: { left: `${pct(t)}%` }, dataset: { l: major ? `${hr}시` : "" } }));
  }

  // 일정 블록
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
      title: `${n}. ${title} ${it.start_at.slice(0, 5)}~${(it.end_at || "").slice(0, 5)} · ${here ? "지금 머무는 중" : ITEM_STATUS_KO[it.status] || it.status}`,
      dataset: { item: String(it.id) },
    }, h("span.n", String(n)), h("span.t", title)));
  }
  if (!n) row.append(h("span.empty", dates.includes(day) ? "일정이 아직 없어요" : "이 날은 계획이 없어요"));

  // 날씨 띠
  const band = h(`div.wband${S.sim?.mode === "sim" ? "" : ".off"}${S.arm ? ".arming" : ""}`,
    { title: S.sim?.mode === "sim" ? "날씨 구간 (구간 밖은 평상시)" : "실시간 DT 사용 중 — 시간표는 가상 시나리오에서만 적용" });
  (S.sim?.timeline || []).forEach((x, i) => {
    const a = Math.max(range[0], ms(x.start));
    const b = Math.min(range[1], ms(x.end));
    if (b <= a) return;
    const m = kindMeta(x.kind);
    band.append(h(`div.wseg.k-${x.kind}`, {
      style: { left: `${pct(a)}%`, width: `${pct(b) - pct(a)}%` },
      title: `${m.ko} ${hm(ms(x.start))}~${hm(ms(x.end))} — 눌러서 삭제·끝 시각 바꾸기`,
      dataset: { seg: String(i) },
    }, h("span", `${m.icon} ${m.ko}`)));
  });
  band.append(h("div.draw-ghost", { hidden: true }));

  const needle = h("div.needle", { hidden: true }, h("div.knob", h("span", "")), h("div.line"));
  const track = h("div.track", { "aria-label": "끌어서 가상 시각 이동" },
    h("div.lane-label.l1", "일정"), h("div.lane-label.l2", "날씨"),
    h("div.lanes", scale, row, band, needle));
  track.addEventListener("pointerdown", onDown);

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
      onclick: () => (playing ? stopPlay() : startPlay(Number(speedSel.value))),
    }, playing ? "■ 멈춤" : "▶ 재생"),
    speedSel,
    h("span.grow"),
    h("button.btn.ghost", { disabled: !ok || !!S.motion, onclick: resetClock, title: "가상 시각을 실제 시각으로 (reset_clock)" }, "지금(실제)으로"));

  box.replaceChildren(head, track, ctrl);
  tickNeedle();
}

/** 바늘만 옮긴다 (250ms 마다). */
function tickNeedle() {
  const box = $("day");
  const needle = box.querySelector(".needle");
  if (!needle) return;
  const t = drag?.mode === "needle" ? drag.t : simNow();
  const inside = t >= range[0] && t <= range[1];
  needle.hidden = !inside;
  needle.classList.toggle("dragging", drag?.mode === "needle");
  if (!inside) return;
  needle.style.left = `${pct(t)}%`;
  needle.querySelector(".knob span").textContent = hm(t);
  // 오늘 범위를 지나면 다시 그린다 (축이 지금을 따라가게)
  if (!drag && (t > range[1] - 60e3)) render();
}

// ---------- 끌기 ----------

function tAt(lanes, clientX, snapMin = 1) {
  const r = lanes.getBoundingClientRect();
  const f = Math.min(1, Math.max(0, (clientX - r.left) / r.width));
  const t = range[0] + f * (range[1] - range[0]);
  const step = snapMin * 60e3;
  return Math.round(t / step) * step;
}

function onDown(ev) {
  if (ev.button !== 0) return;
  const lanes = ev.currentTarget.querySelector(".lanes");
  const seg = ev.target.closest(".wseg");
  const blk = ev.target.closest(".blk");
  const inBand = !!ev.target.closest(".wband");
  if (!canSim()) {
    if (blk) focusItem(Number(blk.dataset.item));
    return;
  }
  if (seg && !S.arm) { segmentMenu(seg, Number(seg.dataset.seg)); return; }
  if (inBand && S.arm) {
    const t = tAt(lanes, ev.clientX, 5);
    drag = { mode: "draw", t0: t, t, lanes };
  } else {
    if (S.motion) { if (blk) focusItem(Number(blk.dataset.item)); else toast("이동 중에는 시각을 옮길 수 없어요. [멈춤] 후 다시 하세요", "warn"); return; }
    drag = { mode: "needle", t: tAt(lanes, ev.clientX), x: ev.clientX, moved: !blk, blk, lanes };
    if (S.play) stopPlay();
  }
  ev.currentTarget.setPointerCapture(ev.pointerId);
  const move = (e) => onMove(e);
  const up = (e) => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    window.removeEventListener("pointercancel", up);
    onUp(e);
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
  window.addEventListener("pointercancel", up);
  onMove(ev);
  ev.preventDefault();
}

function onMove(ev) {
  if (!drag) return;
  if (drag.mode === "needle") {
    if (Math.abs(ev.clientX - drag.x) > 4) drag.moved = true;
    if (!drag.moved) return;
    drag.t = tAt(drag.lanes, ev.clientX);
    tickNeedle();
  } else {
    drag.t = tAt(drag.lanes, ev.clientX, 5);
    const ghost = drag.lanes.querySelector(".draw-ghost");
    const a = Math.min(drag.t0, drag.t);
    const b = Math.max(drag.t0, drag.t);
    ghost.hidden = b <= a;
    ghost.className = `draw-ghost k-${S.arm}`;
    ghost.style.left = `${pct(a)}%`;
    ghost.style.width = `${pct(b) - pct(a)}%`;
    ghost.textContent = `${hm(a)}~${hm(b)}`;
  }
}

async function onUp() {
  const d = drag;
  drag = null;
  if (!d) return;
  if (d.mode === "needle") {
    if (!d.moved && d.blk) { focusItem(Number(d.blk.dataset.item)); tickNeedle(); return; }
    tickNeedle();
    if (Math.abs(d.t - simNow()) >= 30e3) {
      const okk = await setClock(d.t);
      if (!okk) tickNeedle();
    }
    render();
  } else {
    const a = Math.min(d.t0, d.t);
    const b = Math.max(d.t0, d.t);
    const kind = S.arm;
    arm(null);
    if (b - a < 5 * 60e3) { toast("구간이 너무 짧아요 — 띠 위를 옆으로 끌어 주세요", "warn"); render(); return; }
    await addWeather(kind, a, b);
  }
}

/** 날씨 구간을 누르면: [삭제] [끝 시각 바꾸기]. */
function segmentMenu(el, index) {
  const x = S.sim?.timeline?.[index];
  if (!x) return;
  const m = kindMeta(x.kind);
  const end = ms(x.end);
  const time = h("input", { type: "time", value: hm(end), "aria-label": "끝 시각" });
  const apply = () => {
    if (!time.value) return;
    const day = dayOfMs(ms(x.start));
    let t = Date.parse(`${day}T${time.value}:00+09:00`);
    if (t <= ms(x.start)) t += DAY_MS;
    closePopover();
    setSegmentEnd(index, t);
  };
  popover(el, h("div.menu",
    h("div.menu-head", h("b", `${m.icon} ${m.ko}`), h("span.muted", ` ${fmtKst(ms(x.start), { seconds: false })} ~ ${hm(end)}`)),
    h("div.menu-row", h("span.muted.small", "끝 시각"), time, h("button.btn.sm", { onclick: apply }, "바꾸기")),
    h("div.menu-row", h("button.btn.sm.danger-ghost", { onclick: () => { closePopover(); deleteSegment(index); } }, "이 구간 삭제"))),
  { place: "above" });
}
