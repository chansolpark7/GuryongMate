// 조작: 가상 시각(바늘·재생)·날씨·여행 시작/종료·위치(순간이동·걸어가기·머물기). 🔒 = 운영에서 관리 토큰 필요
import { api, errText, isAdminOnly } from "./api.js";
import { S, canSim, emit, prefs, simNow, tripActive } from "./store.js";
import { applySim, applyTrip, loadAlerts, loadEvents, loadPlan, loadSafety, loadSim, loadTrip, mergeEvents } from "./data.js";
import { dist, fmtKst, josa, kstIso, ms, resample, sleep } from "./util.js";
import { kindMeta, toast, showEventBubbles } from "./ui.js";

export const WALK_M_PER_MIN = 70; // 도보 (routing 기본 4.2km/h)
export const CAR_M_PER_MIN = 500; // 차 30km/h 가정
const WALK_STEP_M = 25; // 걸을 때 점 간격
const CAR_STEP_M = 60; // 차는 점 간격 약 7초 (위험 구역 접근 50m 를 크게 건너뛰지 않게)
const STAY_POINT_SEC = 30;
export const CONTINUE_HOURS = 30; // 날씨 "계속" = 지금부터 30시간 (폭풍 프리셋과 같은 길이)

/** 실패를 알리고 다시 던지지 않는다 (버튼 핸들러용). 성공하면 fn 의 결과, 실패하면 undefined. */
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
export function confirmBackward(targetMs) {
  const rec = ms(S.trip?.location?.recorded_at);
  if (Number.isFinite(rec) && targetMs < rec - 1000) {
    return confirm(
      `이전 위치(${fmtKst(rec, { seconds: false })})보다 이른 시각이에요.\n` +
      `${fmtKst(targetMs, { seconds: false })} 로 옮기면 그 시각을 지날 때까지 보내는 위치는 서버가 버립니다. 계속할까요?`);
  }
  return true;
}

/** 🔒 PUT /safety/sim. */
export async function putSim(body) {
  const sim = await api.put("/safety/sim", body);
  applySim(sim);
  return sim;
}

/** 가상 시각을 targetMs 로. 성공하면 true. */
export async function setClock(targetMs, { quiet = false } = {}) {
  if (!confirmBackward(targetMs)) return false;
  const ok = await guard("시각 바꾸기", async () => {
    await putSim({ now: kstIso(targetMs) });
    if (!quiet) toast(`가상 시각 ${fmtKst(targetMs, { seconds: false })}`);
    refreshAfterClock();
    return true;
  });
  return !!ok;
}

export function shiftClock(minutes) {
  return setClock(simNow() + minutes * 60e3);
}

export function resetClock() {
  stopPlay();
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

// ---------- ▶ 재생: 가상 시각을 배속으로 흘린다 (위치 전송 없이 시간만) ----------

export function startPlay(speed = prefs.playSpeed) {
  if (S.play || !canSim()) return;
  if (S.motion) { toast("이동 중에는 재생할 수 없어요. [멈춤] 후 다시 하세요", "warn"); return; }
  const play = { speed, stop: false };
  S.play = play;
  emit("play");
  (async () => {
    const t0 = simNow();
    const r0 = performance.now();
    try {
      while (!play.stop) {
        await sleep(1000);
        if (play.stop) break;
        const target = t0 + (performance.now() - r0) * play.speed;
        await putSim({ now: kstIso(target) });
      }
    } catch (e) {
      toast(`재생 멈춤: ${errText(e)}`, isAdminOnly(e) ? "admin" : "error");
    } finally {
      if (S.play === play) S.play = null;
      emit("play");
      refreshAfterClock();
    }
  })();
}

export function stopPlay() {
  if (S.play) {
    S.play.stop = true;
    S.play = null;
    emit("play");
  }
}

export function setPlaySpeed(speed) {
  if (S.play) S.play.speed = speed;
}

// ---------- 안전 ----------

export function setSource(source) {
  return guard("데이터 전환", async () => {
    const sim = await putSim({ source });
    toast(sim.mode === "live" ? "📡 실시간 DT 로 전환" : "🎬 가상 시나리오로 전환");
    await afterWeather();
  });
}

/** 시나리오 = 날씨 시간표 프리셋 (지금 기준 절대 구간). untilMs 가 있으면 그 시각에 끝난다. (개발자 정보) */
export function setScenario(name, untilMs = null) {
  return guard("시나리오", async () => {
    const body = { scenario: name };
    if (untilMs != null && name !== "calm") body.until = kstIso(untilMs);
    await putSim(body);
    toast(`프리셋 ${name}${body.until ? ` (~${fmtKst(untilMs, { seconds: false })})` : ""}`);
    await afterWeather();
  });
}

/** 🔒 날씨 시간표 전체 교체 [{kind, start, end}]. live 면 sourceMock 으로 가상 시나리오로 함께 바꿀 수 있다. */
export function setTimeline(timeline, label, { sourceMock = false } = {}) {
  return guard("날씨 바꾸기", async () => {
    const body = { timeline };
    if (sourceMock && S.sim?.mode === "live" && S.sim?.switchable) body.source = "mock";
    await putSim(body);
    toast(label);
    if (S.sim?.mode === "sim") await afterWeather();
    else await loadSafety();
    return true;
  });
}

/** 날씨를 바꾼 뒤: 안전 상황 다시 읽기 + (기본) 바로 안전 점검 + 틱. 개발자 정보에서 끌 수 있다. */
async function afterWeather() {
  await loadSafety();
  if (prefs.autoScan) await scanAndTick({ quiet: true });
}

const segLabel = (kind, a, b) =>
  `${kindMeta(kind).icon} ${kindMeta(kind).ko} ${fmtKst(a, { date: false, seconds: false })}~${Number.isFinite(b) ? fmtKst(b, { date: false, seconds: false }) : ""}`;

/** 구간 [a, b) 를 시간표에서 비운다 (걸치는 구간은 잘라서 남긴다). b = Infinity 면 a 뒤 전부. */
function cut(timeline, a, b) {
  const out = [];
  for (const x of timeline) {
    const s = ms(x.start);
    const e = ms(x.end);
    if (e <= a || s >= b) { out.push(x); continue; }
    if (s < a) out.push({ kind: x.kind, start: x.start, end: kstIso(a) });
    if (e > b) out.push({ kind: x.kind, start: kstIso(b), end: x.end });
  }
  return out;
}

/** 날씨 구간 추가: kind 를 [a, b) 에. calm(평상시)은 그 시간을 비운다. 실시간 DT 면 가상 시나리오로 함께 바꾼다. */
export function addWeather(kind, a, b) {
  const tl = (S.sim?.timeline || []).map((x) => ({ kind: x.kind, start: x.start, end: x.end }));
  if (kind === "calm") {
    return setTimeline(cut(tl, a, b), `☀️ 평상시 ${fmtKst(a, { date: false, seconds: false })}부터${Number.isFinite(b) ? `~${fmtKst(b, { date: false, seconds: false })}` : ""}`,
      { sourceMock: true });
  }
  return setTimeline([...tl, { kind, start: kstIso(a), end: kstIso(b) }], `${segLabel(kind, a, b)} 추가`, { sourceMock: true });
}

export function deleteSegment(index) {
  const tl = S.sim?.timeline || [];
  const x = tl[index];
  if (!x) return Promise.resolve();
  return setTimeline(tl.filter((_, j) => j !== index), `${segLabel(x.kind, ms(x.start), ms(x.end))} 삭제`);
}

export function setSegmentEnd(index, endMs) {
  const tl = S.sim?.timeline || [];
  const x = tl[index];
  if (!x) return Promise.resolve();
  if (endMs <= ms(x.start)) { toast("끝 시각은 시작보다 뒤여야 해요", "warn"); return Promise.resolve(); }
  const next = tl.map((y, j) => (j === index ? { kind: y.kind, start: y.start, end: kstIso(endMs) } : y));
  return setTimeline(next, `${segLabel(x.kind, ms(x.start), endMs)} 로 바꿈`);
}

const busyFns = [];
/** 오래 걸리는 조작의 진행 문구 (kind: scan | trip, text: null 이면 끝). */
export function onBusy(fn) { busyFns.push(fn); }
function setBusy(kind, text) { for (const fn of busyFns) fn(kind, text); }

/** 🔒 planner 안전 점검 지금 → 🔒 trip 틱 (?now=가상 시각). 시계 캐시를 먼저 맞춘다 (README §4). */
export function scanAndTick({ quiet = false } = {}) {
  return guard("안전 점검", async () => {
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
      }
      if (!quiet) toast(msg);
      await Promise.allSettled([loadSafety(), loadAlerts(), loadEvents(true)]);
    } finally {
      setBusy("scan", null);
    }
  });
}

// ---------- 여행 ----------

/** 계획 첫날 첫 일정 10분 전 (없으면 day_start). */
export function planStartMs() {
  const p = S.plan || S.plans.find((x) => x.id === S.planId);
  if (!p) return null;
  const first = p.days?.[0]?.items?.find((i) => i.status !== "skipped")?.start_at;
  const hm = first || p.day_start || "09:00";
  const t = Date.parse(`${p.start_date}T${hm.slice(0, 5)}:00+09:00`);
  return first ? t - 10 * 60e3 : t;
}

/** 가상 시각의 날짜가 계획 날짜 안인지. */
export function isTripDay(t = simNow()) {
  const p = S.plan || S.plans.find((x) => x.id === S.planId);
  if (!p) return false;
  const d = kstIso(t).slice(0, 10);
  return d >= p.start_date && d <= (p.end_date || p.start_date);
}

export function startTrip() {
  stopPlay();
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
      toast(`🏁 여행 시작 (${st.day}일차)`);
      await Promise.allSettled([loadEvents(), loadPlan()]);
    } catch (e) {
      if (e.code === "not_trip_day") {
        toast(`${e.message} — 계획 날짜로 옮긴 뒤 다시 시작하세요`, "warn");
        return;
      }
      throw e;
    }
  }).finally(() => setBusy("trip", null));
}

/** [계획 날짜로 이동 후 시작]: 가상 시각을 첫 일정 10분 전으로 → 시작. */
export async function moveAndStart() {
  const t = planStartMs();
  if (t == null) return;
  if (!(await setClock(t, { quiet: true }))) return;
  await startTrip();
}

export function endTrip() {
  if (!confirm("여행을 끝낼까요? (아직 보내지 못한 구룡메이트 메시지는 보내지 않아요)")) return Promise.resolve();
  stopMotion();
  return guard("여행 종료", async () => {
    const st = await api.post(`/trip/plans/${S.planId}/end`, {});
    applyTrip(st);
    toast("🏁 여행을 끝냈습니다");
    await loadEvents();
  });
}

// ---------- 위치 ----------

function point(lat, lng, atMs) {
  return { lat, lng, accuracy: 10, recorded_at: kstIso(atMs), source: "sim" };
}

/** 위치 전송 결과 반영: 상태·이벤트·버려진 점. */
function applyLocation(r, kind) {
  applyTrip(r.state);
  mergeEvents(r.events);
  showEventBubbles(r.events);
  S.locLog.unshift({ at: simNow(), kind, accepted: r.accepted, ignored: r.ignored, events: r.events.length });
  S.locLog.length = Math.min(S.locLog.length, 20);
  if (S.motion) {
    S.motion.sent += r.accepted;
    S.motion.ignored += r.ignored;
  } else if (r.ignored > 0) {
    toast(`버려진 위치 ${r.ignored}개 (이전 위치보다 이른 시각)`, "warn");
  }
  emit("motion");
}

function needTrip() {
  if (S.planId == null) { toast("계획이 없어요", "warn"); return false; }
  if (!tripActive()) { toast("여행을 먼저 시작하세요", "warn"); return false; }
  if (S.motion) { toast("이동 중이에요. [멈춤] 후 다시 하세요", "warn"); return false; }
  stopPlay();
  return true;
}

/** 출발 시각: 지금 가상 시각, 단 마지막 위치보다 이르면 그 1초 뒤 (안 그러면 서버가 버린다). */
function startTime() {
  const rec = ms(S.trip?.location?.recorded_at);
  const now = simNow();
  return Number.isFinite(rec) && rec >= now ? rec + 1000 : now;
}

/** 순간이동: 점 하나, recorded_at = 지금 가상 시각. */
export function teleport(lat, lng, label = null) {
  if (!needTrip()) return Promise.resolve();
  return guard("순간이동", async () => {
    const t = startTime();
    if (t > simNow() + 1000 && !S.simDisabled) await putSim({ now: kstIso(t) });
    const r = await api.post(`/trip/plans/${S.planId}/location`, point(lat, lng, t));
    applyLocation(r, "teleport");
    toast(`${label ? josa(label, "로") : "그 자리로"} 순간이동`);
  });
}

/** 점 목록 [{lat,lng,t}] 을 가상 시각과 함께 재생한다. 점마다 🔒 PUT /safety/sim {now} → 위치 전송 → 배속만큼 기다림. */
async function play(points, motion) {
  const speed = prefs.speed;
  const t0 = points[0]?.t ?? simNow();
  S.motion = { stop: false, sent: 0, ignored: 0, total: points.length, done: 0, t0, t: t0, end: points.at(-1)?.t ?? t0, ...motion };
  emit("motion");
  let simOk = !S.simDisabled;
  try {
    if (speed === 0) {
      // 기다리지 않음: 100점씩 묶어 보내고 시각은 마지막에 한 번
      for (let i = 0; i < points.length && !S.motion.stop; i += 100) {
        const chunk = points.slice(i, i + 100);
        const r = await api.post(`/trip/plans/${S.planId}/location`,
          { points: chunk.map((p) => point(p.lat, p.lng, p.t)) });
        S.motion.done = Math.min(points.length, i + 100);
        S.motion.t = chunk.at(-1).t;
        applyLocation(r, motion.kind);
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
        S.motion.done += 1;
        S.motion.t = p.t;
        applyLocation(r, motion.kind);
      }
    }
    const m = S.motion;
    if (m.ignored) toast(`버려진 위치 ${m.ignored}개 (이전 위치보다 이른 시각)`, "warn");
    else if (m.stop) toast("멈췄습니다");
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
      toast("지금 위치가 없어 목적지로 순간이동합니다", "warn");
      await teleport(lat, lng, label);
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
    await play(uniq, { kind: mode, label, meters, line });
  });
}

/** 머물기: 같은 좌표로 30초(가상) 간격 점을 N분 동안 (도착 판정은 반경 안 90초 이상). 좌표를 주지 않으면 지금 위치. */
export function stay(minutes = 3, lat = null, lng = null, label = null) {
  if (!needTrip()) return Promise.resolve();
  return guard("머물기", async () => {
    const loc = S.trip.location;
    const la = lat ?? loc?.lat;
    const ln = lng ?? loc?.lng;
    if (la == null) { toast("지금 위치가 없어요. 먼저 지도에서 이동하세요", "warn"); return; }
    const t0 = startTime();
    const pts = [];
    for (let s = 0; s <= minutes * 60; s += STAY_POINT_SEC) pts.push({ lat: la, lng: ln, t: t0 + s * 1000 });
    if ((minutes * 60) % STAY_POINT_SEC) pts.push({ lat: la, lng: ln, t: t0 + minutes * 60e3 });
    await play(pts, { kind: "stay", label, minutes });
  });
}

/** 다시 읽기 (연결·설정을 바꾼 뒤). */
export async function reloadAll() {
  await Promise.allSettled([loadSim(), loadSafety(), loadTrip(), loadEvents(true), loadPlan(), loadAlerts()]);
}
