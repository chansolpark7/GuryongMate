// 알림(토스트)·이벤트 말풍선·이벤트 이름
import { h } from "./util.js";

export const EVENT_KO = {
  arrived: "도착",
  departed: "출발",
  delay: "지연",
  safety_raised: "안전 상승",
  safety_eased: "안전 완화",
  near_hazard: "위험 구역 접근",
  day_started: "여행 시작",
  day_ended: "여행 종료",
};

export const STATUS_KO = {
  queued: "대기",
  delivered: "전달",
  suppressed: "억제",
  handled: "확인됨",
  failed: "실패",
};

export function toast(text, kind = "info") {
  const box = document.getElementById("toasts");
  if (!box) return;
  const node = h(`div.toast.${kind}`, { role: kind === "error" ? "alert" : "status" }, text);
  node.addEventListener("click", () => node.remove());
  box.prepend(node);
  while (box.children.length > 5) box.lastChild.remove();
  setTimeout(() => node.remove(), kind === "error" || kind === "admin" ? 9000 : 5000);
}

let bubbleFn = null;
/** map.js 가 등록한다: 이번에 생긴 이벤트를 지도에 잠깐 말풍선으로. */
export function onEventBubbles(fn) { bubbleFn = fn; }
export function showEventBubbles(events) {
  if (events?.length) bubbleFn?.(events);
}
