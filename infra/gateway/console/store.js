// 화면 상태. 서버 상태는 항상 다시 읽어 덮어쓴다 — 대시보드에만 있는 상태는 진행 중인 이동(motion) 하나뿐 (README §4).

export const S = {
  // 연결
  health: null, // true | false
  agentInfo: null,
  routing: null,
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
  // 진행 중인 이동 (이 대시보드에만 있는 상태)
  motion: null, // {kind, label, stop: bool, sent, ignored}
};

const listeners = new Map();

/** 상태가 바뀌었음을 알린다. topic: conn | sim | safety | plan | trip | events | alerts | motion */
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

export function dayItems() {
  const day = S.plan?.days?.find((d) => d.day === currentDay()) || S.plan?.days?.[0];
  return day ? day.items : [];
}

export function itemById(id) {
  for (const d of S.plan?.days || []) {
    for (const it of d.items) if (it.id === id) return it;
  }
  return null;
}

export const tripActive = () => !!S.trip?.active;
