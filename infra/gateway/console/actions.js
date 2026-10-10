// 조작: 가상 시각·안전 상황·여행 시작/종료·위치(순간이동·걸어가기·머물기). 🔒 = 운영에서 관리 토큰 필요
import { api, errText, isAdminOnly } from "./api.js";
import { S, emit, simNow, tripActive } from "./store.js";
import { applySim, applyTrip, loadAlerts, loadEvents, loadPlan, loadSafety, loadSim, loadTrip, mergeEvents } from "./data.js";
import { dist, fmtKst, kstIso, ms, resample, sleep } from "./util.js";
import { toast, showEventBubbles } from "./ui.js";

export const WALK_M_PER_MIN = 70; // 도보 (routing 기본 4.2km/h)
export const CAR_M_PER_MIN = 500; // 차 30km/h 가정
const WALK_STEP_M = 25; // 걸을 때 점 간격
const CAR_STEP_M = 60; // 차는 점 간격 약 7초 (위험 구역 접근 50m 를 크게 건너뛰지 않게)
const STAY_POINT_SEC = 30;

const SPEED_KEY = "gm.console.speed";
export function getSpeed() {
  try {
    const v = Number(localStorage.getItem(SPEED_KEY));
    return Number.isFinite(v) && v >= 0 && localStorage.getItem(SPEED_KEY) !== null ? v : 20;
  } catch { return 20; }
}
export function setSpeed(v) {
  try { localStorage.setItem(SPEED_KEY, String(v)); } catch { /* 무시 */ }
}

/** 실패를 알리고 다시 던지지 않는다 (버튼 핸들러용). */
async function guard(label, fn) {
  try {
    return await fn();
  } catch (e) {
    toast(`${label} 실패: ${errText(e)}`, isAdminOnly(e) ? "admin" : "error");
    return undefined;
  }
}

// ---------- 시각 ----------

/** 마지막 위치보다 이른 시각으로 옮기려면 확인 (그 뒤 위치는 버려진다, README §4). */
function confirmBackward(targetMs) {
  const rec = ms(S.trip?.location?.recorded_at);
  if (Number.isFinite(rec) && targetMs < rec - 1000) {
    return confirm(
      `마지막 위치 시각(${fmtKst(rec)})보다 이른 ${fmtKst(targetMs)} 로 옮깁니다.\n` +
      "이후 보내는 위치는 그 시각을 지날 때까지 서버가 버립니다 (recorded_at 은 단조 증가). 계속할까요?");
  }
  return true;
}

/** 🔒 PUT /safety/sim. */
export async function putSim(body) {
  const sim = await api.put("/safety/sim", body);
  applySim(sim);
  return sim;
}

export function setClock(targetMs) {
  if (!confirmBackward(targetMs)) return Promise.resolve();
  return guard("시각 바꾸기", async () => {
    await putSim({ now: kstIso(targetMs) });
    toast(`가상 시각 ${fmtKst(targetMs)}`);
    refreshAfterClock();
  });
}

export function shiftClock(minutes) {
  return setClock(simNow() + minutes * 60e3);
}

export function resetClock() {
  if (!confirmBackward(Date.now())) return Promise.resolve();
  return guard("실제 시각으로", async () => {
    await putSim({ reset_clock: true });
    toast("실제 시각으로 되돌렸습니다");
    refreshAfterClock();
  });
}

function refreshAfterClock() {
  // trip·planner 는 safety 시각을 5초 캐시한다 — 몇 초 뒤 다시 읽는다
  loadSafety().catch(() => {});
  setTimeout(() => loadTrip().catch(() => {}), 5500);
}

/** trip 의 지금이 목표 시각(±60초)을 따라올 때까지 기다린다 (최대 limitMs). scripts/trip/trip_cli.py _wait_clock. */
export async function waitClock(targetMs, limitMs = 8000) {
  if (S.planId == null || S.simDisabled) return true;
  const end = performance.now() + limitMs;
  while (performance.now() < end) {
    try {
      const st = await api.get(`/trip/plans/${S.planId}/state`);
      applyTrip(st);
      if (Math.abs(Date.parse(st.now) - targetMs) < 60e3) return true;
    } catch { return false; }
    await sleep(800);
  }
  return false;
}

// ---------- 안전 ----------

export function setSource(source) {
  return guard("데이터 전환", async () => {
    const sim = await putSim({ source });
    toast(sim.mode === "live" ? "실제 데이터로 전환" : `가상 상황(${sim.scenario})으로 전환`);
    await loadSafety();
    if (document.getElementById("apply-now")?.checked) await scanAndTick();
  });
}

/** 시나리오 = 날씨 시간표 프리셋 (지금 기준 절대 구간). untilMs 가 있으면 그 시각에 끝난다. */
export function setScenario(name, untilMs = null) {
  return guard("시나리오", async () => {
    const body = { scenario: name };
    if (untilMs != null && name !== "calm") body.until = kstIso(untilMs);
    await putSim(body);
    toast(`가상 상황: ${name}${body.until ? ` (~${fmtKst(untilMs, { seconds: false })})` : ""}`);
    await loadSafety();
    if (document.getElementById("apply-now")?.checked) await scanAndTick();
  });
}

/** 🔒 날씨 시간표 전체 교체 [{kind, start, end}]. 데이터 모드는 바꾸지 않는다 (실제 데이터면 기억만). */
export function setTimeline(timeline, label) {
  return guard("시간표", async () => {
    await putSim({ timeline });
    toast(label);
    await loadSafety();
    if (document.getElementById("apply-now")?.checked && S.sim?.mode === "sim") await scanAndTick();
  });
}

/** 🔒 planner 안전 점검 지금 → 🔒 trip 틱 (?now=가상 시각). 시계 캐시를 먼저 맞춘다 (README §4). */
export function scanAndTick() {
  return guard("안전 점검·틱", async () => {
    const target = simNow();
    setBusy("scan", "시계 맞추는 중…");
    try {
      await waitClock(target);
      setBusy("scan", "안전 점검 중…");
      const rep = await api.post("/planner/safety/scan");
      const nAlerts = rep?.alerts?.length || 0;
      let msg = rep?.ran ? `안전 점검: 계획 ${rep.plans}개, 새 경고 ${nAlerts}개` : `안전 점검: ${rep?.reason || "건너뜀"}`;
      if (S.planId != null && tripActive()) {
        setBusy("scan", "틱…");
        const r = await api.post(`/trip/plans/${S.planId}/tick?now=${encodeURIComponent(kstIso(simNow()))}`);
        applyTrip(r.state);
        mergeEvents(r.events);
        showEventBubbles(r.events);
        msg += ` · 틱: 이벤트 ${r.events.length}개`;
      } else if (S.planId != null) {
        msg += " · 여행 중이 아니라 틱은 건너뜀";
      }
      toast(msg);
      await Promise.allSettled([loadSafety(), loadAlerts(), loadEvents(true)]);
    } finally {
      setBusy("scan", null);
    }
  });
}

// ---------- 여행 ----------

export function startTrip() {
  return guard("여행 시작", async () => {
    if (S.planId == null) return;
    if (!S.simDisabled) {
      setBusy("trip", "시계 맞추는 중…");
      await waitClock(simNow());
    }
    setBusy("trip", null);
    try {
      const st = await api.post(`/trip/plans/${S.planId}/start`, {});
      applyTrip(st);
      toast(`여행 시작: ${st.day}일차 ${st.date}`);
      setTripError(null);
      await Promise.allSettled([loadEvents(), loadPlan()]);
    } catch (e) {
      if (e.code === "not_trip_day") {
        const d = e.detail || {};
        setTripError(`${e.message} — 가상 시각을 계획 날짜(${d.start_date || S.plan?.start_date}${d.end_date && d.end_date !== d.start_date ? ` ~ ${d.end_date}` : ""})로 옮긴 뒤 다시 시작하세요.`, true);
        return;
      }
      throw e;
    }
  }).finally(() => setBusy("trip", null));
}

export function endTrip() {
  if (!confirm("여행을 종료할까요? (보내지 못한 Agent 선제 턴은 suppressed 가 됩니다)")) return Promise.resolve();
  return guard("여행 종료", async () => {
    const st = await api.post(`/trip/plans/${S.planId}/end`, {});
    applyTrip(st);
    toast("여행을 종료했습니다");
    await loadEvents();
  });
}

let tripErrorFn = null;
export function onTripError(fn) { tripErrorFn = fn; }
function setTripError(msg, withMove = false) { tripErrorFn?.(msg, withMove); }

let busyFn = null;
export function onBusy(fn) { busyFn = fn; }
function setBusy(kind, text) { busyFn?.(kind, text); }

// ---------- 위치 ----------

function point(lat, lng, atMs) {
  return { lat, lng, accuracy: 10, recorded_at: kstIso(atMs), source: "sim" };
}

/** 위치 전송 결과 반영: 상태·이벤트·버려진 점. */
function applyLocation(r) {
  applyTrip(r.state);
  mergeEvents(r.events);
  showEventBubbles(r.events);
  if (S.motion) {
    S.motion.sent += r.accepted;
    S.motion.ignored += r.ignored;
    emit("motion");
  } else if (r.ignored > 0) {
    toast(`버려진 점 ${r.ignored}개 (시각이 이전 위치보다 이름)`, "warn");
  }
}

function needTrip() {
  if (S.planId == null) { toast("계획을 먼저 고르세요", "warn"); return false; }
  if (!tripActive()) { toast("여행을 먼저 시작하세요", "warn"); return false; }
  if (S.motion) { toast("이동 중입니다. [정지] 후 다시 하세요", "warn"); return false; }
  return true;
}

/** 출발 시각: 지금 가상 시각, 단 마지막 위치보다 이르면 그 1초 뒤 (안 그러면 서버가 버린다). */
function startTime() {
  const rec = ms(S.trip?.location?.recorded_at);
  const now = simNow();
  return Number.isFinite(rec) && rec >= now ? rec + 1000 : now;
}

/** 순간이동: 점 하나, recorded_at = 지금 가상 시각. */
export function teleport(lat, lng) {
  if (!needTrip()) return Promise.resolve();
  return guard("순간이동", async () => {
    const t = startTime();
    if (t > simNow() + 1000 && !S.simDisabled) await putSim({ now: kstIso(t) });
    const r = await api.post(`/trip/plans/${S.planId}/location`, point(lat, lng, t));
    applyLocation(r);
    toast(`순간이동 ${lat.toFixed(5)}, ${lng.toFixed(5)}${r.events.length ? ` · 이벤트 ${r.events.length}개` : ""}`);
  });
}

/** 점 목록 [{lat,lng,t}] 을 가상 시각과 함께 재생한다. 점마다 🔒 PUT /safety/sim {now} → 위치 전송 → 배속만큼 기다림. */
async function play(points, label) {
  const speed = getSpeed();
  S.motion = { label, stop: false, sent: 0, ignored: 0, total: points.length, done: 0 };
  emit("motion");
  let simOk = !S.simDisabled;
  try {
    if (speed === 0) {
      // 기다리지 않음: 100점씩 묶어 보내고 시각은 마지막에 한 번
      for (let i = 0; i < points.length && !S.motion.stop; i += 100) {
        const chunk = points.slice(i, i + 100);
        const r = await api.post(`/trip/plans/${S.planId}/location`,
          { points: chunk.map((p) => point(p.lat, p.lng, p.t)) });
        applyLocation(r);
        S.motion.done = Math.min(points.length, i + 100);
        emit("motion");
      }
      if (simOk && points.length) await putSim({ now: kstIso(points[points.length - 1].t) });
    } else {
      let prevT = null;
      for (const p of points) {
        if (S.motion.stop) break;
        if (prevT != null) {
          const wait = (p.t - prevT) / speed;
          const until = performance.now() + wait;
          while (performance.now() < until && !S.motion.stop) await sleep(Math.min(200, until - performance.now()));
          if (S.motion.stop) break;
        }
        prevT = p.t;
        if (simOk) {
          try {
            await putSim({ now: kstIso(p.t) });
          } catch (e) {
            if (isAdminOnly(e)) throw e;
            simOk = false; // 가상 시각을 못 쓰면 recorded_at 만으로 진행
            toast(`가상 시각을 옮기지 못해 위치 시각만으로 진행합니다: ${errText(e)}`, "warn");
          }
        }
        const r = await api.post(`/trip/plans/${S.planId}/location`, point(p.lat, p.lng, p.t));
        applyLocation(r);
        S.motion.done += 1;
        emit("motion");
      }
    }
    const m = S.motion;
    toast(`${label} ${m.stop ? "정지" : "끝"} · 점 ${m.sent}개${m.ignored ? ` · 버려진 점 ${m.ignored}개 (시각이 이전 위치보다 이름)` : ""}`,
      m.ignored ? "warn" : "info");
  } finally {
    S.motion = null;
    emit("motion");
    loadPlan().catch(() => {});
    loadEvents().catch(() => {});
  }
}

export function stopMotion() {
  if (S.motion) {
    S.motion.stop = true;
    emit("motion");
  }
}

/** 걸어가기(walk) / 차로 가기(car): 경로선을 따라 일정 간격 점, 속도로 점마다 가상 시각을 계산. */
export function travel(lat, lng, mode = "walk", label = null) {
  if (!needTrip()) return Promise.resolve();
  return guard(mode === "car" ? "차로 가기" : "걸어가기", async () => {
    const loc = S.trip.location;
    if (!loc) {
      toast("현재 위치가 없어 목적지로 순간이동합니다", "warn");
      await teleport(lat, lng);
      return;
    }
    const from = [loc.lat, loc.lng];
    const to = [lat, lng];
    let line;
    try {
      const r = await api.post("/routing/route", {
        mode, stops: [{ lat: from[0], lng: from[1] }, { lat: to[0], lng: to[1] }], steps: false,
      });
      line = r.geometry.coordinates.map(([x, y]) => [y, x]);
      if (line.length < 2) line = [from, to];
    } catch (e) {
      toast(`경로를 못 받아 직선으로 갑니다: ${errText(e)}`, "warn");
      line = [from, to];
    }
    const step = mode === "car" ? CAR_STEP_M : WALK_STEP_M;
    const perMin = mode === "car" ? CAR_M_PER_MIN : WALK_M_PER_MIN;
    const pts = resample(line, step);
    const t0 = startTime();
    let t = t0;
    let prev = from;
    const out = [];
    for (const p of pts) {
      t += (dist(prev, p) / perMin) * 60e3;
      prev = p;
      out.push({ lat: p[0], lng: p[1], t: Math.round(t / 1000) * 1000 });
    }
    // 같은 초로 반올림된 점은 하나만
    const uniq = out.filter((p, i) => i === 0 || p.t > out[i - 1].t || i === out.length - 1);
    const meters = Math.round(line.reduce((s, p, i) => (i ? s + dist(line[i - 1], p) : 0), 0));
    const minutes = Math.round(((uniq.at(-1)?.t ?? t0) - t0) / 60e3);
    await play(uniq, `${label || (mode === "car" ? "차로 가기" : "걸어가기")} (${meters}m, 약 ${minutes}분)`);
  });
}

/** 머물기: 같은 좌표로 30초(가상) 간격 점을 N분 동안 (도착 판정은 반경 안 90초 이상). 좌표를 주지 않으면 현재 위치. */
export function stay(minutes = 3, lat = null, lng = null, label = null) {
  if (!needTrip()) return Promise.resolve();
  return guard("머물기", async () => {
    const loc = S.trip.location;
    const la = lat ?? loc?.lat;
    const ln = lng ?? loc?.lng;
    if (la == null) { toast("현재 위치가 없습니다. 먼저 순간이동하세요", "warn"); return; }
    const t0 = startTime();
    const pts = [];
    for (let s = 0; s <= minutes * 60; s += STAY_POINT_SEC) pts.push({ lat: la, lng: ln, t: t0 + s * 1000 });
    if ((minutes * 60) % STAY_POINT_SEC) pts.push({ lat: la, lng: ln, t: t0 + minutes * 60e3 });
    await play(pts, `${label ? `${label}에서 ` : ""}${minutes}분 머물기`);
  });
}

/** 다시 읽기 (연결·설정을 바꾼 뒤). */
export async function reloadAll() {
  await Promise.allSettled([loadSim(), loadSafety(), loadTrip(), loadEvents(true), loadPlan(), loadAlerts()]);
}
