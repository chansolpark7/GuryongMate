// 지도 (Leaflet, 전역 L): 오늘 일정·구간 경로선·현재 위치(끌어서 순간이동)·안전 구역·센서·피할 구역
import { api } from "./api.js";
import { S, dayItems, on, tripActive } from "./store.js";
import { stay, teleport, travel } from "./actions.js";
import { EVENT_KO, onEventBubbles } from "./ui.js";
import { h, riskKo } from "./util.js";

const CENTER = [35.990, 129.556];
const RISK_COLOR = { safe: "#2f9e44", caution: "#f08c00", danger: "#e03131" };
const NULL_COLOR = "#868e96";
const ITEM_STATUS_KO = { planned: "예정", visiting: "머무는 중", done: "완료", skipped: "건너뜀" };
const SENSOR_TYPE_KO = { HOLE: "맨홀", ROAD: "지표면", RIVER: "하천", RAIN: "강우" };

let map;
const layers = {};
let meMarker = null;
let meCircle = null;
let dragging = false;
const legCache = new Map(); // "mode:a>b" -> [[lat,lng]…] | Promise
let legKey = "";

const color = (risk) => RISK_COLOR[risk] || NULL_COLOR;

export function initMap() {
  map = L.map("map", { zoomControl: true, attributionControl: true }).setView(CENTER, 15);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);

  layers.zones = L.layerGroup().addTo(map);
  layers.avoid = L.layerGroup().addTo(map);
  layers.sensors = L.layerGroup().addTo(map);
  layers.legs = L.layerGroup().addTo(map);
  layers.plan = L.layerGroup().addTo(map);
  layers.me = L.layerGroup().addTo(map);
  layers.bubbles = L.layerGroup().addTo(map);

  L.control.layers(null, {
    "오늘 일정": layers.plan,
    "구간 경로선": layers.legs,
    "현재 위치": layers.me,
    "안전 구역": layers.zones,
    "센서": layers.sensors,
    "피할 구역": layers.avoid,
  }, { collapsed: true, position: "topright" }).addTo(map);
  L.control.scale({ imperial: false }).addTo(map);
  addLegend();

  map.on("click", (ev) => openPlacePopup(ev.latlng.lat, ev.latlng.lng, null));

  on("plan trip", () => { drawPlan(); drawLegs(); });
  on("trip motion", drawMe);
  on("safety", () => { drawZones(); drawSensors(); drawAvoid(); });
  onEventBubbles(showBubbles);
  return map;
}

export function invalidate() {
  map?.invalidateSize();
}

export function focus(lat, lng) {
  map?.panTo([lat, lng]);
}

function addLegend() {
  const legend = L.control({ position: "bottomleft" });
  legend.onAdd = () => h("div.legend",
    ...[["safe", "안전"], ["caution", "주의"], ["danger", "위험"], [null, "판단 보류"]].map(([r, t]) =>
      h("span", h("i", { style: { background: color(r) } }), t)),
    h("span", h("i.hatch"), "피할 구역"));
  legend.addTo(map);
}

// ---------- 조작 팝업 ----------

/** 지도 클릭·일정 마커 공용 팝업: [여기로 순간이동] [여기로 걸어가기] [차로 가기] [여기서 N분 머물기]. */
function actionButtons(lat, lng, label) {
  const active = tripActive();
  const busy = !!S.motion;
  const dis = !active || busy;
  const minutes = h("input.min", { type: "number", min: 1, max: 180, value: 3, title: "머무는 분" });
  const close = () => map.closePopup();
  const box = h("div.popup-actions",
    h("button", { disabled: dis, onclick: () => { close(); teleport(lat, lng); } }, "여기로 순간이동"),
    h("button", { disabled: dis, onclick: () => { close(); travel(lat, lng, "walk", label); } }, "여기로 걸어가기"),
    h("button", { disabled: dis, onclick: () => { close(); travel(lat, lng, "car", label); } }, "차로 가기"),
    h("span.stay", h("button", {
      disabled: dis,
      onclick: () => { close(); stay(Math.max(1, Number(minutes.value) || 3), lat, lng, label); },
    }, "여기서"), minutes, "분 머물기"));
  if (!active) box.append(h("div.hint", "여행을 먼저 시작하세요"));
  else if (busy) box.append(h("div.hint", "이동 중 — [정지] 후 다시"));
  return box;
}

function openPlacePopup(lat, lng, title) {
  const content = h("div.popup",
    title ? h("b", title) : h("b", "이 지점"),
    h("div.sub", `${lat.toFixed(5)}, ${lng.toFixed(5)}`),
    actionButtons(lat, lng, title));
  L.popup({ minWidth: 200 }).setLatLng([lat, lng]).setContent(content).openOn(map);
}

// ---------- 일정 ----------

function drawPlan() {
  layers.plan.clearLayers();
  const items = dayItems();
  let n = 0;
  for (const it of items) {
    if (!it.place) continue;
    const label = it.status === "skipped" ? "–" : String(++n);
    const icon = L.divIcon({
      className: "",
      html: `<div class="pin st-${it.status}">${label}</div>`,
      iconSize: [26, 26],
      iconAnchor: [13, 13],
    });
    const m = L.marker([it.place.lat, it.place.lng], { icon, title: it.place.title, zIndexOffset: 500 });
    m.on("click", () => {
      const time = it.start_at ? `${it.start_at}~${it.end_at || ""}` : "-";
      const real = [it.arrived_at && `도착 ${it.arrived_at}`, it.done_at && `완료 ${it.done_at}`].filter(Boolean).join(" · ");
      const content = h("div.popup",
        h("b", `${label}. ${it.place.title}`),
        h("div.sub", `예정 ${time} · ${ITEM_STATUS_KO[it.status] || it.status}${real ? ` · ${real}` : ""}`),
        actionButtons(it.place.lat, it.place.lng, it.place.title));
      L.popup({ minWidth: 220 }).setLatLng([it.place.lat, it.place.lng]).setContent(content).openOn(map);
    });
    m.bindTooltip(`${label}. ${esc(it.place.title)}`, { direction: "top", offset: [0, -12] });
    layers.plan.addLayer(m);
  }
}

/** 구간 경로선: POST /routing/route {mode: item.leg_mode, stops: [이전, 이번]} (계획이 바뀔 때만, 캐시). */
async function drawLegs() {
  const items = dayItems().filter((it) => it.place && it.status !== "skipped");
  const pairs = [];
  for (let i = 1; i < items.length; i++) {
    const a = items[i - 1].place;
    const b = items[i].place;
    const mode = items[i].leg_mode || S.plan?.travel_mode || "walk";
    pairs.push({ mode, a: [a.lat, a.lng], b: [b.lat, b.lng] });
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
    return { mode: p.mode, line: await legCache.get(k) };
  }));
  if (key !== legKey) return;
  layers.legs.clearLayers();
  for (const { mode, line } of lines) {
    layers.legs.addLayer(L.polyline(line, {
      color: mode === "car" ? "#7048e8" : "#1c7ed6", weight: 3, opacity: 0.7,
      dashArray: mode === "car" ? "6 6" : null, interactive: false,
    }));
  }
}

// ---------- 현재 위치 ----------

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
    meCircle = L.circle(ll, { radius: loc.accuracy || 30, color: "#1971c2", weight: 1, fillOpacity: 0.12, interactive: false });
    meMarker = L.marker(ll, {
      draggable: true,
      zIndexOffset: 1000,
      icon: L.divIcon({ className: "", html: '<div class="me-dot"></div>', iconSize: [20, 20], iconAnchor: [10, 10] }),
      title: "현재 위치 (끌어서 옮기면 순간이동)",
    });
    meMarker.on("dragstart", () => { dragging = true; });
    meMarker.on("dragend", async () => {
      const p = meMarker.getLatLng();
      dragging = false;
      await teleport(p.lat, p.lng);
      drawMe(); // 실패했으면 서버 위치로 되돌린다
    });
    layers.me.addLayer(meCircle);
    layers.me.addLayer(meMarker);
  }
  if (!dragging) meMarker.setLatLng(ll);
  meCircle.setLatLng(ll).setRadius(loc.accuracy || 30);
  if (meMarker.dragging) canDrag ? meMarker.dragging.enable() : meMarker.dragging.disable();
  const t = loc.recorded_at ? loc.recorded_at.slice(11, 19) : "";
  meMarker.bindTooltip(`현재 위치 ±${Math.round(loc.accuracy || 0)}m · ${t} (${loc.source})${canDrag ? " · 끌어서 순간이동" : ""}`);
}

// ---------- 안전 ----------

function zoneTooltip(z, st) {
  const lines = [`<b>${esc(z.name)}</b> · ${riskKo(st?.risk)}`];
  for (const r of st?.reasons || []) lines.push(esc(r));
  if (z.kind === "breakwater") {
    const side = z.id.includes("north") ? "north" : z.id.includes("south") ? "south" : null;
    const act = (S.status?.breakwater_activity || []).find((a) => a.side === side);
    if (act) {
      lines.push(`CCTV ${act.hour.slice(11, 16)} 1시간: 실족 ${act.fall} · 침입 ${act.intrusion} · 접근 ${act.approach} · 이상 ${act.abnormal}`);
    } else lines.push("CCTV 집계 없음");
  }
  return lines.join("<br>");
}

function drawZones() {
  layers.zones.clearLayers();
  const byId = new Map((S.status?.zones || []).map((z) => [z.id, z]));
  for (const z of S.zones || []) {
    const st = byId.get(z.id);
    const c = color(st?.risk);
    let shape;
    if (z.path.length === 1) {
      shape = L.circle(z.path[0], { radius: z.radius_m, color: c, weight: 2, fillColor: c, fillOpacity: 0.12 });
    } else {
      // 방파제: 선 + 반경 버퍼. 화면에서는 굵은 반투명 선 위에 가는 선
      const glow = L.polyline(z.path, { color: c, weight: 14, opacity: 0.25, interactive: false });
      layers.zones.addLayer(glow);
      shape = L.polyline(z.path, { color: c, weight: 4, opacity: 0.9 });
    }
    shape.bindTooltip(() => zoneTooltip(z, byId.get(z.id)), { sticky: true });
    shape.on("click", (ev) => { L.DomEvent.stopPropagation(ev); openPlacePopup(ev.latlng.lat, ev.latlng.lng, z.name); });
    layers.zones.addLayer(shape);
  }
}

function drawSensors() {
  layers.sensors.clearLayers();
  for (const s of S.status?.sensors || []) {
    const c = color(s.risk);
    const m = L.circleMarker([s.lat, s.lng], { radius: 6, color: "#fff", weight: 1.5, fillColor: c, fillOpacity: 0.95 });
    m.bindTooltip(
      `<b>${esc(s.name)}</b><br>${SENSOR_TYPE_KO[s.type] || esc(s.type)} · 값 ${s.value ?? "-"} · 등급 ${s.level ?? "-"} · ${riskKo(s.risk)}` +
      (s.note ? `<br>${esc(s.note)}` : ""));
    layers.sensors.addLayer(m);
  }
}

function drawAvoid() {
  layers.avoid.clearLayers();
  for (const z of S.avoid?.zones || []) {
    const ring = z.polygon.map(([lng, lat]) => [lat, lng]); // [lng, lat] -> [lat, lng]
    const poly = L.polygon(ring, { className: "avoid-zone", color: "#c92a2a", weight: 1.5, dashArray: "4 3" });
    poly.bindTooltip(`<b>피할 구역</b> · ${esc(z.name)} · ${riskKo(z.risk)}<br>${esc(z.reason)}`, { sticky: true });
    poly.on("click", (ev) => { L.DomEvent.stopPropagation(ev); openPlacePopup(ev.latlng.lat, ev.latlng.lng, z.name); });
    layers.avoid.addLayer(poly);
  }
}

// ---------- 이벤트 말풍선 ----------

function showBubbles(events) {
  const loc = S.trip?.location;
  const base = loc ? [loc.lat, loc.lng] : map.getCenter();
  events.forEach((e, i) => {
    const content = `<div class="bubble sev-${e.severity}"><b>${EVENT_KO[e.type] || esc(e.type)}</b> · 레벨 ${e.level}<br>${esc(e.message)}</div>`;
    const tip = L.tooltip({ permanent: true, direction: "top", offset: [0, -14 - i * 4], className: "bubble-tip", opacity: 0.97 })
      .setLatLng(base).setContent(content);
    layers.bubbles.addLayer(tip);
    setTimeout(() => layers.bubbles.removeLayer(tip), 7000 + i * 600);
  });
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
