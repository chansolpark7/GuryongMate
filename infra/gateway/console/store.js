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
  motion: null, // 진행 중인 이동 {kind(walk|car|stay|arrive), label, stop, sent, ignored, total, done, t0, t, end, speed, clock, line, path}
  play: null, // 시간 재생 {speed, stop, t0, r0}
  arm: null, // 날씨 그리기 도구 (종류, null = 꺼짐)
  locLog: [], // 최근 위치 전송 결과 (개발자 정보) [{at, kind, accepted, ignored, events}]
  guideJob: null, // 계정 초기화에서 부른 가이드 준비 작업 (GET /agent/plans/{id}/guide/jobs/{job_id}, 피드 맨 위 한 줄)
};

const listeners = new Map();

/** 상태가 바뀌었음을 알린다. topic: conn | sim | safety | plans | plan | trip | events | alerts | motion | play | prefs | guide */
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

/** 지금 가상 시각 (ms). 걷는 중·재생 중이면 그 배속으로 흐르는 화면 시각 (서버에는 점마다·1초마다 같은 값을 PUT) →
 * safety 시각(받은 뒤 흐른 시간 포함) → 없으면 trip 의 지금 → 없으면 실제 시각. 매 프레임 불러도 된다. */
export function simNow() {
  const c = S.motion?.clock;
  if (c) return Math.min(c.max, c.t + (performance.now() - c.at) * S.motion.speed);
  const p = S.play;
  if (p) return p.t0 + (performance.now() - p.r0) * p.speed;
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
  follow: false, // 걷는 중 지도가 사용자를 따라감
  moveMode: "walk", // 말풍선에서 마지막으로 고른 이동 수단 (walk | car, 강조 표시)
  autoStay: true, // 일정 장소로 걷기·차가 끝나면 2분 머물러 도착 판정을 바로 낸다
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

export function setGuideJob(job) {
  S.guideJob = job;
  emit("guide");
}
