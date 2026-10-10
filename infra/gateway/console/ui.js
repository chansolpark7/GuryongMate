// 알림(토스트)·이벤트 말풍선·떠 있는 작은 메뉴(popover)·화면 문구
import { h } from "./util.js";

/** 이벤트 종류 -> 아이콘·이름 (사람 말). */
export const EVENT_META = {
  arrived: { icon: "📍", ko: "도착" },
  departed: { icon: "👋", ko: "출발" },
  delay: { icon: "⏰", ko: "늦어짐" },
  safety_raised: { icon: "⚠️", ko: "위험" },
  safety_eased: { icon: "✅", ko: "해제" },
  near_hazard: { icon: "🚧", ko: "위험 구역" },
  day_started: { icon: "🏁", ko: "여행 시작" },
  day_ended: { icon: "🏁", ko: "여행 끝" },
};
export const eventMeta = (type) => EVENT_META[type] || { icon: "•", ko: type };

export const STATUS_KO = {
  queued: "대기",
  delivered: "전달",
  suppressed: "억제",
  handled: "확인됨",
  failed: "실패",
};

/** 날씨 구간 종류 (+ 평상시). 순서 = 날씨 카드 버튼 순서. */
export const KIND_META = {
  calm: { icon: "☀️", ko: "평상시" },
  rain: { icon: "🌧", ko: "비" },
  flood: { icon: "🌊", ko: "침수" },
  wind: { icon: "💨", ko: "강풍" },
  storm: { icon: "⛈", ko: "폭풍" },
};
export const kindMeta = (k) => KIND_META[k] || { icon: "•", ko: k };

export const ITEM_STATUS_KO = { planned: "예정", visiting: "머무는 중", done: "다녀옴", skipped: "건너뜀" };

export function toast(text, kind = "info") {
  const box = document.getElementById("toasts");
  if (!box) return;
  const node = h(`div.toast.${kind}`, { role: kind === "error" ? "alert" : "status" }, text);
  node.addEventListener("click", () => node.remove());
  box.prepend(node);
  while (box.children.length > 4) box.lastChild.remove();
  setTimeout(() => node.remove(), kind === "error" || kind === "admin" ? 8000 : 4000);
}

let bubbleFn = null;
/** map.js 가 등록한다: 이번에 생긴 이벤트를 지도에 잠깐 말풍선으로. */
export function onEventBubbles(fn) { bubbleFn = fn; }
export function showEventBubbles(events) {
  if (events?.length) bubbleFn?.(events);
}

// ---------- popover: 버튼 옆에 뜨는 작은 카드. 바깥을 누르거나 Esc 면 닫힌다 ----------

let openPop = null;

export function closePopover() {
  if (!openPop) return;
  const p = openPop;
  openPop = null;
  p.node.remove();
  p.anchor?.setAttribute("aria-expanded", "false");
  p.onClose?.();
}

/**
 * anchor 아래(또는 위)에 content 를 띄운다. 같은 anchor 를 다시 누르면 닫는다.
 * place: "below" | "above". 돌려주는 render(content) 로 내용을 바꿀 수 있다.
 */
export function popover(anchor, content, { place = "below", align = "start", onClose, className = "" } = {}) {
  if (openPop && openPop.anchor === anchor) { closePopover(); return null; }
  closePopover();
  const node = h(`div.pop${className ? `.${className}` : ""}`, { role: "dialog" });
  node.append(content);
  document.body.append(node);
  const pos = () => {
    const r = anchor.getBoundingClientRect();
    const w = node.offsetWidth;
    const ht = node.offsetHeight;
    let left = align === "end" ? r.right - w : r.left;
    left = Math.max(8, Math.min(left, window.innerWidth - w - 8));
    let top = place === "above" ? r.top - ht - 8 : r.bottom + 8;
    if (top + ht > window.innerHeight - 8) top = Math.max(8, r.top - ht - 8);
    if (top < 8) top = Math.min(window.innerHeight - ht - 8, r.bottom + 8);
    node.style.left = `${left}px`;
    node.style.top = `${top}px`;
  };
  pos();
  anchor.setAttribute("aria-expanded", "true");
  openPop = { node, anchor, onClose, pos };
  return {
    node,
    render(c) { node.replaceChildren(c); pos(); },
  };
}

document.addEventListener("pointerdown", (ev) => {
  if (!openPop) return;
  if (openPop.node.contains(ev.target) || openPop.anchor.contains(ev.target)) return;
  closePopover();
}, true);
document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") closePopover(); });
window.addEventListener("resize", () => openPop?.pos());
