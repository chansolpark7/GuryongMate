// 날씨 카드 (지도 위 오른쪽 아래): 실시간 DT ↔ 가상 시나리오, 날씨 아이콘 5개, 한 줄 요약 (redesign.md §2.4)
import { S, canSim, emit, on, prefs, setPref, simNow } from "./store.js";
import { CONTINUE_HOURS, addWeather, onBusy, setSource } from "./actions.js";
import { KIND_META, kindMeta } from "./ui.js";
import { fill, fmtKst, h, kstIso, riskKo } from "./util.js";

const $ = (id) => document.getElementById(id);
const DAY_MS = 24 * 3600e3;
const SOURCE_KO = { sensors: "센서", breakwater: "방파제 CCTV", uv: "자외선", forecast: "예보", warnings: "특보", tide: "조위" };

let detailsOpen = false;
let scanText = null;
let choiceTime = null;
let lastSig = "";

export function initWeather() {
  on("sim safety prefs arm", render);
  onBusy((kind, text) => { if (kind === "scan") { scanText = text; render(); } });
  render();
}

/** 날씨 그리기 도구 (S.arm = 종류, null = 끔). 날씨 아이콘을 고르면 켜지고, 같은 아이콘·Esc 로 끈다.
 * 켜져 있으면 타임라인 날씨 줄을 끌어 그 구간을 그린다 (timeline.js). ☀️ 평상시는 지우개. */
export function arm(kind) {
  if (S.arm === kind) return;
  S.arm = kind;
  emit("arm");
}

const hm = (t) => fmtKst(t, { date: false, seconds: false });

function render() {
  const box = $("weather");
  if (box.contains(document.activeElement) && document.activeElement.tagName === "INPUT") return; // 입력 중에는 그대로
  const sim = S.sim;
  const ok = canSim();
  const open = prefs.weatherOpen;
  const live = !sim || sim.mode === "live";
  // 내용이 같으면 다시 만들지 않는다 (몇 초마다 다시 만들면 누르는 중인 버튼이 사라져 클릭이 빠진다)
  const st = S.status;
  const sig = JSON.stringify([open, live, ok, S.simDisabled, sim?.scenario, sim?.switchable, S.arm, scanText,
    st && [st.warnings, st.preliminary_warnings, st.next_hours, st.zones.map((z) => z.risk), st.data_status && Object.values(st.data_status).map((v) => v.ok)],
    S.avoid?.zones?.length]);
  if (sig === lastSig && box.firstChild) return;
  lastSig = sig;
  box.classList.toggle("collapsed", !open);
  const head = h("div.w-head",
    h("b", "날씨"),
    h("span.w-sum", summaryLine()),
    h("button.icon-btn", {
      title: open ? "접기" : "펼치기", "aria-expanded": String(open),
      onclick: () => setPref("weatherOpen", !open),
    }, open ? "▾" : "▴"));
  if (!open) { box.replaceChildren(head); return; }

  const seg = h("div.seg2", { role: "group", "aria-label": "데이터" },
    h(`button${live ? ".on" : ""}`, {
      disabled: !ok || (!sim.switchable && !live),
      title: sim && !sim.switchable ? "서버가 실제 데이터로 바꿀 수 없게 떠 있어요 (SAFETY_SIM_ENABLED)" : "실제 구룡포 데이터",
      onclick: () => !live || S.simDisabled ? setSource("live") : null,
    }, "📡 실시간 DT"),
    h(`button${!live ? ".on" : ""}`, { disabled: !ok, onclick: () => (live ? setSource("mock") : null) }, "🎬 가상 시나리오"));

  const activeKind = !live ? sim.scenario : null;
  const icons = h(`div.w-icons${live ? ".dim" : ""}`,
    ...Object.entries(KIND_META).map(([k, m]) => h(`button.w-icon${activeKind === k ? ".on" : ""}${S.arm === k ? ".armed" : ""}`, {
      disabled: !ok,
      title: `${m.ko} — ${k === "calm" ? "타임라인 날씨 줄을 끌어 지우기" : "타임라인 날씨 줄을 끌어 그리기"} 또는 지금부터 시간 고르기${live ? " (가상 시나리오로 바뀜)" : ""}`,
      "aria-pressed": String(S.arm === k),
      onclick: () => arm(S.arm === k ? null : k),
    }, h("span.emo", m.icon), h("span.lbl", m.ko))));

  fill(box, head, seg,
    live ? h("p.w-note", S.simDisabled ? "실제 데이터 (가상 상황 꺼짐)" : "지금 구룡포 실제 데이터 (E-UM 센서·CCTV·기상청)") : null,
    icons,
    S.arm ? choicePanel(S.arm) : null,
    S.arm ? h("p.w-armed", `✏️ 그리기 도구 켜짐 — 아래 타임라인 날씨 줄을 끌어 ${kindMeta(S.arm).icon} ${kindMeta(S.arm).ko}${S.arm === "calm" ? "으로 지우세요" : " 구간을 그리세요"} · `,
      h("button.link", { onclick: () => arm(null) }, "끄기 (Esc)")) : null,
    scanText ? h("p.w-busy", `⟳ ${scanText}`) : null,
    details());
}

/** "지금부터 [1시간] [2시간] [계속] / 끝 시각 지정" + [타임라인에 그리기]. */
function choicePanel(kind) {
  const m = kindMeta(kind);
  const now = simNow();
  const go = (end) => { arm(null); addWeather(kind, simNow(), end); };
  // 몇 초마다 다시 그리므로 입력칸은 고른 동안 같은 것을 쓴다 (입력 중인 값이 사라지지 않게)
  if (!choiceTime || choiceTime.dataset.kind !== kind) {
    choiceTime = h("input", { type: "time", "aria-label": "끝 시각", value: hm(now + 60 * 60e3) });
    choiceTime.dataset.kind = kind;
  }
  const time = choiceTime;
  const at = () => {
    if (!time.value) return;
    let t = Date.parse(`${kstIso(simNow()).slice(0, 10)}T${time.value}:00+09:00`);
    if (t <= simNow()) t += DAY_MS;
    go(t);
  };
  return h("div.w-choice",
    h("div.w-choice-head", h("b", `${m.icon} ${m.ko}`), h("span.muted", ` · 지금(${hm(now)})부터`)),
    h("div.w-row",
      h("button.btn", { onclick: () => go(simNow() + 60 * 60e3) }, "1시간"),
      h("button.btn", { onclick: () => go(simNow() + 120 * 60e3) }, "2시간"),
      h("button.btn", {
        title: kind === "calm" ? "지금부터 잡힌 날씨를 모두 비워요" : `${CONTINUE_HOURS}시간`,
        onclick: () => go(kind === "calm" ? Infinity : simNow() + CONTINUE_HOURS * 3600e3),
      }, "계속")),
    h("div.w-row",
      h("span.muted.small", "끝"), time, h("button.btn", { onclick: at }, "적용")));
}

function counts() {
  const st = S.status;
  const c = { danger: 0, caution: 0 };
  for (const z of st?.zones || []) if (c[z.risk] != null) c[z.risk] += 1;
  return c;
}

function summaryLine() {
  const st = S.status;
  if (!st) return "불러오는 중…";
  const c = counts();
  const parts = [];
  if (st.warnings.length) parts.push(`특보 ${st.warnings.length}건`);
  if (c.danger) parts.push(`위험 구역 ${c.danger}곳`);
  if (c.caution) parts.push(`주의 ${c.caution}곳`);
  const failed = Object.values(st.data_status || {}).filter((v) => !v.ok).length;
  if (!parts.length) return h("span.ok-text", failed ? `특이사항 없음 · 못 받은 데이터 ${failed}` : "✓ 특이사항 없음");
  return h(`span.${c.danger || st.warnings.length ? "danger-text" : "warn-text"}`, `⚠ ${parts.join(" · ")}`);
}

function details() {
  const st = S.status;
  if (!st) return null;
  const failed = Object.entries(st.data_status || {}).filter(([, v]) => !v.ok).map(([k]) => SOURCE_KO[k] || k);
  const body = h("div.w-details",
    h("div.wkv", h("span.k", "특보"), st.warnings.length ? h("span", st.warnings.join(", ")) : h("span.muted", "없음"),
      st.preliminary_warnings?.length ? h("span.muted", ` (예비: ${st.preliminary_warnings.join(", ")})`) : null),
    h("div.wkv", h("span.k", "12시간"), st.next_hours.length
      ? h("ul.hz", ...st.next_hours.slice(0, 5).map((x) => h("li",
        h(`span.lv-dot.r-${x.level}`), x.level === "info" ? "참고 " : `${riskKo(x.level)} `, x.reason)),
      st.next_hours.length > 5 ? h("li.muted", `그 밖에 ${st.next_hours.length - 5}건`) : null)
      : h("span.muted", "위험 신호 없음")),
    h("div.wkv", h("span.k", "피할 구역"), `${S.avoid?.zones?.length || 0}곳`),
    failed.length ? h("div.wkv", h("span.k", "못 받음"), h("span.warn-text", failed.join(", "))) : null);
  const d = h("details.w-more", { open: detailsOpen }, h("summary", "자세히"), body);
  d.addEventListener("toggle", () => { detailsOpen = d.open; });
  return d;
}
