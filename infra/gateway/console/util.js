// 공용 도구: 시각(KST), 거리, DOM 만들기

const KST_MS = 9 * 3600e3;

/** ms(epoch) -> "2026-10-10T15:20:00+09:00" (초 단위). */
export function kstIso(ms) {
  const d = new Date(Math.floor(ms / 1000) * 1000 + KST_MS);
  return d.toISOString().slice(0, 19) + "+09:00";
}

/** ms -> "10/10 15:21:07" (KST). */
export function fmtKst(ms, { date = true, seconds = true } = {}) {
  if (ms == null || Number.isNaN(ms)) return "-";
  const s = new Date(ms + KST_MS).toISOString();
  const md = `${s.slice(5, 7)}/${s.slice(8, 10)}`;
  const hm = seconds ? s.slice(11, 19) : s.slice(11, 16);
  return date ? `${md} ${hm}` : hm;
}

/** datetime-local 입력값(KST 로 본다) -> ms. */
export function parseLocalKst(value) {
  if (!value) return NaN;
  const v = value.length === 16 ? `${value}:00` : value;
  return Date.parse(`${v}+09:00`);
}

/** ms -> datetime-local 입력값 "YYYY-MM-DDTHH:MM:SS" (KST). */
export function toLocalKst(ms) {
  return new Date(ms + KST_MS).toISOString().slice(0, 19);
}

/** ISO -> ms (없으면 NaN). */
export const ms = (iso) => (iso ? Date.parse(iso) : NaN);

/** 두 점 [lat, lng] 사이 거리 (m). scripts/trip/scene.py _dist 와 같은 근사. */
export function dist(a, b) {
  const k = 111320 * Math.cos(((a[0] + b[0]) / 2) * Math.PI / 180);
  return Math.hypot((a[0] - b[0]) * 111320, (a[1] - b[1]) * k);
}

/** 경로선 위 step 간격 점 (첫 점 제외, 마지막 점 포함). scene.py resample 과 같다. */
export function resample(line, step) {
  const out = [];
  let walked = 0;
  let want = step;
  for (let i = 0; i + 1 < line.length; i++) {
    const a = line[i];
    const b = line[i + 1];
    const seg = dist(a, b);
    while (seg > 0 && want <= walked + seg) {
      const f = (want - walked) / seg;
      out.push([a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f]);
      want += step;
    }
    walked += seg;
  }
  const last = line[line.length - 1];
  if (last && (!out.length || dist(out[out.length - 1], last) > 1)) out.push(last);
  return out;
}

export const sleep = (msec) => new Promise((r) => setTimeout(r, msec));

/** DOM 만들기: h("button#id.primary", {onclick}, "글자", child…). 문자열 자식은 textContent 로 넣는다 (HTML 해석 안 함). */
export function h(spec, attrs, ...children) {
  let id = null;
  spec = spec.replace(/#([\w-]+)/, (_, x) => { id = x; return ""; });
  const [tag, ...classes] = spec.split(".");
  const node = document.createElement(tag || "div");
  if (id) node.id = id;
  if (classes.length) node.className = classes.join(" ");
  if (attrs && (typeof attrs !== "object" || attrs instanceof Node || Array.isArray(attrs))) {
    children.unshift(attrs);
    attrs = null;
  }
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "class") node.className += ` ${v}`;
    else if (k === "style" && typeof v === "object") Object.assign(node.style, v);
    else if (k === "dataset") Object.assign(node.dataset, v);
    else if (k in node && k !== "list") node[k] = v;
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

/** 위험 단계 -> 한국어. */
export const RISK_KO = { safe: "안전", caution: "주의", danger: "위험" };
export const riskKo = (r) => RISK_KO[r] || "판단 보류";

/** 받침에 맞는 조사: josa("가옥거리", "로") -> "가옥거리로", josa("구룡포항", "로") -> "구룡포항으로". 한글이 아니면 앞의 것. */
export function josa(word, kind) {
  const w = String(word ?? "");
  const c = w.charCodeAt(w.length - 1);
  const hangul = c >= 0xac00 && c <= 0xd7a3;
  const jong = hangul ? (c - 0xac00) % 28 : 0;
  const pairs = { 로: ["로", "으로"], 가: ["가", "이"], 는: ["는", "은"], 를: ["를", "을"], 와: ["와", "과"] };
  const [a, b] = pairs[kind] || [kind, kind];
  if (kind === "로") return w + (jong === 0 || jong === 8 ? a : b); // ㄹ 받침은 "로"
  return w + (jong === 0 ? a : b);
}

/** 분 -> "1시간 20분" / "15분". */
export function fmtMinutes(min) {
  const m = Math.max(0, Math.round(min));
  if (m < 60) return `${m}분`;
  return `${Math.floor(m / 60)}시간${m % 60 ? ` ${m % 60}분` : ""}`;
}

/** node 의 자식을 바꾼다 (null·false 는 건너뜀 — replaceChildren 은 null 을 "null" 글자로 넣는다). */
export function fill(node, ...children) {
  node.replaceChildren(...children.flat().filter((c) => c != null && c !== false));
}
