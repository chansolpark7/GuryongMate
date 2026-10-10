// 지도 (Leaflet, 전역 L): 일정·구간 경로선·사용자(끌어서 순간이동)·주의/위험 구역·피할 구역. 안전한 것은 기본으로 그리지 않는다.
import { api } from "./api.js";
import { S, dayItems, on, prefs, simNow, tripActive } from "./store.js";
import { stay, teleport, travel } from "./actions.js";
import { ITEM_STATUS_KO, eventMeta, onEventBubbles } from "./ui.js";
import { h, riskKo } from "./util.js";

const CENTER = [35.990, 129.556];
const RISK_COLOR = { safe: "#12A150", caution: "#D97706", danger: "#E5484D" };
const NULL_COLOR = "#8B95A1";
const SENSOR_TYPE_KO = { HOLE: "맨홀", ROAD: "지표면", RIVER: "하천", RAIN: "강우" };
const HOT = (r) => r === "caution" || r === "danger";

let map;
const layers = {};
const markers = new Map(); // item id -> marker
let meMarker = null;
let meCircle = null;
let dragging = false;
const legCache = new Map(); // "mode:a>b" -> Promise<[[lat,lng]…]>
let legKey = "";

const color = (risk) => RISK_COLOR[risk] || NULL_COLOR;

export function initMap() {
  map = L.map("map", { zoomControl: false, attributionControl: true }).setView(CENTER, 15);
  L.control.zoom({ position: "topleft" }).addTo(map);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);

  // 그리는 순서 = 쌓이는 순서
  layers.zonesCalm = L.layerGroup(); // 안전·판단 보류 구역 (기본 숨김)
  layers.zones = L.layerGroup().addTo(map); // 주의·위험 구역
  layers.avoid = L.layerGroup().addTo(map);
  layers.sensorsAll = L.layerGroup(); // 센서 전체 (기본 숨김)
  layers.sensors = L.layerGroup().addTo(map); // 등급이 오른 센서
  layers.cctv = L.layerGroup(); // 방파제 CCTV 집계 (기본 숨김)
  layers.legs = L.layerGroup().addTo(map);
  layers.plan = L.layerGroup().addTo(map);
  layers.walk = L.layerGroup().addTo(map); // 걸어가는 경로
  layers.me = L.layerGroup().addTo(map);
  layers.bubbles = L.layerGroup().addTo(map);

  L.control.layers(null, {
    "일정": layers.plan,
    "경로선": layers.legs,
    "주의·위험 구역": layers.zones,
    "피할 구역": layers.avoid,
    "센서 (주의 이상)": layers.sensors,
    "안전한 구역도": layers.zonesCalm,
    "센서 전체": layers.sensorsAll,
    "방파제 CCTV": layers.cctv,
  }, { collapsed: true, position: "topleft" }).addTo(map);
  L.control.scale({ imperial: false, position: "bottomleft" }).addTo(map);
  addLegend();

  map.on("click", (ev) => openSpotPopup(ev.latlng.lat, ev.latlng.lng));
  map.on("overlayadd overlayremove", () => drawCctv());

  on("plan trip", () => { drawPlan(); drawLegs(); });
  on("trip motion", drawMe);
  on("motion", drawWalk);
  on("safety", () => { drawZones(); drawSensors(); drawAvoid(); drawCctv(); });
  onEventBubbles(showBubbles);
  requestAnimationFrame(animateMe);
  return map;
}

export function invalidate() {
  map?.invalidateSize();
}

export function focus(lat, lng) {
  map?.panTo([lat, lng]);
}

/** 타임라인 일정 블록에서: 그 장소로 옮기고 말풍선을 연다. */
export function focusItem(id) {
  const m = markers.get(id);
  if (!m) return;
  map.panTo(m.getLatLng());
  m.fire("click");
}

function addLegend() {
  const legend = L.control({ position: "bottomleft" });
  legend.onAdd = () => h("div.legend",
    h("span", h("i", { style: { background: color("caution") } }), "주의"),
    h("span", h("i", { style: { background: color("danger") } }), "위험"),
    h("span", h("i.hatch"), "피할 구역"),
    h("span", h("i.me"), "사용자"));
  legend.addTo(map);
}

// ---------- 말풍선 (조작) ----------

const close = () => map.closePopup();

/** 머물기 분 선택 (3/10/30). */
function stayPicker() {
  const sel = h("select.stay-min", { "aria-label": "머무는 시간", title: "머무는 시간" },
    ...[3, 10, 30].map((m) => h("option", { value: String(m) }, `${m}분`)));
  return sel;
}

function inactiveNote() {
  if (!tripActive()) return h("p.pop-note", "여행을 시작하면 여기로 이동할 수 있어요");
  if (S.motion) return h("p.pop-note", "이동 중이에요 — [멈춤] 후 다시");
  return null;
}

/** 지도 빈 곳: [여기로 이동] [걸어가기] 두 개만 (차로 가기·머물기는 ⋯ 안). */
function openSpotPopup(lat, lng, title = null) {
  const note = inactiveNote();
  const sel = stayPicker();
  const more = h("div.mpop-more", { hidden: true },
    h("button.btn.sm", { onclick: () => { close(); travel(lat, lng, "car", title); } }, "🚗 차로 가기"),
    h("button.btn.sm", { onclick: () => { close(); stay(Number(sel.value) || 3, lat, lng, title); } }, "여기서 머물기"), sel);
  const content = h("div.mpop",
    title ? h("b.mpop-title", title) : null,
    note || h("div",
      h("div.mpop-actions.three",
        h("button.btn", { onclick: () => { close(); teleport(lat, lng, title); } }, "여기로 이동"),
        h("button.btn.primary", { onclick: () => { close(); travel(lat, lng, "walk", title); } }, "걸어가기"),
        h("button.btn.icon", { title: "더 보기 (차로 가기·머물기)", "aria-label": "더 보기", onclick: () => { more.hidden = !more.hidden; } }, "⋯")),
      more));
  L.popup({ minWidth: 200, className: "gm-popup" }).setLatLng([lat, lng]).setContent(content).openOn(map);
}

/** 일정 마커: 이름 · 예정 시각 · 상태 + [여기로 걸어가기] [여기서 머물기 N분] + ⋯(차로 가기·순간이동). */
function itemPopup(it, n) {
  const p = it.place;
  const title = p.title;
  const time = it.start_at ? `${it.start_at.slice(0, 5)}${it.end_at ? `~${it.end_at.slice(0, 5)}` : ""}` : "시각 미정";
  const visiting = S.trip?.current?.state === "visiting" && S.trip.current.item_id === it.id;
  const real = [it.arrived_at && `도착 ${it.arrived_at.slice(0, 5)}`, it.done_at && `떠남 ${it.done_at.slice(0, 5)}`].filter(Boolean).join(" · ");
  const status = (visiting ? "지금 머무는 중" : ITEM_STATUS_KO[it.status] || it.status) + (real ? ` · ${real}` : "");
  const note = inactiveNote();
  const sel = stayPicker();
  const more = h("div.mpop-more", { hidden: true },
    h("button.btn.sm", { onclick: () => { close(); travel(p.lat, p.lng, "car", title); } }, "🚗 차로 가기"),
    h("button.btn.sm", { onclick: () => { close(); teleport(p.lat, p.lng, title); } }, "⚡ 순간이동"));
  return h("div.mpop",
    h("div.mpop-head", h(`span.num.st-${visiting ? "here" : it.status}`, String(n)), h("b.mpop-title", title)),
    h("div.mpop-sub", `${time} · ${status}`),
    note || h("div",
      h("div.mpop-actions",
        h("button.btn.primary", { onclick: () => { close(); travel(p.lat, p.lng, "walk", title); } }, "여기로 걸어가기"),
        h("div.stay-row",
          h("button.btn", { onclick: () => { close(); stay(Number(sel.value) || 3, p.lat, p.lng, title); } }, "여기서 머물기"),
          sel,
          h("button.btn.icon", {
            title: "더 보기 (차로 가기·순간이동)", "aria-label": "더 보기",
            onclick: () => { more.hidden = !more.hidden; },
          }, "⋯"))),
      more));
}

// ---------- 일정 ----------

function drawPlan() {
  layers.plan.clearLayers();
  markers.clear();
  const items = dayItems();
  const cur = S.trip?.current;
  let n = 0;
  for (const it of items) {
    if (it.status === "skipped") continue;
    n += 1;
    if (!it.place) continue;
    const here = cur?.state === "visiting" && cur.item_id === it.id;
    const st = here ? "here" : it.status;
    const num = n;
    const icon = L.divIcon({
      className: "",
      html: `<div class="pin st-${st}"><span>${num}</span></div>`,
      iconSize: [30, 30],
      iconAnchor: [15, 15],
    });
    const m = L.marker([it.place.lat, it.place.lng], { icon, title: it.place.title, zIndexOffset: here ? 800 : 500 });
    m.on("click", () => {
      L.popup({ minWidth: 230, className: "gm-popup", offset: [0, -10] })
        .setLatLng([it.place.lat, it.place.lng]).setContent(itemPopup(it, num)).openOn(map);
    });
    m.bindTooltip(esc(it.place.title), { direction: "right", offset: [14, 0], className: "pin-label", permanent: true });
    layers.plan.addLayer(m);
    markers.set(it.id, m);
  }
}

/** 구간 경로선: POST /routing/route {mode: item.leg_mode, stops: [이전, 이번]} (계획이 바뀔 때만, 캐시). 지난 구간은 흐리게. */
async function drawLegs() {
  const items = dayItems().filter((it) => it.place && it.status !== "skipped");
  const pairs = [];
  for (let i = 1; i < items.length; i++) {
    const a = items[i - 1].place;
    const b = items[i].place;
    const mode = items[i].leg_mode || S.plan?.travel_mode || "walk";
    pairs.push({ mode, a: [a.lat, a.lng], b: [b.lat, b.lng], past: items[i].status === "done" || items[i].status === "visiting" });
  }
  const key = JSON.stringify(pairs);
  if (key === legKey) return;
  legKey = key;
  const lines = await Promise.all(pairs.map(async (p) => {
    const k = `${p.mode}:${p.a}>${p.b}`;
    if (!legCache.has(k)) {
      legCache.set(k, api.post("/routing/route", {
        mode: p.mode, stops: [{ lat: p.a[0], lng: p.a[1] }, { lat: p.b[0], lng: p.b[1] }], steps: false,
      }).then((r) => r.geometry.coordinates.map(([x, y]) => [y, x])).catch(() => { legCache.delete(k); return [p.a, p.b]; }));
    }
    return { ...p, line: await legCache.get(k) };
  }));
  if (key !== legKey) return;
  layers.legs.clearLayers();
  for (const { mode, line, past } of lines) {
    layers.legs.addLayer(L.polyline(line, { color: "#ffffff", weight: 7, opacity: past ? 0.35 : 0.8, interactive: false }));
    layers.legs.addLayer(L.polyline(line, {
      color: mode === "car" ? "#7B61FF" : "#2F6BFF", weight: 4, opacity: past ? 0.3 : 0.85,
      dashArray: mode === "car" ? "8 8" : null, interactive: false,
    }));
  }
}

/** 걸어가는 중이면 그 경로를 굵게. */
function drawWalk() {
  const m = S.motion;
  const key = m?.line ? JSON.stringify(m.line[0]) + m.line.length : "";
  if (layers.walk._key === key) return;
  layers.walk._key = key;
  layers.walk.clearLayers();
  if (m?.line) {
    layers.walk.addLayer(L.polyline(m.line, { color: "#2F6BFF", weight: 6, opacity: 0.35, interactive: false }));
    layers.walk.addLayer(L.polyline(m.line, { color: "#2F6BFF", weight: 2, opacity: 0.9, dashArray: "2 8", interactive: false }));
  }
}

// ---------- 사용자 위치 ----------

function drawMe() {
  const loc = S.trip?.location;
  if (!loc) {
    layers.me.clearLayers();
    meMarker = meCircle = null;
    return;
  }
  const ll = [loc.lat, loc.lng];
  const canDrag = tripActive() && !S.motion;
  if (!meMarker) {
    meCircle = L.circle(ll, { radius: loc.accuracy || 30, color: "#2F6BFF", weight: 1, fillOpacity: 0.1, interactive: false });
    meMarker = L.marker(ll, {
      draggable: true,
      zIndexOffset: 1000,
      icon: L.divIcon({ className: "", html: '<div class="me-dot"></div>', iconSize: [22, 22], iconAnchor: [11, 11] }),
      title: "사용자 (끌어서 놓으면 그 자리로 이동)",
    });
    meMarker.on("dragstart", () => { dragging = true; map.closePopup(); });
    meMarker.on("dragend", async () => {
      const p = meMarker.getLatLng();
      dragging = false;
      await teleport(p.lat, p.lng);
      drawMe(); // 실패했으면 서버 위치로 되돌린다
    });
    meMarker.on("click", () => {
      const l = S.trip?.location;
      if (!l) return;
      const note = inactiveNote();
      const sel = stayPicker();
      L.popup({ minWidth: 200, className: "gm-popup", offset: [0, -8] }).setLatLng([l.lat, l.lng]).setContent(h("div.mpop",
        h("b.mpop-title", "사용자"),
        h("div.mpop-sub", note ? "" : "끌어서 놓으면 그 자리로 이동"),
        note || h("div.stay-row",
          h("button.btn", { onclick: () => { close(); stay(Number(sel.value) || 3); } }, "여기서 머물기"), sel))).openOn(map);
    });
    layers.me.addLayer(meCircle);
    layers.me.addLayer(meMarker);
  }
  if (!animating()) {
    if (!dragging) meMarker.setLatLng(ll);
    meCircle.setLatLng(ll);
  }
  meCircle.setRadius(loc.accuracy || 30);
  if (meMarker.dragging) canDrag ? meMarker.dragging.enable() : meMarker.dragging.disable();
}

/** 걷기·차로 가기 중이면 마커를 매 프레임 움직인다 (서버 전송은 25m·60m 점마다 그대로). */
const animating = () => !!(S.motion?.path && S.motion.clock);

let pathIdx = 0;
/** path [{ll, t}] 위에서 가상 시각 t 의 위치 (선형 보간). */
function posAt(path, t) {
  if (t <= path[0].t) return path[0].ll;
  if (t >= path[path.length - 1].t) return path[path.length - 1].ll;
  if (pathIdx >= path.length - 1 || path[pathIdx].t > t) pathIdx = 0;
  while (pathIdx < path.length - 2 && path[pathIdx + 1].t <= t) pathIdx += 1;
  const a = path[pathIdx];
  const b = path[pathIdx + 1];
  const f = b.t > a.t ? (t - a.t) / (b.t - a.t) : 1;
  return [a.ll[0] + (b.ll[0] - a.ll[0]) * f, a.ll[1] + (b.ll[1] - a.ll[1]) * f];
}

function animateMe() {
  requestAnimationFrame(animateMe);
  if (!animating() || !meMarker || dragging) return;
  // 화면 시각(simNow)은 다음 전송 점 시각에서 멈추므로 마커가 서버보다 앞서 가지 않는다
  const ll = posAt(S.motion.path, simNow());
  meMarker.setLatLng(ll);
  meCircle.setLatLng(ll);
  // 따라가기: 가운데에서 1px 넘게 벗어나면 그 프레임에 옮긴다 (애니메이션 없이 — 마커와 지도가 함께 부드럽게)
  if (prefs.follow && map.latLngToContainerPoint(ll).distanceTo(map.getSize().divideBy(2)) > 1) {
    map.panTo(ll, { animate: false });
  }
}

// ---------- 안전 ----------

function zoneTooltip(z, st) {
  const lines = [`<b>${esc(z.name)}</b> · ${riskKo(st?.risk)}`];
  for (const r of st?.reasons || []) lines.push(esc(r));
  return lines.join("<br>");
}

function drawZones() {
  layers.zones.clearLayers();
  layers.zonesCalm.clearLayers();
  const byId = new Map((S.status?.zones || []).map((z) => [z.id, z]));
  for (const z of S.zones || []) {
    const st = byId.get(z.id);
    const group = HOT(st?.risk) ? layers.zones : layers.zonesCalm;
    const c = color(st?.risk);
    const strong = st?.risk === "danger";
    let shape;
    if (z.path.length === 1) {
      shape = L.circle(z.path[0], { radius: z.radius_m, color: c, weight: strong ? 2.5 : 1.5, fillColor: c, fillOpacity: strong ? 0.22 : 0.14 });
    } else {
      // 방파제: 선 + 반경 버퍼. 굵은 반투명 선 위에 가는 선
      group.addLayer(L.polyline(z.path, { color: c, weight: 16, opacity: 0.25, interactive: false }));
      shape = L.polyline(z.path, { color: c, weight: 5, opacity: 0.95 });
    }
    shape.bindTooltip(() => zoneTooltip(z, byId.get(z.id)), { sticky: true });
    shape.on("click", (ev) => { L.DomEvent.stopPropagation(ev); openSpotPopup(ev.latlng.lat, ev.latlng.lng, z.name); });
    group.addLayer(shape);
  }
}

function drawSensors() {
  layers.sensors.clearLayers();
  layers.sensorsAll.clearLayers();
  for (const s of S.status?.sensors || []) {
    const c = color(s.risk);
    const tip = `<b>${esc(s.name)}</b><br>${SENSOR_TYPE_KO[s.type] || esc(s.type)} · 값 ${s.value ?? "-"} · 등급 ${s.level ?? "-"} · ${riskKo(s.risk)}` +
      (s.note ? `<br>${esc(s.note)}` : "");
    const mk = () => L.circleMarker([s.lat, s.lng], { radius: 6, color: "#fff", weight: 2, fillColor: c, fillOpacity: 0.95 }).bindTooltip(tip);
    layers.sensorsAll.addLayer(mk());
    if (HOT(s.risk)) layers.sensors.addLayer(mk());
  }
}

/** 방파제 CCTV 최근 1시간 집계 (레이어를 켰을 때만). */
function drawCctv() {
  layers.cctv.clearLayers();
  if (!map.hasLayer(layers.cctv)) return;
  for (const z of S.zones || []) {
    if (z.kind !== "breakwater") continue;
    const side = z.id.includes("north") ? "north" : z.id.includes("south") ? "south" : null;
    const act = (S.status?.breakwater_activity || []).find((a) => a.side === side);
    const mid = z.path[Math.floor(z.path.length / 2)];
    const text = act
      ? `📹 ${esc(z.name)} · ${act.hour.slice(11, 16)}부터 1시간<br>실족 ${act.fall} · 침입 ${act.intrusion} · 접근 ${act.approach} · 이상 ${act.abnormal}`
      : `📹 ${esc(z.name)} · 집계 없음`;
    layers.cctv.addLayer(L.tooltip({ permanent: true, direction: "right", className: "cctv-tip", interactive: false })
      .setLatLng(mid).setContent(text));
  }
}

function drawAvoid() {
  layers.avoid.clearLayers();
  for (const z of S.avoid?.zones || []) {
    const ring = z.polygon.map(([lng, lat]) => [lat, lng]); // [lng, lat] -> [lat, lng]
    const poly = L.polygon(ring, { className: "avoid-zone", color: "#E5484D", weight: 1.5, dashArray: "4 3" });
    poly.bindTooltip(`<b>피할 구역</b> · ${esc(z.name)} · ${riskKo(z.risk)}<br>${esc(z.reason)}`, { sticky: true });
    poly.on("click", (ev) => { L.DomEvent.stopPropagation(ev); openSpotPopup(ev.latlng.lat, ev.latlng.lng, z.name); });
    layers.avoid.addLayer(poly);
  }
}

// ---------- 이벤트 말풍선 (2~3초) ----------

function showBubbles(events) {
  const loc = S.trip?.location;
  const base = loc ? [loc.lat, loc.lng] : map.getCenter();
  events.forEach((e, i) => {
    const m = eventMeta(e.type);
    const content = `<div class="bubble sev-${e.severity}${e.level >= 3 ? " ai" : ""}"><span class="ic">${m.icon}</span><span>${esc(e.message)}</span></div>`;
    const tip = L.tooltip({ permanent: true, direction: "top", offset: [0, -16 - i * 40], className: "bubble-tip", opacity: 1 })
      .setLatLng(base).setContent(content);
    layers.bubbles.addLayer(tip);
    setTimeout(() => layers.bubbles.removeLayer(tip), 3000 + i * 500);
  });
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
