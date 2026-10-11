// 서버에서 읽기 (폴링). 쓰기·조작은 actions.js
import { api, ApiError } from "./api.js";
import { S, emit } from "./store.js";

const PLAN_KEY = "gm.console.plan";

export async function loadConn() {
  const [health, info, routing] = await Promise.allSettled([
    api.get("/health"), api.get("/agent/info"), api.get("/routing/status"),
  ]);
  S.health = health.status === "fulfilled";
  S.agentInfo = info.status === "fulfilled" ? info.value : null;
  S.agentInfoOk = info.status === "fulfilled";
  S.routing = routing.status === "fulfilled" ? routing.value : null;
  emit("conn");
}

export async function loadSim() {
  try {
    const sim = await api.get("/safety/sim");
    S.sim = sim;
    S.simAt = performance.now();
    S.simDisabled = false;
  } catch (e) {
    if (e instanceof ApiError && (e.code === "sim_disabled" || e.status === 404)) {
      S.sim = null;
      S.simDisabled = true;
    } else throw e;
  }
  emit("sim");
}

/** 받은 sim 응답(PUT 결과)을 바로 반영. */
export function applySim(sim) {
  S.sim = sim;
  S.simAt = performance.now();
  S.simDisabled = false;
  emit("sim");
}

export async function loadSafety() {
  const tasks = [api.get("/safety/status"), api.get("/safety/avoid")];
  if (!S.zones.length) tasks.push(api.get("/safety/zones"));
  const [status, avoid, zones] = await Promise.allSettled(tasks);
  if (status.status === "fulfilled") S.status = status.value;
  if (avoid.status === "fulfilled") S.avoid = avoid.value;
  if (zones && zones.status === "fulfilled") S.zones = zones.value.zones || zones.value;
  emit("safety");
}

let planVersion = null;

export async function loadPlans() {
  S.plans = await api.get("/planner/plans");
  let id = S.planId;
  if (id == null) {
    try { id = Number(localStorage.getItem(PLAN_KEY)) || null; } catch { id = null; }
  }
  if (!S.plans.some((p) => p.id === id)) {
    // 기억한 계획이 없으면 가장 최근에 바뀐 계획 (하나면 그것)
    const sorted = [...S.plans].sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at));
    id = sorted[0]?.id ?? null;
  }
  const changed = id !== S.planId;
  if (changed) selectPlan(id, false);
  const summary = S.plans.find((p) => p.id === id);
  const v = summary ? `${summary.version}.${summary.progress_version}` : null;
  emit("plans");
  if (changed || v !== planVersion) {
    planVersion = v;
    await loadPlan();
  }
}

export function selectPlan(id, reload = true) {
  S.planId = id;
  S.plan = null;
  S.trip = null;
  S.events = new Map();
  S.alerts = [];
  planVersion = null;
  try {
    if (id != null) localStorage.setItem(PLAN_KEY, String(id));
  } catch { /* 무시 */ }
  emit("plan");
  emit("trip");
  emit("events");
  emit("alerts");
  if (reload && id != null) return Promise.allSettled([loadPlan(), loadTrip(), loadEvents(true), loadAlerts()]);
  return Promise.resolve();
}

export async function loadPlan() {
  if (S.planId == null) return;
  const id = S.planId;
  const plan = await api.get(`/planner/plans/${id}`);
  if (id !== S.planId) return;
  S.plan = plan;
  planVersion = `${plan.version}.${plan.progress_version}`;
  emit("plan");
}

export async function loadTrip() {
  if (S.planId == null) return;
  const id = S.planId;
  const trip = await api.get(`/trip/plans/${id}/state`);
  if (id !== S.planId) return;
  applyTrip(trip);
}

export function applyTrip(trip) {
  if (!trip || trip.plan_id !== S.planId) return;
  const prev = S.trip;
  S.trip = trip;
  S.tripAt = performance.now();
  emit("trip");
  // 머무는 항목·진행이 바뀌면 일정 상태(색)도 다시 읽는다
  if (prev && (prev.current?.item_id !== trip.current?.item_id || prev.current?.state !== trip.current?.state)) {
    loadPlan().catch(() => {});
  }
}

/** 이벤트를 받아 합친다. full: 처음부터 다시 (상태 변화 반영). */
export async function loadEvents(full = false) {
  if (S.planId == null) return;
  const id = S.planId;
  // 아직 끝나지 않은(queued) 이벤트가 있으면 그 앞부터 다시 읽어 상태 변화(delivered·failed·agent)를 반영한다
  let after = 0;
  if (!full && S.events.size) {
    const ids = [...S.events.keys()];
    const pending = [...S.events.values()].filter((e) => e.status === "queued").map((e) => e.id);
    after = pending.length ? Math.min(...pending) - 1 : Math.max(...ids);
  }
  for (let page = 0; page < 20; page++) {
    const list = await api.get(`/trip/plans/${id}/events?after=${after}&limit=200`);
    if (id !== S.planId) return;
    mergeEvents(list, false);
    if (list.length < 200) break;
    after = list[list.length - 1].id;
  }
  emit("events");
}

export function mergeEvents(list, notify = true) {
  let changed = false;
  for (const e of list || []) {
    if (e.plan_id !== S.planId) continue;
    const old = S.events.get(e.id);
    if (!old || JSON.stringify(old) !== JSON.stringify(e)) {
      S.events.set(e.id, e);
      changed = true;
    }
  }
  if (changed && notify) emit("events");
  return changed;
}

export async function loadAlerts() {
  if (S.planId == null) return;
  const id = S.planId;
  const list = await api.get(`/planner/plans/${id}/safety/alerts`);
  if (id !== S.planId) return;
  S.alerts = list;
  emit("alerts");
}
