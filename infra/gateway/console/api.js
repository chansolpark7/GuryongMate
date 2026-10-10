// 게이트웨이 API 호출 (로그인·토큰 갱신·관리 토큰). 토큰·비밀번호는 URL·콘솔 로그에 남기지 않는다.
// 설정·토큰은 이 브라우저의 localStorage 에만 둔다 (docs/console/README.md §1.2).

const KEY = {
  base: "gm.console.base",
  admin: "gm.console.adminToken",
  tokens: "gm.console.tokens",
  devUser: "gm.console.devUser",
};

function load(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function save(key, value) {
  try {
    if (value == null || value === "") localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch { /* 저장소를 못 쓰면 이번 창에서만 기억 */ }
}

export class ApiError extends Error {
  constructor(status, code, message, detail) {
    super(message);
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

/** 서버 오류 본문({"detail": {"code", "message"}} | {"detail": "..."} | 422 목록)을 사람이 읽을 문장으로. */
function parseError(status, body) {
  const d = body && body.detail;
  if (d && typeof d === "object" && !Array.isArray(d)) {
    return new ApiError(status, d.code || String(status), d.message || d.code || `HTTP ${status}`, d);
  }
  if (Array.isArray(d)) {
    const msg = d.map((x) => `${(x.loc || []).slice(1).join(".")}: ${x.msg}`).join("; ");
    return new ApiError(status, "invalid_request", msg || `HTTP ${status}`, d);
  }
  if (typeof d === "string") return new ApiError(status, d, d, d);
  return new ApiError(status, String(status), `HTTP ${status}`, body);
}

class Api {
  constructor() {
    this.base = load(KEY.base) || location.origin;
    this.adminToken = load(KEY.admin) || "";
    this.devUser = load(KEY.devUser) || "";
    try { this.tokens = JSON.parse(load(KEY.tokens) || "null"); } catch { this.tokens = null; }
    this._refreshing = null;
  }

  setBase(url) {
    this.base = (url || location.origin).replace(/\/+$/, "");
    save(KEY.base, this.base === location.origin ? "" : this.base);
  }
  setAdminToken(token) {
    this.adminToken = (token || "").trim();
    save(KEY.admin, this.adminToken);
  }
  setTokens(t) {
    this.tokens = t ? { access_token: t.access_token, refresh_token: t.refresh_token, user: t.user } : null;
    save(KEY.tokens, this.tokens ? JSON.stringify(this.tokens) : "");
  }
  setDevUser(id) {
    this.devUser = (id || "").trim();
    save(KEY.devUser, this.devUser);
  }
  get signedIn() { return !!(this.tokens || this.devUser); }
  get userLabel() {
    if (this.tokens) return this.tokens.user?.login_id || this.tokens.user?.display_name || this.tokens.user?.id || "로그인됨";
    if (this.devUser) return `${this.devUser} (X-User-Id)`;
    return "";
  }

  _headers(json) {
    const h = {};
    if (json) h["Content-Type"] = "application/json";
    if (this.tokens) h.Authorization = `Bearer ${this.tokens.access_token}`;
    else if (this.devUser) h["X-User-Id"] = this.devUser;
    if (this.adminToken) h["X-Admin-Token"] = this.adminToken;
    return h;
  }

  async _fetch(method, path, body) {
    let resp;
    try {
      resp = await fetch(this.base + path, {
        method,
        headers: this._headers(body !== undefined),
        body: body === undefined ? undefined : JSON.stringify(body),
        cache: "no-store",
      });
    } catch (e) {
      throw new ApiError(0, "network", "서버에 연결할 수 없습니다", String(e));
    }
    return resp;
  }

  async _refresh() {
    if (!this.tokens?.refresh_token) return false;
    if (!this._refreshing) {
      this._refreshing = (async () => {
        const resp = await this._fetch("POST", "/auth/refresh", { refresh_token: this.tokens.refresh_token });
        if (!resp.ok) { this.setTokens(null); return false; }
        const t = await resp.json();
        this.setTokens({ ...t, user: t.user || this.tokens?.user });
        return true;
      })().finally(() => { setTimeout(() => { this._refreshing = null; }, 0); });
    }
    return this._refreshing;
  }

  /** 요청 하나. 401(만료)이면 refresh 한 번 후 다시 보낸다. 실패는 ApiError. */
  async req(method, path, body) {
    let resp = await this._fetch(method, path, body);
    if (resp.status === 401 && this.tokens && !path.startsWith("/auth/")) {
      if (await this._refresh()) resp = await this._fetch(method, path, body);
    }
    if (resp.status === 204) return null;
    let data = null;
    const text = await resp.text();
    if (text) {
      try { data = JSON.parse(text); } catch { data = text; }
    }
    if (!resp.ok) throw parseError(resp.status, data);
    return data;
  }

  get(path) { return this.req("GET", path); }
  post(path, body) { return this.req("POST", path, body === undefined ? {} : body); }
  put(path, body) { return this.req("PUT", path, body); }

  async login(loginId, password) {
    const t = await this.req("POST", "/auth/login", { login_id: loginId, password });
    this.setDevUser("");
    this.setTokens(t);
    return t;
  }

  async logout() {
    const rt = this.tokens?.refresh_token;
    this.setTokens(null);
    this.setDevUser("");
    if (rt) {
      try { await this._fetch("POST", "/auth/logout", { refresh_token: rt }); } catch { /* 이미 끊김 */ }
    }
  }
}

export const api = new Api();

/** 관리 토큰이 필요한 API(🔒)가 403 admin_only 를 돌려줬는지. */
export function isAdminOnly(e) {
  return e instanceof ApiError && e.status === 403 && e.code === "admin_only";
}

/** 오류를 화면용 한 줄로. */
export function errText(e) {
  if (isAdminOnly(e)) return "관리 토큰이 필요한 API 입니다 — 설정(⚙)에서 관리 토큰을 입력하세요";
  if (e instanceof ApiError) {
    if (e.code === "trip_not_active") return "여행을 먼저 시작하세요 (trip_not_active)";
    if (e.code === "sim_disabled") return "가상 상황 꺼짐 — 서버에 SAFETY_SIM_ENABLED=true 필요";
    return `${e.message}${e.code && e.code !== e.message && !/^\d+$/.test(e.code) ? ` (${e.code})` : e.status ? ` (${e.status})` : ""}`;
  }
  return String(e?.message || e);
}
