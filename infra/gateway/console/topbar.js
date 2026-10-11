// 상단 바 (시각·날씨·여행 한 줄) · 문제가 있을 때만 노란 띠 · 👤 메뉴 · 여행 칩 메뉴 (redesign.md §2.1)
import { api } from "./api.js";
import { S, canSim, dayItems, itemById, on, simNow, tripActive } from "./store.js";
import { endTrip, moveAndStart, isTripDay, startTrip, stay } from "./actions.js";
import { selectPlan } from "./data.js";
import { focus } from "./map.js";
import { kindMeta, popover, closePopover } from "./ui.js";
import { fmtKst, fmtMinutes, h, josa, ms } from "./util.js";

const $ = (id) => document.getElementById(id);
let handlers = {};

/** handlers: {openSettings, openReset, openDev, logout} — app.js 가 넘긴다. */
export function initTopbar(h_) {
  handlers = h_;
  $("btn-user").addEventListener("click", openUserMenu);
  $("trip-chip").addEventListener("click", openTripMenu);
  on("conn sim", () => { renderWeatherChip(); renderAlertbar(); });
  on("safety", renderWeatherChip);
  on("plans plan trip motion", renderTripChip);
  // 매 프레임 (재생·걷기 중에도 초가 부드럽게). 바뀐 글자만 쓴다
  const loop = () => { renderClock(); requestAnimationFrame(loop); };
  requestAnimationFrame(loop);
}

let clockSig = "";

// ---------- 시각 ----------

function renderClock() {
  const now = simNow();
  const text = fmtKst(now);
  const diff = Math.round((now - Date.now()) / 60e3);
  const sig = `${text}|${diff}|${S.play?.speed ?? ""}|${!!S.sim}|${S.simDisabled}`;
  if (sig === clockSig) return;
  clockSig = sig;
  $("clock-text").textContent = text;
  const tag = $("clock-tag");
  const virtual = !!S.sim && Math.abs(diff) >= 1;
  tag.hidden = !virtual && !S.play;
  tag.textContent = S.play ? `▶ ×${S.play.speed}` : "가상";
  tag.classList.toggle("playing", !!S.play);
  const d = Math.abs(diff);
  const txt = d >= 1440 ? `${Math.floor(d / 1440)}일 ${Math.floor((d % 1440) / 60)}시간` : fmtMinutes(d);
  $("clock").title = virtual ? `가상 시각 — 실제보다 ${txt} ${diff > 0 ? "앞" : "뒤"}` : S.simDisabled ? "실제 시각 (가상 상황 꺼짐)" : "실제 시각";
}

// ---------- 날씨 칩 ----------

const hm = (t) => fmtKst(t, { date: false, seconds: false });

/** 끝 시각 표시: 같은 날이면 "~17:00", 아니면 "~10/11 06:00". */
function endText(t) {
  const sameDay = fmtKst(t).slice(0, 5) === fmtKst(simNow()).slice(0, 5);
  return `~${sameDay ? hm(t) : fmtKst(t, { seconds: false })}`;
}

export function weatherSummary() {
  const sim = S.sim;
  if (S.simDisabled || !sim) return { icon: "📡", text: "실제 데이터", cls: "live" };
  if (sim.mode === "live") return { icon: "📡", text: "실시간 DT", cls: "live" };
  const seg = sim.segment;
  if (seg) {
    const m = kindMeta(seg.kind);
    return { icon: m.icon, text: `${m.ko} ${endText(ms(seg.end))}`, cls: `k-${seg.kind}` };
  }
  // 평상시: 오늘 다가오는 구간이 있으면 함께
  const now = simNow();
  const next = (sim.timeline || []).filter((x) => ms(x.start) > now).sort((a, b) => ms(a.start) - ms(b.start))[0];
  const today = next && fmtKst(ms(next.start)).slice(0, 5) === fmtKst(now).slice(0, 5);
  return { icon: "☀️", text: `평상시${today ? ` · ${hm(ms(next.start))} ${kindMeta(next.kind).icon} 예정` : ""}`, cls: "calm" };
}

function renderWeatherChip() {
  const w = weatherSummary();
  const chip = $("weather-chip");
  chip.className = `pill ${w.cls}`;
  chip.replaceChildren(h("span.ic", w.icon), w.text);
}

// ---------- 여행 칩 ----------

function itemTitle(id) {
  return itemById(id)?.place?.title || "다음 장소";
}

/** 지금 상태 문장 (예: "가옥거리 머무는 중", "과메기문화관으로 이동 중 · 15분 늦음"). */
export function tripSentence() {
  const t = S.trip;
  const m = S.motion;
  if (m) {
    const name = m.label || "그 자리";
    if (m.kind === "stay") return `${name} 머무는 중`;
    return `${josa(name, "로")} ${m.kind === "car" ? "차로 가는" : "걷는"} 중`;
  }
  if (!t?.active) return "";
  const cur = t.current || {};
  const late = t.delay_minutes > 0 ? ` · ${t.delay_minutes}분 늦음` : "";
  if (cur.state === "visiting") return `${itemTitle(cur.item_id)} 머무는 중`;
  if (t.next) return `${josa(t.next.title || itemTitle(t.next.item_id), "로")} ${cur.state === "moving" ? "이동 중" : "가는 길"}${late}`;
  if (cur.state === "moving") return "이동 중";
  const left = dayItems().some((it) => it.status === "planned" || it.status === "visiting");
  return left ? "출발 전" : "일정을 모두 마쳤어요";
}

function renderTripChip() {
  const chip = $("trip-chip");
  const t = S.trip;
  chip.classList.toggle("on", !!t?.active);
  chip.classList.toggle("late", !!t?.active && t.delay_minutes > 0);
  if (S.planId == null) { chip.textContent = S.plans.length ? "계획 고르기" : "계획 없음"; return; }
  if (!t) { chip.textContent = "…"; return; }
  if (t.active) {
    chip.replaceChildren(h("span.ic", "🚶"), h("b", "동행 중"), h("span.sep", "·"), tripSentence());
  } else {
    chip.textContent = t.ended_at ? "🏁 여행 끝" : "여행 전";
  }
}

function openTripMenu(ev) {
  const anchor = ev.currentTarget;
  const t = S.trip;
  const p = S.plan;
  const rows = [];
  if (p) rows.push(h("div.menu-head", h("b", p.title), h("span.muted", ` ${p.start_date.slice(5).replace("-", "/")} ${p.day_start || ""}~${p.day_end || ""}`)));
  if (t?.active) {
    const nx = t.next;
    const late = t.delay_minutes == null ? "" : t.delay_minutes > 0 ? `${t.delay_minutes}분 늦음` : t.delay_minutes < 0 ? `${-t.delay_minutes}분 여유` : "예정대로";
    rows.push(h("dl.facts",
      h("dt", "지금"), h("dd", tripSentence() || "-"),
      h("dt", "다음"), h("dd", nx ? `${nx.title || itemTitle(nx.item_id)} · 예정 ${nx.planned_start || "-"}${nx.eta ? ` · 도착 ${nx.eta}` : ""}${late ? ` (${late})` : ""}` : "-"),
      h("dt", "위치"), h("dd", t.location
        ? h("button.link", { onclick: () => { focus(t.location.lat, t.location.lng); closePopover(); } }, `지도에서 보기 (${t.location.recorded_at.slice(11, 16)})`)
        : "없음")));
    const sel = h("select", { "aria-label": "머무는 시간" }, ...[3, 10, 30].map((m) => h("option", { value: String(m) }, `${m}분`)));
    rows.push(h("div.menu-row",
      h("button.btn", { disabled: !!S.motion, onclick: () => { closePopover(); stay(Number(sel.value) || 3); } }, "지금 위치에서 머물기"), sel));
    rows.push(h("div.menu-row", h("button.btn.danger-ghost", { disabled: !!S.motion, onclick: () => { closePopover(); endTrip(); } }, "여행 끝내기")));
  } else if (S.planId != null) {
    const sameDay = isTripDay();
    rows.push(h("p.muted.small", t?.ended_at ? "여행이 끝났어요." : "아직 여행 전이에요."));
    rows.push(h("div.menu-row", h("button.btn.primary", {
      disabled: !!S.motion || !dayItems().length,
      onclick: () => { closePopover(); sameDay || !canSim() ? startTrip() : moveAndStart(); },
    }, sameDay || !canSim() ? "여행 시작" : "계획 날짜로 이동 후 시작")));
  } else {
    rows.push(h("p.muted.small", "계획이 없어요. 👤 → 계정 초기화 → [데모 시작 상태로] 로 빈 데모 계획을 만들 수 있어요."));
  }
  popover(anchor, h("div.menu", ...rows), { className: "trip-pop" });
}

// ---------- 👤 메뉴 ----------

function openUserMenu(ev) {
  const anchor = ev.currentTarget;
  const sel = h("select#plan-select", { "aria-label": "계획" },
    ...(S.plans.length ? S.plans.map((p) => h("option", { value: String(p.id) }, `${p.title} · ${p.start_date.slice(5).replace("-", "/")}`))
      : [h("option", { value: "" }, "계획 없음")]));
  sel.value = String(S.planId ?? "");
  sel.addEventListener("change", () => { selectPlan(Number(sel.value) || null); closePopover(); });
  const item = (icon, label, fn, cls = "") => h(`button.menu-item${cls}`, { onclick: () => { closePopover(); fn(); } }, h("span.ic", icon), label);
  popover(anchor, h("div.menu",
    h("div.menu-head", h("b", api.userLabel || "로그인 안 됨"), h("span.muted.small", api.base.replace(/^https?:\/\//, ""))),
    h("label.menu-field", h("span.muted.small", "계획"), sel),
    h("hr"),
    item("⚙️", "설정 (서버·관리 토큰)", handlers.openSettings),
    item("🧹", "계정 초기화", handlers.openReset),
    item("🛠", "개발자 정보", handlers.openDev),
    h("hr"),
    item("↩", "로그아웃", handlers.logout, ".muted")), { align: "end", className: "user-pop" });
}

// ---------- 노란 띠: 문제가 있을 때만 ----------

function renderAlertbar() {
  const msgs = [];
  if (S.health === false) msgs.push(["서버에 연결할 수 없어요", `${api.base} 응답 없음`]);
  else if (S.health && S.agentInfoOk === false) msgs.push(["AI 서버 응답 없음", "GET /agent/info 실패 — 구룡메이트 메시지가 늦거나 실패할 수 있어요"]);
  if (S.simDisabled) msgs.push(["가상 상황 꺼짐", "시각·날씨는 바꿀 수 없어요 (서버에 SAFETY_SIM_ENABLED=true 필요). 지도·위치는 돼요"]);
  if (S.adminNeeded && !api.adminToken) msgs.push(["관리 토큰 필요", "시각·날씨를 바꾸려면 👤 → 설정에서 관리 토큰을 넣으세요"]);
  const bar = $("alertbar");
  bar.hidden = !msgs.length;
  bar.replaceChildren(...msgs.map(([b, t]) => h("span", h("b", `⚠ ${b}`), ` — ${t}`)));
  bar.onclick = S.adminNeeded && !api.adminToken ? () => handlers.openSettings() : null;
}
export { renderAlertbar };
