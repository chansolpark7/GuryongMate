// 계정 초기화 (👤 → 계정 초기화, redesign.md §3). 로그인한 사용자 자신의 데이터만 지운다 — 계정 탈퇴(DELETE /users/me)는 부르지 않는다.
//  - 내 데이터 지우기: 계획마다 여행 끝내기(실패 무시) → 서비스별 DELETE /me (planner 계획·일정·제안 / trip 여행 상태·이벤트 / docent 메모리·가이드 노트 / agent 대화 세션)
//  - 데모 시작 상태로: 위 + 가상 시각 계획 전날 10:00 · 평상시 + 빈 계획 + 대화 세션 = scripts/demo/run_demo.py prep (--plan chat)
//  - 환경 원래대로: 🔒 실제 데이터 + 실제 시각 + 시간표 비움 (서버 전체 공용)
import { api, errText } from "./api.js";
import { S, emit } from "./store.js";
import { loadPlans, loadSafety, loadSim, selectPlan } from "./data.js";
import { putSim, reloadAll, stopMotion, stopPlay } from "./actions.js";
import { toast } from "./ui.js";
import { h, kstIso } from "./util.js";

const $ = (id) => document.getElementById(id);
const WIPE = [
  ["/planner/me", "계획·일정·제안"],
  ["/trip/me", "여행 기록·이벤트"],
  ["/docent/me", "메모리·가이드 노트"],
  ["/agent/me", "대화"],
];

let demoPlan = null; // demo_plan.json = scripts/trip/scenes/scene4_safety.json 의 plan (scripts/demo/check_console_plan.py 로 같은지 확인)
let busy = false;

async function getDemoPlan() {
  if (!demoPlan) {
    const r = await fetch("demo_plan.json", { cache: "no-store" });
    if (!r.ok) throw new Error(`demo_plan.json ${r.status}`);
    demoPlan = await r.json();
  }
  return demoPlan;
}

/** run_demo.py PLAN_NOW: 계획 전날 10:00 (장면 ① 은 여행 전날). */
function demoNow(plan) {
  const t = Date.parse(`${plan.start_date}T10:00:00+09:00`) - 24 * 3600e3;
  return kstIso(t);
}

export async function openReset() {
  const dlg = $("dlg-reset");
  const id = api.loginId;
  const plans = S.plans;
  let plan = null;
  try { plan = await getDemoPlan(); } catch { /* 아래에서 버튼을 끈다 */ }
  const confirmIn = h("input", { autocomplete: "off", spellcheck: false, placeholder: id || "아이디", "aria-label": "확인용 아이디" });
  const log = h("ol.reset-log", { "aria-live": "polite" });
  const btnWipe = h("button.btn.danger", { disabled: true }, "지우기");
  const btnDemo = h("button.btn.primary", { disabled: true }, "데모 시작 상태로");
  const btnEnv = h("button.btn", { disabled: !S.sim }, "원래대로");
  const gate = () => {
    const ok = !!id && confirmIn.value.trim() === id && !busy;
    btnWipe.disabled = !ok;
    btnDemo.disabled = !ok || !plan;
  };
  confirmIn.addEventListener("input", gate);
  const line = (ok, text) => log.append(h(`li.${ok === true ? "ok" : ok === false ? "fail" : "info"}`, text));
  const run = async (fn) => {
    if (busy) return;
    busy = true;
    for (const b of [btnWipe, btnDemo, btnEnv]) b.disabled = true;
    log.replaceChildren();
    try { await fn(line); } catch (e) { line(false, `중단: ${errText(e)}`); } finally { busy = false; gate(); btnEnv.disabled = !S.sim; }
  };
  btnWipe.onclick = () => run(async (log_) => {
    const ok = await wipe(log_);
    await afterReset();
    log_(null, ok ? "끝 — 화면을 다시 불러왔어요" : "끝 — 실패한 것이 있어요 (위 ✗)");
  });
  btnDemo.onclick = () => run(async (log_) => {
    const ok = await demoStart(plan, log_);
    await afterReset();
    log_(null, ok ? "끝 — 이제 run_demo.py --from s1_chat 으로 이어갈 수 있어요" : "끝 — 실패한 단계가 있어요 (위 ✗). 고친 뒤 다시 누르세요");
  });
  btnEnv.onclick = () => {
    if (!confirm("실제 데이터 + 실제 시각 + 시간표 비움으로 되돌립니다.\n같은 서버를 쓰는 모든 사람에게 적용돼요. 계속할까요?")) return;
    run(async (log_) => { await envReset(log_); log_(null, "끝"); });
  };

  const planLine = plans.length ? `계획 ${plans.length}개 (${plans.slice(0, 3).map((p) => `'${p.title}'`).join(", ")}${plans.length > 3 ? " …" : ""})` : "계획 없음";
  const demoDesc = plan
    ? `위 삭제 + 가상 시각 ${demoNow(plan).slice(5, 16).replace("-", "/").replace("T", " ")} · 평상시(시간표 비움) + 빈 계획 '${plan.title}'(${plan.start_date.slice(5).replace("-", "/")} ${plan.day_start}~${plan.day_end}) + 대화 세션. run_demo.py 의 준비(prep)와 같은 결과예요.`
    : "demo_plan.json 을 읽지 못해 쓸 수 없어요.";
  dlg.replaceChildren(h("div.reset",
    h("div.dev-head", h("h2#reset-title", "계정 초기화"), h("button.icon-btn", { "aria-label": "닫기", onclick: () => !busy && dlg.close() }, "✕")),
    h("p.reset-who", h("b", id || "(로그인 안 됨)"), h("span.muted", ` · ${planLine}`)),
    h("div.reset-card",
      h("div.rc-text", h("b", "🧹 내 데이터 지우기"), h("p", "이 계정의 계획·대화·여행 기록·메모리·가이드 노트를 지워요. 계정과 로그인은 그대로예요.")),
      btnWipe),
    h("div.reset-card.demo",
      h("div.rc-text", h("b", "🎬 데모 시작 상태로"), h("p", demoDesc)),
      btnDemo),
    h("label.reset-confirm", h("span", "위 두 버튼을 쓰려면 아이디를 그대로 입력하세요"), confirmIn),
    h("div.reset-card.env",
      h("div.rc-text", h("b", "🌐 환경 원래대로"), h("p", "📡 실시간 DT + 실제 시각 + 날씨 시간표 비움."),
        h("p.warn-text", "⚠ 같은 서버를 쓰는 모든 사람에게 적용돼요. 발표·리허설이 끝났을 때 쓰세요.")),
      btnEnv),
    log,
    h("p.muted.small", "계정 탈퇴는 하지 않아요 (DELETE /users/me 를 부르지 않음). 데이터 삭제는 관리 토큰 없이 되고, 시각·날씨는 운영 서버에서 관리 토큰이 필요해요.")));
  if (!dlg.open) dlg.showModal();
  dlg.oncancel = (e) => { if (busy) e.preventDefault(); };
  setTimeout(() => confirmIn.focus(), 0);
}

/** 이 사용자의 데이터를 지운다. 하나가 실패해도 나머지는 계속, 실패한 것을 알린다. */
async function wipe(log) {
  stopMotion();
  stopPlay();
  const plans = await api.get("/planner/plans");
  let ended = 0;
  for (const p of plans) {
    try { await api.post(`/trip/plans/${p.id}/end`, {}); ended += 1; } catch { /* 여행 중이 아니면 실패 — 무시 */ }
  }
  log(true, `계획 ${plans.length}개 확인${ended ? ` · 진행 중이던 여행 ${ended}개 끝냄` : ""}`);
  let ok = true;
  for (const [path, what] of WIPE) {
    try {
      await api.del(path);
      log(true, `${what} 삭제 (DELETE ${path})`);
    } catch (e) {
      ok = false;
      log(false, `${what} 삭제 실패 (DELETE ${path}): ${errText(e)}`);
    }
  }
  return ok;
}

async function demoStart(plan, log) {
  let ok = await wipe(log);
  const now = demoNow(plan);
  try {
    await putSim({ source: "mock", scenario: "calm", now });
    log(true, `가상 시각 ${now.slice(5, 16).replace("T", " ")} · 평상시`);
  } catch (e) {
    ok = false;
    log(false, `가상 시각·날씨 실패: ${errText(e)}`);
  }
  const p = await api.post("/planner/plans", {
    title: plan.title, start_date: plan.start_date, end_date: plan.end_date ?? null,
    day_start: plan.day_start, day_end: plan.day_end, travel_mode: "walk", memo: "",
  });
  log(true, `빈 계획 #${p.id} '${p.title}' (${p.start_date} ${p.day_start}~${p.day_end})`);
  const s = await api.post("/agent/sessions", { plan_id: p.id });
  log(true, `대화 세션 ${String(s.id).slice(0, 8)}…`);
  return ok;
}

async function envReset(log) {
  stopPlay();
  const body = { reset_clock: true, timeline: [] };
  if (S.sim?.switchable) body.source = "live";
  try {
    const sim = await putSim(body);
    log(true, `${sim.mode === "live" ? "📡 실시간 DT" : "가상 상황 (전환 불가 서버)"} · 실제 시각 · 시간표 비움`);
  } catch (e) {
    log(false, `실패: ${errText(e)}`);
  }
  await loadSafety().catch(() => {});
}

/** 지운 뒤: 선택 계획·이벤트 커서·걷기 진행을 비우고 다시 불러온다. */
async function afterReset() {
  await selectPlan(null, false);
  S.plans = [];
  S.locLog = [];
  emit("plans");
  emit("motion");
  try { localStorage.removeItem("gm.console.plan"); } catch { /* 무시 */ }
  await loadPlans().catch((e) => toast(`계획 목록 실패: ${errText(e)}`, "error"));
  await Promise.allSettled([loadSim(), reloadAll()]);
}
