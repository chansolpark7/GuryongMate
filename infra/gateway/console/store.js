// 화면 상태. 서버 상태는 항상 다시 읽어 덮어쓴다 — 대시보드에만 있는 상태는 진행 중인 이동(motion)·시간 재생(play)뿐 (README §4).

export const S = {
  // 연결
  health: null, // true | false
  agentInfo: null,
  agentInfoOk: null, // GET /agent/info 응답 여부
  routing: null,
  adminNeeded: false, // 🔒 API 가 403 admin_only 를 돌려준 적이 있음 (관리 토큰을 넣으면 풀림)
  // 시각·안전
  sim: null, // GET /safety/sim 응답
  simAt: 0, // sim 을 받은 performance.now()
  simDisabled: false, // 404 sim_disabled
  status: null, // GET /safety/status
  zones: [], // GET /safety/zones
  avoid: null, // GET /safety/avoid
  // 계획·여행
  plans: [],
  planId: null,
  plan: null, // GET /planner/plans/{id}
  trip: null, // GET /trip/plans/{id}/state
  tripAt: 0,
  events: new Map(), // id -> Event
  alerts: [],
  // 이 대시보드에만 있는 상태
  motion: null, // 진행 중인 이동 {kind, label, stop, sent, ignored, total, done, t0, t, end}
  play: null, // 시간 재생 {speed, stop}
  locLog: [], // 최근 위치 전송 결과 (개발자 정보) [{at, kind, accepted, ignored, events}]
};

const listeners = new Map();

/** 상태가 바뀌었음을 알린다. topic: conn | sim | safety | plans | plan | trip | events | alerts | motion | play | prefs */
export function emit(topic) {
  for (const fn of listeners.get(topic) || []) {
    try { fn(); } catch (e) { console.error(e); }
  }
}

export function on(topics, fn) {
  for (const t of topics.split(" ")) {
    if (!listeners.has(t)) listeners.set(t, []);
    listeners.get(t).push(fn);
  }
}

/** 지금 가상 시각 (ms). safety 시각(받은 뒤 흐른 시간 포함) → 없으면 trip 의 지금 → 없으면 실제 시각. */
export function simNow() {
  if (S.sim) return Date.parse(S.sim.now) + (performance.now() - S.simAt);
  if (S.trip) return Date.parse(S.trip.now) + (performance.now() - S.tripAt);
  return Date.now();
}

/** 지금 보여줄 여행일 (여행 중이거나 마지막 여행일, 없으면 1일차). */
export function currentDay() {
  return S.trip?.day || 1;
}

export function dayOf(n = currentDay()) {
  return S.plan?.days?.find((d) => d.day === n) || S.plan?.days?.[0] || null;
}

export function dayItems() {
  return dayOf()?.items || [];
}

export function itemById(id) {
  for (const d of S.plan?.days || []) {
    for (const it of d.items) if (it.id === id) return it;
  }
  return null;
}

export const tripActive = () => !!S.trip?.active;
export const canSim = () => !!S.sim && !S.simDisabled;

// ---------- 이 브라우저의 설정 (localStorage) ----------

const PREF_KEY = "gm.console.prefs";
const PREF_DEFAULT = {
  autoScan: true, // 날씨를 바꾸면 바로 안전 점검 + 틱
  speed: 20, // 걷기·머물기 배속 (실제 1초 = 가상 N초, 0 = 즉시)
  playSpeed: 20, // ▶ 재생 배속
  feedOpen: null, // 오른쪽 피드 (null = 화면 폭으로 정함)
  weatherOpen: true,
};

export const prefs = (() => {
  let saved = {};
  try {
    saved = JSON.parse(localStorage.getItem(PREF_KEY) || "{}") || {};
    // v1 의 개별 키 (배속·바로 반영)
    const sp = localStorage.getItem("gm.console.speed");
    if (sp !== null && saved.speed == null) saved.speed = Number(sp);
    const ap = localStorage.getItem("gm.console.applyNow");
    if (ap !== null && saved.autoScan == null) saved.autoScan = ap === "1";
  } catch { saved = {}; }
  return { ...PREF_DEFAULT, ...saved };
})();

export function setPref(key, value) {
  prefs[key] = value;
  try { localStorage.setItem(PREF_KEY, JSON.stringify(prefs)); } catch { /* 이번 창에서만 */ }
  emit("prefs");
}
