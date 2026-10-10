// 구룡메이트 데모 관제 대시보드 — 시작·로그인·설정·폴링. 명세: docs/console/README.md
import { api, ApiError, errText } from "./api.js";
import { S, emit, tripActive } from "./store.js";
import { loadAlerts, loadConn, loadEvents, loadPlans, loadSafety, loadSim, loadTrip, selectPlan } from "./data.js";
import { reloadAll } from "./actions.js";
import { initMap, invalidate } from "./map.js";
import { initPanels, renderConnDetail } from "./panels.js";
import { toast } from "./ui.js";

const $ = (id) => document.getElementById(id);
const PANEL_KEY = "gm.console.panelHidden";

// ---------- 폴링 ----------

/** fn 을 주기마다 (앞 호출이 끝난 뒤). 실패는 조용히 (연결 표시가 알린다). */
function every(msec, fn, when = () => true) {
  let running = false;
  const run = async () => {
    if (running || !when()) return;
    running = true;
    try { await fn(); } catch (e) { onPollError(e); } finally { running = false; }
  };
  setInterval(run, msec);
  return run;
}

let authPrompted = false;
function onPollError(e) {
  if (e instanceof ApiError && e.status === 401 && !authPrompted) {
    authPrompted = true;
    api.setTokens(null);
    openLogin("로그인이 만료됐습니다. 다시 로그인하세요.");
  }
}

const signedIn = () => api.signedIn;
const hasPlan = () => signedIn() && S.planId != null;

function startPolling() {
  every(5000, loadConn);
  every(5000, loadSim);
  every(10000, loadSafety);
  every(5000, loadPlans, signedIn);
  every(2000, loadTrip, hasPlan);
  // 이벤트: 여행 중 2초, 아니면 10초
  let tick = 0;
  every(2000, async () => {
    tick += 1;
    if (tripActive() || tick % 5 === 0) await loadEvents(tick % 10 === 0);
  }, hasPlan);
  every(15000, loadAlerts, hasPlan);
}

// ---------- 로그인·설정 ----------

function openLogin(msg) {
  const dlg = $("dlg-login");
  $("login-server").textContent = api.base;
  const err = $("login-error");
  err.hidden = !msg;
  err.textContent = msg || "";
  if (!dlg.open) dlg.showModal();
  setTimeout(() => $(($("login-id").value ? "login-pw" : "login-id")).focus(), 0);
}

async function afterSignIn() {
  authPrompted = false;
  $("user-label").textContent = api.userLabel;
  try {
    await loadPlans();
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) { openLogin("로그인이 필요합니다."); return; }
    toast(`계획 목록 실패: ${errText(e)}`, "error");
  }
  await reloadAll();
}

function initDialogs() {
  $("login-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const id = $("login-id").value.trim();
    const pw = $("login-pw").value;
    $("btn-login").disabled = true;
    try {
      await api.login(id, pw);
      $("login-pw").value = "";
      $("dlg-login").close();
      toast(`${api.userLabel} 로그인`);
      await afterSignIn();
    } catch (e) {
      $("login-error").hidden = false;
      $("login-error").textContent = e.status === 401 ? "아이디 또는 비밀번호가 맞지 않습니다" : errText(e);
    } finally {
      $("btn-login").disabled = false;
    }
  });
  $("btn-dev-user").addEventListener("click", async () => {
    const id = $("login-id").value.trim();
    if (!id) return;
    api.setTokens(null);
    api.setDevUser(id);
    try {
      await api.get("/planner/plans");
    } catch (e) {
      api.setDevUser("");
      $("login-error").hidden = false;
      $("login-error").textContent = e.status === 401
        ? "이 서버는 로그인이 필요합니다 (AUTH_REQUIRED=true) — 비밀번호로 로그인하세요" : errText(e);
      return;
    }
    $("dlg-login").close();
    toast(`개발 모드: X-User-Id ${id}`);
    await afterSignIn();
  });
  $("btn-login-settings").addEventListener("click", () => openSettings());
  $("dlg-login").addEventListener("cancel", (ev) => { if (!api.signedIn) ev.preventDefault(); });

  $("btn-logout").addEventListener("click", async () => {
    await api.logout();
    selectPlan(null, false);
    S.plans = [];
    emit("plans");
    $("user-label").textContent = "-";
    openLogin();
  });

  $("btn-settings").addEventListener("click", openSettings);
  $("btn-admin-clear").addEventListener("click", () => {
    api.setAdminToken("");
    $("set-admin").value = "";
    renderAdminState();
    renderConnDetail();
    toast("관리 토큰을 지웠습니다");
  });
  $("settings-form").addEventListener("submit", async (ev) => {
    const action = ev.submitter?.value;
    if (action !== "save") return; // 닫기
    const base = $("set-base").value.trim();
    const changedBase = (base || location.origin).replace(/\/+$/, "") !== api.base;
    api.setBase(base);
    if ($("set-admin").value) api.setAdminToken($("set-admin").value);
    $("set-admin").value = "";
    renderAdminState();
    toast("설정을 저장했습니다");
    if (changedBase) {
      api.setTokens(null);
      api.setDevUser("");
      selectPlan(null, false);
      S.zones = [];
      await loadConn().catch(() => {});
      openLogin("서버를 바꿨습니다. 다시 로그인하세요.");
    } else {
      await Promise.allSettled([loadConn(), reloadAll()]);
    }
  });

  $("plan-select").addEventListener("change", (ev) => {
    const id = Number(ev.target.value) || null;
    selectPlan(id);
  });

  $("btn-panel").addEventListener("click", () => setPanelHidden(!document.body.classList.contains("panel-hidden")));
}

function openSettings() {
  $("set-base").value = api.base === location.origin ? "" : api.base;
  $("set-admin").value = "";
  renderAdminState();
  renderConnDetail();
  $("dlg-settings").showModal();
}

function renderAdminState() {
  $("admin-state").textContent = api.adminToken ? "지금: 입력됨" : "지금: 없음";
}

function setPanelHidden(hidden) {
  document.body.classList.toggle("panel-hidden", hidden);
  $("btn-panel").textContent = hidden ? "◂ 패널" : "패널 ▸";
  $("btn-panel").setAttribute("aria-expanded", String(!hidden));
  try { localStorage.setItem(PANEL_KEY, hidden ? "1" : "0"); } catch { /* 무시 */ }
  setTimeout(invalidate, 50);
}

// ---------- 시작 ----------

async function main() {
  initMap();
  initPanels();
  initDialogs();
  try { if (localStorage.getItem(PANEL_KEY) === "1") setPanelHidden(true); } catch { /* 무시 */ }
  window.addEventListener("resize", () => invalidate());

  await Promise.allSettled([loadConn(), loadSim(), loadSafety()]);
  startPolling();
  if (!api.signedIn) {
    openLogin();
    return;
  }
  await afterSignIn();
}

main();
