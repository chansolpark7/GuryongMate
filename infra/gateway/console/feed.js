// 오른쪽 피드 "지금 일어난 일": 이벤트를 사람 문장 카드로 (redesign.md §2.5). 레벨·전송 상태 원문은 개발자 정보에.
import { S, on, prefs, setPref, tripActive } from "./store.js";
import { invalidate } from "./map.js";
import { eventMeta } from "./ui.js";
import { h } from "./util.js";

const $ = (id) => document.getElementById(id);
const SHOW = 8;
let showAll = false;
let seenMax = 0; // 접어 둔 동안 새로 생긴 이벤트 수를 세는 기준 (가장 큰 id)
let firstLoad = true;
const fresh = new Set(); // 화면을 연 뒤 새로 들어온 이벤트 (6초 강조)
const known = new Set();

export function initFeed() {
  $("feed-rail").addEventListener("click", () => setOpen(true));
  $("feed-close").addEventListener("click", () => setOpen(false));
  $("feed-more").addEventListener("click", () => { showAll = !showAll; render(); });
  on("events", render);
  on("trip", render);
  on("guide", renderGuide);
  // 960px 폭(발표 시 에뮬레이터와 나란히)에서는 접힌 상태로 시작
  apply(prefs.feedOpen ?? window.innerWidth >= 1200);
  render();
}

function apply(open) {
  document.body.classList.toggle("feed-closed", !open);
  $("feed-rail").setAttribute("aria-expanded", String(open));
  if (open) seenMax = maxId();
  updateBadge();
  setTimeout(invalidate, 220);
}

function setOpen(open) {
  setPref("feedOpen", open);
  apply(open);
}

const maxId = () => Math.max(0, ...S.events.keys());

function updateBadge() {
  const open = !document.body.classList.contains("feed-closed");
  if (open) seenMax = maxId();
  const n = [...S.events.keys()].filter((id) => id > seenMax).length;
  const b = $("feed-badge");
  b.hidden = !n;
  b.textContent = n > 9 ? "9+" : String(n);
}

function render() {
  const list = [...S.events.values()].sort((a, b) => b.id - a.id);
  if (firstLoad && list.length) { firstLoad = false; seenMax = Math.max(seenMax, ...list.map((e) => e.id)); }
  else if (!firstLoad) {
    for (const e of list) {
      if (e.id > seenMax && !known.has(e.id)) {
        known.add(e.id);
        fresh.add(e.id);
        setTimeout(() => { fresh.delete(e.id); render(); }, 6000);
      }
    }
  }
  updateBadge();
  renderGuide();
  const ul = $("feed-list");
  if (!list.length) {
    firstLoad = true; // 계획을 바꾸거나 지운 뒤 처음 읽는 것은 강조하지 않는다
    ul.replaceChildren(h("li.feed-empty", S.planId == null ? "계획이 없어요" : tripActive() ? "아직 일어난 일이 없어요" : "여행을 시작하면 여기에 쌓여요"));
    $("feed-more").hidden = true;
    return;
  }
  const shown = showAll ? list : list.slice(0, SHOW);
  ul.replaceChildren(...shown.map(card));
  $("feed-more").hidden = list.length <= SHOW;
  $("feed-more").textContent = showAll ? "최근 것만" : `더 보기 (${list.length - SHOW})`;
}

/** 가이드 노트 준비 한 줄 (계정 초기화에서 "가이드 노트도 지금 준비"를 켰을 때). 이벤트 목록과 따로 그려 폴링에 흔들리지 않게. */
function renderGuide() {
  const j = S.guideJob;
  const el = $("feed-guide");
  el.hidden = !j || j.plan_id !== S.planId;
  if (el.hidden) return;
  const done = j.status !== "running";
  el.className = `feed-guide${done ? (j.failed ? " fail" : " ok") : ""}`;
  el.textContent = done
    ? `📝 가이드 노트 ${j.ready}/${j.total} 준비됨${j.failed ? ` · ${j.failed}곳 실패` : ""}`
    : `📝 가이드 노트 준비 중 ${j.finished}/${j.total}${j.eta_sec ? ` · 약 ${Math.max(1, Math.round(j.eta_sec / 60))}분 남음` : ""}`;
}

function card(e) {
  const m = eventMeta(e.type);
  const ai = e.level >= 3;
  const delivery = e.status === "queued" ? h("span.dl.wait", "곧 전달")
    : e.status === "failed" ? h("span.dl.fail", "전달 실패") : null;
  const aiLine = ai && (e.status === "delivered" || e.status === "handled" || e.agent)
    ? h("div.ai-line", "✨ 구룡메이트가 먼저 말을 걸었어요")
    : ai && e.status === "queued" ? h("div.ai-line.wait", "✨ 구룡메이트가 곧 말을 걸어요") : null;
  return h(`li.fcard.sev-${e.severity}${ai ? ".ai" : ""}${fresh.has(e.id) ? ".fresh" : ""}`,
    h("span.ic", m.icon),
    h("div.fc-body",
      h("div.fc-top", h("b", m.ko), h("span.time", e.now.slice(11, 16)), delivery),
      h("div.msg", e.message),
      aiLine));
}
