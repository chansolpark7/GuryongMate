// 상단 바와 오른쪽 패널 ①~④ 그리기
import { api } from "./api.js";
import { S, dayItems, on, simNow, tripActive } from "./store.js";
import {
  endTrip, getSpeed, resetClock, scanAndTick, setClock, setScenario, setSource, setSpeed, setTimeline, shiftClock,
  startTrip, stay, stopMotion, teleport, travel, onBusy, onTripError,
} from "./actions.js";
import { EVENT_KO, STATUS_KO } from "./ui.js";
import { focus } from "./map.js";
import { fmtKst, h, kstIso, ms, parseLocalKst, riskKo, toLocalKst } from "./util.js";

const $ = (id) => document.getElementById(id);
const ITEM_STATUS_KO = { planned: "예정", visiting: "머무는 중", done: "완료", skipped: "건너뜀" };
const SOURCE_KO = { sensors: "센서", breakwater: "방파제 CCTV", uv: "자외선", forecast: "예보", warnings: "특보", tide: "조위" };
const APPLY_KEY = "gm.console.applyNow";

export function initPanels() {
  // ① 시각
  for (const b of $("clock-shift").querySelectorAll("button")) {
    b.addEventListener("click", () => shiftClock(Number(b.dataset.shift)));
  }
  $("clock-apply").addEventListener("click", () => {
    const t = parseLocalKst($("clock-input").value);
    if (Number.isFinite(t)) setClock(t);
  });
  $("clock-reset").addEventListener("click", resetClock);
  $("clock-plan").addEventListener("click", () => {
    const t = planStartMs();
    if (t) setClock(t);
  });

  // ② 안전
  $("src-live").addEventListener("click", () => setSource("live"));
  $("src-mock").addEventListener("click", () => setSource("mock"));
  $("btn-scan").addEventListener("click", scanAndTick);
  for (const b of $("until-seg").querySelectorAll("button")) {
    b.addEventListener("click", () => {
      untilChoice = b.dataset.until;
      if (untilChoice === "at" && !$("until-time").value) $("until-time").value = hhmm(simNow() + 60 * 60e3);
      renderSafety();
    });
  }
  $("seg-add").addEventListener("submit", (e) => {
    e.preventDefault();
    addSegment();
  });
  try {
    const v = localStorage.getItem(APPLY_KEY);
    if (v !== null) $("apply-now").checked = v === "1";
  } catch { /* 무시 */ }
  $("apply-now").addEventListener("change", (e) => {
    try { localStorage.setItem(APPLY_KEY, e.target.checked ? "1" : "0"); } catch { /* 무시 */ }
  });

  // ③ 여행
  $("btn-start").addEventListener("click", startTrip);
  $("btn-end").addEventListener("click", endTrip);
  $("btn-stay").addEventListener("click", () => stay(Math.max(1, Number($("stay-min").value) || 3)));
  $("btn-stop").addEventListener("click", stopMotion);
  $("speed").value = String(getSpeed());
  if (!$("speed").value) $("speed").value = "20";
  $("speed").addEventListener("change", (e) => setSpeed(Number(e.target.value)));

  onBusy((kind, text) => {
    const node = kind === "scan" ? $("scan-busy") : $("trip-busy");
    node.textContent = text || "";
    node.hidden = !text;
    if (kind === "scan") $("btn-scan").disabled = !!text || !canSim();
  });
  onTripError((msg, withMove) => {
    const box = $("trip-error");
    box.replaceChildren();
    box.hidden = !msg;
    if (!msg) return;
    box.append(msg);
    const t = planStartMs();
    if (withMove && t && canSim()) {
      box.append(" ", h("button.link", { onclick: () => setClock(t) }, `가상 시각을 ${fmtKst(t, { seconds: false })} 로`));
    }
  });

  on("conn", renderConn);
  on("sim", () => { renderConn(); renderClockControls(); renderSafety(); });
  on("safety", renderSafety);
  on("plans", renderPlans);
  on("plan", () => { renderPlans(); renderItinerary(); renderClockControls(); renderSafety(); });
  on("trip", () => { renderTrip(); renderItinerary(); renderTimeline(); });
  on("motion", () => { renderMotion(); renderTrip(); renderItinerary(); });
  on("events", renderTimeline);
  on("alerts", renderAlerts);
  setInterval(renderClock, 250);
}

const canSim = () => !!S.sim && !S.simDisabled;

/** 계획 첫날 첫 일정 10분 전 (없으면 day_start). not_trip_day 안내용. */
function planStartMs() {
  const p = S.plan || S.plans.find((x) => x.id === S.planId);
  if (!p) return null;
  const first = p.days?.[0]?.items?.find((i) => i.status !== "skipped")?.start_at;
  const hhmm = first || p.day_start || "09:00";
  const t = Date.parse(`${p.start_date}T${hhmm.slice(0, 5)}:00+09:00`);
  return first ? t - 10 * 60e3 : t;
}

// ---------- 상단 ----------

function renderConn() {
  const conn = $("conn");
  conn.classList.toggle("ok", S.health === true);
  conn.classList.toggle("bad", S.health === false);
  $("conn-text").textContent = `${api.base.replace(/^https?:\/\//, "")} · ${S.health ? "연결" : S.health === false ? "끊김" : "확인 중"}`;
  const prov = S.agentInfo?.provider || "-";
  const llm = $("llm");
  llm.textContent = `LLM ${prov}`;
  llm.className = `chip ${prov.startsWith("scripted") ? "warn" : prov === "-" ? "" : "ok"}`;
  const mc = $("mode-chip");
  if (S.simDisabled) {
    mc.textContent = "안전: 실제 데이터 (가상 상황 꺼짐)";
    mc.className = "chip";
  } else if (S.sim) {
    mc.textContent = S.sim.mode === "live" ? "안전: 실제 데이터" : `안전: 가상(${S.sim.scenario})`;
    mc.className = `chip ${S.sim.mode === "live" ? "ok" : "sim"}`;
  }
  $("user-label").textContent = api.userLabel || "-";
  renderConnDetail();
}

export function renderConnDetail() {
  const ul = $("conn-detail");
  if (!ul) return;
  const eng = S.routing?.engine;
  const rows = [
    ["게이트웨이 /health", S.health ? "정상" : S.health === false ? "응답 없음" : "-"],
    ["LLM /agent/info", S.agentInfo?.provider || "-"],
    ["가상 상황 /safety/sim", S.simDisabled ? "꺼짐 — SAFETY_SIM_ENABLED=true 필요 (시각·안전 조작 비활성)"
      : S.sim ? `${S.sim.mode === "live" ? "실제 데이터" : `가상(${S.sim.scenario})`} · 전환 ${S.sim.switchable ? "가능" : "불가"} · 실제와 ${S.sim.offset_minutes}분 차이` : "-"],
    ["길찾기 /routing/status", eng ? (eng.ok ? `엔진 정상 ${eng.version || ""}` : `엔진 없음 — 직선 근사 (${eng.error || ""})`) : "-"],
    ["관리 토큰", api.adminToken ? "입력됨 (이 브라우저)" : "없음 (운영 서버의 🔒 API 는 403)"],
  ];
  ul.replaceChildren(...rows.map(([k, v]) => h("li", h("span.k", k), h("span.v", v))));
}

function renderPlans() {
  const sel = $("plan-select");
  const cur = String(S.planId ?? "");
  const opts = S.plans.map((p) => h("option", { value: String(p.id) }, `${p.title} (#${p.id}, ${p.start_date.slice(5)})`));
  if (!opts.length) opts.push(h("option", { value: "" }, "계획 없음"));
  const sig = S.plans.map((p) => `${p.id}:${p.title}`).join("|");
  if (sel.dataset.sig !== sig) {
    sel.replaceChildren(...opts);
    sel.dataset.sig = sig;
  }
  sel.value = cur;
}

function renderClock() {
  const now = simNow();
  const label = fmtKst(now);
  $("clock-top").textContent = `${S.sim || S.trip ? "가상 시각" : "시각"} ${label}`;
  $("clock-now").textContent = label;
  const diff = Math.round((now - Date.now()) / 60e3);
  const badge = $("clock-badge");
  if (S.sim && Math.abs(diff) >= 1) {
    const d = Math.abs(diff);
    const txt = d >= 1440 ? `${Math.floor(d / 1440)}일 ${Math.floor((d % 1440) / 60)}시간` : d >= 60 ? `${Math.floor(d / 60)}시간 ${d % 60}분` : `${d}분`;
    badge.textContent = `가상 · 실제보다 ${txt} ${diff > 0 ? "앞" : "뒤"}`;
    badge.className = "badge sim";
  } else {
    badge.textContent = S.sim ? "실제 시각" : S.simDisabled ? "가상 상황 꺼짐" : "";
    badge.className = "badge";
  }
}

function renderClockControls() {
  const ok = canSim();
  for (const b of document.querySelectorAll("#clock-shift button, #clock-apply, #clock-reset, #clock-plan")) b.disabled = !ok;
  $("clock-input").disabled = !ok;
  if (ok && document.activeElement !== $("clock-input")) $("clock-input").value = toLocalKst(simNow());
  const note = $("clock-note");
  note.hidden = ok;
  note.textContent = S.simDisabled ? "가상 상황 꺼짐 — 서버에 SAFETY_SIM_ENABLED=true 필요. 시각·안전 조작은 못 하지만 지도·상태·위치는 된다." : "";
  const t = planStartMs();
  const btn = $("clock-plan");
  btn.hidden = !t;
  if (t) btn.textContent = `계획 날짜로: ${fmtKst(t, { seconds: false })} (첫 일정 10분 전)`;
}

// ---------- ② 안전 ----------

function renderSafety() {
  const sim = S.sim;
  const ok = canSim();
  $("src-live").classList.toggle("on", sim?.mode === "live" || S.simDisabled);
  $("src-mock").classList.toggle("on", sim?.mode === "sim");
  $("src-live").disabled = !ok || !sim.switchable;
  $("src-mock").disabled = !ok || (!sim.switchable && sim.mode === "sim");
  const note = $("src-note");
  if (S.simDisabled) {
    note.hidden = false;
    note.textContent = "가상 상황 꺼짐 — SAFETY_SIM_ENABLED=true 필요";
  } else if (sim && !sim.switchable) {
    note.hidden = false;
    note.textContent = "실제 ↔ 가상 전환 불가: 서버가 SAFETY_SOURCE=mock 이고 SAFETY_SIM_ENABLED 가 꺼져 있다 (시나리오·시각만 바꿀 수 있음)";
  } else note.hidden = true;

  const chips = $("scenario-chips");
  const names = Object.keys(sim?.scenarios || {});
  chips.replaceChildren(...names.map((n) => h("button.chip-btn", {
    class: sim.scenario === n ? (sim.mode === "sim" ? "on" : "remembered") : "",
    disabled: !ok,
    title: `${sim.scenarios[n]} — 시간표를 이 프리셋으로 바꾼다 (지금 기준, 시각을 옮겨도 밀리지 않음)`,
    onclick: () => setScenario(n, untilMs()),
  }, SCENARIO_KO[n] ? `${SCENARIO_KO[n]}` : n)));
  for (const b of $("until-seg").querySelectorAll("button")) {
    b.classList.toggle("on", b.dataset.until === untilChoice);
    b.disabled = !ok;
  }
  $("until-time").hidden = untilChoice !== "at";
  const desc = $("scenario-desc");
  if (sim) {
    const seg = sim.segment;
    const now = `지금 ${KIND_KO[sim.scenario] || sim.scenario}${seg ? ` (${span(seg)})` : ""}`;
    desc.textContent = sim.mode === "sim"
      ? `가상 상황 · ${now}. 칩은 시간표 프리셋 (끝: ${UNTIL_KO[untilChoice]})`
      : `실제 데이터 사용 중 — 시간표는 가상 상황에서만 적용 (가상으로 바꾸면 ${now}).`;
  } else desc.textContent = "";
  renderSimTimeline(sim, ok);
  $("btn-scan").disabled = !ok || !$("scan-busy").hidden;

  const st = S.status;
  const box = $("safety-summary");
  if (!st) { box.replaceChildren(h("p.muted", "안전 상황을 불러오는 중…")); return; }
  const failed = Object.entries(st.data_status || {}).filter(([, v]) => !v.ok).map(([k, v]) => `${SOURCE_KO[k] || k}${v.error ? ` (${v.error.slice(0, 40)})` : ""}`);
  const notes = [...new Set(Object.values(st.data_status || {}).map((v) => v.note).filter(Boolean))];
  const counts = { safe: 0, caution: 0, danger: 0, null: 0 };
  for (const z of st.zones) counts[z.risk ?? "null"] += 1;
  box.replaceChildren(...[
    h("div.kv-line", h("span.k", "전체"), h(`span.risk.r-${st.overall_risk ?? "null"}`, riskKo(st.overall_risk)),
      h("span.muted", ` 구역 안전 ${counts.safe} · 주의 ${counts.caution} · 위험 ${counts.danger}${counts.null ? ` · 보류 ${counts.null}` : ""}`)),
    h("div.kv-line", h("span.k", "특보"), st.warnings.length ? h("span.danger-text", st.warnings.join(", ")) : h("span.muted", "없음"),
      st.preliminary_warnings?.length ? h("span.muted", ` (예비: ${st.preliminary_warnings.join(", ")})`) : null),
    h("div.kv-line", h("span.k", "12시간"), st.next_hours.length
      ? h("ul.hz", ...st.next_hours.slice(0, 5).map((x) => h("li", h(`span.risk.r-${x.level === "info" ? "info" : x.level}`, x.level === "info" ? "참고" : riskKo(x.level)), ` ${x.reason}`)),
        st.next_hours.length > 5 ? h("li.muted", `그 밖에 ${st.next_hours.length - 5}건`) : null)
      : h("span.muted", "위험 신호 없음")),
    failed.length ? h("div.kv-line", h("span.k", "못 받음"), h("span.warn-text", failed.join(", "))) : null,
    notes.length ? h("div.kv-line.muted", notes.join(" · ")) : null,
    h("div.kv-line.muted", `피할 구역 ${S.avoid?.zones?.length || 0}곳 · 기준 ${fmtKst(ms(st.now))} · ${st.mode === "sim" ? `가상(${st.scenario})` : "실제 데이터"}`),
  ].filter(Boolean));
}

const SCENARIO_KO = { calm: "평상시", rain: "비", wind_tomorrow: "내일 강풍", flood: "침수 센서", storm: "폭풍" };
const KIND_KO = { storm: "폭풍", flood: "침수", wind: "강풍", rain: "비", calm: "평상시" };
const UNTIL_KO = { none: "계속", 60: "1시간 뒤", 120: "2시간 뒤", at: "시각 지정" };
const DAY_MS = 24 * 3600e3;
let untilChoice = "none"; // 시나리오 칩의 끝: none | 60 | 120 | at

const hhmm = (t) => fmtKst(t, { date: false, seconds: false });
const kstDay = (t) => kstIso(t).slice(0, 10);
const dayStart = (t) => Date.parse(`${kstDay(t)}T00:00:00+09:00`);

/** "15:20~17:00" (가상 시각과 다른 날이면 날짜도). */
function span(seg) {
  const a = ms(seg.start);
  const b = ms(seg.end);
  const day = kstDay(a) === kstDay(simNow()) ? "" : `${fmtKst(a).slice(0, 5)} `;
  return `${day}${hhmm(a)}~${kstDay(b) === kstDay(a) ? hhmm(b) : fmtKst(b, { seconds: false })}`;
}

/** 시나리오 칩의 끝 시각 (ms) 또는 null(계속). 시각 지정이 지금보다 이르면 다음 날. */
function untilMs() {
  if (untilChoice === "none") return null;
  const now = simNow();
  if (untilChoice !== "at") return now + Number(untilChoice) * 60e3;
  const v = $("until-time").value;
  if (!v) return null;
  const t = Date.parse(`${kstDay(now)}T${v}:00+09:00`);
  return t <= now ? t + DAY_MS : t;
}

/** 날씨 시간표: 그날(가상 시각 날짜) 0~24시 막대(구간 색칠·지금 선·일정 눈금) + 구간 목록(삭제). */
function renderSimTimeline(sim, ok) {
  const box = $("sim-timeline");
  const note = $("timeline-note");
  const kinds = Object.keys(sim?.kinds || {});
  const sel = $("seg-kind");
  if (sel.options.length !== kinds.length) {
    sel.replaceChildren(...kinds.map((k) => h("option", { value: k, title: sim.kinds[k] }, KIND_KO[k] || k)));
  }
  for (const id of ["seg-kind", "seg-start", "seg-end", "seg-add-btn"]) $(id).disabled = !ok;
  if (!sim) { box.replaceChildren(); note.hidden = true; return; }
  note.hidden = sim.mode === "sim";
  note.textContent = "실제 데이터 모드: 시간표는 가상 상황에서만 적용된다 (기억해 두었다가 [가상 상황] 으로 바꾸면 씀).";

  const now = simNow();
  const d0 = dayStart(now);
  const pct = (t) => Math.min(100, Math.max(0, ((t - d0) / DAY_MS) * 100));
  // 내용이 같으면 다시 만들지 않고 지금 선만 옮긴다 (안전 상황을 몇 초마다 다시 그려서, 다시 만들면 [삭제] 클릭이 사라진다)
  const items = dayItems().filter((it) => it.status !== "skipped" && it.start_at);
  const sig = JSON.stringify([sim.mode, ok, kstDay(now), sim.timeline, sim.segment,
    (sim.timeline || []).filter((x) => ms(x.end) <= now).length, items.map((it) => [it.start_at, it.place_id])]);
  const nowLine = box.querySelector(".tl-now");
  if (box.dataset.sig === sig && nowLine) {
    nowLine.style.left = `${pct(now)}%`;
    nowLine.title = `지금 ${hhmm(now)}`;
    return;
  }
  box.dataset.sig = sig;
  const bar = h(`div.tl-bar${sim.mode === "sim" ? "" : ".off"}`, { title: `${kstDay(now)} 0~24시 (가상 시각 날짜)` });
  for (const x of sim.timeline || []) {
    const a = pct(ms(x.start));
    const b = pct(ms(x.end));
    if (b <= a) continue; // 그날 밖
    bar.append(h(`div.tl-seg.k-${x.kind}`, {
      style: { left: `${a}%`, width: `${b - a}%` }, title: `${KIND_KO[x.kind] || x.kind} ${span(x)}`,
    }));
  }
  for (const it of items) {
    const t = Date.parse(`${kstDay(now)}T${it.start_at.slice(0, 5)}:00+09:00`);
    bar.append(h("div.tl-item", { style: { left: `${pct(t)}%` }, title: `${it.start_at.slice(0, 5)} ${it.place?.title || it.place_id}` }));
  }
  bar.append(h("div.tl-now", { style: { left: `${pct(now)}%` }, title: `지금 ${hhmm(now)}` }));
  const axis = h("div.tl-axis", ...[0, 6, 12, 18, 24].map((x) => h("span", { style: { left: `${(x / 24) * 100}%` } }, String(x))));

  const cur = sim.segment;
  const list = (sim.timeline || []).map((x, i) => {
    const isCur = cur && x.kind === cur.kind && x.start === cur.start && x.end === cur.end;
    const past = ms(x.end) <= now;
    return h(`li${isCur ? ".cur" : past ? ".past" : ""}`,
      h(`span.tl-dot.k-${x.kind}`), ` ${KIND_KO[x.kind] || x.kind} ${span(x)}`,
      isCur ? h("span.muted", " ← 지금") : past ? h("span.muted", " (지남)") : null,
      h("button.link", {
        disabled: !ok, title: "이 구간 삭제",
        onclick: () => setTimeline((sim.timeline || []).filter((_, j) => j !== i), `구간 삭제: ${KIND_KO[x.kind] || x.kind} ${span(x)}`),
      }, "삭제"));
  });
  box.replaceChildren(
    h("div.tl-head", h("span.muted", `날씨 시간표 ${kstDay(now).slice(5).replace("-", "/")} (구간 밖은 평상시)`),
      h("span.tl-legend", ...["storm", "flood", "wind", "rain"].map((k) => h("span", h(`span.tl-dot.k-${k}`), KIND_KO[k])),
        h("span", h("span.tl-tick"), "일정"))),
    bar, axis,
    list.length ? h("ul.tl-list", ...list) : h("p.muted.tl-empty", "시간표 비어 있음 — 지금은 평상시"),
  );
}

/** [구간 추가]: 가상 시각 날짜의 시작~끝 (끝이 시작보다 이르면 다음 날). */
function addSegment() {
  const sim = S.sim;
  const a = $("seg-start").value;
  const b = $("seg-end").value;
  if (!sim || !a || !b) return;
  const day = kstDay(simNow());
  const start = Date.parse(`${day}T${a}:00+09:00`);
  let end = Date.parse(`${day}T${b}:00+09:00`);
  if (end <= start) end += DAY_MS;
  const kind = $("seg-kind").value;
  setTimeline([...(sim.timeline || []), { kind, start: kstIso(start), end: kstIso(end) }],
    `구간 추가: ${KIND_KO[kind] || kind} ${a}~${b}${b <= a ? " (다음 날)" : ""}`);
}

// ---------- ③ 위치 · 여행 ----------

function renderTrip() {
  const t = S.trip;
  const box = $("trip-state");
  const chip = $("trip-chip");
  const hasPlan = S.planId != null;
  $("btn-start").disabled = !hasPlan || !!S.motion || !!t?.active;
  $("btn-end").disabled = !t?.active || !!S.motion;
  $("btn-stay").disabled = !t?.active || !!S.motion;
  if (!t) {
    chip.textContent = hasPlan ? "여행 -" : "계획 없음";
    chip.className = "chip";
    box.replaceChildren(h("p.muted", hasPlan ? "불러오는 중…" : "계획이 없습니다. run_demo.py 로 데모 계획을 만드세요."));
    return;
  }
  if (t.active) {
    chip.textContent = `● 동행 중 ${t.day}일차`;
    chip.className = "chip ok";
  } else {
    chip.textContent = t.ended_at ? `여행 종료 (${t.ended_reason === "auto" ? "자동" : "사용자"})` : "여행 전";
    chip.className = "chip";
  }
  const cur = t.current || {};
  const curItem = cur.item_id ? itemTitle(cur.item_id) : null;
  const curText = !t.active ? "-" : cur.state === "visiting" ? `머무는 중 · ${curItem} (${cur.since}부터)`
    : cur.state === "moving" ? `이동 중${curItem ? ` · ${curItem}에서 떠남 (${cur.since})` : ""}` : "대기 (아직 도착 전)";
  const nx = t.next;
  const delay = t.delay_minutes;
  const late = delay == null ? "" : delay > 0 ? `${delay}분 늦음` : delay < 0 ? `${-delay}분 여유` : "예정대로";
  const loc = t.location;
  box.replaceChildren(
    h("div.kv-line", h("span.k", "상태"), t.active ? h("b", `${t.day}일차 ${t.date} 여행 중`) : h("span.muted", chip.textContent),
      h("span.muted", ` · trip 지금 ${t.now.slice(11, 19)} (${t.clock === "sim" ? "가상" : "시스템"})`)),
    h("div.kv-line", h("span.k", "지금"), curText),
    h("div.kv-line", h("span.k", "다음"), nx ? `${nx.title || `항목 ${nx.item_id}`} · 예정 ${nx.planned_start || "-"} · 예상 도착 ${nx.eta || "-"}` : "-",
      late ? h(`span.${delay > 0 ? "warn-text" : "muted"}`, ` (${late})`) : null),
    h("div.kv-line", h("span.k", "위치"), loc
      ? h("button.link", { onclick: () => focus(loc.lat, loc.lng), title: "지도에서 보기" },
        `${loc.lat.toFixed(5)}, ${loc.lng.toFixed(5)} ±${Math.round(loc.accuracy || 0)}m · ${loc.recorded_at.slice(11, 19)} (${loc.source})`)
      : h("span.muted", "없음")),
  );
}

function itemTitle(id) {
  for (const d of S.plan?.days || []) for (const it of d.items) if (it.id === id) return it.place?.title || `항목 ${id}`;
  return `항목 ${id}`;
}

function renderItinerary() {
  const ol = $("itinerary");
  const items = dayItems();
  const dis = !tripActive() || !!S.motion;
  const day = S.plan?.days?.find((d) => d.day === (S.trip?.day || 1)) || S.plan?.days?.[0];
  ol.replaceChildren(
    ...(day ? [h("li.head", `${day.day}일차 ${day.date} (${day.weekday}) 일정${day.end_at ? ` · 종료 ${day.end_at}` : ""}`)] : []),
    ...items.map((it) => {
      const p = it.place;
      const title = p?.title || it.place_id;
      const visiting = S.trip?.current?.state === "visiting" && S.trip.current.item_id === it.id;
      return h(`li.item.st-${it.status}`, { class: visiting ? "here" : "" },
        h("div.it-main",
          h("b", title),
          h("span.muted", ` ${it.start_at || "--:--"}~${it.end_at || "--:--"} · ${ITEM_STATUS_KO[it.status] || it.status}`
            + (it.arrived_at ? ` · 도착 ${it.arrived_at}` : "") + (it.done_at ? ` · 완료 ${it.done_at}` : ""))),
        p ? h("div.it-actions",
          h("button.sm", { disabled: dis, title: "순간이동", onclick: () => teleport(p.lat, p.lng) }, "이동"),
          h("button.sm", { disabled: dis, title: "도보 경로로 걸어가기", onclick: () => travel(p.lat, p.lng, "walk", title) }, "걸어가기"),
          h("button.sm", { disabled: dis, title: `여기서 ${Number($("stay-min").value) || 3}분 머물기`, onclick: () => stay(Math.max(1, Number($("stay-min").value) || 3), p.lat, p.lng, title) }, "머물기"),
          h("button.sm.ghost", { title: "지도에서 보기", onclick: () => focus(p.lat, p.lng) }, "⌖")) : null);
    }),
  );
  if (!items.length) ol.replaceChildren(h("li.muted", S.plan ? "이 날 일정이 없습니다" : "-"));
}

function renderMotion() {
  const m = S.motion;
  const bar = $("motion-bar");
  bar.hidden = !m;
  if (!m) return;
  $("motion-text").textContent = `${m.stop ? "정지하는 중… " : ""}${m.label} · ${m.done}/${m.total}점${m.ignored ? ` · 버려진 점 ${m.ignored}` : ""}`;
  $("motion-progress").value = m.total ? m.done / m.total : 0;
}

// ---------- ④ 타임라인 ----------

function renderTimeline() {
  const list = [...S.events.values()].sort((a, b) => b.id - a.id);
  $("tl-count").textContent = list.length ? `(${list.length})` : "";
  const ul = $("timeline");
  if (!list.length) {
    ul.replaceChildren(h("li.muted", S.planId == null ? "-" : tripActive() ? "아직 이벤트가 없습니다" : "여행을 시작하면 이벤트가 쌓입니다"));
    return;
  }
  ul.replaceChildren(...list.map((e) => {
    const agent = e.agent?.proposal_id ? `Agent 제안 #${e.agent.proposal_id} 보냄`
      : e.agent?.message_id ? `Agent 메시지 #${e.agent.message_id}` : null;
    const why = e.status === "failed" || (e.status === "queued" && e.last_error)
      ? `${e.status === "failed" ? "전송 실패" : "대기"} ${e.attempts}번: ${e.last_error || ""}` : null;
    const li = h(`li.ev.sev-${e.severity}.lv-${e.level}`,
      h("div.ev-head",
        h("span.time", e.now.slice(11, 19)),
        h("span.type", EVENT_KO[e.type] || e.type),
        h(`span.lv.lv${e.level}`, { title: "개입 레벨 (0 상태 · 1 알림 · 2 카드 · 3 Agent)" }, `L${e.level}`),
        h(`span.st.st-${e.status}`, { title: e.status }, STATUS_KO[e.status] || e.status),
        h("span.id.muted", `#${e.id}`)),
      h("div.msg", e.message),
      agent || e.card || why ? h("div.meta",
        agent ? h("span.agent", agent) : null,
        e.card ? h("span", `카드: ${e.card.kind === "docent" ? "해설" : e.card.kind}`) : null,
        why ? h("span.warn-text", why) : null) : null);
    if (e.status === "failed" || e.last_error) li.title = `${e.last_error || ""}\n${JSON.stringify(e.payload).slice(0, 600)}`;
    else li.title = JSON.stringify(e.payload).slice(0, 600);
    return li;
  }));
}

function renderAlerts() {
  const list = S.alerts.slice(0, 8);
  $("alerts-count").textContent = S.alerts.length ? `(${S.alerts.length})` : "";
  $("alerts").replaceChildren(...(list.length ? list.map((a) => h("li",
    h("span.time", fmtKst(ms(a.checked_at || a.created_at), { seconds: false })), " ",
    h(`span.risk.r-${a.level}`, riskKo(a.level)), " ",
    a.needs_action ? h("span.warn-text", "조치 필요 ") : null,
    h("span.muted", `${a.status === "new" ? "새 경고" : "확인됨"} · `), a.message)) : [h("li.muted", "없음")]));
}
