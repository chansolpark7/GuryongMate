// 계정 초기화 (👤 → 계정 초기화, redesign.md §3). 로그인한 사용자 자신의 데이터만 지운다 — 계정 탈퇴(DELETE /users/me)는 부르지 않는다.
//  - 내 데이터 지우기: 계획마다 여행 끝내기(실패 무시) → 서비스별 DELETE /me (planner 계획·일정·제안 / trip 여행 상태·이벤트 / docent 메모리·가이드 노트 / agent 대화 세션)
//  - 데모 시작 상태로: 위 + 가상 시각 계획 전날 10:00 · 평상시 + 빈 계획 + 대화 세션 = scripts/demo/run_demo.py prep (--plan chat)
//  - 데모 계획으로 시작: 위 삭제 + 가상 시각(여행 당일 12:20 / 전날 10:00) · 평상시 + demo_plan.json 계획·일정 5곳·규칙 + 대화 세션
//    = scripts/trip/trip_cli.py _create_plan (run_demo.py --plan prebuilt). 여행은 시작하지 않는다. 가이드 노트는 체크했을 때만 지금 준비 (LLM 비용)
//  - 환경 원래대로: 🔒 실제 데이터 + 실제 시각 + 시간표 비움 (서버 전체 공용)
import { api, errText } from "./api.js";
import { S, emit, setGuideJob } from "./store.js";
import { loadPlans, loadSafety, loadSim, selectPlan } from "./data.js";
import { putSim, reloadAll, stopMotion, stopPlay } from "./actions.js";
import { toast } from "./ui.js";
import { h, kstIso, sleep } from "./util.js";

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

/** 여행 당일 day_start 10분 전 (12:30 → 12:20, 지도 [계획 날짜로 이동 후 시작]과 같은 시각). */
function tripDayNow(plan) {
  return kstIso(Date.parse(`${plan.start_date}T${plan.day_start}:00+09:00`) - 10 * 60e3);
}

const fmtNow = (iso) => iso.slice(5, 16).replace("-", "/").replace("T", " ");
const RULE_KO = { end_before: (p) => `${p.time} 전 종료` };
const ruleText = (r) => RULE_KO[r.type]?.(r.params) || r.type;

export async function openReset() {
  const dlg = $("dlg-reset");
  const id = api.loginId;
  const plans = S.plans;
  let plan = null;
  try { plan = await getDemoPlan(); } catch { /* 아래에서 버튼을 끈다 */ }
  const confirmIn = h("input", { autocomplete: "off", spellcheck: false, placeholder: id || "아이디", "aria-label": "확인용 아이디" });
  const log = h("ol.reset-log", { "aria-live": "polite" });
  const btnWipe = h("button.btn.danger", { disabled: true }, "지우기");
  const btnDemo = h("button.btn", { disabled: true }, "데모 시작 상태로");
  const btnFull = h("button.btn.primary", { disabled: true }, "데모 계획으로 시작");
  const whenIn = (value, label, checked) => h("label.radio", h("input", { type: "radio", name: "reset-when", value, checked }), h("span", label));
  const when = plan && h("div.reset-opts", { role: "radiogroup", "aria-label": "가상 시각" },
    whenIn("day", `여행 당일 ${fmtNow(tripDayNow(plan))} — 바로 여행 시작 가능`, true),
    whenIn("eve", `여행 전날 ${fmtNow(demoNow(plan))}`, false));
  const guideIn = h("input", { type: "checkbox" });
  const guideOpt = plan && h("label.check", guideIn,
    h("span", "가이드 노트도 지금 준비 ", h("span.warn-text", `(LLM 비용 — Claude Haiku 로 ${plan.items.length}곳 약 $0.015)`)));
  const btnEnv = h("button.btn", { disabled: !S.sim }, "원래대로");
  const gate = () => {
    const ok = !!id && confirmIn.value.trim() === id && !busy;
    btnWipe.disabled = !ok;
    btnDemo.disabled = !ok || !plan;
    btnFull.disabled = !ok || !plan;
  };
  confirmIn.addEventListener("input", gate);
  const line = (ok, text) => log.append(h(`li.${ok === true ? "ok" : ok === false ? "fail" : "info"}`, text));
  const run = async (fn) => {
    if (busy) return;
    busy = true;
    for (const b of [btnWipe, btnDemo, btnFull, btnEnv]) b.disabled = true;
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
  btnFull.onclick = () => run(async (log_) => {
    const at = when.querySelector("input:checked")?.value === "eve" ? demoNow(plan) : tripDayNow(plan);
    const r = await demoPlanStart(plan, at, guideIn.checked, log_);
    await afterReset(r.planId);
    log_(null, r.ok ? "끝 — 지도에 일정이 보여요. 지도의 [🏁 여행 시작]으로 동행을 시작하세요" : "끝 — 실패한 단계가 있어요 (위 ✗). 고친 뒤 다시 누르세요");
  });
  btnEnv.onclick = () => {
    if (!confirm("실제 데이터 + 실제 시각 + 시간표 비움으로 되돌립니다.\n같은 서버를 쓰는 모든 사람에게 적용돼요. 계속할까요?")) return;
    run(async (log_) => { await envReset(log_); log_(null, "끝"); });
  };

  const planLine = plans.length ? `계획 ${plans.length}개 (${plans.slice(0, 3).map((p) => `'${p.title}'`).join(", ")}${plans.length > 3 ? " …" : ""})` : "계획 없음";
  const demoDesc = plan
    ? `위 삭제 + 가상 시각 ${fmtNow(demoNow(plan))} · 평상시(시간표 비움) + 빈 계획 '${plan.title}'(${plan.start_date.slice(5).replace("-", "/")} ${plan.day_start}~${plan.day_end}) + 대화 세션. run_demo.py 의 준비(prep)와 같은 결과예요.`
    : "demo_plan.json 을 읽지 못해 쓸 수 없어요.";
  const fullDesc = plan
    ? `위 삭제 + 계획 '${plan.title}'(${plan.start_date.slice(5).replace("-", "/")} ${plan.day_start}~${plan.day_end})에 일정 ${plan.items.length}곳·규칙(${plan.constraints.map(ruleText).join(", ")}) + 대화 세션, 평상시(시간표 비움). 여행은 시작하지 않아요 — 지도의 [🏁 여행 시작]으로.`
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
    h("div.reset-card.demo",
      h("div.rc-text", h("b", "🗺 데모 계획으로 시작"), h("p", fullDesc), when, guideOpt,
        plan && h("p.muted", "체크하지 않으면 가이드 노트는 여행을 시작할 때 자동으로 준비돼요 (같은 LLM 비용).")),
      btnFull),
    h("label.reset-confirm", h("span", "위 세 버튼을 쓰려면 아이디를 그대로 입력하세요"), confirmIn),
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

/** 데모 계획으로 시작: trip_cli.py _create_plan 과 같은 순서 (계획 → 일정 → 규칙) + 대화 세션. 여행은 시작하지 않는다. */
async function demoPlanStart(plan, now, prepareGuide, log) {
  let ok = await wipe(log);
  try {
    await putSim({ source: "mock", scenario: "calm", now }); // calm 프리셋 = 시간표 비움 (scenario 와 timeline 은 함께 못 씀)
    log(true, `가상 시각 ${fmtNow(now)} · 평상시 (시간표 비움)`);
  } catch (e) {
    ok = false;
    log(false, `가상 시각·날씨 실패: ${errText(e)}`);
  }
  const p = await api.post("/planner/plans", {
    title: plan.title, start_date: plan.start_date, end_date: plan.end_date ?? null,
    day_start: plan.day_start, day_end: plan.day_end, travel_mode: plan.travel_mode ?? "walk", memo: "",
  });
  log(true, `계획 #${p.id} '${p.title}' (${p.start_date} ${p.day_start}~${p.day_end})`);
  for (const [i, it] of plan.items.entries()) {
    const body = { place_id: it.place_id, day: it.day ?? 1, stay_minutes: it.stay_minutes ?? 60 };
    if (it.start_time) body.start_time = it.start_time;
    if (it.travel_mode) body.travel_mode = it.travel_mode;
    try {
      const d = await api.post(`/planner/plans/${p.id}/items`, body);
      const added = d.days?.[body.day - 1]?.items?.filter((x) => x.place_id === it.place_id).at(-1);
      log(true, `일정 ${i + 1} ${added?.place?.title || it.place_id} · ${body.stay_minutes}분${it.travel_mode === "car" ? " · 🚗 차로" : ""}`);
    } catch (e) {
      ok = false;
      log(false, `일정 ${i + 1} ${it.place_id} 추가 실패: ${errText(e)}`);
    }
  }
  for (const r of plan.constraints || []) {
    const hard = r.hard ?? true;
    try {
      await api.post(`/planner/plans/${p.id}/constraints`, { type: r.type, params: r.params, hard, source: "user", note: r.note ?? "" });
      log(true, `규칙 ${ruleText(r)} (${hard ? "반드시" : "권장"})`);
    } catch (e) {
      ok = false;
      log(false, `규칙 ${ruleText(r)} 추가 실패: ${errText(e)}`);
    }
  }
  try {
    const s = await api.post("/agent/sessions", { plan_id: p.id });
    log(true, `대화 세션 ${String(s.id).slice(0, 8)}…`);
  } catch (e) {
    ok = false;
    log(false, `대화 세션 실패: ${errText(e)}`);
  }
  if (prepareGuide) {
    try {
      const job = await api.post(`/agent/plans/${p.id}/guide/prepare`, {});
      log(true, `가이드 노트 준비 시작 (${job.total}곳) — 진행은 오른쪽 피드 맨 위에`);
      watchGuide(p.id, job);
    } catch (e) {
      ok = false;
      log(false, `가이드 노트 준비 실패: ${errText(e)}`);
    }
  }
  return { ok, planId: p.id };
}

/** 가이드 준비 작업을 2초마다 읽어 피드 맨 위 한 줄로 보여 준다 (끝나면 1분 뒤 지움). */
async function watchGuide(planId, job) {
  setGuideJob(job);
  for (let i = 0; i < 300 && job.status === "running"; i++) {
    await sleep(2000);
    if (S.guideJob?.job_id !== job.job_id) return; // 다시 초기화해서 바뀜
    try { job = await api.get(`/agent/plans/${planId}/guide/jobs/${job.job_id}`); } catch { break; }
    if (S.guideJob?.job_id !== job.job_id) return;
    setGuideJob(job);
  }
  setTimeout(() => { if (S.guideJob?.job_id === job.job_id) setGuideJob(null); }, 60e3);
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

/** 지운 뒤: 선택 계획·이벤트 커서·걷기 진행을 비우고 다시 불러온다. planId: 새로 만든 계획을 고른다. */
async function afterReset(planId = null) {
  if (planId == null || S.guideJob?.plan_id !== planId) setGuideJob(null);
  await selectPlan(null, false);
  S.plans = [];
  S.locLog = [];
  emit("plans");
  emit("motion");
  try {
    if (planId == null) localStorage.removeItem("gm.console.plan");
    else localStorage.setItem("gm.console.plan", String(planId));
  } catch { /* 무시 */ }
  await loadPlans().catch((e) => toast(`계획 목록 실패: ${errText(e)}`, "error"));
  await Promise.allSettled([loadSim(), reloadAll()]);
}
