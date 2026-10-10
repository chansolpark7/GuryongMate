// 지도 위: 여행 전 큰 [여행 시작] 버튼 · 걸어가는 중 진행 카드 (redesign.md §2.2)
import { S, canSim, dayItems, on } from "./store.js";
import { isTripDay, moveAndStart, onBusy, planStartMs, startTrip, stopMotion } from "./actions.js";
import { fmtKst, fmtMinutes, h, josa } from "./util.js";

const $ = (id) => document.getElementById(id);
let tripBusy = null;

export function initOverlays() {
  on("plans plan trip motion sim", renderCta);
  on("motion", renderMotion);
  onBusy((kind, text) => {
    if (kind === "trip") { tripBusy = text; renderCta(); }
  });
  setInterval(renderCta, 5000); // 시각이 흘러 계획 날짜가 되면
}

function renderCta() {
  const box = $("cta");
  const t = S.trip;
  const show = S.planId != null && !!t && !t.active && !S.motion;
  box.hidden = !show;
  if (!show) return;
  const items = dayItems().filter((it) => it.status !== "skipped");
  if (!items.length) {
    box.replaceChildren(h("div.cta-card", h("b", "일정이 아직 없어요"), h("span", "앱에서 구룡메이트와 대화로 일정을 만들면 여기에 나타나요")));
    return;
  }
  const sameDay = isTripDay() || !canSim();
  const start = planStartMs();
  const label = tripBusy || (sameDay ? (t.ended_at ? "여행 다시 시작" : "여행 시작")
    : `계획 날짜(${fmtKst(start, { seconds: false }).slice(0, 5)})로 이동 후 시작`);
  box.replaceChildren(h("button.cta-btn", {
    disabled: !!tripBusy,
    title: sameDay ? "POST /trip/plans/{id}/start" : `가상 시각을 ${fmtKst(start, { seconds: false })} (첫 일정 10분 전)로 옮기고 시작`,
    onclick: () => (sameDay ? startTrip() : moveAndStart()),
  }, h("span.ic", "🏁"), label));
}

function renderMotion() {
  const m = S.motion;
  const box = $("motion-card");
  box.hidden = !m;
  if (!m) return;
  const left = Math.max(0, (m.end - m.t) / 60e3);
  const name = m.label || "그 자리";
  const what = m.kind === "stay" ? `${name}에서 머무는 중` : `${josa(name, "로")} ${m.kind === "car" ? "차로 가는" : "걷는"} 중`;
  const pct = m.total ? Math.round((m.done / m.total) * 100) : 0;
  box.replaceChildren(
    h("div.mc-row",
      h("span.ic", m.kind === "stay" ? "⏸" : m.kind === "car" ? "🚗" : "🚶"),
      h("span.mc-text", h("b", what), h("span.muted", ` · ${m.stop ? "멈추는 중…" : `${fmtMinutes(left)} 남음`}`)),
      h("button.btn.sm", { onclick: stopMotion, disabled: m.stop }, "멈춤")),
    h("div.mc-bar", h("i", { style: { width: `${pct}%` } })));
}
