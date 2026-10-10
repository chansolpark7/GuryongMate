// 개발자 정보 서랍 (👤 → 개발자 정보): 화면에서 뺀 상세를 모두 여기에 (redesign.md §2.6)
// 연결·LLM 공급자, 조작 스위치(자동 점검·틱, 배속), 시각 직접 입력, 시간표 원본·프리셋, 안전 데이터, 위치 전송 결과, 원시 이벤트, planner 경고, 도움말
import { api } from "./api.js";
import { S, canSim, on, prefs, setPref, simNow } from "./store.js";
import {
  deleteSegment, onBusy, resetClock, scanAndTick, setClock, setScenario, setTimeline, shiftClock, stay,
} from "./actions.js";
import { STATUS_KO, eventMeta, kindMeta } from "./ui.js";
import { fill, fmtKst, h, kstIso, ms, parseLocalKst, riskKo, toLocalKst } from "./util.js";

const $ = (id) => document.getElementById(id);
const SOURCE_KO = { sensors: "센서", breakwater: "방파제 CCTV", uv: "자외선", forecast: "예보", warnings: "특보", tide: "조위" };
const SCENARIO_KO = { calm: "평상시", rain: "비", wind_tomorrow: "내일 강풍", flood: "침수 센서", storm: "폭풍" };
const DAY_MS = 24 * 3600e3;
const expanded = new Set(); // 펼친 이벤트 id
let scanText = null;

export function initDev() {
  on("conn sim", () => isOpen() && (renderConn(), renderTimeline(), renderClockInput()));
  on("safety", () => isOpen() && renderSafety());
  on("trip motion", () => isOpen() && renderTrip());
  on("events", () => isOpen() && renderEvents());
  on("alerts", () => isOpen() && renderAlerts());
  onBusy((kind, text) => {
    if (kind !== "scan") return;
    scanText = text;
    if (isOpen()) { $("dev-scan-busy").textContent = text || ""; $("dev-scan").disabled = !!text || !canSim(); }
  });
}

const isOpen = () => $("dlg-dev").open;

export function openDev() {
  const dlg = $("dlg-dev");
  dlg.replaceChildren(skeleton());
  renderAll();
  dlg.showModal();
}

function renderAll() {
  renderConn(); renderTimeline(); renderClockInput(); renderSafety(); renderTrip(); renderEvents(); renderAlerts();
}

const sec = (title, ...body) => h("details.dev-sec", { open: true }, h("summary", title), ...body);

function skeleton() {
  const ok = canSim();
  const sim = S.sim;
  // 조작 스위치
  const auto = h("input", { type: "checkbox", checked: prefs.autoScan, onchange: (e) => setPref("autoScan", e.target.checked) });
  const autoStay = h("input", { type: "checkbox", checked: prefs.autoStay, onchange: (e) => setPref("autoStay", e.target.checked) });
  const speed = h("select", { onchange: (e) => setPref("speed", Number(e.target.value)) },
    ...[[0, "즉시"], [5, "5배"], [10, "10배"], [20, "20배"], [60, "60배"], [120, "120배"]].map(([v, t]) => h("option", { value: String(v) }, t)));
  speed.value = String(prefs.speed);
  const stayMin = h("input", { type: "number", min: 1, max: 180, value: 3, "aria-label": "머무는 분", style: { width: "72px" } });
  const clockIn = h("input#dev-clock", { type: "datetime-local", step: 1, "aria-label": "가상 시각 직접 입력 (KST)", disabled: !ok });
  // 시간표 구간 추가
  const kinds = Object.keys(sim?.kinds || {});
  const kindSel = h("select", { "aria-label": "구간 종류" }, ...kinds.map((k) => h("option", { value: k, title: sim.kinds[k] }, kindMeta(k).ko)));
  const a = h("input", { type: "time", required: true, "aria-label": "시작" });
  const b = h("input", { type: "time", required: true, "aria-label": "끝 (포함 안 함)" });
  const addSeg = () => {
    if (!a.value || !b.value || !S.sim) return;
    const day = kstIso(simNow()).slice(0, 10);
    const start = Date.parse(`${day}T${a.value}:00+09:00`);
    let end = Date.parse(`${day}T${b.value}:00+09:00`);
    if (end <= start) end += DAY_MS;
    setTimeline([...(S.sim.timeline || []), { kind: kindSel.value, start: kstIso(start), end: kstIso(end) }],
      `구간 추가: ${kindMeta(kindSel.value).ko} ${a.value}~${b.value}${b.value <= a.value ? " (다음 날)" : ""}`);
  };
  // 프리셋 (시나리오) + 끝
  let until = "none";
  const untilTime = h("input", { type: "time", hidden: true, "aria-label": "끝나는 시각" });
  const untilSeg = h("div.seg2.small", ...[["none", "계속"], ["60", "1시간"], ["120", "2시간"], ["at", "시각 지정"]].map(([v, t]) =>
    h(`button${v === until ? ".on" : ""}`, {
      type: "button", disabled: !ok,
      onclick: (e) => {
        until = v;
        for (const x of untilSeg.children) x.classList.toggle("on", x === e.currentTarget);
        untilTime.hidden = v !== "at";
        if (v === "at" && !untilTime.value) untilTime.value = fmtKst(simNow() + 3600e3, { date: false, seconds: false });
      },
    }, t)));
  const untilMs = () => {
    if (until === "none") return null;
    const now = simNow();
    if (until !== "at") return now + Number(until) * 60e3;
    if (!untilTime.value) return null;
    const t = Date.parse(`${kstIso(now).slice(0, 10)}T${untilTime.value}:00+09:00`);
    return t <= now ? t + DAY_MS : t;
  };
  const presets = h("div.chips", ...Object.keys(sim?.scenarios || {}).map((n) => h("button.btn.sm", {
    disabled: !ok, title: `${sim.scenarios[n]} — 시간표를 이 프리셋으로 바꿔요 (지금 기준)`,
    onclick: () => setScenario(n, untilMs()),
  }, `${n} · ${SCENARIO_KO[n] || n}`)));

  return h("div.dev",
    h("div.dev-head", h("h2", "🛠 개발자 정보"), h("button.icon-btn", { onclick: () => $("dlg-dev").close(), "aria-label": "닫기" }, "✕")),
    sec("연결", h("ul.kv#dev-conn")),
    sec("조작",
      h("label.check", auto, " 날씨를 바꾸면 바로 안전 점검 + 틱 (끄면 planner 주기를 기다림)"),
      h("label.check", autoStay, " 도착하면 자동으로 머물기 (일정 장소로 걷기·차가 닿으면 2분, 도착 판정이 바로 남)"),
      h("div.row", h("span.muted", "걷기·머물기 배속"), speed, h("span.muted.small", "실제 1초 = 가상 N초")),
      h("div.row", stayMin, h("span.muted", "분"),
        h("button.btn.sm", { onclick: () => stay(Math.min(180, Math.max(1, Number(stayMin.value) || 3))) }, "지금 위치에서 머물기")),
      h("div.row",
        h("button.btn.sm#dev-scan", { disabled: !ok || !!scanText, title: "POST /planner/safety/scan → POST /trip/plans/{id}/tick?now=", onclick: () => scanAndTick() }, "안전 점검 지금 + 틱"),
        h("span.muted.small#dev-scan-busy", scanText || "")),
      h("div.row", clockIn,
        h("button.btn.sm", { disabled: !ok, onclick: () => { const t = parseLocalKst(clockIn.value); if (Number.isFinite(t)) setClock(t); } }, "적용"),
        h("button.btn.sm", { disabled: !ok, onclick: () => shiftClock(5) }, "+5분"),
        h("button.btn.sm", { disabled: !ok, onclick: () => shiftClock(60) }, "+1시간"),
        h("button.btn.sm", { disabled: !ok, onclick: resetClock }, "실제 시각으로"))),
    sec("날씨 시간표 (원본)",
      h("ul.dev-tl#dev-tl"),
      h("div.row", kindSel, a, h("span.muted", "~"), b, h("button.btn.sm", { disabled: !ok, onclick: addSeg, title: "PUT /safety/sim {timeline}" }, "구간 추가")),
      h("div.row", h("span.muted.small", "프리셋 (PUT {scenario, until}) — 시간표 전체를 바꿈. 끝:"), untilSeg, untilTime),
      presets),
    sec("안전 데이터", h("div#dev-safety")),
    sec("여행·위치", h("div#dev-trip")),
    sec("원시 이벤트", h("div#dev-events")),
    sec("planner 안전 경고", h("ul.dev-alerts#dev-alerts")),
    sec("도움말", h("ul.help",
      h("li", "대시보드는 상태 표시 + 수동 조작이에요. 장면 순서·대화·제안 승인은 scripts/demo/run_demo.py 가 맡아요 (같이 써도 돼요. 단 걸어가기와 run_demo 재생을 동시에 돌리지 마세요)."),
      h("li", "같은 장면을 다시 보려면 초기화가 필요해요 (같은 원인으로는 한 번만 알림). 👤 → 계정 초기화 → [데모 시작 상태로], 또는 run_demo.py --fast-to <단계>."),
      h("li", "시계는 safety 하나. trip·planner 는 5초 캐시하므로 시각을 크게 옮긴 직후엔 몇 초 기다려요 (안전 점검·여행 시작은 자동으로 기다림)."),
      h("li", "위치의 시각은 단조 증가해야 해요. 이전 위치보다 이른 가상 시각으로 돌리면 그 사이 위치는 버려져요."),
      h("li", "도착: 장소 반경(60m + 정확도) 안에 90초 이상. 출발: 반경 밖 2분 이상. 순간이동 한 번으로는 도착이 안 나요 → [여기서 머물기]."),
      h("li", "이벤트 읽음 처리(seen)는 하지 않아요 — 앱의 몫."))));
}

function renderConn() {
  const ul = $("dev-conn");
  if (!ul) return;
  const eng = S.routing?.engine;
  const sim = S.sim;
  const rows = [
    ["서버", `${api.base} · /health ${S.health ? "정상" : S.health === false ? "응답 없음" : "-"}`],
    ["사용자", api.userLabel || "-"],
    ["LLM 공급자 /agent/info", S.agentInfo ? `${S.agentInfo.provider || "-"}${S.agentInfo.model ? ` · ${S.agentInfo.model}` : ""}` : S.agentInfoOk === false ? "응답 없음" : "-"],
    ["가상 상황 /safety/sim", S.simDisabled ? "꺼짐 — SAFETY_SIM_ENABLED=true 필요 (시각·날씨 조작 비활성)"
      : sim ? `${sim.mode === "live" ? "실제 데이터 (live)" : `가상 (sim) · 지금 ${sim.scenario}`} · 전환 ${sim.switchable ? "가능" : "불가"} · 실제와 ${sim.offset_minutes}분 차이` : "-"],
    ["길찾기 /routing/status", eng ? (eng.ok ? `엔진 정상 ${eng.version || ""}` : `엔진 없음 — 직선 근사 (${eng.error || ""})`) : "-"],
    ["관리 토큰", api.adminToken ? "입력됨 (이 브라우저)" : `없음${S.adminNeeded ? " — 403 admin_only 받음" : " (운영 서버의 🔒 API 는 403)"}`],
    ["계획", S.planId != null ? `#${S.planId} ${S.plan?.title || ""} · version ${S.plan?.version ?? "-"}.${S.plan?.progress_version ?? "-"}` : "-"],
  ];
  ul.replaceChildren(...rows.map(([k, v]) => h("li", h("span.k", k), h("span.v", v))));
}

function renderClockInput() {
  const el = $("dev-clock");
  if (el && document.activeElement !== el && canSim()) el.value = toLocalKst(simNow());
}

function renderTimeline() {
  const ul = $("dev-tl");
  if (!ul) return;
  const sim = S.sim;
  const tl = sim?.timeline || [];
  const now = simNow();
  const cur = sim?.segment;
  ul.replaceChildren(...(tl.length ? tl.map((x, i) => {
    const isCur = cur && x.kind === cur.kind && x.start === cur.start && x.end === cur.end;
    return h(`li${isCur ? ".cur" : ms(x.end) <= now ? ".past" : ""}`,
      h("code", x.kind), ` ${fmtKst(ms(x.start), { seconds: false })} ~ ${fmtKst(ms(x.end), { seconds: false })}`,
      isCur ? h("span.muted", " ← 지금") : null,
      h("button.link", { disabled: !canSim(), onclick: () => deleteSegment(i) }, "삭제"));
  }) : [h("li.muted", `비어 있음 — 지금은 평상시${sim?.mode === "live" ? " (실제 데이터 모드: 시간표는 가상에서만 적용)" : ""}`)]));
}

function renderSafety() {
  const box = $("dev-safety");
  if (!box) return;
  const st = S.status;
  if (!st) { box.replaceChildren(h("p.muted", "불러오는 중…")); return; }
  const counts = { safe: 0, caution: 0, danger: 0, null: 0 };
  for (const z of st.zones) counts[z.risk ?? "null"] += 1;
  const notes = [...new Set(Object.values(st.data_status || {}).map((v) => v.note).filter(Boolean))];
  fill(box,
    h("div.kv-line", h("span.k", "전체"), `${riskKo(st.overall_risk)} · 구역 안전 ${counts.safe} · 주의 ${counts.caution} · 위험 ${counts.danger}${counts.null ? ` · 보류 ${counts.null}` : ""} · 센서 ${st.sensors.length}대`),
    h("div.kv-line", h("span.k", "모드"), `${st.mode}${st.scenario ? ` · ${st.scenario}` : ""}${st.segment ? ` (${st.segment.kind} ${st.segment.start.slice(11, 16)}~${st.segment.end.slice(11, 16)})` : ""} · 기준 ${fmtKst(ms(st.now))}`),
    h("div.kv-line", h("span.k", "특보"), st.warnings.join(", ") || "없음", st.preliminary_warnings?.length ? ` (예비: ${st.preliminary_warnings.join(", ")})` : ""),
    h("div.kv-line", h("span.k", "12시간"), st.next_hours.length ? h("ul.hz", ...st.next_hours.map((x) => h("li", `${x.level} · ${x.reason}`))) : "없음"),
    h("table.dev-table", h("tr", h("th", "데이터"), h("th", "상태"), h("th", "비고")),
      ...Object.entries(st.data_status || {}).map(([k, v]) => h("tr",
        h("td", SOURCE_KO[k] || k), h(`td${v.ok ? "" : ".warn-text"}`, v.ok ? "받음" : "실패"), h("td.muted", (v.error || "").slice(0, 80))))),
    notes.length ? h("p.muted.small", notes.join(" · ")) : null,
    h("div.kv-line", h("span.k", "피할 구역"), `${S.avoid?.zones?.length || 0}곳`),
    st.guidance ? h("p.muted.small", st.guidance) : null);
}

function renderTrip() {
  const box = $("dev-trip");
  if (!box) return;
  const t = S.trip;
  const loc = t?.location;
  fill(box,
    t ? h("div.kv-line", h("span.k", "상태"), `active ${t.active} · ${t.day ?? "-"}일차 ${t.date ?? ""} · trip 지금 ${t.now.slice(11, 19)} (${t.clock}) · current ${t.current?.state || "-"}${t.current?.item_id ? ` #${t.current.item_id}` : ""} · next ${t.next ? `#${t.next.item_id} 예정 ${t.next.planned_start || "-"} eta ${t.next.eta || "-"}` : "-"} · 지연 ${t.delay_minutes ?? "-"}분 · session ${t.session_id || "-"}`) : h("p.muted", "-"),
    h("div.kv-line", h("span.k", "위치"), loc ? `${loc.lat.toFixed(5)}, ${loc.lng.toFixed(5)} ±${Math.round(loc.accuracy || 0)}m · ${loc.recorded_at.slice(11, 19)} (${loc.source})` : "없음"),
    S.motion ? h("div.kv-line", h("span.k", "이동"), `${S.motion.kind} ${S.motion.done}/${S.motion.total}점 · 보냄 ${S.motion.sent} · 버림 ${S.motion.ignored}`) : null,
    h("table.dev-table", h("tr", h("th", "가상 시각"), h("th", "종류"), h("th", "accepted"), h("th", "ignored"), h("th", "이벤트")),
      ...(S.locLog.length ? S.locLog.map((r) => h("tr", h("td", fmtKst(r.at, { date: false })), h("td", r.kind), h("td", String(r.accepted)),
        h(`td${r.ignored ? ".warn-text" : ""}`, String(r.ignored)), h("td", String(r.events))))
        : [h("tr", h("td.muted", { colSpan: 5 }, "아직 보낸 위치가 없어요"))])));
}

function renderEvents() {
  const box = $("dev-events");
  if (!box) return;
  const list = [...S.events.values()].sort((a, b) => b.id - a.id);
  if (!list.length) { box.replaceChildren(h("p.muted", "없음")); return; }
  box.replaceChildren(h("table.dev-table.ev",
    h("tr", h("th", "#"), h("th", "시각"), h("th", "종류"), h("th", "L"), h("th", "심각도"), h("th", "상태"), h("th", "Agent·카드")),
    ...list.flatMap((e) => {
      const agent = e.agent ? [e.agent.proposal_id && `제안 #${e.agent.proposal_id}`, e.agent.message_id && `메시지 #${e.agent.message_id}`].filter(Boolean).join(" · ") || "agent" : "";
      const row = h(`tr.clickable.lv-${e.level}`, { onclick: () => { expanded.has(e.id) ? expanded.delete(e.id) : expanded.add(e.id); renderEvents(); } },
        h("td", String(e.id)), h("td", e.now.slice(11, 19)), h("td", `${eventMeta(e.type).icon} ${e.type}`), h("td", `L${e.level}`),
        h("td", e.severity), h(`td.st-${e.status}`, `${STATUS_KO[e.status] || e.status}${e.attempts ? ` (${e.attempts}회)` : ""}`),
        h("td", [agent, e.card ? `카드 ${e.card.kind}` : ""].filter(Boolean).join(" · ")));
      if (!expanded.has(e.id)) return [row];
      return [row, h("tr.payload", h("td", { colSpan: 7 },
        h("div.msg", e.message),
        e.last_error ? h("div.warn-text", `last_error: ${e.last_error}`) : null,
        h("pre", JSON.stringify({ payload: e.payload, agent: e.agent, card: e.card, item_ids: e.item_ids, seen_at: e.seen_at }, null, 1))))];
    })));
}

function renderAlerts() {
  const ul = $("dev-alerts");
  if (!ul) return;
  const list = S.alerts.slice(0, 12);
  ul.replaceChildren(...(list.length ? list.map((a) => h("li",
    h("span.time", fmtKst(ms(a.checked_at || a.created_at), { seconds: false })), " ",
    h(`span.risk.r-${a.level}`, riskKo(a.level)), " ",
    a.needs_action ? h("span.warn-text", "조치 필요 ") : null,
    h("span.muted", `${a.status === "new" ? "새 경고" : "확인됨"} · `), a.message)) : [h("li.muted", "없음")]));
}
